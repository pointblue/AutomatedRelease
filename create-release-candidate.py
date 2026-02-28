import asyncio
import json
import httpx
import os
import sys
import re
from dotenv import load_dotenv
from github_utils import get_gh_token, fetch_json_pages, get_main_or_master_branch, check_commit_in_branch


def parse_args():
    if len(sys.argv) < 2:
        print("Missing JSON file argument. Usage: python3 create-release-candidate.py <json_file>")
        sys.exit(1)

    json_file = sys.argv[1]
    if not os.path.exists(json_file):
        print(f"Error: File '{json_file}' not found")
        sys.exit(1)

    return json_file


async def get_existing_rc_info(client, owner, repo_name, version_prefix):
    """
    Check for existing RC PRs for this sprint.
    Returns a tuple: (has_open_rc, open_rc_info, next_rc_number)
    - has_open_rc: True if there's an open RC PR
    - open_rc_info: Dict with PR details if open RC exists, None otherwise
    - next_rc_number: The next RC number to use if creating a new PR
    """
    try:
        url = f"https://api.github.com/repos/{owner}/{repo_name}/pulls"
        params = {
            "state": "all",  # Check both open and closed PRs
            "per_page": 100,
        }

        max_rc = -1
        open_rc_pr = None
        pattern = re.compile(rf"^{re.escape(version_prefix)}-rc(\d+)$")

        async for page in fetch_json_pages(client, url, params=params):
            for pr in page:
                title = pr.get("title", "")
                match = pattern.match(title)
                if match:
                    rc_num = int(match.group(1))
                    max_rc = max(max_rc, rc_num)

                    # Check if this PR is open
                    if pr.get("state") == "open" and open_rc_pr is None:
                        open_rc_pr = {
                            "number": pr.get("number"),
                            "title": title,
                            "url": pr.get("html_url"),
                            "rc_number": rc_num
                        }

        has_open_rc = open_rc_pr is not None
        next_rc = max_rc + 1

        return has_open_rc, open_rc_pr, next_rc
    except Exception as e:
        print(f"Warning: Error checking existing RCs for {owner}/{repo_name}: {e}", file=sys.stderr)
        return False, None, 0


async def create_release_pr(client, owner, repo_name, release_branch, version_title, dry_run=False):
    """
    Create a PR from dev to release branch (main/master).
    Returns True if successful, False otherwise.
    """
    try:
        # Check if dev branch exists
        dev_check_url = f"https://api.github.com/repos/{owner}/{repo_name}/branches/dev"
        dev_response = await client.get(dev_check_url)
        if dev_response.status_code != 200:
            print(f"  [WARNING] Dev branch not found for {owner}/{repo_name}", file=sys.stderr)
            return False

        if dry_run:
            print(f"  [DRY RUN] Would create PR: {version_title} from dev -> {release_branch}")
            return True

        # Create the PR
        pr_url = f"https://api.github.com/repos/{owner}/{repo_name}/pulls"
        pr_data = {
            "title": version_title,
            "head": "dev",
            "base": release_branch,
            "body": f"Release candidate {version_title}\n\nAutomatically generated release candidate PR.",
        }

        response = await client.post(pr_url, json=pr_data)

        if response.status_code == 201:
            pr_info = response.json()
            print(f"  [SUCCESS] Created PR #{pr_info['number']}: {pr_info['html_url']}")
            return True
        elif response.status_code == 422:
            # PR might already exist or no changes between branches
            error_data = response.json()
            error_msg = error_data.get("errors", [{}])[0].get("message", "")
            if "No commits between" in error_msg or "pull request already exists" in error_msg:
                print(f"  [INFO] PR already exists or no changes between dev and {release_branch}", file=sys.stderr)
            else:
                print(f"  [ERROR] Failed to create PR: {error_data.get('message', 'Unknown error')}", file=sys.stderr)
            return False
        elif response.status_code == 403:
            # Permission denied - likely token doesn't have write access
            try:
                error_data = response.json()
                error_message = error_data.get('message', 'Permission denied')
            except:
                error_message = 'Permission denied'
            print(f"  [ERROR] Failed to create PR (HTTP 403): {error_message}", file=sys.stderr)
            print(f"  [ERROR] Your GitHub token may not have write access to this repository.", file=sys.stderr)
            print(f"  [ERROR] Required scopes: 'repo' or 'public_repo' (for public repos)", file=sys.stderr)
            return False
        else:
            try:
                error_data = response.json()
                error_message = error_data.get('message', 'Unknown error')
            except:
                error_message = 'Unknown error'
            print(f"  [ERROR] Failed to create PR (HTTP {response.status_code}): {error_message}", file=sys.stderr)
            return False

    except httpx.HTTPError as exc:
        print(f"  [ERROR] HTTP error creating PR for {owner}/{repo_name}: {exc}", file=sys.stderr)
        return False
    except Exception as exc:
        print(f"  [ERROR] Error creating PR for {owner}/{repo_name}: {exc}", file=sys.stderr)
        return False


async def has_unreleased_prs(client, owner, repo_name, release_branch, pull_requests):
    """
    Determine if any PR commit is not yet in the release branch.
    """
    for pr in pull_requests:
        commit_id = pr.get("commit_id")
        if not commit_id:
            # If the source data does not include a commit id, treat as unreleased.
            return True
        in_release = await check_commit_in_branch(client, owner, repo_name, commit_id, release_branch)
        if not in_release:
            return True
    return False


async def process_repository(client, repo_name, pull_requests, version_prefix, semaphore, dry_run=False):
    """Process a single repository and create release candidate PR."""
    async with semaphore:
        try:
            owner, repo = repo_name.split("/")

            release_branch = await get_main_or_master_branch(client, owner, repo)
            if not release_branch:
                print(f"[WARNING] {repo_name}: No release branch found", file=sys.stderr)
                return repo_name, False, None, "no_release_branch"

            if not await has_unreleased_prs(client, owner, repo, release_branch, pull_requests):
                print(f"[SKIP] {repo_name}: Skipping (all PRs already in {release_branch})", file=sys.stderr)
                return repo_name, False, None, "all_released"

            # Check for existing RC PRs
            has_open_rc, open_rc_info, next_rc = await get_existing_rc_info(client, owner, repo, version_prefix)

            if has_open_rc:
                # There's already an open RC PR - skip creating a new one
                print(f"[SKIP] {repo_name}: Open RC PR already exists")
                print(f"       PR #{open_rc_info['number']}: {open_rc_info['title']}")
                print(f"       URL: {open_rc_info['url']}")
                print(f"       Merge this PR before creating a new release candidate")
                return repo_name, False, open_rc_info, "open_rc_exists"

            version_title = f"{version_prefix}-rc{next_rc}"
            print(f"[PROCESSING] {repo_name}: Creating {version_title} (dev -> {release_branch})")
            success = await create_release_pr(client, owner, repo, release_branch, version_title, dry_run)

            return repo_name, success, None, None
        except Exception as exc:
            print(f"[ERROR] Error processing {repo_name}: {exc}", file=sys.stderr)
            return repo_name, False, None, "error"


async def main():
    load_dotenv()
    json_file = parse_args()
    github_token = get_gh_token()

    # Check for dry-run flag
    dry_run = "--dry-run" in sys.argv

    # Load JSON input
    with open(json_file, 'r') as fh:
        input_data = json.load(fh)

    sprint_label = input_data.get('sprint_label', '')

    # Extract version from sprint label (e.g., "Sprint v2026.10 ..." -> "v2026.10")
    version_match = re.search(r'(v\d{4}\.\d{2})', sprint_label)
    if not version_match:
        print("Error: Could not extract version from sprint_label", file=sys.stderr)
        sys.exit(1)

    version_prefix = version_match.group(1)

    print(f"Creating release candidates for {version_prefix}")
    print(f"Organization: {input_data.get('organization')}")
    print(f"Sprint: {sprint_label}")
    if dry_run:
        print("[DRY RUN MODE] No PRs will be created")
    print()

    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        # Process all repositories concurrently
        semaphore = asyncio.Semaphore(5)  # Limit to 5 concurrent to avoid rate limiting on writes
        tasks = []

        for repo_data in input_data.get("release_prs", []):
            repo_name = repo_data.get("repository")
            pull_requests = repo_data.get("pull_requests", [])

            if repo_name:
                task = asyncio.create_task(
                    process_repository(client, repo_name, pull_requests, version_prefix, semaphore, dry_run)
                )
                tasks.append(task)

        # Collect results
        results = []
        skipped_open_prs = []
        skipped_all_released = 0
        skipped_no_release_branch = 0
        for task in asyncio.as_completed(tasks):
            repo_name, success, open_rc_info, skip_reason = await task
            results.append((repo_name, success))
            if open_rc_info:
                skipped_open_prs.append((repo_name, open_rc_info))
            if skip_reason == "all_released":
                skipped_all_released += 1
            elif skip_reason == "no_release_branch":
                skipped_no_release_branch += 1

        # Print summary
        print("\n" + "="*60)
        print("SUMMARY")
        print("="*60)
        successful = sum(1 for _, success in results if success)
        skipped = len(skipped_open_prs)
        failed = len(results) - successful - skipped - skipped_all_released - skipped_no_release_branch
        total = len(results)
        print(f"Successful (created RC PRs): {successful}/{total}")
        print(f"Skipped (open RC exists): {skipped}/{total}")
        print(f"Skipped (all PRs in release branch): {skipped_all_released}/{total}")
        print(f"Skipped (no release branch): {skipped_no_release_branch}/{total}")
        print(f"Failed: {failed}/{total}")

        if skipped_open_prs:
            print("\n" + "="*60)
            print("SKIPPED - OPEN RC PRs REQUIRING MERGE")
            print("="*60)
            for repo_name, open_rc_info in skipped_open_prs:
                print(f"{repo_name}")
                print(f"  PR #{open_rc_info['number']}: {open_rc_info['title']}")
                print(f"  {open_rc_info['url']}")

        if dry_run:
            print("\n[DRY RUN] This was a dry run. Use without --dry-run to actually create PRs.")


if __name__ == "__main__":
    asyncio.run(main())
