import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import httpx
import os
import sys

def get_gh_token():
    github_token = os.getenv('GITHUB_TOKEN')
    if not github_token:
        envpath = os.path.exists('env')
        print('No Github Token')
        if not (envpath):
            with open('.env', 'w') as fh:
                github_token = input('Please enter your GitHub personal access token so that it can be stored for later use: ').strip()
                fh.write(f'GITHUB_TOKEN={github_token}')
        else:
            print('Please add your GitHub token to your dotenv file')
    return github_token

def _last_even_iso_week(year):
    last_week = datetime(year, 12, 28).isocalendar()[1]
    if last_week % 2 != 0:
        last_week -= 1
    return last_week


def get_sprintdates(now=None, target_week=None):
    """
    Calculate the current or most recent 2-week sprint dates based on even ISO week numbers.

    Sprints run for 2 weeks, starting on the Monday of an odd ISO week and ending on the
    Sunday of the following even ISO week. This function determines which sprint period
    the current date falls into.

    Args:
        now: Optional datetime to use instead of current time (useful for testing)
        target_week: Optional tuple of (year, week_number) to calculate a specific sprint

    Returns:
        tuple: (sprint_start, sprint_end, sprint_end_year, sprint_end_week, iso_week)
            - sprint_start: datetime of sprint start (Monday of odd week)
            - sprint_end: datetime of sprint end (Sunday of even week)
            - sprint_end_year: ISO year of the sprint end week
            - sprint_end_week: ISO week number of the sprint end (always even)
            - iso_week: Current ISO week number
    """
    today = now or datetime.now(timezone.utc)
    iso_year, iso_week, _ = today.isocalendar()

    # If a specific target week is provided, use it directly
    if target_week:
        sprint_end_year, sprint_end_week = target_week
    else:
        # Determine the most recent even week (sprint end week)
        # If we're in an even week, that's the sprint end
        # If we're in an odd week, use the previous week as sprint end
        if iso_week % 2 == 0:
            sprint_end_week = iso_week
            sprint_end_year = iso_year
        else:
            sprint_end_week = iso_week - 1
            sprint_end_year = iso_year

        # Handle edge case: if sprint_end_week becomes 0 or negative,
        # we need to wrap to the previous year's last even week
        if sprint_end_week < 1:
            sprint_end_year -= 1
            sprint_end_week = _last_even_iso_week(sprint_end_year)

    # Calculate actual dates from ISO week numbers
    # Sprint ends on Sunday of the even week (day 7)
    # Sprint starts on Monday of the previous (odd) week
    even_week_monday = datetime.fromisocalendar(sprint_end_year, sprint_end_week, 1)
    sprint_start = (even_week_monday - timedelta(weeks=1)).replace(tzinfo=timezone.utc)
    sprint_end = (even_week_monday + timedelta(days=6)).replace(tzinfo=timezone.utc)

    return sprint_start, sprint_end, sprint_end_year, sprint_end_week, iso_week


def get_first_paragraph(description, pr_message):
    # base case is full description has length of 1 or is followed by a new line
    if len(description) == 1 or description[1] in ['\n', '\r']:
        pr_message += description[0]
        return pr_message

    # handle bullet points, e.g. description is followed by -
    elif description[1].startswith('-'):
        pr_message += f'{description[0]}\n'

    # Otherwise, just try again with the second line
    return get_first_paragraph(description[1:], pr_message)


def parse_github_datetime(value):
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).astimezone(timezone.utc)

def parse_args():
    if len(sys.argv) < 2:
        print("Missing organization name argument. Please provide an organization name: python3 main.py <org name> [team name] [format] [branch_filter] [week]")
        sys.exit()

    org_name = sys.argv[1]
    team_name = None
    output_format = "text"
    branch_filter = "all"
    sprint_week = None

    for arg in sys.argv[2:]:
        lower = arg.lower()
        if lower in {"text", "json"} and output_format == "text":
            output_format = lower
        elif lower in {"all", "release", "dev"} and branch_filter == "all":
            branch_filter = lower
        elif arg.startswith("week="):
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
                sprint_week = (year, week_num)
            except (ValueError, IndexError):
                print("Invalid week format. Use week=YYYY.WW (e.g., week=2026.08)")
                sys.exit(1)
        elif team_name is None:
            team_name = arg
        else:
            print("Too many arguments provided. Usage: python3 main.py <org> [team] [format] [branch_filter] [week=YYYY.WW]")
            sys.exit(1)

    if output_format not in {"text", "json"}:
        print("Output format must be either 'text' or 'json'")
        sys.exit(1)

    if branch_filter not in {"all", "release", "dev"}:
        print("Branch filter must be 'all' (dev/main/master), 'release' (main/master), or 'dev' (dev only)")
        sys.exit(1)

    return org_name, team_name, output_format, branch_filter, sprint_week

async def fetch_json_pages(client, url, params=None):
    while url:
        response = await client.get(url, params=params)
        response.raise_for_status()
        yield response.json()
        next_link = response.links.get("next", {}).get("url")
        url, params = next_link, None


async def get_repositories(client, org_name, team_name=None):
    if team_name:
        base_url = f"https://api.github.com/orgs/{org_name}/teams/{team_name}/repos"
    else:
        base_url = f"https://api.github.com/orgs/{org_name}/repos"
    params = {"per_page": 100, "type": "all", "sort": "full_name"}
    repos = []
    async for page in fetch_json_pages(client, base_url, params=params):
        repos.extend(page)
    return repos


async def fetch_repo_tags(client, owner, repo_name):
    """Fetch all tags for a repository and return a mapping of commit SHA to tag names."""
    try:
        url = f"https://api.github.com/repos/{owner}/{repo_name}/tags"
        params = {"per_page": 100}
        commit_to_tags = {}

        async for page in fetch_json_pages(client, url, params=params):
            for tag in page:
                tag_name = tag.get("name", "")
                commit_sha = tag.get("commit", {}).get("sha")
                if commit_sha:
                    if commit_sha not in commit_to_tags:
                        commit_to_tags[commit_sha] = []
                    commit_to_tags[commit_sha].append(tag_name)

        return commit_to_tags
    except Exception:
        return {}


async def fetch_prs_within_sprint(client, repo, sprint_start_date, sprint_end_date, allowed_branches, commit_to_tags):
    owner = repo["owner"]["login"]
    name = repo["name"]
    url = f"https://api.github.com/repos/{owner}/{name}/pulls"
    params = {
        "state": "closed",
        "sort": "updated",
        "direction": "desc",
        "per_page": 100,
    }

    sprint_prs = []
    async for page in fetch_json_pages(client, url, params=params):
        if not page:
            break

        should_continue = False
        for pull in page:
            base_branch = (pull.get("base") or {}).get("ref", "")
            if base_branch not in allowed_branches:
                continue
            pr_date = parse_github_datetime(pull.get("merged_at") or pull.get("closed_at") or pull.get("updated_at"))
            if not pr_date:
                continue

            if pr_date < sprint_start_date:
                should_continue = False
                break

            if pr_date <= sprint_end_date:
                body = pull.get("body") or ""
                full_description = body.split('\n') if body else None
                message = get_first_paragraph(full_description, "") if full_description else ""
                author = pull.get("user", {}).get("login", "unknown")
                title = pull.get("title", "Untitled PR")
                issue_match = re.search(r"(PBT-\d+)", title)
                gitlab_issue = issue_match.group(1) if issue_match else None

                # Look up tags for the merge commit from pre-fetched mapping
                merge_commit_sha = pull.get("merge_commit_sha")
                tags = commit_to_tags.get(merge_commit_sha, []) if merge_commit_sha else []

                sprint_prs.append((pr_date, title, message, pull.get("html_url"), author, gitlab_issue, tags))

            should_continue = True

        if not should_continue:
            break
    return sprint_prs


async def process_repository(client, repo, sprint_start_date, sprint_end_date, allowed_branches, semaphore):
    async with semaphore:
        try:
            owner = repo["owner"]["login"]
            name = repo["name"]
            # Fetch tags once for the entire repo
            commit_to_tags = await fetch_repo_tags(client, owner, name)
            prs = await fetch_prs_within_sprint(client, repo, sprint_start_date, sprint_end_date, allowed_branches, commit_to_tags)
            return repo, prs
        except httpx.HTTPError as exc:
            print(f"HTTP error while fetching repo {repo.get('full_name')}: {exc}")
            return repo, []


async def print_commits():
    load_dotenv()
    org_name, team_name, output_format, branch_filter, sprint_week = parse_args()

    ###if the program can't find github token, it checks if path to dotenv file exists, if not, then it creates one and configures it
    github_token = get_gh_token()

    sprint_start_date, sprint_end_date, sprint_end_year, sprint_end_week, iso_week = get_sprintdates(target_week=sprint_week)
    version_label = f"v{sprint_end_year}.{sprint_end_week:02d}"
    sprint_label = f'Sprint {version_label} ({sprint_start_date.strftime("%a %Y-%m-%d")} to {sprint_end_date.strftime("%a %Y-%m-%d")})'
    if output_format == "text":
        if team_name:
            print(f"Printing PRs for repos accessible by team {team_name} in organization {org_name}\n")
        else:
            print(f"No team specified. Printing PRs for all repos in organization {org_name}\n")
        print(f"Showing items for: {sprint_label}")
        print(f"Current Week Number: {iso_week}")
        print(f"Output format: {output_format}")
        print(f"Branch filter: {branch_filter}")

    if branch_filter == "all":
        allowed_branches = {"dev", "main", "master"}
    elif branch_filter == "dev":
        allowed_branches = {"dev"}
    else:
        allowed_branches = {"main", "master"}

    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json, application/vnd.github.mercy-preview+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        repos = await get_repositories(client, org_name, team_name)
        deployable_repos = [repo for repo in repos if "deployer-php" in (repo.get("topics") or [])]
        repos = deployable_repos
        if output_format == "text":
            print(f"Discovered {len(repos)} repositories to scan\n")

        semaphore = asyncio.Semaphore(8)
        tasks = [
            asyncio.create_task(process_repository(client, repo, sprint_start_date, sprint_end_date, allowed_branches, semaphore))
            for repo in repos
        ]

        repo_results = []
        for task in asyncio.as_completed(tasks):
            repo, prs = await task
            if prs:
                repo_results.append((repo, prs))

        if output_format == "json":
            repo_payload = []
            for repo, prs in repo_results:
                repo_name = repo.get('full_name')
                repo_payload.append({
                    "repository": repo_name,
                    "pull_requests": [
                        {
                            "date": pr_date.isoformat(),
                            "title": pr_title,
                            "author": author,
                            "description": pr_message,
                            "link": pr_link,
                            "gitlab_issue": gitlab_issue,
                            "tags": tags,
                        }
                        for pr_date, pr_title, pr_message, pr_link, author, gitlab_issue, tags in prs
                    ],
                })
            payload = {
                "organization": org_name,
                "team": team_name,
                "current_week_number": iso_week,
                "sprint_label": sprint_label,
                "branch_filter": branch_filter,
                "release_prs": repo_payload,
            }
            print(json.dumps(payload, indent=2))
        else:
            for repo, prs in repo_results:
                repo_name = repo.get('full_name')
                header = f"=== Repository: {repo_name} ==="
                print(header)
                for pr_date, pr_title, pr_message, pr_link, author, gitlab_issue, tags in prs:
                    formatted_date = pr_date.strftime('%Y-%m-%d %H:%M:%S %Z')
                    print(f'Date: {formatted_date}')
                    print(f'Title: {pr_title}')
                    if gitlab_issue:
                        print(f'GitLab Issue: {gitlab_issue}')
                    print(f'Author: {author}')
                    print(f'Description: {pr_message}')
                    print(f'Link: {pr_link}')
                    if tags:
                        print(f'Tags: {", ".join(tags)}')
                    print('-' * len(header))

    if output_format == "text":
        print('END')


if __name__ == "__main__":
    asyncio.run(print_commits())
