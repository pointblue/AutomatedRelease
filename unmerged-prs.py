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
    parse_week_value, parse_time_offset,
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
            print("Usage: python3 unmerged-prs.py [org=<org>] [team=<team>] [name=<repo>] [format=console|text|json|markdown] [branch=all|release|dev] [week=YYYY.WW|YYYY.WW-WW|YYYY.WW-YYYY.WW] [offset=<Nh|Nd|Nw>] [--output]")
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
        print("Usage: python3 unmerged-prs.py [org=<org>] [team=<team>] [name=<repo>] [format=console|text|json|markdown] [branch=all|release|dev] [week=YYYY.WW|YYYY.WW-WW|YYYY.WW-YYYY.WW] [offset=<Nh|Nd|Nw>]")
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


async def get_sprint_approvals(client, owner, repo_name, pr_number, sprint_start, sprint_end):
    """
    Fetch reviews for a PR and determine approvals within the sprint window.
    Uses each reviewer's latest review within the sprint to determine their final state.
    Returns (approvers, latest_approval_at) or ([], None) if not approved within sprint.
    """
    try:
        url = f"https://api.github.com/repos/{owner}/{repo_name}/pulls/{pr_number}/reviews"
        params = {"per_page": 100}
        reviews = []
        async for page in fetch_json_pages(client, url, params=params):
            reviews.extend(page)

        # Filter to reviews within the sprint window
        sprint_reviews = []
        for review in reviews:
            submitted_at = parse_github_datetime(review.get("submitted_at"))
            if submitted_at and sprint_start <= submitted_at <= sprint_end:
                sprint_reviews.append((review.get("user", {}).get("login", "unknown"), review.get("state"), submitted_at))

        if not sprint_reviews:
            return [], None

        # Get each reviewer's latest review within the sprint
        latest_by_user = {}
        for login, state, submitted_at in sprint_reviews:
            if login not in latest_by_user or submitted_at > latest_by_user[login][1]:
                latest_by_user[login] = (state, submitted_at)

        approvers = [login for login, (state, _) in latest_by_user.items() if state == "APPROVED"]
        if not approvers:
            return [], None

        latest_approval_at = max(latest_by_user[login][1] for login in approvers)
        return approvers, latest_approval_at

    except Exception:
        return [], None


async def get_pr_mergeable(client, owner, repo_name, pr_number):
    """
    Fetch individual PR to get its mergeable status.
    Returns True (can merge), False (has conflicts), or None (GitHub hasn't computed it yet).
    """
    try:
        url = f"https://api.github.com/repos/{owner}/{repo_name}/pulls/{pr_number}"
        response = await client.get(url)
        response.raise_for_status()
        return response.json().get("mergeable")
    except Exception:
        return None


async def has_any_approval(client, owner, repo_name, pr_number):
    """Check if a PR has at least one APPROVED review (at any time)."""
    try:
        url = f"https://api.github.com/repos/{owner}/{repo_name}/pulls/{pr_number}/reviews"
        params = {"per_page": 100}
        reviews = []
        async for page in fetch_json_pages(client, url, params=params):
            reviews.extend(page)
        return any(r.get("state") == "APPROVED" for r in reviews)
    except Exception:
        return False


async def fetch_approved_merged_count(client, repo, sprint_start_date, sprint_end_date, allowed_branches, semaphore):
    """Count PRs merged within the sprint that have at least one approval (at any time)."""
    async with semaphore:
        try:
            owner = repo["owner"]["login"]
            name = repo["name"]
            url = f"https://api.github.com/repos/{owner}/{name}/pulls"
            params = {
                "state": "closed",
                "sort": "updated",
                "direction": "desc",
                "per_page": 100,
            }

            candidates = []
            async for page in fetch_json_pages(client, url, params=params):
                if not page:
                    break

                reached_before_sprint = False
                for pull in page:
                    base_branch = (pull.get("base") or {}).get("ref", "")
                    if base_branch not in allowed_branches:
                        continue

                    merged_at = parse_github_datetime(pull.get("merged_at"))
                    if not merged_at:
                        continue  # closed but not merged

                    if merged_at < sprint_start_date:
                        reached_before_sprint = True
                        break

                    if merged_at <= sprint_end_date:
                        candidates.append(pull)

                if reached_before_sprint:
                    break

            if not candidates:
                return 0

            approval_results = await asyncio.gather(*[
                has_any_approval(client, owner, name, pull["number"])
                for pull in candidates
            ])
            return sum(1 for approved in approval_results if approved)

        except Exception:
            return 0


async def fetch_approved_unmerged_prs(client, repo, sprint_start_date, sprint_end_date, allowed_branches):
    """
    Fetch open PRs that received an approval within the sprint window and can be merged.
    Uses updated_at to stop fetching early — approvals update a PR's updated_at timestamp.
    PRs with confirmed merge conflicts (mergeable=False) are excluded. PRs where GitHub
    hasn't computed mergeability yet (mergeable=None) are included to avoid missing PRs.
    """
    owner = repo["owner"]["login"]
    name = repo["name"]
    url = f"https://api.github.com/repos/{owner}/{name}/pulls"
    params = {
        "state": "open",
        "sort": "updated",
        "direction": "desc",
        "per_page": 100,
    }

    candidates = []
    async for page in fetch_json_pages(client, url, params=params):
        if not page:
            break

        reached_before_sprint = False
        for pull in page:
            base_branch = (pull.get("base") or {}).get("ref", "")
            if base_branch not in allowed_branches:
                continue

            updated_at = parse_github_datetime(pull.get("updated_at"))
            if not updated_at:
                continue

            # Stop once PRs haven't been touched since sprint started
            if updated_at < sprint_start_date:
                reached_before_sprint = True
                break

            candidates.append(pull)

        if reached_before_sprint:
            break

    # Fetch reviews and mergeability for all candidates concurrently
    review_tasks = [
        get_sprint_approvals(client, owner, name, pull["number"], sprint_start_date, sprint_end_date)
        for pull in candidates
    ]
    mergeable_tasks = [
        get_pr_mergeable(client, owner, name, pull["number"])
        for pull in candidates
    ]
    all_results = await asyncio.gather(*review_tasks, *mergeable_tasks)
    review_results = all_results[:len(candidates)]
    mergeable_results = all_results[len(candidates):]

    sprint_prs = []
    for pull, (approvers, approved_at), mergeable in zip(candidates, review_results, mergeable_results):
        if not approvers:
            continue
        if mergeable is False:
            continue

        body = pull.get("body") or ""
        full_description = body.split('\n') if body else None
        message = get_first_paragraph(full_description, "") if full_description else ""
        message = message.replace('\n', ' ').replace('\r', ' ').strip()
        author = pull.get("user", {}).get("login", "unknown")
        title = pull.get("title", "Untitled PR")
        issue_match = re.search(r"((?:PBT|GSS)-\d+)", title)
        gitlab_issue = issue_match.group(1) if issue_match else None

        sprint_prs.append((approved_at, title, message, pull.get("html_url"), author, gitlab_issue, approvers))

    sprint_prs.sort(key=lambda x: x[0])
    return sprint_prs


async def process_repository(client, repo, sprint_start_date, sprint_end_date, allowed_branches, semaphore):
    async with semaphore:
        try:
            prs = await fetch_approved_unmerged_prs(client, repo, sprint_start_date, sprint_end_date, allowed_branches)
            return repo, prs
        except httpx.HTTPError as exc:
            print(f"HTTP error while fetching repo {repo.get('full_name')}: {exc}")
            return repo, []


async def print_unmerged_prs():
    load_dotenv()
    org_name, team_name, output_format, branch_filter, week_filter, repo_name_filter, week_offset, week_offset_raw, write_output_file = parse_args()
    deployable_topic = get_deployable_topic()
    date_range_tz, date_range_tz_raw = get_date_range_timezone()

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

    if week_offset != timedelta(0):
        sprint_start_date = sprint_start_date + week_offset
        sprint_end_date = sprint_end_date + week_offset

    sprint_label = f"{label_prefix} ({format_timestamp(sprint_start_date, date_range_tz)} to {format_timestamp(sprint_end_date, date_range_tz)})"

    ext = {"console": "txt", "text": "txt", "json": "json", "markdown": "md"}[output_format]
    output_file_path = os.path.join("output", f"{version_label}-unmerged-prs.{ext}")
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
        GREEN  = "\033[32m"
        YELLOW = "\033[33m"
    else:
        RESET = BOLD = DIM = CYAN = WHITE = GREEN = YELLOW = ""

    def field(name, value, width=26):
        return f"{DIM}{name:<{width}}{RESET}: {value}"

    if output_format in {"console", "text"}:
        title = f"  {label_prefix} — Approved Unmerged PRs  "
        border = "=" * max(60, len(title))
        print(f"\n{BOLD}{CYAN}{border}{RESET}")
        print(f"{BOLD}{WHITE}{title.center(len(border))}{RESET}")
        print(f"{BOLD}{CYAN}{border}{RESET}\n")
        if team_name:
            print(f"Printing approved unmerged PRs for repos accessible by team {team_name} in organization {org_name}\n")
        else:
            print(f"No team specified. Printing approved unmerged PRs for all repos in organization {org_name}\n")
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
                )
            )
            for repo in repos
        ]
        merged_count_tasks = [
            asyncio.create_task(
                fetch_approved_merged_count(
                    client,
                    repo,
                    sprint_start_date,
                    sprint_end_date,
                    allowed_branches,
                    semaphore,
                )
            )
            for repo in repos
        ]

        repos_scanned = len(repos)
        repo_results = []
        for task in asyncio.as_completed(tasks):
            repo, prs = await task
            if prs:
                repo_results.append((repo, prs))

        merged_count_results = await asyncio.gather(*merged_count_tasks)
        merged_counts = {
            repo.get('full_name'): count
            for repo, count in zip(repos, merged_count_results)
            if count > 0
        }

        if output_format == "json":
            repo_payload = []
            for repo, prs in repo_results:
                repo_name = repo.get('full_name')
                def _pr_entry(approved_at, pr_title, pr_message, pr_link, author, gitlab_issue, approved_by):
                    entry = {
                        "approved_at": approved_at.isoformat(),
                    }
                    if date_range_tz != timezone.utc:
                        entry["approved_at_local"] = format_timestamp(approved_at, date_range_tz)
                    entry.update({
                        "title": pr_title,
                        "author": author,
                        "description": pr_message,
                        "link": pr_link,
                        "gitlab_issue": gitlab_issue,
                        "approved_by": approved_by,
                    })
                    return entry

                repo_payload.append({
                    "repository": repo_name,
                    "approved_count": len(prs),
                    "approved_merged_count": merged_counts.get(repo_name, 0),
                    "pull_requests": [
                        _pr_entry(approved_at, pr_title, pr_message, pr_link, author, gitlab_issue, approved_by)
                        for approved_at, pr_title, pr_message, pr_link, author, gitlab_issue, approved_by in prs
                    ],
                })
            total_unmerged = sum(len(prs) for _, prs in repo_results)
            total_merged = sum(merged_counts.values())
            all_repo_names = set(r.get('full_name') for r, _ in repo_results) | set(merged_counts.keys())
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
                "repositories_scanned": repos_scanned,
                "repositories_with_prs": len(all_repo_names),
                "total_approved_count": total_unmerged,
                "total_approved_merged_count": total_merged,
                "unmerged_prs": repo_payload,
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
                for approved_at, pr_title, pr_message, pr_link, author, gitlab_issue, approved_by in prs:
                    if date_range_tz != timezone.utc:
                        print(field("Approved (local)", format_timestamp(approved_at, date_range_tz), width=16))
                    else:
                        print(field("Approved", approved_at.strftime('%Y-%m-%d %H:%M:%S %Z'), width=16))
                    print(field("Title", pr_title, width=16))
                    if gitlab_issue:
                        print(field("GitLab Issue", gitlab_issue, width=16))
                    print(field("Author", author, width=16))
                    print(field("Description", pr_message, width=16))
                    print(field("Link", pr_link, width=16))
                    print(field("Approved By", ", ".join(approved_by), width=16))
                    print(pr_divider)

        elif output_format == "markdown":
            print(f"# {label_prefix} — Approved Unmerged PRs\n")
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
                for approved_at, pr_title, pr_message, pr_link, author, gitlab_issue, approved_by in prs:
                    print(f"| | {pr_title} |")
                    print("|---|---|")
                    if date_range_tz != timezone.utc:
                        print(f"| Approved (local) | {format_timestamp(approved_at, date_range_tz)} |")
                    else:
                        print(f"| Approved | {approved_at.strftime('%Y-%m-%d %H:%M:%S %Z')} |")
                    if gitlab_issue:
                        prefix, issue_num = gitlab_issue.split("-", 1)
                        project = "pointblue-gss" if prefix == "GSS" else "point-blue-tech"
                        issue_url = f"https://pblgssgitlab01.aws.pointblue.org/point-blue-engineering-team/{project}/-/issues/{issue_num}"
                        print(f"| GitLab Issue | [{gitlab_issue}]({issue_url}) |")
                    print(f"| Author | {author} |")
                    print(f"| Description | {pr_message} |")
                    print(f"| Link | {pr_link} |")
                    print(f"| Approved By | {', '.join(approved_by)} |")
                    print()

    if output_format in {"console", "text", "markdown"}:
        total_unmerged = sum(len(prs) for _, prs in repo_results)
        total_merged = sum(merged_counts.values())
        unmerged_by_name = {repo.get('full_name'): prs for repo, prs in repo_results}
        all_repo_names = sorted(set(unmerged_by_name.keys()) | set(merged_counts.keys()))

        separator = "=" * 60
        print(f"\n{BOLD}{CYAN}{separator}{RESET}")
        print(f"{BOLD}{WHITE}  SUMMARY{RESET}")
        print(f"{BOLD}{CYAN}{separator}{RESET}\n")

        if all_repo_names:
            for repo_name in all_repo_names:
                parts = []
                unmerged = unmerged_by_name.get(repo_name, [])
                merged = merged_counts.get(repo_name, 0)
                if unmerged:
                    pr_word = "PR" if len(unmerged) == 1 else "PRs"
                    parts.append(f"{len(unmerged)} approved unmerged {pr_word}")
                if merged:
                    pr_word = "PR" if merged == 1 else "PRs"
                    parts.append(f"{merged} approved merged {pr_word}")
                print(field(repo_name, ", ".join(parts), width=50))

            print(f"\n{DIM}{'-' * 60}{RESET}")
            totals = []
            if total_unmerged:
                pr_word = "PR" if total_unmerged == 1 else "PRs"
                totals.append(f"{BOLD}{YELLOW}{total_unmerged} approved unmerged {pr_word}{RESET}")
            else:
                totals.append(f"{BOLD}{GREEN}no approved unmerged PRs{RESET}")
            if total_merged:
                pr_word = "PR" if total_merged == 1 else "PRs"
                totals.append(f"{total_merged} approved merged {pr_word}")
            repo_word = "repository" if len(all_repo_names) == 1 else "repositories"
            print(f"{', '.join(totals)} across {len(all_repo_names)} {repo_word} ({repos_scanned} scanned)")
        else:
            print(f"No approved PRs found across {repos_scanned} repositories.")
        print()

    if output_file:
        sys.stdout = sys.__stdout__
        output_file.close()
        print(f"Output written to {output_file_path}")


if __name__ == "__main__":
    asyncio.run(print_unmerged_prs())
