# AutomatedRelease

This repo provides tooling to manage releases for Agile sprint periods.

**The preferred way to create a release is the guided `release-wizard.py` script** — see [Release Wizard (Recommended)](#release-wizard-recommended) below. It walks you through the entire process end to end (pick a sprint → review in-scope PRs → create release candidates → merge them → verify → generate release notes → publish to Confluence), confirming at every step.

The wizard orchestrates a set of underlying scripts. You don't need to run these directly for a normal release, but they remain fully available and documented for finer control or for performing a single step manually (see [Manual Usage (Individual Scripts)](#manual-usage-individual-scripts)):

1. **`main.py`** - Outputs merged PRs from the last 2-week sprint for every repo within the given organization. Shows PR details including commit IDs and tags.

2. **`unmerged-prs.py`** - Finds open PRs that were approved within a sprint window but not yet merged. Useful for identifying work that is ready to ship but hasn't been released.

3. **`create-release-candidate.py`** - Creates release candidate PRs from dev to release branches for repositories with unreleased changes. Automatically determines the correct RC version number.

4. **`create-release-notes.py`** - Generates markdown release notes for a sprint version by analyzing merged RC PRs and merge commits on release branches.

5. **`create-confluence-page.py`** - Publishes a release notes markdown file to Confluence as a new page under the appropriate year page.

Only repositories tagged with the topic from `DEPLOYABLE_TOPIC` in `.env` are considered deployable (defaults to `deployer-php`). If you do not already have a `.env` file configured for this repo, one will be created and configured for you. 

## Prerequisites

These are required before running **any** of the scripts, including the wizard:

1. **Python 3.7 or later** — all scripts require Python 3.7+ (`asyncio.run` is the binding constraint). Any modern 3.x release (3.10+) works fine.

2. **Dependencies installed** — `pip install httpx python-dotenv markdown` (see [Dependencies](#dependencies) for details).

3. **A GitHub Personal Access Token** to authenticate with GitHub.
   - Generate one at [GitHub Personal Access Tokens](https://github.com/settings/tokens) with the permissions you need:
     - **Read-only** (e.g. viewing sprint PRs with `main.py`): `read:org` and `repo` (read access)
     - **Creating PRs** (the wizard and `create-release-candidate.py`): `read:org` and full `repo`
   - On first run you'll be prompted for the token and a `.env` will be created automatically; you can also do this manually via `cp .env.example .env` and paste the token in yourself.

4. **A configured `.env`** — set `ORG_NAME` (and optional `TEAM_NAME`). To publish release notes to Confluence, also set `CONFLUENCE_EMAIL` and `CONFLUENCE_API_TOKEN`.

## Release Wizard (Recommended)

`release-wizard.py` is the primary, recommended way to cut a release. It ties the whole process together into an interactive, step-by-step flow with an explicit confirmation at every gate. It is a thin orchestrator: it reuses the sprint-date logic from `src/github_utils.py` and runs the underlying scripts under the hood (parsing their output), so their individual behavior is unchanged.

For a normal release you only need this script — you do not need to run the individual scripts yourself.

**First-time setup:** make sure you've met the [Prerequisites](#prerequisites) above — dependencies installed and your `.env` configured (GitHub token, org/team, and Confluence credentials for the publish step).

**Usage:** `python3 release-wizard.py [org=<org name>] [team=<team name>]`

**Arguments:**
  - `[org=<org name>]` (optional) - GitHub organization name. If omitted, `ORG_NAME` from `.env` is used. The wizard aborts immediately if neither is set.
  - `[team=<team name>]` (optional) - Filter by team. If omitted, `TEAM_NAME` from `.env` is used when present.

**The guided flow:**
  1. **Select a sprint** - choose previous, current, or next; each is shown with its `vYYYY.WW` version and date window. The current sprint is the default (press Enter to select it).
  2. **Review PRs in scope** - runs `main.py format=console` and shows its output so you can see exactly what merged this sprint. If the sprint has no PRs (common for the *next* sprint), you can pick a different one.
  3. **Create release candidates** - once you confirm, the wizard generates the JSON input (`output/vYYYY.WW.json`), previews with a dry run, asks for explicit confirmation, then runs `create-release-candidate.py` and lists the created RC PR links.
  4. **Merge the RC PRs (manual)** - the wizard prints the PR links and waits. You open and merge each one yourself in GitHub; this friction is intentional.
  5. **Verify** - re-runs the dry run to confirm which repos are now fully merged versus still have an open, un-merged RC PR. If any remain un-merged, it loops back so you can merge them.
  6. **Generate release notes** - runs `create-release-notes.py --output` (writing `output/vYYYY.WW-release-notes.md`) and previews the result for your confirmation.
  7. **Publish to Confluence (optional)** - if `CONFLUENCE_EMAIL`/`CONFLUENCE_API_TOKEN` are set and you confirm, runs `create-confluence-page.py` (with an optional `--draft`). If the Confluence credentials are missing, this step is skipped gracefully and the notes file is left in `output/`.

You can abort at any prompt by typing `q` (or `quit`/`abort`); no further actions are taken.

**Resuming an interrupted run:** the wizard records its progress to `output/.wizard-progress.json` (git-ignored). If you quit, are interrupted, or close the terminal partway through, the next run detects the unfinished release and offers to resume it — either at the *creating/merging release candidates* stage or the *release notes* stage. Resuming re-checks the live state on GitHub (which RC PRs exist and which are merged), so it continues correctly regardless of how far the previous run got. You can also decline and either discard the saved progress or start a different sprint. Progress is cleared automatically once a release completes.

**Example:**
```bash
python3 release-wizard.py org=my-org
```

**Note:** This script reuses the underlying scripts, so it relies on the same `.env` configuration (GitHub token, org/team, and Confluence credentials for the publish step).

## Manual Usage (Individual Scripts)

For most releases you should use the [Release Wizard](#release-wizard-recommended) above, which runs these scripts for you in the correct order. The sections below document the individual scripts for finer control, troubleshooting, or running a single step on its own.

### main.py - View Sprint PRs

This script retrieves merged PRs from the last 2-week sprint and outputs details including commit IDs and tags.

It also surfaces **nested feature-branch merges**: if a PR was merged into another feature branch and that branch was later merged to `dev` via its own PR, the inner PR is included in the output even though its base branch is not `dev`. These entries are annotated with the dev PR that carried them into the release (`Note: merged into dev via #<n>` in console/text/markdown, `merged_via_pr` in JSON). This relies on standard `Merge pull request #N` merge commits; feature-to-feature PRs that were squash- or rebase-merged are not detected.

Repository order in the output follows the optional `RELEASE_REPO_ORDER` key in `.env` (same rules as [create-release-notes.py](#create-release-notespy---generate-release-notes)). When unset, repositories are ordered alphabetically by full name.

**Usage:** `python3 main.py [org=<org name>] [team=<team name>] [name=<repo name>] [format=console|text|json|markdown] [branch=all|release|dev] [week=YYYY.WW|YYYY.WW-WW|YYYY.WW-YYYY.WW] [offset=<Nh|Nd|Nw>]`

**Arguments:**
  - `[org=<org name>]` (optional) - GitHub organization name. If omitted, `ORG_NAME` from `.env` is used.
  - `[team=<team name>]` (optional) - Filter by specific team. If omitted, `TEAM_NAME` from `.env` is used when present.
  - `[name=<repo name>]` (optional) - Filter output to one repository. Supports repository name (for example `my-service`) or full name (for example `my-org/my-service`). If no exact match is found, the closest repository name is used.
  - `[format=console|text|json|markdown]` (optional) - Output format. Defaults to `json`. `console` outputs colored text for the terminal. `text` outputs the same layout without ANSI colors. `markdown` produces a table-based layout suitable for saving to a `.md` file.
  - `[branch=all|release|dev]` (optional) - Branch filter. Defaults to `dev`.
  - `[week=YYYY.WW|YYYY.WW-WW|YYYY.WW-YYYY.WW]` (optional) - Week filter. Single week uses sprint behavior and must be even (end week). Range values include all dates from Monday of the start week through Sunday of the end week, interpreted in `DATE_RANGE_TZ_OFFSET`.
  - `[week-offset=<Nh|Nd|Nw>]` or `[offset=<Nh|Nd|Nw>]` (optional) - Shifts the date filter window by the offset. Examples: `1d`, `1w`, `12h`, `1.5h`.
  - `week=...` is required when `name=<repo name>` is provided.

**Optional `.env` keys for `main.py`:**
  - `ORG_NAME=<your-org>`
  - `TEAM_NAME=<your-team>`
  - `DEPLOYABLE_TOPIC=<repo-topic>` (defaults to `deployer-php`)
  - `DATE_RANGE_TZ_OFFSET=<timezone-offset>` (defaults to `0`; examples: `-8`, `5.5`, `+05:30`)

**Examples:**
  * `python3 main.py org=my-org` – JSON output, dev branch only, current sprint
  * `python3 main.py org=my-org format=console` – text output, dev branch only, current sprint
  * `python3 main.py org=my-org team=my-team format=json branch=dev` – JSON output for specific team, dev branch only
  * `python3 main.py format=json` – uses `ORG_NAME` from `.env`, outputs JSON
  * `python3 main.py org=my-org format=console week=2026.08` – text output for sprint ending in week 8 of 2026
  * `python3 main.py org=my-org team=my-team format=json week=2025.52` – JSON output for specific team and sprint
  * `python3 main.py org=my-org name=my-service format=console week=2026.08` – text output for one repo in the specified sprint window
  * `python3 main.py org=my-org name=my-service format=console week=2026.07-11` – one repo for ISO week range within 2026
  * `python3 main.py org=my-org name=my-service format=console week=2025.50-2026.05` – one repo for ISO week range across years
  * `python3 main.py org=my-org name=my-service format=console week=2026.07-11 offset=12h` – same range shifted by 12 hours
  * `python3 main.py org=my-org format=markdown > sprint.md` – markdown output saved to a file

**Note:** Scripts can also be run directly without `python3`:
```bash
./main.py org=my-org
```
If that doesn't work, your Python installation may require `python` instead of `python3`.

**Tip:** When using `format=console`, pipe through `less -R` to preserve ANSI colors while scrolling:
```bash
./main.py org=my-org format=console | less -R
```

### unmerged-prs.py - Find Approved Unmerged PRs

This script finds open PRs that received an approval within the sprint window but have not yet been merged. It takes the same arguments as `main.py`.

**Usage:** `./unmerged-prs.py [org=<org name>] [team=<team name>] [name=<repo name>] [format=console|text|json|markdown] [branch=all|release|dev] [week=YYYY.WW|YYYY.WW-WW|YYYY.WW-YYYY.WW] [offset=<Nh|Nd|Nw>] [--output]`

**Arguments:** Same as `main.py` — see above.

**Examples:**
  * `./unmerged-prs.py org=my-org` – JSON output, dev branch only, current sprint
  * `./unmerged-prs.py org=my-org format=console week=2026.08` – colored output for a specific sprint
  * `./unmerged-prs.py org=my-org format=console week=2026.08 --output` – write to `output/v2026.08-unmerged-prs.txt`

**How it works:**
  - Fetches open PRs sorted by `updated_at` and stops when PRs predate the sprint window
  - For each candidate PR, concurrently fetches its reviews and its mergeability status
  - Uses each reviewer's *latest* review within the sprint to determine their final state — so an approve followed by a request for changes counts as not approved
  - Includes a PR only if at least one reviewer's final sprint review is `APPROVED` and the PR has no merge conflicts
  - PRs where GitHub hasn't computed mergeability yet (`null`) are included to avoid missing forgotten PRs
  - Output shows the latest approval timestamp, PR details, and the list of approvers
  - A summary at the end lists per-repo counts for both unmerged and merged approved PRs; in `console` format the unmerged count is bold yellow (action may be needed) and "no approved unmerged PRs" is bold green (all clear)
  - Repos with only approved merged PRs (no unmerged) appear in the summary for accuracy confirmation, but not in the main PR details
  - JSON output includes `repositories_scanned`, `repositories_with_prs`, `total_approved_count`, `total_approved_merged_count` at the top level and `approved_count`/`approved_merged_count` per repository

### create-release-candidate.py - Create Release PRs

This script takes the JSON output from `main.py` and creates release candidate PRs from dev to release branches (main/master) for repositories with unreleased changes. It automatically determines the correct RC version number by checking existing PRs.

**Usage:** `python3 create-release-candidate.py <json_file> [--dry-run]`

**Arguments:**
  - `<json_file>` (required) - JSON output file from main.py
  - `[--dry-run]` (optional) - Preview what would happen without creating PRs

**Examples:**
  * Generate JSON from main.py:
    ```bash
    python3 main.py org=my-org format=json > sprint-prs.json
    ```
  * Preview release candidates (dry run):
    ```bash
    python3 create-release-candidate.py sprint-prs.json --dry-run
    ```
  * Create release candidate PRs:
    ```bash
    python3 create-release-candidate.py sprint-prs.json
    ```

**How it works:**
  - Reads the sprint version from the JSON (e.g., `v2026.10`)
  - For each repository in the JSON input:
    - Detects the release branch (`main` or `master`)
    - Compares the `dev` branch against the release branch to check for unreleased commits
  - For repositories where `dev` is ahead of the release branch:
    - Checks for existing release candidate PRs (e.g., `v2026.10-rc0`, `v2026.10-rc1`)
    - Creates a new PR with the next RC number (e.g., `v2026.10-rc2`)
    - Creates PR from `dev` branch to release branch (`main` or `master`)
  - Skips repositories where `dev` has no commits ahead of the release branch
  - Works correctly regardless of whether the release branch uses regular or squash merges

**Requirements:**
  - Your GitHub token must have full `repo` permissions to create PRs
  - Repositories must have a `dev` branch

### create-release-notes.py - Generate Release Notes

This script generates markdown release notes for a release version (`vYYYY.WW`) across deployable repositories (topic from `DEPLOYABLE_TOPIC`, default `deployer-php`). It finds merged RC PRs for the target version and builds notes from commits tied to those RC PRs.

**Usage:** `python3 create-release-notes.py [org=<org name>] [team=<team name>] [name=<repo name>] [week=YYYY.WW] [--output]`

**Arguments:**
  - `[org=<org name>]` (optional) - GitHub organization name. If omitted, `ORG_NAME` from `.env` is used.
  - `[team=<team name>]` (optional) - Filter repositories by team. If omitted, `TEAM_NAME` from `.env` is used when present.
  - `[name=<repo name>]` (optional) - Filter output to one repository. Supports repository name or full name. If no exact match is found, the closest repository name is used.
  - `[week=YYYY.WW]` (optional) - Target release version week (must be even). If omitted, current even week is used, or the most recent even week if the current week is odd.
  - `[--output]` (optional) - Write output to `output/vYYYY.WW-release-notes.md` instead of stdout.
  - `week=YYYY.WW` is required when `name=<repo name>` is provided.

**Examples:**
  * Current/most recent release notes for an organization:
    ```bash
    python3 create-release-notes.py org=my-org
    ```
  * Notes for a specific team:
    ```bash
    python3 create-release-notes.py org=my-org team=my-team
    ```
  * Notes for a specific release week:
    ```bash
    python3 create-release-notes.py org=my-org week=2026.08
    ```
  * Team + specific release week:
    ```bash
    python3 create-release-notes.py org=my-org team=my-team week=2026.08
    ```
  * One repository + specific release week:
    ```bash
    python3 create-release-notes.py org=my-org name=my-service week=2026.08
    ```
  * Write release notes to file:
    ```bash
    python3 create-release-notes.py org=my-org week=2026.08 --output
    ```

**Output format:**
  - Markdown grouped by repository
  - Repository order is controlled by the optional `RELEASE_REPO_ORDER` key in `.env` (see below). When unset, repositories are ordered alphabetically by full name
  - Includes release branch, current RC PR, and previous RC PR (if any)
  - Each entry includes a PR link (`#<number>` linking to the GitHub PR), PR title, and first non-empty paragraph from the PR body
  - Markdown formatting in that paragraph that would break the layout of the (markdown) release notes — headings, blockquotes, and emphasis (bold/italic) — is stripped. Bullet lists, links (`[text](url)`), and inline code spans (`` `code` ``) are preserved; bullet lists render as a nested list under the entry
  - Any `PBT-XXXX` references (4 digits) in rendered text are converted to GitLab issue links:
    - `https://pblgssgitlab01.aws.pointblue.org/point-blue-engineering-team/point-blue-tech/-/issues/XXXX`

**How it works:**
  - Reads organization/team repositories and keeps deployable repos only (topic from `DEPLOYABLE_TOPIC`, default `deployer-php`)
  - Detects the release branch (`main` or `master`)
  - Looks for merged RC PRs matching the target version (for example `v2026.08-rc1`)
  - Uses commits between the previous RC merge commit and the current RC merge commit
  - If there is no previous RC, uses commits contained in the current RC PR
  - Excludes RC commits and formats merged PR data as release notes

**Repository ordering (`RELEASE_REPO_ORDER`):**
  - Optional `.env` key. A comma-separated list of patterns, highest priority first
  - A pattern containing `*` is matched as a glob (e.g. `*auth*` matches any repo whose name contains `auth`); any other pattern is an exact match (e.g. `my-org/api`, `my-login-app`)
  - Each pattern is tested against both the repo's full name (`org/repo`) and short name (`repo`), case-insensitively
  - Repos matching an earlier pattern are listed before repos matching a later pattern; repos matching no pattern are listed last. The first matching pattern wins for a repo that could match several
  - Within any single group, repos are ordered alphabetically by full name
  - Example: `RELEASE_REPO_ORDER='*dblib*,my-org/api,*auth*,my-login-app'` lists `dblib`-named repos first (alphabetically), then `my-orf/api`, then any repo containing `auth`, then `my-login-app`, then everything else


### create-confluence-page.py - Publish Release Notes to Confluence

This script reads a release notes markdown file (produced by `create-release-notes.py`) and creates a new Confluence page for that sprint release under the appropriate year page.

**Usage:** `python3 create-confluence-page.py <release-notes-file> [--draft]`

**Arguments:**
  - `<release-notes-file>` (required) - Path to the markdown file, e.g. `output/v2026.10-release-notes.md`
  - `[--draft]` (optional) - Create the page as an unpublished draft. Skips the duplicate page check. Useful for testing formatting before the real page exists.

**Examples:**
  * Publish release notes for a sprint:
    ```bash
    python3 create-confluence-page.py output/v2026.10-release-notes.md
    ```
  * Create an unpublished draft to preview formatting:
    ```bash
    python3 create-confluence-page.py output/v2026.10-release-notes.md --draft
    ```
  * Full workflow — generate then publish:
    ```bash
    python3 create-release-notes.py org=my-org week=2026.10 --output
    python3 create-confluence-page.py output/v2026.10-release-notes.md
    ```

**How it works:**
  - Parses the version (e.g. `v2026.10`) and year from the filename
  - Page title is set to `v2026.10 Release`
  - Aborts with an error if a page with that title already exists in Confluence
  - If the year page (e.g. `v2026 Releases`) doesn't exist yet, it is created automatically under the main releases page
  - Converts the markdown to Confluence storage format and creates the sprint page under the year page

**Required `.env` keys:**
  - `CONFLUENCE_EMAIL` — your Atlassian account email
  - `CONFLUENCE_API_TOKEN` — generate one by going to your Atlassian account settings → [Security](https://id.atlassian.com/manage-profile/security) → API tokens


## Dependencies
1.   [httpx](https://pypi.org/project/httpx/)
2.   [python-dotenv](https://pypi.org/project/python-dotenv/)
3.   [markdown](https://pypi.org/project/Markdown/) *(required for `create-confluence-page.py`)*

In order to run this program, you will need to have installed the proper dependencies.
You can do this by opening an IDE and running the command `pip install {example_module}` for each dependency you are missing.
 
 
