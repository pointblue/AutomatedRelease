"""
Shared utilities for GitHub API operations used across scripts.
"""
import difflib
import fnmatch
import os
import re
import sys
from datetime import datetime, timedelta, timezone
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


def get_release_repo_order():
    """Parse RELEASE_REPO_ORDER from .env into an ordered list of match rules.

    The value is a comma-separated list of patterns, highest priority first.
    A pattern containing ``*`` is matched as a glob (``fnmatch``); any other
    pattern is matched exactly. Both forms are tested against the repository's
    full name (``org/repo``) and its short name (``repo``), case-insensitively.

    Example (list deju* repos first, then pointblue/api, then any repo
    containing "auth", then apps-login, then everything else)::

        RELEASE_REPO_ORDER=*deju*,pointblue/api,*auth*,apps-login

    Returns ``[]`` when unset, in which case callers fall back to plain
    alphabetical ordering.
    """
    raw = os.getenv("RELEASE_REPO_ORDER", "")
    return [pattern.strip().lower() for pattern in raw.split(",") if pattern.strip()]


def release_repo_sort_key(full_name, rules):
    """Sort key ordering ``full_name`` by the rules from get_release_repo_order().

    Repos matching an earlier rule sort before those matching a later rule; a
    repo matching no rule sorts last. The first matching rule wins, so a repo
    that could match several rules takes its highest-priority position. Within
    any single group, repos are ordered alphabetically by full name.
    """
    name = full_name.lower()
    short = name.split("/", 1)[-1]
    for index, rule in enumerate(rules):
        if "*" in rule:
            if fnmatch.fnmatch(name, rule) or fnmatch.fnmatch(short, rule):
                return (index, name)
        elif rule in (name, short):
            return (index, name)
    return (len(rules), name)


def _last_even_iso_week(year):
    last_week = datetime(year, 12, 28).isocalendar()[1]
    if last_week % 2 != 0:
        last_week -= 1
    return last_week


def get_sprintdates(now=None, target_week=None, range_tz=timezone.utc):
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
    """
    today = now or datetime.now(range_tz)
    iso_year, iso_week, _ = today.isocalendar()

    if target_week:
        sprint_end_year, sprint_end_week = target_week
    else:
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
    sprint_start = (even_week_monday - timedelta(weeks=1)).replace(tzinfo=range_tz).astimezone(timezone.utc)
    sprint_end = (even_week_monday + timedelta(days=7) - timedelta(seconds=1)).replace(tzinfo=range_tz).astimezone(timezone.utc)

    return sprint_start, sprint_end, sprint_end_year, sprint_end_week, iso_week


def get_first_paragraph(description, pr_message):
    if len(description) == 1 or description[1] in ['\n', '\r']:
        pr_message += description[0]
        return pr_message
    elif description[1].startswith('-'):
        pr_message += f'{description[0]}\n'
    return get_first_paragraph(description[1:], pr_message)


def parse_github_datetime(value):
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def parse_timezone_offset(value):
    """
    Parse timezone offset for date-range calculations.
    Supported formats: 0, -8, 5.5, +05:30, -07:00
    Returns a timezone object.
    """
    raw = (value or "0").strip()
    if raw in {"0", "+0", "-0", "+00:00", "-00:00"}:
        return timezone.utc

    hhmm_match = re.fullmatch(r"([+-])(\d{2}):(\d{2})", raw)
    if hhmm_match:
        sign = -1 if hhmm_match.group(1) == "-" else 1
        hours = int(hhmm_match.group(2))
        minutes = int(hhmm_match.group(3))
        if hours > 14 or minutes > 59:
            raise ValueError
        total_minutes = sign * (hours * 60 + minutes)
        if total_minutes < -12 * 60 or total_minutes > 14 * 60:
            raise ValueError
        return timezone(timedelta(minutes=total_minutes))

    decimal_match = re.fullmatch(r"[+-]?\d+(?:\.\d+)?", raw)
    if decimal_match:
        hours_float = float(raw)
        if hours_float < -12 or hours_float > 14:
            raise ValueError
        total_minutes = int(round(hours_float * 60))
        return timezone(timedelta(minutes=total_minutes))

    raise ValueError


def format_timestamp(dt, display_tz):
    local = dt.astimezone(display_tz)
    offset = local.strftime("%z")
    offset = f"{offset[:3]}:{offset[3:]}"
    return f"{local.strftime('%Y-%m-%d %H:%M:%S')} UTC{offset}"


def get_date_range_timezone():
    tz_raw = os.getenv("DATE_RANGE_TZ_OFFSET", "0")
    try:
        tzinfo = parse_timezone_offset(tz_raw)
    except ValueError:
        print("Invalid DATE_RANGE_TZ_OFFSET in .env. Use values like 0, -8, 5.5, +05:30, or -07:00.")
        sys.exit(1)
    return tzinfo, tz_raw.strip()


def parse_week_value(week_value):
    """
    Parse week filter values:
    - Single sprint end week: YYYY.WW
    - Same-year range: YYYY.WW-WW
    - Cross-year range: YYYY.WW-YYYY.WW
    Returns a dict describing the parsed mode.
    """
    def _parse_year_week(token):
        year_str, week_num_str = token.split(".")
        year = int(year_str)
        week_num = int(week_num_str)
        if week_num < 1 or week_num > 53:
            raise ValueError
        datetime.fromisocalendar(year, week_num, 1)
        return year, week_num

    if "-" not in week_value:
        year, week_num = _parse_year_week(week_value)
        if week_num % 2 != 0:
            raise ValueError("single_week_must_be_even")
        return {"mode": "single", "start": (year, week_num), "end": None}

    start_token, end_token = week_value.split("-", 1)
    start_year, start_week = _parse_year_week(start_token)

    if "." in end_token:
        end_year, end_week = _parse_year_week(end_token)
    else:
        end_year = start_year
        end_week = int(end_token)
        if end_week < 1 or end_week > 53:
            raise ValueError
        datetime.fromisocalendar(end_year, end_week, 1)

    start_date = datetime.fromisocalendar(start_year, start_week, 1)
    end_date = datetime.fromisocalendar(end_year, end_week, 7)
    if end_date < start_date:
        raise ValueError("end_before_start")

    return {"mode": "range", "start": (start_year, start_week), "end": (end_year, end_week)}


def parse_time_offset(offset_value):
    """
    Parse a time offset string such as 1d, 1w, 12h, 1.5h.
    """
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([hdwHDW])", offset_value.strip())
    if not match:
        raise ValueError

    value = float(match.group(1))
    unit = match.group(2).lower()
    if value < 0:
        raise ValueError

    if unit == "h":
        return timedelta(hours=value)
    if unit == "d":
        return timedelta(days=value)
    if unit == "w":
        return timedelta(weeks=value)
    raise ValueError
