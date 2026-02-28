"""
Shared utilities for GitHub API operations used across scripts.
"""
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
