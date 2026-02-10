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


def get_sprintdates(now=None):
    today = now or datetime.now(timezone.utc)
    iso_year, iso_week, _ = today.isocalendar()

    if iso_week % 2 == 0:
        sprint_end_week = iso_week
        sprint_end_year = iso_year
    else:
        sprint_end_week = iso_week - 1
        sprint_end_year = iso_year

    if sprint_end_week < 1:
        sprint_end_year -= 1
        sprint_end_week = _last_even_iso_week(sprint_end_year)

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
        print("Missing organization name argument. Please provide an organization name: python3 main.py <org name> [team name] [format]")
        sys.exit()

    org_name = sys.argv[1]
    team_name = None
    output_format = "text"

    if len(sys.argv) > 2:
        potential = sys.argv[2]
        if potential.lower() in {"text", "json"}:
            output_format = potential.lower()
        else:
            team_name = potential

    if len(sys.argv) > 3:
        output_format = sys.argv[3].lower()

    if output_format not in {"text", "json"}:
        print("Output format must be either 'text' or 'json'")
        sys.exit(1)

    return org_name, team_name, output_format

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


async def fetch_prs_within_sprint(client, repo, sprint_start_date, sprint_end_date):
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
                sprint_prs.append((pr_date, title, message, pull.get("html_url"), author, gitlab_issue))

            should_continue = True

        if not should_continue:
            break
    return sprint_prs


async def process_repository(client, repo, sprint_start_date, sprint_end_date, semaphore):
    async with semaphore:
        try:
            prs = await fetch_prs_within_sprint(client, repo, sprint_start_date, sprint_end_date)
            return repo, prs
        except httpx.HTTPError as exc:
            print(f"HTTP error while fetching repo {repo.get('full_name')}: {exc}")
            return repo, []


async def print_commits():
    load_dotenv()
    org_name, team_name, output_format = parse_args()

    ###if the program can't find github token, it checks if path to dotenv file exists, if not, then it creates one and configures it
    github_token = get_gh_token()

    sprint_start_date, sprint_end_date, sprint_end_year, sprint_end_week, iso_week = get_sprintdates()
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

    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        repos = await get_repositories(client, org_name, team_name)
        if output_format == "text":
            print(f"Discovered {len(repos)} repositories to scan\n")

        semaphore = asyncio.Semaphore(8)
        tasks = [
            asyncio.create_task(process_repository(client, repo, sprint_start_date, sprint_end_date, semaphore))
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
                        }
                        for pr_date, pr_title, pr_message, pr_link, author, gitlab_issue in prs
                    ],
                })
            payload = {
                "organization": org_name,
                "team": team_name,
                "current_week_number": iso_week,
                "sprint_label": sprint_label,
                "release_prs": repo_payload,
            }
            print(json.dumps(payload, indent=2))
        else:
            for repo, prs in repo_results:
                repo_name = repo.get('full_name')
                header = f"=== Repository: {repo_name} ==="
                print(header)
                for pr_date, pr_title, pr_message, pr_link, author, gitlab_issue in prs:
                    formatted_date = pr_date.strftime('%Y-%m-%d %H:%M:%S %Z')
                    print(f'Date: {formatted_date}')
                    print(f'Title: {pr_title}')
                    if gitlab_issue:
                        print(f'GitLab Issue: {gitlab_issue}')
                    print(f'Author: {author}')
                    print(f'Description: {pr_message}')
                    print(f'Link: {pr_link}')
                    print('-' * len(header))

    if output_format == "text":
        print('END')


if __name__ == "__main__":
    asyncio.run(print_commits())
