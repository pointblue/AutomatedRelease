# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

Install dependencies:
```bash
pip install httpx python-dotenv markdown
```

Configure environment:
```bash
cp .env.example .env
# Edit .env with your GitHub token and org/team settings
```

On first run without a `.env`, scripts will prompt for your GitHub token and create one automatically.

## Running the Scripts

```bash
# View merged PRs for current sprint (JSON output)
python3 main.py org=my-org

# View merged PRs for a specific sprint in text format
python3 main.py org=my-org format=console week=2026.08

# View a specific repo's PRs for a given week range
python3 main.py org=my-org name=my-service format=console week=2026.07-11

# Create release candidate PRs (preview first)
python3 main.py org=my-org format=json > sprint-prs.json
python3 create-release-candidate.py sprint-prs.json --dry-run
python3 create-release-candidate.py sprint-prs.json

# Generate release notes
python3 create-release-notes.py org=my-org week=2026.08

# Publish release notes to Confluence
python3 create-confluence-page.py output/v2026.08-release-notes.md
```

## Architecture

All three scripts share `github_utils.py`, which provides:
- `get_gh_token()` — reads `GITHUB_TOKEN` from env or prompts user
- `fetch_json_pages()` — async generator that handles GitHub API pagination
- `get_main_or_master_branch()` — detects release branch name (`main` or `master`)
- `check_commit_in_branch()` — uses GitHub compare API to test if a commit is already in a branch
- `get_deployable_topic()` — reads `DEPLOYABLE_TOPIC` from env (default: `deployer-php`)

### Script responsibilities

**`main.py`** — fetches merged PRs within a sprint window across all deployable repos in an org/team. Uses async concurrency (semaphore of 8). Outputs JSON (piped to `create-release-candidate.py`) or human-readable text. Sprint dates are computed from even ISO week numbers: each sprint starts Monday of an odd week and ends Sunday of the following even week.

**`create-release-candidate.py`** — reads JSON output from `main.py`. For each repo with unreleased commits (checked via `check_commit_in_branch`), creates a `dev → main/master` PR titled `vYYYY.WW-rcN` where N is auto-incremented. Skips repos where all PRs are already released or where an open RC PR already exists.

**`create-confluence-page.py`** — reads a release notes markdown file (output of `create-release-notes.py`) and publishes it as a new Confluence page under the appropriate year page in the `IM` space. Aborts if the page already exists. Creates the year page automatically if it doesn't exist yet.

**`create-release-notes.py`** — queries GitHub for merged RC PRs matching the target version. For each repo, finds the current and previous RC merge commits, then retrieves commits in that range. Filters to only PR-merge commits, fetches PR titles/bodies, and outputs markdown. Converts `PBT-XXXX` references to GitLab issue links.

### Maintenance conventions

- When changing CLI arguments, valid values, or behavior in any script, update `README.md` to match — including usage strings, argument descriptions, and examples.

### Key conventions

- Repos must have a `DEPLOYABLE_TOPIC` tag (default `deployer-php`) to be considered deployable.
- Version format: `vYYYY.WW` where WW is always an even ISO week number.
- RC PR titles follow the exact pattern `vYYYY.WW-rcN` — scripts match this with regex.
- All GitHub API calls use `httpx.AsyncClient` with Bearer token auth and `X-GitHub-Api-Version: 2022-11-28`.
- Concurrency is limited by `asyncio.Semaphore` (8 for reads, 5 for writes).

### `.env` keys

| Key | Default | Used by |
|-----|---------|---------|
| `GITHUB_TOKEN` | (required) | all |
| `ORG_NAME` | (required if not passed as arg) | all |
| `TEAM_NAME` | (optional) | all |
| `DEPLOYABLE_TOPIC` | `deployer-php` | all |
| `DATE_RANGE_TZ_OFFSET` | `0` | `main.py` |
| `WEEK_OFFSET` | (optional) | `main.py` |
| `CONFLUENCE_EMAIL` | (required) | `create-confluence-page.py` |
| `CONFLUENCE_API_TOKEN` | (required) | `create-confluence-page.py` |
