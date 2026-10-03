"""Smoke test for WTA-9: verify that Playwright can open a real page inside the worker image.

Usage (inside the worker container):
    docker compose --env-file .env -f app/docker-compose.yml exec worker python -m worker.check_browser
"""
import sys

from playwright.sync_api import sync_playwright

URL = "https://example.com"


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(URL, timeout=15_000)
        title = page.title()
        browser.close()

    print(f"{URL} -> title: {title!r}")
    if not title:
        print("Page opened but the title is empty.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
