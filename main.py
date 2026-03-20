#!/usr/bin/env python3
import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import httpx
import os
import sys
from src.github_utils import get_gh_token, fetch_json_pages, get_deployable_topic, make_github_headers, get_repositories, select_repositories_by_name

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
            - sprint_start: datetime of sprint start (Monday of odd week)
            - sprint_end: datetime of sprint end (Sunday of even week)
            - sprint_end_year: ISO year of the sprint end week
            - sprint_end_week: ISO week number of the sprint end (always even)
            - iso_week: Current ISO week number
    """
    today = now or datetime.now(range_tz)
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
    sprint_start = (even_week_monday - timedelta(weeks=1)).replace(tzinfo=range_tz).astimezone(timezone.utc)
    sprint_end = (even_week_monday + timedelta(days=6)).replace(tzinfo=range_tz).astimezone(timezone.utc)

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


def parse_timezone_offset(value):
    """
    Parse timezone offset for date-range calculations.
    Supported formats:
    - 0
    - -8
    - 5.5
    - +05:30
    - -07:00
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
        # Validate ISO week/year combination.
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
    Parse a time offset string such as:
    - 1d (1 day)
    - 1w (1 week)
    - 12h (12 hours)
    - 1.5h (1.5 hours)
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

def parse_args():
    env_org_name = os.getenv("ORG_NAME")
    env_team_name = os.getenv("TEAM_NAME")
    env_week_offset_raw = os.getenv("WEEK_OFFSET")

    org_name = None
    team_name = None
    output_format = "json"
    branch_filter = "dev"
    week_filter = None
    repo_name_filter = None
    week_offset = timedelta(0)
    week_offset_raw = None
    write_output_file = False

    for arg in sys.argv[1:]:
        lower = arg.lower()
        if lower.startswith("format="):
            try:
                output_format = arg.split("=", 1)[1].lower()
                if output_format not in {"console", "text", "json", "markdown"}:
                    raise ValueError
            except ValueError:
                print("Invalid format argument. Use format=console, format=text, format=json, or format=markdown.")
                sys.exit(1)
        elif lower.startswith(("branch=", "branch_filter=", "branch-filter=")):
            try:
                branch_filter = arg.split("=", 1)[1].lower()
                if branch_filter not in {"all", "release", "dev"}:
                    raise ValueError
            except ValueError:
                print("Invalid branch argument. Use branch=all, branch=release, or branch=dev.")
                sys.exit(1)
        elif lower.startswith("week="):
            # Parse week argument:
            # - week=YYYY.WW
            # - week=YYYY.WW-WW
            # - week=YYYY.WW-YYYY.WW
            try:
                week_str = arg.split("=", 1)[1]
                week_filter = parse_week_value(week_str)
            except ValueError as exc:
                if str(exc) == "single_week_must_be_even":
                    print("For a single week, the week number must be even (sprint end week).")
                elif str(exc) == "end_before_start":
                    print("Invalid week range: end week must be on or after start week.")
                else:
                    print("Invalid week format. Use week=YYYY.WW, week=YYYY.WW-WW, or week=YYYY.WW-YYYY.WW")
                sys.exit(1)
        elif lower.startswith(("week-offset=", "week_offset=", "offset=")):
            try:
                week_offset_raw = arg.split("=", 1)[1]
                week_offset = parse_time_offset(week_offset_raw)
            except ValueError:
                print("Invalid week offset. Use values like offset=1d, offset=1w, offset=12h, or offset=1.5h")
                sys.exit(1)
        elif lower.startswith(("org=", "org_name=", "org-name=")):
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
        elif arg == "--output":
            write_output_file = True
        else:
            print("Unrecognized argument.")
            print("Usage: python3 main.py [org=<org>] [team=<team>] [name=<repo>] [format=console|text|json|markdown] [branch=all|release|dev] [week=YYYY.WW|YYYY.WW-WW|YYYY.WW-YYYY.WW] [offset=<Nh|Nd|Nw>] [--output]")
            sys.exit(1)

    if org_name is None:
        org_name = env_org_name
    if team_name is None:
        team_name = env_team_name
    if week_offset_raw is None and env_week_offset_raw:
        try:
            week_offset_raw = env_week_offset_raw
            week_offset = parse_time_offset(week_offset_raw)
        except ValueError:
            print("Invalid WEEK_OFFSET in .env. Use values like 1d, 1w, 12h, or 1.5h")
            sys.exit(1)

    if not org_name:
        print("Missing organization name. Provide it as an argument or set ORG_NAME in .env.")
        print("Usage: python3 main.py [org=<org>] [team=<team>] [name=<repo>] [format=console|json] [branch=all|release|dev] [week=YYYY.WW|YYYY.WW-WW|YYYY.WW-YYYY.WW] [offset=<Nh|Nd|Nw>]")
        sys.exit(1)

    if output_format not in {"console", "text", "json", "markdown"}:
        print("Output format must be 'console', 'text', 'json', or 'markdown'")
        sys.exit(1)

    if branch_filter not in {"all", "release", "dev"}:
        print("Branch filter must be 'all' (dev/main/master), 'release' (main/master), or 'dev' (dev only)")
        sys.exit(1)

    if repo_name_filter and week_filter is None:
        print("When using name=<repo>, you must also provide week=YYYY.WW, week=YYYY.WW-WW, or week=YYYY.WW-YYYY.WW.")
        sys.exit(1)

    return org_name, team_name, output_format, branch_filter, week_filter, repo_name_filter, week_offset, week_offset_raw, write_output_file



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


async def fetch_prs_within_sprint(client, repo, sprint_start_date, sprint_end_date, allowed_branches, commit_to_tags, ignore_date_range=False):
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

        reached_before_sprint_window = False
        for pull in page:
            base_branch = (pull.get("base") or {}).get("ref", "")
            if base_branch not in allowed_branches:
                continue
            pr_date = parse_github_datetime(pull.get("merged_at") or pull.get("closed_at") or pull.get("updated_at"))
            if not pr_date:
                continue

            if not ignore_date_range and pr_date < sprint_start_date:
                reached_before_sprint_window = True
                break

            if ignore_date_range or pr_date <= sprint_end_date:
                body = pull.get("body") or ""
                full_description = body.split('\n') if body else None
                message = get_first_paragraph(full_description, "") if full_description else ""
                # Remove newline characters from the description
                message = message.replace('\n', ' ').replace('\r', ' ').strip()
                author = pull.get("user", {}).get("login", "unknown")
                title = pull.get("title", "Untitled PR")
                issue_match = re.search(r"((?:PBT|GSS)-\d+)", title)
                gitlab_issue = issue_match.group(1) if issue_match else None

                # Look up tags for the merge commit from pre-fetched mapping
                merge_commit_sha = pull.get("merge_commit_sha")
                tags = commit_to_tags.get(merge_commit_sha, []) if merge_commit_sha else []

                sprint_prs.append((pr_date, title, message, pull.get("html_url"), author, gitlab_issue, tags, merge_commit_sha))

        if reached_before_sprint_window:
            break
    sprint_prs.sort(key=lambda x: x[0])
    return sprint_prs


async def process_repository(client, repo, sprint_start_date, sprint_end_date, allowed_branches, semaphore, ignore_date_range=False):
    async with semaphore:
        try:
            owner = repo["owner"]["login"]
            name = repo["name"]
            # Fetch tags once for the entire repo
            commit_to_tags = await fetch_repo_tags(client, owner, name)
            prs = await fetch_prs_within_sprint(
                client,
                repo,
                sprint_start_date,
                sprint_end_date,
                allowed_branches,
                commit_to_tags,
                ignore_date_range=ignore_date_range,
            )
            return repo, prs
        except httpx.HTTPError as exc:
            print(f"HTTP error while fetching repo {repo.get('full_name')}: {exc}")
            return repo, []


async def print_commits():
    load_dotenv()
    org_name, team_name, output_format, branch_filter, week_filter, repo_name_filter, week_offset, week_offset_raw, write_output_file = parse_args()
    deployable_topic = get_deployable_topic()
    date_range_tz, date_range_tz_raw = get_date_range_timezone()

    ###if the program can't find github token, it checks if path to dotenv file exists, if not, then it creates one and configures it
    github_token = get_gh_token()

    iso_year, iso_week, _ = datetime.now(date_range_tz).isocalendar()
    if week_filter and week_filter["mode"] == "range":
        start_year, start_week = week_filter["start"]
        end_year, end_week = week_filter["end"]
        sprint_start_date = datetime.fromisocalendar(start_year, start_week, 1).replace(tzinfo=date_range_tz).astimezone(timezone.utc)
        sprint_end_date = datetime.fromisocalendar(end_year, end_week, 7).replace(tzinfo=date_range_tz).astimezone(timezone.utc)
        if start_year == end_year:
            version_label = f"v{start_year}.{start_week:02d}-{end_week:02d}"
        else:
            version_label = f"v{start_year}.{start_week:02d}-{end_year}.{end_week:02d}"
        label_prefix = f"Weeks {version_label}"
    else:
        target_week = week_filter["start"] if week_filter and week_filter["mode"] == "single" else None
        sprint_start_date, sprint_end_date, sprint_end_year, sprint_end_week, iso_week = get_sprintdates(
            target_week=target_week,
            range_tz=date_range_tz,
        )
        version_label = f"v{sprint_end_year}.{sprint_end_week:02d}"
        label_prefix = f"Sprint {version_label}"

    # Shift the date window by offset: start is delayed, end is extended by the same offset.
    if week_offset != timedelta(0):
        sprint_start_date = sprint_start_date + week_offset
        sprint_end_date = sprint_end_date + week_offset

    sprint_label = f"{label_prefix} ({format_timestamp(sprint_start_date, date_range_tz)} to {format_timestamp(sprint_end_date, date_range_tz)})"

    ext = {"console": "txt", "text": "txt", "json": "json", "markdown": "md"}[output_format]
    output_file_path = os.path.join("output", f"{version_label}.{ext}")
    output_file = None
    if write_output_file:
        output_file = open(output_file_path, "w")
        sys.stdout = output_file

    if output_format == "console":
        RESET  = "\033[0m"
        BOLD   = "\033[1m"
        DIM    = "\033[2m"
        CYAN   = "\033[36m"
        WHITE  = "\033[97m"
    else:
        RESET = BOLD = DIM = CYAN = WHITE = ""

    def field(name, value, width=26):
        return f"{DIM}{name:<{width}}{RESET}: {value}"

    if output_format in {"console", "text"}:
        title = f"  {label_prefix}  "
        border = "=" * max(60, len(title))
        print(f"\n{BOLD}{CYAN}{border}{RESET}")
        print(f"{BOLD}{WHITE}{title.center(len(border))}{RESET}")
        print(f"{BOLD}{CYAN}{border}{RESET}\n")
        if team_name:
            print(f"Printing PRs for repos accessible by team {team_name} in organization {org_name}\n")
        else:
            print(f"No team specified. Printing PRs for all repos in organization {org_name}\n")
        print(field("Sprint version", label_prefix))
        print(field("Sprint begin", format_timestamp(sprint_start_date, date_range_tz)))
        print(field("Sprint end", format_timestamp(sprint_end_date, date_range_tz)))
        print(field("Current Week Number", iso_week))
        print(field("Output format", output_format))
        print(field("Branch filter", branch_filter))
        print(field("Deployable topic", deployable_topic))
        print(field("Date range timezone offset", date_range_tz_raw))
        if week_filter and week_filter["mode"] == "range":
            print(field("Week filter", version_label))
        if week_offset_raw:
            print(field("Week offset", week_offset_raw))
        if repo_name_filter:
            print(field("Repository name filter", repo_name_filter))

    if branch_filter == "all":
        allowed_branches = {"dev", "main", "master"}
    elif branch_filter == "dev":
        allowed_branches = {"dev"}
    else:
        allowed_branches = {"main", "master"}

    headers = make_github_headers(github_token, mercy_preview=True)

    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        repos = await get_repositories(client, org_name, team_name)
        deployable_repos = [repo for repo in repos if deployable_topic in (repo.get("topics") or [])]
        repos = deployable_repos
        matched_repo_name = None
        if repo_name_filter:
            repos, matched_repo_name = select_repositories_by_name(repos, repo_name_filter)
        if output_format in {"console", "text"}:
            if matched_repo_name:
                print(f"Resolved repository to: {matched_repo_name}")
            print(f"Discovered {len(repos)} repositories to scan\n")

        semaphore = asyncio.Semaphore(8)
        tasks = [
            asyncio.create_task(
                process_repository(
                    client,
                    repo,
                    sprint_start_date,
                    sprint_end_date,
                    allowed_branches,
                    semaphore,
                    ignore_date_range=False,
                )
            )
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
                def _pr_entry(pr_date, pr_title, pr_message, pr_link, author, gitlab_issue, tags, commit_id):
                    entry = {
                        "date": pr_date.isoformat(),
                    }
                    if date_range_tz != timezone.utc:
                        entry["date_local"] = format_timestamp(pr_date, date_range_tz)
                    entry.update({
                        "title": pr_title,
                        "author": author,
                        "description": pr_message,
                        "link": pr_link,
                        "gitlab_issue": gitlab_issue,
                        "tags": tags,
                        "commit_id": commit_id,
                    })
                    return entry

                repo_payload.append({
                    "repository": repo_name,
                    "pull_requests": [
                        _pr_entry(pr_date, pr_title, pr_message, pr_link, author, gitlab_issue, tags, commit_id)
                        for pr_date, pr_title, pr_message, pr_link, author, gitlab_issue, tags, commit_id in prs
                    ],
                })
            payload = {
                "organization": org_name,
                "team": team_name,
                "current_week_number": iso_week,
                "sprint_label": sprint_label,
                "branch_filter": branch_filter,
                "week_filter_mode": week_filter["mode"] if week_filter else "default",
                "week_offset": week_offset_raw,
                "date_range_tz_offset": date_range_tz_raw,
                "repo_name_filter": repo_name_filter,
                "resolved_repo_name": matched_repo_name,
                "date_filter": "sprint",
                "release_prs": repo_payload,
            }
            print(json.dumps(payload, indent=2))
        elif output_format in {"console", "text"}:
            separator = "=" * 60
            pr_divider = f"{DIM}{'-' * 60}{RESET}"
            for repo, prs in repo_results:
                repo_name = repo.get('full_name')
                print(f"\n{BOLD}{CYAN}{separator}{RESET}")
                print(f"{BOLD}{WHITE}  {repo_name}{RESET}")
                print(f"{BOLD}{CYAN}{separator}{RESET}\n")
                for pr_date, pr_title, pr_message, pr_link, author, gitlab_issue, tags, commit_id in prs:
                    formatted_date = pr_date.strftime('%Y-%m-%d %H:%M:%S %Z')
                    if date_range_tz != timezone.utc:
                        print(field("Date (local)", format_timestamp(pr_date, date_range_tz), width=12))
                    else:
                        print(field("Date", formatted_date, width=12))
                    print(field("Title", pr_title, width=12))
                    if gitlab_issue:
                        print(field("GitLab Issue", gitlab_issue, width=12))
                    print(field("Author", author, width=12))
                    print(field("Description", pr_message, width=12))
                    print(field("Link", pr_link, width=12))
                    if tags:
                        print(field("Tags", ", ".join(tags), width=12))
                    print(pr_divider)

        elif output_format == "markdown":
            def md_field(name, value, width=26):
                padded = name + '\u00a0' * (width - len(name))
                return f"{padded}: {value}  "

            print(f"# {label_prefix}\n")
            print("| | |")
            print("|---|---|")
            print(f"| Organization | {org_name} |")
            if team_name:
                print(f"| Team | {team_name} |")
            print(f"| Sprint version | {label_prefix} |")
            print(f"| Sprint begin | {format_timestamp(sprint_start_date, date_range_tz)} |")
            print(f"| Sprint end | {format_timestamp(sprint_end_date, date_range_tz)} |")
            print(f"| Current Week Number | {iso_week} |")
            print(f"| Branch filter | {branch_filter} |")
            print(f"| Deployable topic | {deployable_topic} |")
            print(f"| Date range timezone offset | {date_range_tz_raw} |")
            if week_filter and week_filter["mode"] == "range":
                print(f"| Week filter | {version_label} |")
            if week_offset_raw:
                print(f"| Week offset | {week_offset_raw} |")
            if repo_name_filter:
                print(f"| Repository name filter | {repo_name_filter} |")

            for repo, prs in repo_results:
                repo_name = repo.get('full_name')
                print(f"\n## {repo_name}\n")
                for pr_date, pr_title, pr_message, pr_link, author, gitlab_issue, tags, commit_id in prs:
                    formatted_date = pr_date.strftime('%Y-%m-%d %H:%M:%S %Z')
                    print(f"| | {pr_title} |")
                    print("|---|---|")
                    if date_range_tz != timezone.utc:
                        print(f"| Date (local) | {format_timestamp(pr_date, date_range_tz)} |")
                    else:
                        print(f"| Date | {formatted_date} |")
                    if gitlab_issue:
                        prefix, issue_num = gitlab_issue.split("-", 1)
                        project = "pointblue-gss" if prefix == "GSS" else "point-blue-tech"
                        issue_url = f"https://pblgssgitlab01.aws.pointblue.org/point-blue-engineering-team/{project}/-/issues/{issue_num}"
                        print(f"| GitLab Issue | [{gitlab_issue}]({issue_url}) |")
                    print(f"| Author | {author} |")
                    print(f"| Description | {pr_message} |")
                    print(f"| Link | {pr_link} |")
                    if tags:
                        print(f"| Tags | {', '.join(tags)} |")
                    print()

    if output_format in {"console", "text", "markdown"}:
        print('END')

    if output_file:
        sys.stdout = sys.__stdout__
        output_file.close()
        print(f"Output written to {output_file_path}")


if __name__ == "__main__":
    asyncio.run(print_commits())
