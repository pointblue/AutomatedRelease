#!/usr/bin/env python3
"""Guided, interactive release wizard.

Walks the operator through a full sprint release with an explicit confirmation
at every gate, while preserving the intentional friction of merging the release
candidate PRs by hand.

This script is a thin orchestrator. It does not reimplement any release logic:
it reuses ``get_sprintdates`` for sprint math and shells out to the four
existing scripts (``main.py``, ``create-release-candidate.py``,
``create-release-notes.py``, ``create-confluence-page.py``), treating their CLI
as a stable contract.

Usage:
    python3 release-wizard.py [org=<org>] [team=<team>]

Org/team are read from .env (ORG_NAME/TEAM_NAME) and may be overridden on the
command line.
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta

from dotenv import load_dotenv

from src.github_utils import (
    get_sprintdates,
    get_date_range_timezone,
    format_timestamp,
)

REPO_DIR = os.path.dirname(os.path.abspath(__file__))

MAIN = "main.py"
CREATE_RC = "create-release-candidate.py"
CREATE_NOTES = "create-release-notes.py"
CREATE_CONFLUENCE = "create-confluence-page.py"

# Progress is recorded here so an interrupted run can be resumed. It lives in
# output/, which is git-ignored.
STATE_PATH = os.path.join(REPO_DIR, "output", ".wizard-progress.json")

ABORT_WORDS = {"q", "quit", "abort", "exit"}

# ---------------------------------------------------------------------------
# Output styling (tty-aware, degrades gracefully when piped)
# ---------------------------------------------------------------------------
_TTY = sys.stdout.isatty()
BOLD = "\033[1m" if _TTY else ""
DIM = "\033[2m" if _TTY else ""
CYAN = "\033[36m" if _TTY else ""
GREEN = "\033[32m" if _TTY else ""
YELLOW = "\033[33m" if _TTY else ""
RED = "\033[31m" if _TTY else ""
RESET = "\033[0m" if _TTY else ""


class WizardAbort(Exception):
    """Raised by any prompt when the user chooses to quit."""


# ---------------------------------------------------------------------------
# Resumable progress state (best-effort; never fatal)
# ---------------------------------------------------------------------------
# Coarse stages a run can be resumed from:
#   "release" — sprint chosen & confirmed; RC PRs not yet created
#   "verify"  — RC PRs created/open; waiting on manual merges & re-verifying
#   "notes"   — RCs are done; still generating/reviewing notes and publishing
RESUMABLE_STEPS = ("release", "verify", "notes")


def save_state(sprint, cfg, step, excluded_repos=None):
    state = {
        "version": sprint["version"],
        "week_arg": sprint["week_arg"],
        "org": cfg["org"],
        "team": cfg["team"],
        "step": step,
        "excluded_repos": list(excluded_repos or []),
    }
    try:
        os.makedirs(os.path.join(REPO_DIR, "output"), exist_ok=True)
        with open(STATE_PATH, "w") as fh:
            json.dump(state, fh, indent=2)
    except OSError:
        pass  # progress tracking is best-effort; never block the release on it


def load_state():
    try:
        with open(STATE_PATH) as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict) or state.get("step") not in RESUMABLE_STEPS:
        return None
    if not state.get("version") or not state.get("week_arg"):
        return None
    return state


def clear_state():
    try:
        os.remove(STATE_PATH)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Interactive UX helpers (mirror the plain input() style of get_gh_token)
# ---------------------------------------------------------------------------
def section(title):
    print(f"\n{BOLD}{CYAN}{'=' * 60}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'=' * 60}{RESET}")


def _ask(prompt):
    try:
        resp = input(prompt).strip()
    except EOFError:
        raise WizardAbort
    if resp.lower() in ABORT_WORDS:
        raise WizardAbort
    return resp


def confirm(prompt, default=None):
    if default is True:
        suffix = " [Y/n] "
    elif default is False:
        suffix = " [y/N] "
    else:
        suffix = " [y/n] "
    while True:
        resp = _ask(f"{prompt}{suffix}(q to abort): ").lower()
        if not resp and default is not None:
            return default
        if resp in {"y", "yes"}:
            return True
        if resp in {"n", "no"}:
            return False
        print(f"{YELLOW}Please answer y or n (or q to abort).{RESET}")


def choose(prompt, options, default=None):
    """options: list of (key, label). Returns the chosen key.

    default is an optional 1-based index selected when the user presses Enter.
    """
    print(f"\n{BOLD}{prompt}{RESET}")
    for idx, (_, label) in enumerate(options, start=1):
        marker = f" {GREEN}(default){RESET}" if idx == default else ""
        print(f"  {BOLD}{idx}{RESET}) {label}{marker}")
    hint = f" [default: {default}]" if default else ""
    while True:
        resp = _ask(f"Enter a number{hint} (q to abort): ")
        if not resp and default:
            return options[default - 1][0]
        if resp.isdigit():
            n = int(resp)
            if 1 <= n <= len(options):
                return options[n - 1][0]
        print(f"{YELLOW}Please enter a number between 1 and {len(options)}.{RESET}")


def pause(message):
    _ask(f"{message} ")


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
def load_config():
    load_dotenv()

    arg_org = None
    arg_team = None
    for arg in sys.argv[1:]:
        lower = arg.lower()
        if lower.startswith("org=") or lower.startswith("org_name=") or lower.startswith("org-name="):
            arg_org = arg.split("=", 1)[1].strip()
        elif lower.startswith("team=") or lower.startswith("team_name=") or lower.startswith("team-name="):
            arg_team = arg.split("=", 1)[1].strip()

    org = arg_org or os.getenv("ORG_NAME")
    team = arg_team or os.getenv("TEAM_NAME")

    if not org:
        print(
            f"{RED}Missing organization.{RESET} Provide org=<org> on the command "
            "line or set ORG_NAME in .env."
        )
        sys.exit(1)

    range_tz, _ = get_date_range_timezone()

    return {"org": org, "team": team or None, "range_tz": range_tz}


def compute_sprint_options(range_tz):
    base = datetime.now(range_tz)
    specs = [
        ("previous", -14),
        ("current", 0),
        ("next", 14),
    ]
    options = []
    for label, day_delta in specs:
        start, end, year, week, _ = get_sprintdates(
            now=base + timedelta(days=day_delta), range_tz=range_tz
        )
        options.append(
            {
                "label": label,
                "version": f"v{year}.{week:02d}",
                "week_arg": f"{year}.{week:02d}",
                "start": start,
                "end": end,
                "tz": range_tz,
            }
        )
    return options


# ---------------------------------------------------------------------------
# Subprocess layer
# ---------------------------------------------------------------------------
def run_script(args):
    """Run a sibling script with the same interpreter from the repo root.

    Returns (returncode, stdout, stderr). Both streams are fully captured to
    avoid pipe-buffer deadlocks and to allow parsing.
    """
    cmd = [sys.executable] + args
    result = subprocess.run(cmd, cwd=REPO_DIR, text=True, capture_output=True)
    return result.returncode, result.stdout, result.stderr


def _fail(label, returncode, stdout, stderr):
    print(f"\n{RED}{label} failed (exit {returncode}).{RESET}")
    if stderr.strip():
        print(stderr.rstrip())
    elif stdout.strip():
        print(stdout.rstrip())
    raise WizardAbort


def parse_main_json(stdout):
    start = stdout.find("{")
    if start == -1:
        raise ValueError("no JSON object found in main.py output")
    return json.loads(stdout[start:])


def parse_created_rc_prs(text):
    """-> list of (number, url) for newly created RC PRs."""
    return re.findall(r"\[SUCCESS\] Created PR #(\d+):\s*(\S+)", text)


def parse_open_rc_prs(text):
    """-> list of (repo, url) for repos with a pre-existing open RC PR."""
    pattern = re.compile(
        r"\[SKIP\] (\S+): Open RC PR already exists\n\s+PR #\d+:[^\n]*\n\s+URL:\s*(\S+)"
    )
    return pattern.findall(text)


def parse_would_create(text):
    """-> list of repo names a (dry) run would create a new RC PR for."""
    return re.findall(r"\[PROCESSING\] (\S+): Creating", text)


def parse_all_released(text):
    """-> list of repo names whose dev branch is fully merged into release."""
    return re.findall(r"\[SKIP\] (\S+): Skipping \(all PRs already in", text)


def apply_exclusions(json_path, excluded_repos):
    """Drop repos from the wizard's saved release-input JSON.

    create-release-candidate.py only ever looks at entries under
    "release_prs", so removing a repo's entry there means the script won't
    consider it at all — no RC PR gets created and it won't show up as
    skipped either.
    """
    if not excluded_repos:
        return
    abs_path = os.path.join(REPO_DIR, json_path)
    with open(abs_path) as fh:
        payload = json.load(fh)
    payload["release_prs"] = [
        entry
        for entry in payload.get("release_prs", [])
        if entry.get("repository") not in excluded_repos
    ]
    with open(abs_path, "w") as fh:
        json.dump(payload, fh, indent=2)


def choose_repos_to_exclude(candidates):
    """Prompt for repos to exclude from `candidates`. Returns a list of names."""
    print(f"\n{BOLD}Repos that would get a new RC PR:{RESET}")
    for idx, repo in enumerate(candidates, start=1):
        print(f"  {BOLD}{idx}{RESET}) {repo}")
    resp = _ask(
        "Enter repo number(s) to exclude, comma-separated (or Enter for none, "
        "q to abort): "
    )
    if not resp:
        return []
    excluded = []
    for token in resp.split(","):
        token = token.strip()
        if token.isdigit() and 1 <= int(token) <= len(candidates):
            repo = candidates[int(token) - 1]
            if repo not in excluded:
                excluded.append(repo)
        elif token:
            print(f"{YELLOW}Ignoring invalid entry: {token}{RESET}")
    return excluded


# ---------------------------------------------------------------------------
# Wizard steps
# ---------------------------------------------------------------------------
def step_select_sprint(options):
    section("Step 1 — Select a sprint")
    menu = []
    default_index = None
    for idx, opt in enumerate(options, start=1):
        # Display the window in the user's configured range timezone, matching
        # how main.py renders its sprint_label.
        window = (
            f"{format_timestamp(opt['start'], opt['tz'])} → "
            f"{format_timestamp(opt['end'], opt['tz'])}"
        )
        # The current sprint is the most common choice, so make it the default.
        if opt["label"] == "current":
            default_index = idx
        menu.append(
            (
                opt,
                f"{BOLD}{opt['label'].capitalize()} sprint{RESET}  "
                f"{GREEN}{opt['version']}{RESET}\n     {DIM}{window}{RESET}",
            )
        )
    return choose("Which sprint do you want to release?", menu, default=default_index)


def step_show_scope(sprint, cfg):
    """Display main.py's console output for the sprint. Returns True if any PRs."""
    section(f"Step 2 — PRs in scope for {sprint['version']}")
    args = [MAIN, f"org={cfg['org']}", f"week={sprint['week_arg']}", "format=console"]
    if cfg["team"]:
        args.insert(2, f"team={cfg['team']}")

    print(f"{DIM}Scanning repositories for merged dev PRs… (this may take a moment){RESET}")
    rc, out, err = run_script(args)
    if rc != 0:
        _fail("main.py", rc, out, err)

    print(out.rstrip())
    if err.strip():
        print(f"{DIM}{err.rstrip()}{RESET}")

    # main.py prints a "-"*60 divider only inside its per-PR loop, so its
    # presence means at least one PR was listed (the "="*60 header is always
    # printed and cannot be used to detect emptiness).
    has_prs = ("-" * 60) in out
    if not has_prs:
        print(f"\n{YELLOW}No merged dev PRs found in this sprint window.{RESET}")
    return has_prs


def generate_scope_json(sprint, cfg):
    """Re-run main.py in JSON form and persist it for create-release-candidate.py."""
    args = [MAIN, f"org={cfg['org']}", f"week={sprint['week_arg']}", "format=json"]
    if cfg["team"]:
        args.insert(2, f"team={cfg['team']}")

    print(f"{DIM}Generating release input…{RESET}")
    rc, out, err = run_script(args)
    if rc != 0:
        _fail("main.py", rc, out, err)

    try:
        payload = parse_main_json(out)
    except (ValueError, json.JSONDecodeError):
        _fail("main.py (could not parse JSON)", rc, out, err)

    os.makedirs(os.path.join(REPO_DIR, "output"), exist_ok=True)
    rel_json_path = os.path.join("output", f"{sprint['version']}.json")
    with open(os.path.join(REPO_DIR, rel_json_path), "w") as fh:
        json.dump(payload, fh, indent=2)
    print(f"{DIM}Saved release input to {rel_json_path}{RESET}")
    return rel_json_path


def step_create_rcs(sprint, json_path, excluded_repos=None):
    """Preview (dry-run), let the operator exclude repos, then create.

    Returns (created, open_rc, excluded_repos) — excluded_repos is the
    cumulative list of repos left out of this release (including any passed
    in from a resumed run), so callers can persist it across steps/resume.
    """
    excluded_repos = list(excluded_repos or [])
    section(f"Step 3 — Create release candidates for {sprint['version']}")

    if excluded_repos:
        print(
            f"{DIM}Already excluded from this release: "
            f"{', '.join(excluded_repos)}{RESET}"
        )

    print(f"{DIM}Previewing release candidates (dry run)…{RESET}")
    rc, out, err = run_script([CREATE_RC, json_path, "--dry-run"])
    if rc != 0:
        _fail("create-release-candidate.py --dry-run", rc, out, err)
    print(out.rstrip())
    if err.strip():
        print(f"{DIM}{err.rstrip()}{RESET}")

    would_create = parse_would_create(out)
    open_rc = parse_open_rc_prs(out)

    while True:
        if not would_create and not open_rc:
            print(
                f"\n{GREEN}Nothing new to release — every remaining repo is "
                f"already merged into its release branch.{RESET}"
            )
            return [], [], excluded_repos

        if not would_create and open_rc:
            # Only pre-existing open RC PRs; nothing new to create.
            print(
                f"\n{YELLOW}No new RC PRs to create, but {len(open_rc)} open RC "
                f"PR(s) already exist and still need to be merged.{RESET}"
            )
            return [], open_rc, excluded_repos

        action = choose(
            f"{len(would_create)} repo(s) need a new RC PR for "
            f"{sprint['version']}. What do you want to do?",
            [
                ("create", f"Create RC PR(s) for all {len(would_create)} repo(s)"),
                ("exclude", "Exclude one or more repos from this release"),
                ("abort", "Abort"),
            ],
            default=1,
        )
        if action == "abort":
            raise WizardAbort
        if action == "create":
            break

        # action == "exclude"
        newly_excluded = choose_repos_to_exclude(would_create)
        if not newly_excluded:
            continue

        excluded_repos = excluded_repos + newly_excluded
        apply_exclusions(json_path, newly_excluded)
        print(
            f"\n{DIM}Excluding from this release: {', '.join(newly_excluded)}{RESET}"
        )

        print(f"\n{DIM}Re-checking remaining repos (dry run)…{RESET}")
        rc, out, err = run_script([CREATE_RC, json_path, "--dry-run"])
        if rc != 0:
            _fail("create-release-candidate.py --dry-run", rc, out, err)
        print(out.rstrip())
        if err.strip():
            print(f"{DIM}{err.rstrip()}{RESET}")
        would_create = parse_would_create(out)
        open_rc = parse_open_rc_prs(out)

    print(f"\n{DIM}Creating release candidate PRs…{RESET}")
    rc, out2, err2 = run_script([CREATE_RC, json_path])
    if rc != 0:
        _fail("create-release-candidate.py", rc, out2, err2)
    print(out2.rstrip())
    if err2.strip():
        print(f"{DIM}{err2.rstrip()}{RESET}")

    created = parse_created_rc_prs(out2)
    open_rc = parse_open_rc_prs(out2)
    return created, open_rc, excluded_repos


def step_wait_for_merges(created, open_rc):
    section("Step 4 — Merge the release candidate PRs (manual)")
    print(
        "Open each release candidate PR below, review it, and merge it in GitHub.\n"
        "This step is intentionally manual — the wizard will not merge for you.\n"
    )
    if created:
        print(f"{BOLD}Newly created RC PRs:{RESET}")
        for number, url in created:
            print(f"  {GREEN}#{number}{RESET}  {url}")
    if open_rc:
        print(f"{BOLD}Pre-existing open RC PRs (also need merging):{RESET}")
        for repo, url in open_rc:
            print(f"  {YELLOW}{repo}{RESET}  {url}")
    print()
    pause("Press Enter once you have merged all the RC PRs (or q to abort)…")


def step_verify(sprint, json_path):
    """Re-run the dry-run to confirm merge status.

    Returns (ready, unmerged_repos): ready is True when every repo is fully
    merged; unmerged_repos is the list of repo names still pending (either an
    open RC PR or new unreleased commits on dev), for use if the operator
    chooses to proceed without them.
    """
    section("Step 5 — Verify what is being released")
    print(f"{DIM}Re-checking merge status…{RESET}")
    rc, out, err = run_script([CREATE_RC, json_path, "--dry-run"])
    if rc != 0:
        _fail("create-release-candidate.py --dry-run", rc, out, err)

    combined = out + "\n" + err
    released = parse_all_released(combined)
    open_rc = parse_open_rc_prs(out)
    would_create = parse_would_create(out)

    if released:
        print(f"\n{GREEN}Merged and ready to release:{RESET}")
        for repo in released:
            print(f"  {GREEN}✓{RESET} {repo}")

    unmerged = bool(open_rc) or bool(would_create)
    if open_rc:
        print(f"\n{YELLOW}Still have an OPEN, un-merged RC PR:{RESET}")
        for repo, url in open_rc:
            print(f"  {YELLOW}•{RESET} {repo}  {url}")
    if would_create:
        print(
            f"\n{YELLOW}Have new unreleased commits on dev (RC may need to be "
            f"recreated/merged):{RESET}"
        )
        for repo in would_create:
            print(f"  {YELLOW}•{RESET} {repo}")

    unmerged_repos = [repo for repo, _ in open_rc] + list(would_create)
    return not unmerged, unmerged_repos


def step_release_notes(sprint, cfg):
    section(f"Step 6 — Generate release notes for {sprint['version']}")
    args = [CREATE_NOTES, f"org={cfg['org']}", f"week={sprint['week_arg']}", "--output"]
    if cfg["team"]:
        args.insert(1, f"team={cfg['team']}")

    print(f"{DIM}Generating release notes…{RESET}")
    rc, out, err = run_script(args)
    if rc != 0:
        _fail("create-release-notes.py", rc, out, err)

    rel_notes_path = os.path.join("output", f"{sprint['version']}-release-notes.md")
    abs_notes_path = os.path.join(REPO_DIR, rel_notes_path)
    if not os.path.exists(abs_notes_path):
        print(f"{RED}Expected release notes file was not created: {rel_notes_path}{RESET}")
        if out.strip():
            print(out.rstrip())
        raise WizardAbort

    print(f"\n{DIM}Release notes ({rel_notes_path}):{RESET}\n")
    with open(abs_notes_path) as fh:
        print(fh.read().rstrip())
    print(f"\n{DIM}End of release notes ({rel_notes_path}).{RESET}")
    return rel_notes_path


def step_publish_confluence(notes_path):
    section("Step 7 — Publish to Confluence (optional)")
    if not os.getenv("CONFLUENCE_EMAIL") or not os.getenv("CONFLUENCE_API_TOKEN"):
        print(
            f"{YELLOW}CONFLUENCE_EMAIL / CONFLUENCE_API_TOKEN are not set — skipping "
            f"publish.{RESET}\nRelease notes are saved at {BOLD}{notes_path}{RESET}."
        )
        return

    if not confirm("Publish these release notes to Confluence?", default=False):
        print(f"Skipped publishing. Release notes are saved at {BOLD}{notes_path}{RESET}.")
        return

    draft = confirm("Create as an unpublished draft first?", default=False)
    args = [CREATE_CONFLUENCE, notes_path]
    if draft:
        args.append("--draft")

    print(f"{DIM}Publishing to Confluence…{RESET}")
    rc, out, err = run_script(args)
    if out.strip():
        print(out.rstrip())
    if rc != 0:
        # Surface the error (e.g. "page already exists") but don't abort — we are
        # at the end of the flow and the notes file already exists.
        print(f"\n{YELLOW}Confluence publish did not complete:{RESET}")
        if err.strip():
            print(err.rstrip())


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def run_release(sprint, cfg, start="release", excluded_repos=None):
    """Run the release pipeline from `start` onward.

    The RC phase re-derives merge status live from GitHub (idempotent dry-run),
    so resuming at "release" continues correctly regardless of how far the
    earlier run got with creating RC PRs. Once RC PRs exist, progress is saved
    as "verify" so resuming doesn't re-offer to create them again — it goes
    straight to the merge-verification loop.

    `excluded_repos` carries forward repos the operator has chosen to leave
    out of this release (e.g. a closed RC PR), so a resumed run doesn't
    re-offer to create RC PRs for them.
    """
    excluded_repos = list(excluded_repos or [])
    json_path = os.path.join("output", f"{sprint['version']}.json")

    if start == "release":
        json_path = generate_scope_json(sprint, cfg)
        apply_exclusions(json_path, excluded_repos)
        created, open_rc, excluded_repos = step_create_rcs(
            sprint, json_path, excluded_repos
        )

        if not created and not open_rc:
            # Everything already released — skip straight to release notes.
            if not confirm("Generate release notes now?", default=True):
                raise WizardAbort
            save_state(sprint, cfg, "notes", excluded_repos)
            start = "notes"
        else:
            step_wait_for_merges(created, open_rc)
            save_state(sprint, cfg, "verify", excluded_repos)
            start = "verify"

    if start == "verify":
        # Verify-and-merge loop until nothing is left un-merged, unless the
        # operator explicitly chooses to proceed without the stragglers
        # (e.g. they closed an auto-created RC PR on purpose).
        while True:
            ready, unmerged_repos = step_verify(sprint, json_path)
            if ready:
                break
            print(
                f"\n{YELLOW}Some repos still have un-merged release candidates.{RESET}"
            )
            if confirm(
                f"Proceed without releasing {len(unmerged_repos)} un-merged "
                "repo(s) this sprint?",
                default=False,
            ):
                print(
                    f"\n{DIM}Excluding from this release: "
                    f"{', '.join(unmerged_repos)}{RESET}"
                )
                break
            pause("Merge the remaining RC PRs, then press Enter to re-check (or q to abort)…")
        if not confirm("Releases look correct — generate release notes?"):
            raise WizardAbort

        save_state(sprint, cfg, "notes", excluded_repos)
        start = "notes"

    if start == "notes":
        notes_path = step_release_notes(sprint, cfg)
        if not confirm("Do the release notes look good?"):
            raise WizardAbort

        step_publish_confluence(notes_path)

        clear_state()
        section("Done")
        print(f"{GREEN}Release flow complete for {sprint['version']}.{RESET}")


def main():
    cfg = load_config()
    print(f"{BOLD}Release wizard{RESET} — org {GREEN}{cfg['org']}{RESET}"
          + (f", team {GREEN}{cfg['team']}{RESET}" if cfg["team"] else ""))

    options = compute_sprint_options(cfg["range_tz"])

    try:
        # Offer to resume an interrupted run before starting a fresh one.
        state = load_state()
        if state:
            stage = {
                "release": "creating release candidates",
                "verify": "merging/verifying release candidates",
                "notes": "release notes",
            }[state["step"]]
            print(
                f"\n{YELLOW}An unfinished release for {GREEN}{state['version']}{YELLOW} "
                f"was found (stopped at: {stage}).{RESET}"
            )
            if confirm(f"Resume the release for {state['version']}?", default=True):
                sprint = {"version": state["version"], "week_arg": state["week_arg"]}
                resume_cfg = dict(cfg)
                resume_cfg["org"] = state.get("org") or cfg["org"]
                resume_cfg["team"] = state.get("team")
                run_release(
                    sprint,
                    resume_cfg,
                    start=state["step"],
                    excluded_repos=state.get("excluded_repos"),
                )
                return
            if confirm("Discard the saved progress and start fresh?", default=False):
                clear_state()

        # Select a sprint; allow re-picking if it has no PRs in scope.
        while True:
            sprint = step_select_sprint(options)
            if step_show_scope(sprint, cfg):
                break
            if not confirm("Pick a different sprint?", default=True):
                raise WizardAbort

        if not confirm(f"Create release candidate PRs for {sprint['version']}?"):
            raise WizardAbort

        save_state(sprint, cfg, "release")
        run_release(sprint, cfg, start="release")

    except WizardAbort:
        print(f"\n{DIM}Aborted. No further actions taken. Re-run the wizard to "
              f"resume where you left off.{RESET}")
        sys.exit(0)
    except KeyboardInterrupt:
        print(f"\n{DIM}Interrupted. No further actions taken. Re-run the wizard to "
              f"resume where you left off.{RESET}")
        sys.exit(130)


if __name__ == "__main__":
    main()
