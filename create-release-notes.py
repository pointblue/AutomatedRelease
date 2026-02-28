import asyncio
import json
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
    """Parse command line arguments."""
    if len(sys.argv) < 2:
        print("Missing organization name argument. Usage: python3 create-release-notes.py <org_name> [team_name] [week=YYYY.WW]")
        sys.exit(1)

    org_name = sys.argv[1]
    team_name = None
    target_week = None

    for arg in sys.argv[2:]:
        if arg.startswith("week="):
            # Parse week argument in format: week=YYYY.WW (e.g., week=2026.08)
            try:
                week_str = arg.split("=")[1]
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
        elif team_name is None:
            team_name = arg
        else:
            print("Too many arguments. Usage: python3 create-release-notes.py <org_name> [team_name] [week=YYYY.WW]")
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

    return org_name, team_name, target_week


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
    Returns (current_rc_sha, current_rc_title, previous_rc_sha, previous_rc_title)
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
            return None, None, None, None

        # Sort current RCs by RC number descending
        current_rcs.sort(reverse=True)
        current_rc = current_rcs[0]
        current_sha = current_rc[2]
        current_pr_link = f"[#{current_rc[3]}](https://github.com/{owner}/{repo_name}/pull/{current_rc[3]})"
        current_title = f"{current_rc[1]} (PR {current_pr_link})"
        current_merge_date = current_rc[4]

        # Sort all RCs by merge date descending
        all_rcs.sort(key=lambda x: x[3], reverse=True)

        # Find the previous RC (first RC that's not the current version and was merged before current)
        previous_sha = None
        previous_title = None
        for title, merge_sha, pr_num, merge_date in all_rcs:
            if not current_pattern.match(title) and merge_date < current_merge_date:
                # This is a different version merged before current - it's the previous one
                previous_sha = merge_sha
                previous_pr_link = f"[#{pr_num}](https://github.com/{owner}/{repo_name}/pull/{pr_num})"
                previous_title = f"{title} (PR {previous_pr_link})"
                break

        return current_sha, current_title, previous_sha, previous_title

    except Exception as e:
        print(f"Warning: Error finding RC PRs for {owner}/{repo_name}: {e}", file=sys.stderr)
        return None, None, None, None


async def get_commit_range(client, owner, repo_name, release_branch, from_commit_sha):
    """
    Get all commits from from_commit_sha to HEAD of release branch.
    Returns list of commit objects (only merge commits from PRs).
    """
    try:
        if from_commit_sha:
            url = f"https://api.github.com/repos/{owner}/{repo_name}/compare/{from_commit_sha}...{release_branch}"
        else:
            # No previous RC PR, get all commits on release branch
            url = f"https://api.github.com/repos/{owner}/{repo_name}/commits"
            params = {"sha": release_branch, "per_page": 100}
            commits = []
            async for page in fetch_json_pages(client, url, params=params):
                commits.extend(page)
            # Filter to only include merge commits (commits with more than 1 parent)
            return [c for c in commits if len(c.get("parents", [])) > 1]

        response = await client.get(url)
        response.raise_for_status()
        compare_data = response.json()

        commits = compare_data.get("commits", [])
        # Filter to only include merge commits (commits with more than 1 parent)
        merge_commits = [c for c in commits if len(c.get("parents", [])) > 1]

        return merge_commits

    except Exception as e:
        print(f"Warning: Error getting commit range for {owner}/{repo_name}: {e}", file=sys.stderr)
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


async def check_rc_exists(client, owner, repo_name, release_branch, version_prefix):
    """
    Check if there is a merged RC PR for this version prefix.
    Returns True if an RC PR exists (merged), False otherwise.
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

        pattern = re.compile(rf"^{re.escape(version_prefix)}-rc(\d+)$")

        async for page in fetch_json_pages(client, url, params=params):
            for pr in page:
                merged_at = pr.get("merged_at")
                if not merged_at:
                    continue

                # Check if this is an RC PR for this version
                title = pr.get("title", "").strip()  # Remove leading/trailing whitespace
                if pattern.match(title):
                    return True

        return False
    except Exception as e:
        print(f"Warning: Error checking RC PRs for {owner}/{repo_name}: {e}", file=sys.stderr)
        return False


async def process_repository(client, repo, version_prefix, sprint_start, sprint_end, semaphore):
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

            # Check if there is a merged RC PR for this version
            has_rc_pr = await check_rc_exists(client, owner, repo_name, release_branch, version_prefix)
            if not has_rc_pr:
                return None

            # Find the current and previous RC PRs
            current_rc_sha, current_rc_title, previous_rc_sha, previous_rc_title = await find_rc_prs(client, owner, repo_name, release_branch, version_prefix)

            if not current_rc_sha:
                return None

            # Get commits from previous RC to current RC
            commits = await get_commit_range(client, owner, repo_name, release_branch, previous_rc_sha if previous_rc_sha else current_rc_sha)

            if not commits:
                # Still return the repo even if no commits, to show it was included
                pass

            # Extract PR information from merge commits
            # Filter out RC merge commits (e.g., "Merge pull request #123 from org/dev v2026.10-rc0")
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

                # Extract PR number from merge commit
                pr_match = pr_pattern.search(message)
                if pr_match:
                    pr_number = pr_match.group(1)
                    # Fetch the PR to get its title
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
                    except Exception as e:
                        # If we can't fetch the PR, fall back to using the commit message
                        first_para = extract_first_paragraph(message)
                        if first_para:
                            commit_url = f"https://github.com/{full_name}/commit/{full_sha}"
                            commit_notes.append({
                                "sha": sha,
                                "message": first_para,
                                "url": commit_url
                            })
                else:
                    # Not a merge commit, use the commit message
                    first_para = extract_first_paragraph(message)
                    if first_para:
                        commit_url = f"https://github.com/{full_name}/commit/{full_sha}"
                        commit_notes.append({
                            "sha": sha,
                            "message": first_para,
                            "url": commit_url
                        })

            if not commit_notes:
                return None

            return {
                "repo": full_name,
                "release_branch": release_branch,
                "current_rc": current_rc_title,
                "previous_rc": previous_rc_title,
                "commits": commit_notes
            }

        except Exception as exc:
            print(f"Error processing {repo.get('full_name')}: {exc}", file=sys.stderr)
            return None


async def main():
    load_dotenv()
    org_name, team_name, target_week = parse_args()
    github_token = get_gh_token()
    deployable_topic = get_deployable_topic()

    year, week_num = target_week
    version_prefix = f"v{year}.{week_num:02d}"

    # Calculate sprint start and end dates
    from datetime import timedelta
    even_week_monday = datetime.fromisocalendar(year, week_num, 1).replace(tzinfo=timezone.utc)
    sprint_start = (even_week_monday - timedelta(weeks=1))
    sprint_end = (even_week_monday + timedelta(days=6))

    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        # Get repositories
        repos = await get_repositories(client, org_name, team_name)
        deployable_repos = [repo for repo in repos if deployable_topic in (repo.get("topics") or [])]

        # Process repositories concurrently
        semaphore = asyncio.Semaphore(8)
        tasks = [
            asyncio.create_task(process_repository(client, repo, version_prefix, sprint_start, sprint_end, semaphore))
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
        print(f"**Sprint Period:** {sprint_start.strftime('%B %d, %Y')} - {sprint_end.strftime('%B %d, %Y')}\n")
        print(f"**Organization:** {org_name}\n")

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
            print("*No repositories with merged PRs found for this sprint.*\n")


if __name__ == "__main__":
    asyncio.run(main())
