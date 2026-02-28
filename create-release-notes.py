import asyncio
import difflib
import httpx
import os
import sys
import re
from datetime import datetime, timezone
from dotenv import load_dotenv
from github_utils import get_gh_token, fetch_json_pages, get_main_or_master_branch, get_deployable_topic


def _last_even_iso_week(year):
    last_week = datetime(year, 12, 28).isocalendar()[1]
    if last_week % 2 != 0:
        last_week -= 1
    return last_week


def parse_args():
    env_org_name = os.getenv("ORG_NAME")
    env_team_name = os.getenv("TEAM_NAME")

    org_name = None
    team_name = None
    repo_name_filter = None
    target_week = None

    for arg in sys.argv[1:]:
        lower = arg.lower()
        if lower.startswith(("org=", "org_name=", "org-name=")):
            try:
                org_name = arg.split("=", 1)[1]
                if not org_name:
                    raise ValueError
            except ValueError:
                print("Invalid org argument. Use org=<org name> (or org-name=<org name>).")
                sys.exit(1)
        elif lower.startswith(("team=", "team_name=", "team-name=")):
            try:
                team_name = arg.split("=", 1)[1]
                if not team_name:
                    raise ValueError
            except ValueError:
                print("Invalid team argument. Use team=<team name> (or team-name=<team name>).")
                sys.exit(1)
        elif lower.startswith(("name=", "repo=", "repo_name=", "repo-name=")):
            try:
                repo_name_filter = arg.split("=", 1)[1].strip()
                if not repo_name_filter:
                    raise ValueError
            except ValueError:
                print("Invalid name argument. Use name=<repo name> (or repo=<repo name>).")
                sys.exit(1)
        elif lower.startswith("week="):
            try:
                week_str = arg.split("=", 1)[1]
                year_str, week_num_str = week_str.split(".")
                year = int(year_str)
                week_num = int(week_num_str)
                if week_num < 1 or week_num > 53:
                    print("Week number must be between 1 and 53")
                    sys.exit(1)
                if week_num % 2 != 0:
                    print("Week number must be even (sprint end week)")
                    sys.exit(1)
                target_week = (year, week_num)
            except (ValueError, IndexError):
                print("Invalid week format. Use week=YYYY.WW (e.g., week=2026.08)")
                sys.exit(1)
        else:
            print("Unrecognized argument.")
            print("Usage: python3 create-release-notes.py [org=<org>] [team=<team>] [name=<repo>] [week=YYYY.WW]")
            sys.exit(1)

    if org_name is None:
        org_name = env_org_name
    if team_name is None:
        team_name = env_team_name

    if not org_name:
        print("Missing organization name. Provide it as an argument or set ORG_NAME in .env.")
        print("Usage: python3 create-release-notes.py [org=<org>] [team=<team>] [name=<repo>] [week=YYYY.WW]")
        sys.exit(1)

    if repo_name_filter and target_week is None:
        print("When using name=<repo>, you must also provide week=YYYY.WW.")
        sys.exit(1)

    # If no week specified, use current or most recent even week
    if not target_week:
        today = datetime.now(timezone.utc)
        iso_year, iso_week, _ = today.isocalendar()
        if iso_week % 2 == 0:
            target_week = (iso_year, iso_week)
        else:
            week_num = iso_week - 1
            if week_num < 1:
                iso_year -= 1
                week_num = _last_even_iso_week(iso_year)
            target_week = (iso_year, week_num)

    return org_name, team_name, repo_name_filter, target_week


def _select_repositories_by_name(repos, repo_name_filter):
    target = repo_name_filter.strip().lower()
    target_repo_name = target.split("/", 1)[-1]

    exact_matches = [
        repo for repo in repos
        if repo.get("name", "").lower() == target_repo_name
        or repo.get("full_name", "").lower() == target
        or repo.get("full_name", "").lower().endswith(f"/{target_repo_name}")
    ]
    if exact_matches:
        return exact_matches, None

    by_name = {repo.get("name", "").lower(): repo for repo in repos if repo.get("name")}
    by_full_name = {repo.get("full_name", "").lower(): repo for repo in repos if repo.get("full_name")}
    candidates = list(by_name.keys()) + list(by_full_name.keys())
    close = difflib.get_close_matches(target, candidates, n=1, cutoff=0.75)
    if not close:
        close = difflib.get_close_matches(target_repo_name, list(by_name.keys()), n=1, cutoff=0.75)

    if close:
        key = close[0]
        matched_repo = by_name.get(key) or by_full_name.get(key)
        if matched_repo:
            return [matched_repo], matched_repo.get("full_name")

    return [], None


async def get_repositories(client, org_name, team_name=None):
    """Fetch repositories for the organization or team."""
    if team_name:
        base_url = f"https://api.github.com/orgs/{org_name}/teams/{team_name}/repos"
    else:
        base_url = f"https://api.github.com/orgs/{org_name}/repos"
    params = {"per_page": 100, "type": "all", "sort": "full_name"}
    repos = []
    async for page in fetch_json_pages(client, base_url, params=params):
        repos.extend(page)
    return repos


async def find_rc_prs(client, owner, repo_name, release_branch, version_prefix):
    """
    Find the current and previous RC PRs for this version.
    Returns (current_rc, previous_rc)
    current_rc/previous_rc are dicts with: sha, title, pr_number, merged_at
    """
    try:
        url = f"https://api.github.com/repos/{owner}/{repo_name}/pulls"
        params = {
            "state": "closed",
            "base": release_branch,
            "sort": "updated",
            "direction": "desc",
            "per_page": 100,
        }

        # Find current version RCs
        current_pattern = re.compile(rf"^{re.escape(version_prefix)}-rc(\d+)$")
        # Find ANY RC pattern (for previous versions)
        any_rc_pattern = re.compile(r"^v\d{4}\.\d{2}-rc(\d+)$")

        current_rcs = []
        all_rcs = []

        async for page in fetch_json_pages(client, url, params=params):
            for pr in page:
                # Only consider merged PRs
                merged_at = pr.get("merged_at")
                if not merged_at:
                    continue

                # Parse merge date for sorting
                if merged_at.endswith("Z"):
                    merged_at = merged_at[:-1] + "+00:00"
                from datetime import datetime, timezone
                merged_date = datetime.fromisoformat(merged_at).astimezone(timezone.utc)

                title = pr.get("title", "").strip()  # Remove leading/trailing whitespace
                merge_commit_sha = pr.get("merge_commit_sha")
                pr_number = pr.get("number")

                if not merge_commit_sha:
                    continue

                # Check if this is a current version RC
                current_match = current_pattern.match(title)
                if current_match:
                    rc_num = int(current_match.group(1))
                    current_rcs.append((rc_num, title, merge_commit_sha, pr_number, merged_date))

                # Check if this is any RC (for finding previous)
                any_match = any_rc_pattern.match(title)
                if any_match:
                    all_rcs.append((title, merge_commit_sha, pr_number, merged_date))

        if not current_rcs:
            return None, None

        # Sort current RCs by RC number descending
        current_rcs.sort(reverse=True)
        current_rc = current_rcs[0]
        current_sha = current_rc[2]
        current_pr_number = current_rc[3]
        current_pr_link = f"[#{current_pr_number}](https://github.com/{owner}/{repo_name}/pull/{current_pr_number})"
        current_title = f"{current_rc[1]} (PR {current_pr_link})"
        current_merge_date = current_rc[4]
        current_rc_info = {
            "sha": current_sha,
            "title": current_title,
            "pr_number": current_pr_number,
            "merged_at": current_merge_date,
        }

        # Sort all RCs by merge date descending
        all_rcs.sort(key=lambda x: x[3], reverse=True)

        # Find the previous RC (first RC that's not the current version and was merged before current)
        previous_rc_info = None
        for title, merge_sha, pr_num, merge_date in all_rcs:
            if not current_pattern.match(title) and merge_date < current_merge_date:
                # This is a different version merged before current - it's the previous one
                previous_pr_link = f"[#{pr_num}](https://github.com/{owner}/{repo_name}/pull/{pr_num})"
                previous_rc_info = {
                    "sha": merge_sha,
                    "title": f"{title} (PR {previous_pr_link})",
                    "pr_number": pr_num,
                    "merged_at": merge_date,
                }
                break

        return current_rc_info, previous_rc_info

    except Exception as e:
        print(f"Warning: Error finding RC PRs for {owner}/{repo_name}: {e}", file=sys.stderr)
        return None, None


async def get_commit_range(client, owner, repo_name, from_commit_sha, to_commit_sha):
    """
    Get all commits between two RC merge commits.
    Returns list of commit objects.
    """
    try:
        url = f"https://api.github.com/repos/{owner}/{repo_name}/compare/{from_commit_sha}...{to_commit_sha}"
        response = await client.get(url)
        response.raise_for_status()
        compare_data = response.json()
        return compare_data.get("commits", [])
    except Exception as e:
        print(f"Warning: Error getting commit range for {owner}/{repo_name}: {e}", file=sys.stderr)
        return []


async def get_pr_commits(client, owner, repo_name, pr_number):
    """
    Get commits that were part of a specific RC PR.
    Used when there is no previous RC baseline.
    """
    try:
        url = f"https://api.github.com/repos/{owner}/{repo_name}/pulls/{pr_number}/commits"
        params = {"per_page": 100}
        commits = []
        async for page in fetch_json_pages(client, url, params=params):
            commits.extend(page)
        return commits

    except Exception as e:
        print(f"Warning: Error getting commits for PR #{pr_number} in {owner}/{repo_name}: {e}", file=sys.stderr)
        return []


def extract_first_paragraph(commit_message):
    """Extract the first paragraph from a commit message."""
    if not commit_message:
        return ""

    lines = commit_message.split('\n')
    if not lines:
        return ""

    # First line is the subject
    first_line = lines[0].strip()

    # Look for body after blank line
    paragraph_lines = [first_line]
    in_body = False

    for line in lines[1:]:
        stripped = line.strip()

        if not in_body and not stripped:
            in_body = True
            continue

        if in_body:
            if not stripped:
                # End of first paragraph
                break
            paragraph_lines.append(stripped)

    return ' '.join(paragraph_lines)


def extract_first_non_empty_paragraph(text):
    """Extract the first non-empty paragraph from free-form text."""
    if not text:
        return ""

    lines = text.splitlines()
    paragraph_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if paragraph_lines:
                break
            continue
        paragraph_lines.append(stripped)

    return " ".join(paragraph_lines)


def link_pbt_issues(text):
    """Convert PBT-XXXX references into markdown links to GitLab issues."""
    if not text:
        return text

    pattern = re.compile(r"\bPBT-(\d{4})\b")
    return pattern.sub(
        lambda m: (
            f"[PBT-{m.group(1)}]"
            f"(https://pblgssgitlab01.aws.pointblue.org/point-blue-engineering-team/point-blue-tech/-/issues/{m.group(1)})"
        ),
        text,
    )


async def process_repository(client, repo, version_prefix, semaphore):
    """Process a single repository to generate release notes."""
    async with semaphore:
        try:
            owner = repo["owner"]["login"]
            repo_name = repo["name"]
            full_name = repo["full_name"]

            # Get the release branch
            release_branch = await get_main_or_master_branch(client, owner, repo_name)
            if not release_branch:
                return None

            # Find the current and previous RC PRs
            current_rc, previous_rc = await find_rc_prs(client, owner, repo_name, release_branch, version_prefix)

            if not current_rc:
                return None

            # Build notes from RC-generated history:
            # - If previous RC exists: commits between previous RC merge and current RC merge.
            # - If no previous RC: commits included in the current RC PR itself.
            if previous_rc:
                commits = await get_commit_range(client, owner, repo_name, previous_rc["sha"], current_rc["sha"])
            else:
                commits = await get_pr_commits(client, owner, repo_name, current_rc["pr_number"])

            # Extract only PR-merge information from commits.
            # Filter out RC commits and non-PR commits.
            rc_pattern = re.compile(r'v\d{4}\.\d{2}-rc\d+')
            pr_pattern = re.compile(r'Merge pull request #(\d+)')

            commit_notes = []
            for commit in commits:
                commit_data = commit.get("commit", {})
                message = commit_data.get("message", "")
                full_sha = commit.get("sha", "")
                sha = full_sha[:7]

                # Skip RC merge commits
                if rc_pattern.search(message):
                    continue

                # Keep only commits that are explicit PR merge commits.
                pr_match = pr_pattern.search(message)
                if not pr_match:
                    continue

                pr_number = pr_match.group(1)
                # Fetch the PR to get its title/body details.
                try:
                    pr_url = f"https://api.github.com/repos/{owner}/{repo_name}/pulls/{pr_number}"
                    pr_response = await client.get(pr_url)
                    pr_response.raise_for_status()
                    pr_data = pr_response.json()
                    pr_title = pr_data.get("title", "")
                    pr_body = pr_data.get("body", "")
                    pr_html_url = pr_data.get("html_url") or f"https://github.com/{full_name}/pull/{pr_number}"
                    pr_first_paragraph = extract_first_non_empty_paragraph(pr_body)

                    if pr_title:
                        # Create GitHub commit URL
                        commit_url = f"https://github.com/{full_name}/commit/{full_sha}"
                        message = f"[#{pr_number}]({pr_html_url}): {pr_title}"
                        if pr_first_paragraph:
                            message += f" - {pr_first_paragraph}"
                        message = link_pbt_issues(message)
                        commit_notes.append({
                            "sha": sha,
                            "message": message,
                            "url": commit_url
                        })
                except Exception:
                    # Keep merge entry even if PR fetch fails.
                    first_para = extract_first_paragraph(message)
                    if first_para:
                        commit_url = f"https://github.com/{full_name}/commit/{full_sha}"
                        commit_notes.append({
                            "sha": sha,
                            "message": first_para,
                            "url": commit_url
                        })

            return {
                "repo": full_name,
                "release_branch": release_branch,
                "current_rc": current_rc["title"],
                "previous_rc": previous_rc["title"] if previous_rc else None,
                "commits": commit_notes
            }

        except Exception as exc:
            print(f"Error processing {repo.get('full_name')}: {exc}", file=sys.stderr)
            return None


async def main():
    load_dotenv()
    org_name, team_name, repo_name_filter, target_week = parse_args()
    github_token = get_gh_token()
    deployable_topic = get_deployable_topic()

    year, week_num = target_week
    version_prefix = f"v{year}.{week_num:02d}"

    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        # Get repositories
        repos = await get_repositories(client, org_name, team_name)
        deployable_repos = [repo for repo in repos if deployable_topic in (repo.get("topics") or [])]
        resolved_repo_name = None
        if repo_name_filter:
            deployable_repos, resolved_repo_name = _select_repositories_by_name(deployable_repos, repo_name_filter)

        # Process repositories concurrently
        semaphore = asyncio.Semaphore(8)
        tasks = [
            asyncio.create_task(process_repository(client, repo, version_prefix, semaphore))
            for repo in deployable_repos
        ]

        repo_results = []
        for task in asyncio.as_completed(tasks):
            result = await task
            if result:
                repo_results.append(result)

        # Sort by repo name
        repo_results.sort(key=lambda x: x["repo"])

        # Generate markdown output
        print(f"# Release Notes - {version_prefix}\n")
        print(f"**Organization:** {org_name}\n")
        if repo_name_filter:
            print(f"**Repository Filter:** {repo_name_filter}")
            if resolved_repo_name:
                print(f"**Resolved Repository:** {resolved_repo_name}")
            print()

        if repo_results:
            for repo_data in repo_results:
                print(f"## {repo_data['repo']}\n")
                print(f"**Branch:** {repo_data['release_branch']}")
                print(f"**Current RC:** {repo_data['current_rc']}")
                if repo_data['previous_rc']:
                    print(f"**Previous RC:** {repo_data['previous_rc']}")
                else:
                    print(f"**Previous RC:** (no previous RC PR)")
                print()

                for commit in repo_data['commits']:
                    print(f"- `{commit['sha']}` {commit['message']}")
                print()
        else:
            print(f"*No repositories with merged RC PRs found for {version_prefix}.*\n")


if __name__ == "__main__":
    asyncio.run(main())
