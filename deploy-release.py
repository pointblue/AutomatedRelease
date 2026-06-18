#!/usr/bin/env python3
"""
deploy-release.py — deploy the release-ready software for a sprint.

This script automates the manual deployment an operator otherwise performs by hand,
running as the configured DEPLOY_USER wherever the repos are cloned. Read top to
bottom, it tells the deployment story:

  1. Work out which sprint version we are releasing (e.g. v2026.08).
  2. Walk every cloned repo, fetch it, and find the ones whose release candidate for
     that version has landed on the release branch (main/master). Those are the repos
     "targeted for release".
  3. For each targeted repo, take a snapshot of what is *currently* deployed (branch and
     revision) BEFORE we change anything. This snapshot is the ground truth we restore
     to if anything goes wrong.
  4. Deploy them one at a time, in order. If a repo is currently on an ad-hoc branch (a
     hot-fix or feature deploy, i.e. not main/master), first roll that ad-hoc deployment
     back, then deploy the official release.
  5. If ANY repo fails, stop and put every repo we touched back exactly the way it was
     before this run started — then exit non-zero.

The deploy and rollback commands are configured in .env. What matters for the restore
logic is the deploy tool's release model: each deploy builds a new numbered release and,
on success, repoints the live "current" pointer at it; each rollback destroys the most
recent release and repoints "current" back. So a single rollback cleanly undoes a single
deploy — except for ad-hoc repos, which need special care (see restore_all below).

Discovery is done purely with local git — no GitHub API and no token required.
"""
import getpass
import os
import re
import sys
from datetime import datetime

from dotenv import load_dotenv

from src.github_utils import (
    get_sprintdates,
    parse_week_value,
    get_date_range_timezone,
    get_release_repo_order,
    release_repo_sort_key,
)
from src.deploy_utils import (
    get_deploy_config,
    DeployConfigError,
    run_command,
    build_command,
    read_deploy_status,
    discover_repos,
)


# ---------------------------------------------------------------------------
# Output styling + logging (tty-aware, degrades gracefully when piped)
# ---------------------------------------------------------------------------
_TTY = sys.stdout.isatty()
BOLD = "\033[1m" if _TTY else ""
DIM = "\033[2m" if _TTY else ""
CYAN = "\033[36m" if _TTY else ""
GREEN = "\033[32m" if _TTY else ""
YELLOW = "\033[33m" if _TTY else ""
RED = "\033[31m" if _TTY else ""
RESET = "\033[0m" if _TTY else ""

# Every line we emit is also kept here so `--output` can write a plain-text record of
# the whole run to disk. This is an audit trail for a production deploy, so we capture
# the narration verbatim (without the ANSI colors).
_LOG_LINES = []


def log(message, *, error=False):
    """Print a line to the console and record an uncolored copy for the run log."""
    stream = sys.stderr if error else sys.stdout
    print(message, file=stream)
    _LOG_LINES.append(_strip_ansi(message))


def _strip_ansi(text):
    return re.sub(r"\033\[[0-9;]*m", "", text)


def section(title):
    log(f"\n{BOLD}{CYAN}{'=' * 64}{RESET}")
    log(f"{BOLD}{CYAN}  {title}{RESET}")
    log(f"{BOLD}{CYAN}{'=' * 64}{RESET}")


# ---------------------------------------------------------------------------
# A targeted repository and the snapshot we will restore it to
# ---------------------------------------------------------------------------
class TargetRepo:
    """One repo we intend to deploy, plus the pre-run snapshot of its deployed state.

    The snapshot (original_branch / original_rev) is captured during discovery, before
    we touch anything. EVERY restore decision later keys off this snapshot — never off
    how far the deploy got — so it must reflect the true starting state.
    """

    def __init__(self, name, path, release_branch, status):
        self.name = name
        self.path = path
        self.release_branch = release_branch

        # status is None when the deployment status file is missing/unreadable, i.e. we
        # cannot tell what is currently deployed. We treat that case conservatively.
        self.status_known = status is not None
        self.original_branch = (status or {}).get("branch")
        self.original_rev = (status or {}).get("rev")

        # "Ad-hoc" means the repo is currently serving something other than the official
        # release branch (e.g. a hot-fix or feature branch). Only a known, non-main/master
        # branch counts; an unknown branch is handled as "unknown", not ad-hoc.
        self.is_adhoc = bool(self.original_branch) and self.original_branch not in ("main", "master")

        # Outcome flags, filled in as the run progresses (used for the final summary).
        self.deployed = False   # the official release was deployed successfully
        self.restored = False   # we rolled this repo back / re-pointed it to its origin
        self.failed = False     # this is the repo whose command errored


class DeployFailure(Exception):
    """Raised the moment any deploy/rollback step fails, to trigger a full restore."""


# ---------------------------------------------------------------------------
# Small git wrapper used only for read-only discovery
# ---------------------------------------------------------------------------
def git(repo_path, *args):
    """Run a git subcommand inside repo_path; returns (returncode, stdout, stderr)."""
    return run_command(["git", *args], cwd=repo_path)


def detect_release_branch(repo_path):
    """Return 'main' or 'master' (whichever the repo's origin has), else None."""
    for branch in ("main", "master"):
        rc, _, _ = git(repo_path, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}")
        if rc == 0:
            return branch
    return None


def release_branch_has_rc(repo_path, release_branch, rc_regex):
    """True if the release branch's history contains the target version's RC.

    When an RC PR (titled e.g. v2026.08-rc1) is merged into main/master, that version
    string lands in the merge commit's message. So grepping the release branch's log
    for the version's RC pattern tells us the release candidate is present — which is
    exactly what "this repo is ready to deploy for this sprint" means.
    """
    rc, out, _ = git(
        repo_path,
        "log",
        f"origin/{release_branch}",
        f"--grep={rc_regex}",
        "-E",
        "--format=%H",
        "-n", "1",
    )
    return rc == 0 and out.strip() != ""


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
def parse_args():
    """Parse CLI args, matching the key=value style of the sibling scripts.

    Supported:
      week=YYYY.WW   release a specific sprint (even week); default is the current sprint
      target=<name>  override the deploy target (default: DEPLOY_DEFAULT_DEPLOY_TARGET)
      name=<repo>    limit the run to a single repo directory
      --dry-run      discover and print the plan, but run no deploy/rollback commands
      --yes          skip the interactive confirmation (for non-interactive use)
      --output       also write the run log to output/<version>-deploy.log
    """
    args = {
        "target_week": None,   # (year, week) tuple, or None for "current sprint"
        "target": None,        # deploy target override
        "repo_name": None,     # single-repo filter
        "dry_run": False,
        "assume_yes": False,
        "write_output": False,
    }

    for arg in sys.argv[1:]:
        lower = arg.lower()
        if lower.startswith("week="):
            try:
                parsed = parse_week_value(arg.split("=", 1)[1])
            except ValueError as exc:
                if str(exc) == "single_week_must_be_even":
                    print("For a single week, the week number must be even (sprint end week).")
                else:
                    print("Invalid week. Use week=YYYY.WW with an even week number.")
                sys.exit(1)
            # A release is one sprint, so only a single even week makes sense here.
            if parsed["mode"] != "single":
                print("deploy-release.py releases a single sprint; use week=YYYY.WW (not a range).")
                sys.exit(1)
            args["target_week"] = parsed["start"]
        elif lower.startswith("target="):
            value = arg.split("=", 1)[1].strip()
            if not value:
                print("Invalid target argument. Use target=<deploy target>.")
                sys.exit(1)
            args["target"] = value
        elif lower.startswith(("name=", "repo=", "repo_name=", "repo-name=")):
            value = arg.split("=", 1)[1].strip()
            if not value:
                print("Invalid name argument. Use name=<repo name>.")
                sys.exit(1)
            args["repo_name"] = value
        elif arg == "--dry-run":
            args["dry_run"] = True
        elif arg == "--yes":
            args["assume_yes"] = True
        elif arg == "--output":
            args["write_output"] = True
        else:
            print("Unrecognized argument.")
            print("Usage: python3 deploy-release.py [week=YYYY.WW] [target=<name>] [name=<repo>] [--dry-run] [--yes] [--output]")
            sys.exit(1)

    return args


# ---------------------------------------------------------------------------
# Discovery: which repos are ready to deploy, and what is deployed right now
# ---------------------------------------------------------------------------
def discover_targets(config, version, rc_regex, repo_name_filter):
    """Walk the cloned repos and return the ordered list of TargetRepo to deploy.

    For each repo we: fetch from origin (so the release-branch history is current),
    find the release branch, check whether the target version's RC is present, and —
    for the ones that qualify — prep the dev branch and snapshot the currently deployed
    state. Nothing here mutates a deployment; it is all reads and local git.
    """
    base_path = config["DEPLOY_SOURCE_REPO_PATH"]
    repo_names = discover_repos(base_path)

    # Honor an optional single-repo filter (e.g. to re-deploy just one service).
    if repo_name_filter:
        repo_names = [n for n in repo_names if n == repo_name_filter]
        if not repo_names:
            log(f"{YELLOW}[WARNING]{RESET} No repo named '{repo_name_filter}' found under {base_path}.")
            return []

    # Deploy in the configured release order (same rules as the release notes), so the
    # most important services go first; fall back to alphabetical within each group.
    order_rules = get_release_repo_order()
    repo_names.sort(key=lambda name: release_repo_sort_key(name, order_rules))

    targets = []
    for name in repo_names:
        repo_path = os.path.join(base_path, name)

        # Step 1: get the repo up to date so the release-branch history we inspect next
        # reflects what was actually merged for this sprint.
        rc, _, err = git(repo_path, "fetch", "--all", "--prune")
        if rc != 0:
            log(f"{YELLOW}[WARNING]{RESET} {name}: git fetch failed; skipping. {err.strip()}")
            continue

        # Step 2: which branch is the release branch here, main or master?
        release_branch = detect_release_branch(repo_path)
        if not release_branch:
            log(f"{YELLOW}[WARNING]{RESET} {name}: no origin/main or origin/master; skipping.")
            continue

        # Step 3: is this repo part of THIS release? Only if the version's RC is on the
        # release branch. Repos without it merged are simply not ready and are skipped.
        if not release_branch_has_rc(repo_path, release_branch, rc_regex):
            continue

        # Step 4: this repo is targeted. Mirror the operator's habit of leaving the repo
        # checked out on an up-to-date dev branch. This is prep only; the deploy itself
        # is driven by the deploy tool, so a missing dev branch is a warning, not a blocker.
        rc, _, _ = git(repo_path, "checkout", "dev")
        if rc == 0:
            git(repo_path, "pull", "--ff-only")
        else:
            log(f"{YELLOW}[WARNING]{RESET} {name}: no dev branch to update (continuing).")

        # Step 5: snapshot what is deployed RIGHT NOW, before we change anything. This is
        # the state we will restore to on failure, so we read it once, here, up front.
        status = read_deploy_status(
            repo_path,
            config["DEPLOY_REPO_DEPLOY_STATUS_FILENAME"],
            config["DEPLOY_REPO_DEPLOY_STATUS_BRANCH_KEY"],
            config["DEPLOY_REPO_DEPLOY_STATUS_REV_KEY"],
        )
        targets.append(TargetRepo(name, repo_path, release_branch, status))

    return targets


def print_plan(targets, version, target, dry_run):
    """Show the operator exactly what will happen before any irreversible action."""
    section(f"Deployment plan for {version}  (target: {target})")
    for repo in targets:
        if not repo.status_known:
            state = f"{YELLOW}currently deployed state UNKNOWN (no status file){RESET}"
        elif repo.is_adhoc:
            state = (
                f"{YELLOW}ad-hoc: {repo.original_branch} @ {repo.original_rev or '?'}{RESET} "
                f"-> will roll back first, then deploy"
            )
        else:
            state = f"on {repo.original_branch} (clean) -> will deploy"
        log(f"  {BOLD}{repo.name}{RESET}: {state}")
    if dry_run:
        log(f"\n{DIM}[DRY RUN] No deploy or rollback commands will be run.{RESET}")


# ---------------------------------------------------------------------------
# Restore-on-failure: put every touched repo back the way it started
# ---------------------------------------------------------------------------
def restore_all(processed, config, target, dry_run):
    """Undo this run for every repo we touched, in reverse order.

    Why reverse: we undo the most recent change first, mirroring how the operator would
    walk back out of the deployment.

    For each repo we ALWAYS run one rollback. Per the deploy tool's release model, a
    single rollback destroys the release this run created (or the dangling release left
    by a failed deploy) and repoints "current" at the prior release:

      * A repo that started on main/master is now back on its original official release.
        One rollback fully restores it.

      * A repo that started ad-hoc is the tricky case. During the forward pass we rolled
        its ad-hoc release away and then deployed the official release on top. A single
        rollback here only steps back to the release that preceded the ad-hoc one — NOT
        the ad-hoc state we found. To truly restore it we must re-deploy the exact
        revision that was deployed before this run started:
            <rollback command> <target>                       # undo our official deploy
            <deploy command> <target> <rev flag> <orig_rev>   # rebuild the original ad-hoc state
        That pair is what the operator runs by hand, and it is why we captured
        original_rev up front.
    """
    section("FAILURE — restoring every touched repo to its original state")
    for repo in reversed(processed):
        # Undo the release this run produced on this repo.
        log(f"  {repo.name}: rolling back the release this run created.")
        if not dry_run:
            rc, _, err = run_command(build_command(config["DEPLOY_ROLLBACK_COMMAND"], target), cwd=repo.path)
            if rc != 0:
                log(f"  {RED}[ERROR]{RESET} {repo.name}: rollback failed — needs manual review. {err.strip()}", error=True)
        repo.restored = True

        # Ad-hoc repos need the original revision re-deployed to land back on the exact
        # state we found them in (see the docstring above for why a rollback alone isn't
        # enough).
        if repo.is_adhoc:
            if repo.original_rev:
                log(f"  {repo.name}: re-deploying original revision {repo.original_rev} (was on {repo.original_branch}).")
                if not dry_run:
                    cmd = build_command(
                        config["DEPLOY_DEPLOY_COMMAND"],
                        target,
                        config["DEPLOY_DEPLOY_COMMAND_REVISION_FLAG"],
                        repo.original_rev,
                    )
                    rc, _, err = run_command(cmd, cwd=repo.path)
                    if rc != 0:
                        log(f"  {RED}[ERROR]{RESET} {repo.name}: re-deploy of original revision failed — needs manual review. {err.strip()}", error=True)
            else:
                # We knew it was ad-hoc but have no revision to return to. We cannot
                # restore it exactly; the rollback above is the best we can do.
                log(
                    f"  {RED}[WARNING]{RESET} {repo.name}: was ad-hoc on {repo.original_branch} but no "
                    f"original revision was recorded — exact restoration is impossible. "
                    f"MANUAL REVIEW REQUIRED.",
                    error=True,
                )


# ---------------------------------------------------------------------------
# The sequential deploy loop
# ---------------------------------------------------------------------------
def deploy_all(targets, config, target, dry_run):
    """Deploy each targeted repo in turn; on the first failure, restore everything.

    Returns True if every repo deployed cleanly, False if a failure triggered a restore.
    `processed` accumulates each repo the moment we begin working on it, so that if a
    later repo fails we know exactly which repos need restoring.
    """
    section(f"Deploying {len(targets)} repo(s) to '{target}'")
    processed = []
    try:
        for repo in targets:
            # Mark this repo as touched as soon as we begin — if anything below fails,
            # restore_all must include it.
            processed.append(repo)
            log(f"\n{BOLD}{repo.name}{RESET}")

            # If the repo is currently serving an ad-hoc deployment, clear it first so we
            # deploy the official release onto a clean main/master baseline. This rollback
            # is an irreversible production action.
            if repo.is_adhoc:
                log(f"  Currently ad-hoc ({repo.original_branch}); rolling that back before deploying.")
                if not dry_run:
                    rc, _, err = run_command(build_command(config["DEPLOY_ROLLBACK_COMMAND"], target), cwd=repo.path)
                    if rc != 0:
                        raise DeployFailure(f"{repo.name}: pre-deploy rollback failed. {err.strip()}")
            elif not repo.status_known:
                # We could not read the status file, so we cannot prove the repo is on a
                # clean branch. We do NOT pre-roll-back (no evidence of an ad-hoc deploy),
                # but we flag it so the operator knows this repo went out unverified.
                log(f"  {YELLOW}[WARNING]{RESET} deployed state unknown (no status file); deploying without a pre-rollback.")

            # Deploy the official release. This is the irreversible action this whole
            # script exists to perform.
            log(f"  Deploying the {repo.release_branch} release.")
            if not dry_run:
                rc, out, err = run_command(build_command(config["DEPLOY_DEPLOY_COMMAND"], target), cwd=repo.path)
                if rc != 0:
                    repo.failed = True
                    raise DeployFailure(f"{repo.name}: deploy failed. {err.strip() or out.strip()}")

            repo.deployed = True
            log(f"  {GREEN}[SUCCESS]{RESET} {repo.name} deployed.")

        return True

    except DeployFailure as exc:
        # One repo failed. Per the all-or-nothing contract, stop here and put every repo
        # we touched (including this one) back the way it was before the run started.
        log(f"\n{RED}[ERROR]{RESET} {exc}", error=True)
        restore_all(processed, config, target, dry_run)
        return False


def print_summary(targets, succeeded):
    """A final per-repo tally so the operator can see the end state at a glance."""
    section("Summary")
    for repo in targets:
        if repo.deployed and succeeded:
            log(f"  {GREEN}DEPLOYED{RESET}  {repo.name}")
        elif repo.failed:
            log(f"  {RED}FAILED{RESET}    {repo.name} (deploy errored; restored)")
        elif repo.restored:
            log(f"  {YELLOW}RESTORED{RESET}  {repo.name} (rolled back to original state)")
        else:
            log(f"  {DIM}SKIPPED{RESET}   {repo.name} (not reached before the run aborted)")


def write_run_log(version):
    """Persist the full narration to output/<version>-deploy.log as an audit trail."""
    os.makedirs("output", exist_ok=True)
    path = os.path.join("output", f"{version}-deploy.log")
    with open(path, "w") as fh:
        fh.write("\n".join(_LOG_LINES) + "\n")
    print(f"Run log written to {path}")


# ---------------------------------------------------------------------------
# Entry point — ties the whole story together
# ---------------------------------------------------------------------------
def main():
    load_dotenv()
    args = parse_args()

    # Load and validate the deployment configuration up front; refuse to run a
    # production deploy against a half-configured environment.
    try:
        config = get_deploy_config()
    except DeployConfigError as exc:
        print(f"{RED}[ERROR]{RESET} {exc}", file=sys.stderr)
        sys.exit(1)

    # The deploy target: an explicit target= wins, else the configured default.
    target = args["target"] or config["DEPLOY_DEFAULT_DEPLOY_TARGET"]

    # Work out the sprint version to release. With no week= argument we release the
    # current sprint, computed exactly as main.py does so the two agree on "current".
    date_range_tz, _ = get_date_range_timezone()
    _, _, sprint_year, sprint_week, _ = get_sprintdates(target_week=args["target_week"], range_tz=date_range_tz)
    version = f"v{sprint_year}.{sprint_week:02d}"

    # The marker we look for on each release branch: this version's RC, e.g.
    # "v2026.08-rc" followed by a number. re.escape keeps the dot literal.
    rc_regex = rf"{re.escape(version)}-rc[0-9]+"

    section(f"deploy-release.py — releasing {version}")
    log(f"  Deploy target : {target}")
    log(f"  Repo path     : {config['DEPLOY_SOURCE_REPO_PATH']}")
    log(f"  Run at        : {datetime.now().isoformat(timespec='seconds')}")

    # Soft user check: this is expected to run as the configured DEPLOY_USER. If it is
    # not, warn loudly but do not block — the environment may legitimately differ.
    current_user = getpass.getuser()
    if current_user != config["DEPLOY_USER"]:
        log(f"  {YELLOW}[WARNING]{RESET} running as '{current_user}', expected '{config['DEPLOY_USER']}'.")

    # Discovery: find the repos whose RC for this version is ready, and snapshot their
    # current deployed state. This phase is entirely read-only.
    targets = discover_targets(config, version, rc_regex, args["repo_name"])
    if not targets:
        log(f"\nNo repos are targeted for release {version}. Nothing to deploy.")
        if args["write_output"]:
            write_run_log(version)
        sys.exit(0)

    print_plan(targets, version, target, args["dry_run"])

    # A dry run stops here: the operator has seen the plan, but we change nothing.
    if args["dry_run"]:
        if args["write_output"]:
            write_run_log(version)
        sys.exit(0)

    # Final human gate before any irreversible action (unless --yes was passed).
    if not args["assume_yes"]:
        try:
            answer = input(f"\nDeploy these {len(targets)} repo(s) to '{target}'? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            log("Aborted by operator. Nothing was deployed.")
            sys.exit(1)

    # Do the work. On failure, deploy_all has already restored every touched repo.
    succeeded = deploy_all(targets, config, target, args["dry_run"])
    print_summary(targets, succeeded)

    if args["write_output"]:
        write_run_log(version)

    # Exit non-zero if anything failed, so callers/CI can detect the aborted release.
    sys.exit(0 if succeeded else 1)


if __name__ == "__main__":
    main()
