import httpx
import markdown as md_lib
import os
import re
import sys
from dotenv import load_dotenv


CONFLUENCE_BASE_URL = "https://pointblue.atlassian.net/wiki"
SPACE_KEY = "IM"
MAIN_RELEASES_PAGE_ID = "1566769153"


def get_confluence_config():
    email = os.getenv("CONFLUENCE_EMAIL")
    token = os.getenv("CONFLUENCE_API_TOKEN")
    if not email:
        print("Missing CONFLUENCE_EMAIL. Set it in .env.")
        sys.exit(1)
    if not token:
        print("Missing CONFLUENCE_API_TOKEN. Set it in .env.")
        sys.exit(1)
    return email, token


def parse_version_from_filepath(filepath):
    filename = os.path.basename(filepath)
    match = re.match(r"(v(\d{4})\.(\d+))-release-notes\.md$", filename)
    if not match:
        print(f"Could not parse version from filename: {filename}")
        print("Expected format: v2026.10-release-notes.md")
        sys.exit(1)
    version = match.group(1)   # e.g. v2026.10
    year = match.group(2)      # e.g. 2026
    return version, year


def find_page_by_title(client, base_url, title):
    url = f"{base_url}/rest/api/content"
    params = {"title": title, "spaceKey": SPACE_KEY, "type": "page"}
    response = client.get(url, params=params)
    response.raise_for_status()
    results = response.json().get("results", [])
    return results[0] if results else None


def create_page(client, base_url, title, parent_id, body_html, draft=False):
    url = f"{base_url}/rest/api/content"
    payload = {
        "type": "page",
        "title": title,
        "status": "draft" if draft else "current",
        "space": {"key": SPACE_KEY},
        "ancestors": [{"id": parent_id}],
        "body": {
            "storage": {
                "value": body_html,
                "representation": "storage",
            }
        },
    }
    response = client.post(url, json=payload)
    response.raise_for_status()
    return response.json()


def markdown_to_storage(content):
    return md_lib.markdown(content, extensions=["tables"])


def main():
    load_dotenv()

    args = sys.argv[1:]
    draft = "--draft" in args
    args = [a for a in args if a != "--draft"]

    if len(args) != 1:
        print("Usage: python3 create-confluence-page.py <release-notes-file> [--draft]")
        print("Example: python3 create-confluence-page.py output/v2026.10-release-notes.md")
        sys.exit(1)

    filepath = args[0]
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        sys.exit(1)

    version, year = parse_version_from_filepath(filepath)
    page_title = f"{version} Release DRAFT" if draft else f"{version} Release"
    year_page_title = f"v{year} Releases"

    with open(filepath, "r") as f:
        md_content = f.read()

    email, token = get_confluence_config()
    base_url = CONFLUENCE_BASE_URL

    headers = {"Accept": "application/json", "Content-Type": "application/json"}

    with httpx.Client(auth=(email, token), headers=headers, timeout=30) as client:
        # Abort if the sprint page already exists (skip check in draft mode)
        if not draft:
            existing = find_page_by_title(client, base_url, page_title)
            if existing:
                page_url = f"{base_url}{existing['_links']['webui']}"
                print(f"Error: Page '{page_title}' already exists: {page_url}")
                sys.exit(1)

        # Find or create the year page
        year_page = find_page_by_title(client, base_url, year_page_title)
        if not year_page:
            print(f"Year page '{year_page_title}' not found. Creating it...")
            year_page = create_page(
                client,
                base_url,
                year_page_title,
                MAIN_RELEASES_PAGE_ID,
                f"<h1>All {year} Releases</h1>",
            )
            print(f"Created: {year_page_title}")

        year_page_id = year_page["id"]

        # Convert markdown and create the sprint release page
        body_html = markdown_to_storage(md_content)
        new_page = create_page(client, base_url, page_title, year_page_id, body_html, draft=draft)
        page_url = f"{base_url}{new_page['_links']['webui']}"
        print(f"Created {'draft' if draft else 'page'}: {page_title}")
        if draft:
            print("(Unpublished — find it in your Confluence drafts)")
        print(f"URL: {page_url}")


if __name__ == "__main__":
    main()
