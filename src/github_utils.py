"""
Shared utilities for GitHub API operations used across scripts.
"""
import difflib
import os
from dotenv import load_dotenv


def get_gh_token():
    """
    Get GitHub token from environment or prompt user to create one.
    Returns the GitHub token string.
    """
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


def make_github_headers(token, mercy_preview=False):
    """
    Build the standard GitHub API request headers.
    Set mercy_preview=True to include the mercy-preview Accept type (needed for topics).
    """
    accept = "application/vnd.github+json"
    if mercy_preview:
        accept += ", application/vnd.github.mercy-preview+json"
    return {
        "Authorization": f"Bearer {token}",
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def fetch_json_pages(client, url, params=None):
    """
    Fetch paginated JSON responses from GitHub API.
    Yields JSON response data for each page.
    """
    while url:
        response = await client.get(url, params=params)
        response.raise_for_status()
        yield response.json()
        next_link = response.links.get("next", {}).get("url")
        url, params = next_link, None


async def get_repositories(client, org_name, team_name=None):
    """Fetch all repositories for an organization or team."""
    if team_name:
        base_url = f"https://api.github.com/orgs/{org_name}/teams/{team_name}/repos"
    else:
        base_url = f"https://api.github.com/orgs/{org_name}/repos"
    params = {"per_page": 100, "type": "all", "sort": "full_name"}
    repos = []
    async for page in fetch_json_pages(client, base_url, params=params):
        repos.extend(page)
    return repos


def select_repositories_by_name(repos, repo_name_filter):
    """
    Filter a list of repos to those matching repo_name_filter.
    Supports exact name, full name, and fuzzy matching.
    Returns (matched_repos, resolved_full_name) where resolved_full_name is set
    only when a fuzzy match was used.
    """
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


async def get_main_or_master_branch(client, owner, repo_name):
    """
    Determine if repo uses 'main' or 'master' branch by checking which exists.
    Returns 'main', 'master', or None if neither exists.
    """
    try:
        # Try 'main' first
        url = f"https://api.github.com/repos/{owner}/{repo_name}/branches/main"
        response = await client.get(url)
        if response.status_code == 200:
            return "main"
    except Exception:
        pass

    try:
        # Try 'master' if 'main' doesn't exist
        url = f"https://api.github.com/repos/{owner}/{repo_name}/branches/master"
        response = await client.get(url)
        if response.status_code == 200:
            return "master"
    except Exception:
        pass

    return None


async def check_commit_in_branch(client, owner, repo_name, commit_sha, branch):
    """
    Check if a commit exists in a specific branch using GitHub compare API.
    Returns True if commit is in the branch, False otherwise.
    """
    try:
        # Use the compare API to check if commit is in the branch
        url = f"https://api.github.com/repos/{owner}/{repo_name}/compare/{branch}...{commit_sha}"
        response = await client.get(url)
        response.raise_for_status()
        compare_data = response.json()

        # If status is 'identical' or 'behind', the commit is in the branch
        # If 'ahead', the commit is not yet in the branch
        status = compare_data.get("status")
        return status in ["identical", "behind"]
    except Exception:
        return False


def get_deployable_topic():
    """
    Get the repository topic used to identify deployable repositories.
    Defaults to 'deployer-php' when not configured.
    """
    return os.getenv("DEPLOYABLE_TOPIC", "deployer-php").strip().lower()
