#!/usr/bin/env python3
import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import httpx
import os
import sys
from src.github_utils import (
    get_gh_token, fetch_json_pages, get_deployable_topic, make_github_headers,
    get_repositories, select_repositories_by_name, get_sprintdates, get_first_paragraph,
    parse_github_datetime, parse_timezone_offset, format_timestamp, get_date_range_timezone,
    parse_week_value, parse_time_offset, get_release_repo_order, release_repo_sort_key,
)


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


NESTED_MERGE_PATTERN = re.compile(r"Merge pull request #(\d+)")


def build_pr_tuple(pull, commit_to_tags, via_pr=None):
    """Build the per-PR tuple shared by every output format.

    ``via_pr`` is the number of the dev-merged PR that carried this PR into the
    release. It is ``None`` for PRs merged directly to a tracked branch and set
    for nested feature->feature merges surfaced by ``expand_nested_prs``.
    """
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
    pr_date = parse_github_datetime(pull.get("merged_at"))
    return (pr_date, title, message, pull.get("html_url"), author, gitlab_issue, tags, merge_commit_sha, via_pr)


async def fetch_pull(client, owner, name, pr_number):
    """Fetch a single PR's details, or None on error."""
    try:
        resp = await client.get(f"https://api.github.com/repos/{owner}/{name}/pulls/{pr_number}")
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


async def fetch_pr_commit_messages(client, owner, name, pr_number):
    """Return every commit message on a PR's branch."""
    messages = []
    try:
        url = f"https://api.github.com/repos/{owner}/{name}/pulls/{pr_number}/commits"
        async for page in fetch_json_pages(client, url, params={"per_page": 100}):
            for commit in page:
                msg = (commit.get("commit") or {}).get("message", "")
                if msg:
                    messages.append(msg)
    except Exception:
        pass
    return messages


async def expand_nested_prs(client, owner, name, parent_pr_numbers, allowed_branches, commit_to_tags, seen):
    """Surface feature->feature PR merges that reach dev through a dev-merged PR.

    A PR merged into another feature branch (base not in ``allowed_branches``)
    never appears via the base-branch filter, but its changes ship as soon as
    that branch is merged to dev. For each dev PR, scan its branch commits for
    ``Merge pull request #N`` entries and surface any such N as its own entry,
    annotated (``via_pr``) with the dev PR it rode in on. Recurses so multi-level
    nesting is captured; ``seen`` guards against duplicates and cycles.
    """
    results = []
    # work items: (pr_to_scan, ride_in_dev_pr)
    work = [(num, num) for num in parent_pr_numbers]
    while work:
        scan_num, ride_in = work.pop(0)
        for msg in await fetch_pr_commit_messages(client, owner, name, scan_num):
            match = NESTED_MERGE_PATTERN.search(msg)
            if not match:
                continue
            nested_num = int(match.group(1))
            if nested_num in seen:
                continue
            seen.add(nested_num)
            pull = await fetch_pull(client, owner, name, nested_num)
            if not pull or not pull.get("merged_at"):
                continue
            # Skip merges into a tracked branch — those are dev/release-level PRs,
            # not the feature->feature merges we are trying to surface.
            base_ref = (pull.get("base") or {}).get("ref", "")
            if base_ref in allowed_branches:
                continue
            results.append(build_pr_tuple(pull, commit_to_tags, via_pr=ride_in))
            # The feature branch may itself contain further nested merges.
            work.append((nested_num, ride_in))
    return results


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
    direct_pr_numbers = []
    async for page in fetch_json_pages(client, url, params=params):
        if not page:
            break

        reached_before_sprint_window = False
        for pull in page:
            base_branch = (pull.get("base") or {}).get("ref", "")
            if base_branch not in allowed_branches:
                continue
            pr_date = parse_github_datetime(pull.get("merged_at"))
            if not pr_date:
                continue

            if not ignore_date_range and pr_date < sprint_start_date:
                reached_before_sprint_window = True
                break

            if ignore_date_range or pr_date <= sprint_end_date:
                sprint_prs.append(build_pr_tuple(pull, commit_to_tags))
                if pull.get("number") is not None:
                    direct_pr_numbers.append(pull["number"])

        if reached_before_sprint_window:
            break

    # Surface feature->feature PR merges that reached dev through these dev PRs
    # (e.g. a PR merged into another feature branch, then that branch merged to
    # dev). They never match the base-branch filter above, but their changes
    # ship with the release, so they belong in the sprint scope.
    seen = set(direct_pr_numbers)
    sprint_prs.extend(
        await expand_nested_prs(client, owner, name, direct_pr_numbers, allowed_branches, commit_to_tags, seen)
    )

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

        # Order repos by the configurable RELEASE_REPO_ORDER rules (alphabetical
        # within each group, and entirely alphabetical when no rules are set).
        # Tasks complete out of order, so this also makes output deterministic.
        repo_order_rules = get_release_repo_order()
        repo_results.sort(key=lambda item: release_repo_sort_key(item[0].get('full_name') or "", repo_order_rules))

        if output_format == "json":
            repo_payload = []
            for repo, prs in repo_results:
                repo_name = repo.get('full_name')
                def _pr_entry(pr_date, pr_title, pr_message, pr_link, author, gitlab_issue, tags, commit_id, via_pr):
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
                    if via_pr is not None:
                        entry["merged_via_pr"] = via_pr
                    return entry

                repo_payload.append({
                    "repository": repo_name,
                    "pull_requests": [
                        _pr_entry(pr_date, pr_title, pr_message, pr_link, author, gitlab_issue, tags, commit_id, via_pr)
                        for pr_date, pr_title, pr_message, pr_link, author, gitlab_issue, tags, commit_id, via_pr in prs
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
                for pr_date, pr_title, pr_message, pr_link, author, gitlab_issue, tags, commit_id, via_pr in prs:
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
                    if via_pr is not None:
                        print(field("Note", f"merged into dev via #{via_pr}", width=12))
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
                for pr_date, pr_title, pr_message, pr_link, author, gitlab_issue, tags, commit_id, via_pr in prs:
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
                    if via_pr is not None:
                        print(f"| Note | merged into dev via #{via_pr} |")
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
