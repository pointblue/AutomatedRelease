# AutomatedRelease

This repo provides scripts to help manage releases for Agile sprint periods:

1. **`main.py`** - Outputs merged PRs from the last 2-week sprint for every repo within the given organization. Shows PR details including commit IDs, tags, and whether commits are in the release branch (main/master).

2. **`create-release-candidate.py`** - Creates release candidate PRs from dev to release branches for repositories with unreleased changes. Automatically determines the correct RC version number.

3. **`create-release-notes.py`** - Generates markdown release notes for a sprint version by analyzing merged RC PRs and merge commits on release branches.

Only repositories tagged with the `deployer-php` topic are considered deployable. If you do not already have a `.env` file configured for this repo, one will be created and configured for you. 

## Authentication Instructions
	
 1.  Github Token Verification
   - You need a GitHub Personal Access Token to authenticate Python with GitHub. If you don't have a token, follow the
steps below to generate one:
     - Visit [GitHub Personal Access Tokens](https://github.com/settings/tokens) page.
     - Click on "Generate token" and provide the following permissions:
       - **For `main.py` (read-only)**: `read:org` and `repo` (read access)
       - **For `create-release-candidate.py` (creates PRs)**: `read:org` and full `repo` permissions
     - Copy the generated token.
   - On first run, the script will prompt you to enter your token and will automatically create an `.env` file; you may also do this manually via `cp .env.example .env` and
   paste your token in yourself.

 2.   Running the Scripts
  * Clone the repo 'AutomatedRelease' to your machine
  * Install dependencies (listed below)
    * `pip install httpx`
    * `pip install python-dotenv`
  * If a `.env` file does not already exist, you will be prompted to enter your GitHub token and a `.env` file will be created automatically

### main.py - View Sprint PRs

This script retrieves merged PRs from the last 2-week sprint and outputs details including commit IDs, tags, and whether commits are in the release branch.

**Usage:** `python3 main.py <org name> [team name] [format] [branch filter] [week=YYYY.WW]`

**Arguments:**
  - `<org name>` (required) - GitHub organization name
  - `[team name]` (optional) - Filter by specific team
  - `[format]` (optional) - Output format: `text` (default) or `json`
  - `[branch filter]` (optional) - Branch filter: `all` (default, dev/main/master), `dev` (dev only), or `release` (main/master only)
  - `[week=YYYY.WW]` (optional) - Specific sprint week (must be even number)

**Examples:**
  * `python3 main.py my-org` – text output, all branches, current sprint
  * `python3 main.py my-org json` – JSON output, all branches, current sprint
  * `python3 main.py my-org my-team json dev` – JSON output for specific team, dev branch only
  * `python3 main.py my-org week=2026.08` – text output for sprint ending in week 8 of 2026
  * `python3 main.py my-org my-team json week=2025.52` – JSON output for specific team and sprint

**Note:** Depending on how Python is installed, you may need to use `python` instead of `python3`

### create-release-candidate.py - Create Release PRs

This script takes the JSON output from `main.py` and creates release candidate PRs from dev to release branches (main/master) for repositories with unreleased changes. It automatically determines the correct RC version number by checking existing PRs.

**Usage:** `python3 create-release-candidate.py <json_file> [--dry-run]`

**Arguments:**
  - `<json_file>` (required) - JSON output file from main.py
  - `[--dry-run]` (optional) - Preview what would happen without creating PRs

**Examples:**
  * Generate JSON from main.py:
    ```bash
    python3 main.py my-org json > sprint-prs.json
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
  - For each repository with unreleased PRs (where `in_release_branch: false`):
    - Checks for existing release candidate PRs (e.g., `v2026.10-rc0`, `v2026.10-rc1`)
    - Creates a new PR with the next RC number (e.g., `v2026.10-rc2`)
    - Creates PR from `dev` branch to release branch (`main` or `master`)
  - Skips repositories where all PRs are already in the release branch

**Requirements:**
  - Your GitHub token must have full `repo` permissions to create PRs
  - Repositories must have a `dev` branch

### create-release-notes.py - Generate Release Notes

This script generates markdown release notes for a sprint version (`vYYYY.WW`) across deployable repositories (`deployer-php` topic). It finds the latest merged RC PR for the target version, compares commits since the previous RC, and summarizes merged PRs.

**Usage:** `python3 create-release-notes.py <org_name> [team_name] [week=YYYY.WW]`

**Arguments:**
  - `<org_name>` (required) - GitHub organization name
  - `[team_name]` (optional) - Filter repositories by team
  - `[week=YYYY.WW]` (optional) - Sprint end week (must be even). If omitted, current even week is used, or the most recent even week if the current week is odd.

**Examples:**
  * Current/most recent sprint notes for an organization:
    ```bash
    python3 create-release-notes.py my-org
    ```
  * Notes for a specific team:
    ```bash
    python3 create-release-notes.py my-org my-team
    ```
  * Notes for a specific sprint week:
    ```bash
    python3 create-release-notes.py my-org week=2026.08
    ```
  * Team + specific sprint week:
    ```bash
    python3 create-release-notes.py my-org my-team week=2026.08
    ```

**Output format:**
  - Markdown grouped by repository
  - Includes release branch, current RC PR, and previous RC PR (if any)
  - Commit entries include:
    - short commit SHA (plain text)
    - PR link (`#<number>` links to GitHub PR)
    - PR title
    - first non-empty paragraph from the PR body
  - Any `PBT-XXXX` references (4 digits) in rendered text are converted to GitLab issue links:
    - `https://pblgssgitlab01.aws.pointblue.org/point-blue-engineering-team/point-blue-tech/-/issues/XXXX`

**How it works:**
  - Reads organization/team repositories and keeps deployable repos only (`deployer-php` topic)
  - Detects the release branch (`main` or `master`)
  - Looks for merged RC PRs matching the sprint version (for example `v2026.08-rc1`)
  - Compares commits from the previous RC merge commit to the current RC merge commit range
  - Excludes RC merge commits and formats merged PR data as release notes


## Dependencies
1.   [httpx](https://pypi.org/project/httpx/)
2.   [python-dotenv](https://pypi.org/project/python-dotenv/)

In order to run this program, you will need to have installed the proper dependencies.
You can do this by opening an IDE and running the command `pip install {example_module}` for each dependency you are missing.
 
 
