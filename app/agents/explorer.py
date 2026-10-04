"""Explorer: same-domain BFS crawler with a page limit (WTA-10).

Deterministic by design (ADR-01): no LLM calls here. The crawler visits the user's site
breadth-first so the pages closest to the home page (the most important ones) are covered
before going deep, and stops after `max_pages`.

Guardrails:
- Same domain only (``www.`` is ignored when comparing hosts).
- Every URL goes through the anti-SSRF check before being fetched (a site can link to
  http://192.168.1.1/ and the worker runs inside our infrastructure).
- robots.txt is honoured and we identify ourselves with a stable User-Agent.
- Per-page timeout: a slow page is skipped, it never blocks the whole crawl.

URL normalization decision: the fragment (``#section``) and the query string
(``?page=2``) are dropped. Two URLs that only differ in those almost always render the
same template, and with an 8-page budget we would rather spend it on distinct pages.
"""
from collections import deque
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from protego import Protego

from security.ssrf import UnsafeURLError, validate_public_url

USER_AGENT = "WebTestAgent/0.1"
DEFAULT_MAX_PAGES = 8
DEFAULT_TIMEOUT = 15  # seconds, per page
SKIPPED_SCHEMES = {"mailto", "tel", "javascript", "data"}


@dataclass
class CrawledPage:
    url: str
    title: str | None
    status_code: int
    depth: int  # 0 = start URL, 1 = linked from it, ...


# Called after each page is fetched; the worker uses it to persist the page immediately
PageCallback = Callable[[CrawledPage], None]


def normalize_url(url: str) -> str | None:
    """Canonical form used for the visited set. Returns None for URLs we never follow."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme in SKIPPED_SCHEMES or scheme not in {"http", "https"} or not parts.netloc:
        return None
    path = parts.path.rstrip("/") or "/"
    # query and fragment are intentionally dropped (see module docstring)
    return urlunsplit((scheme, parts.netloc.lower(), path, "", ""))


def _site_key(url: str) -> str:
    """Host used to decide 'same domain'; www.example.com and example.com are one site."""
    host = urlsplit(url).hostname or ""
    return host[4:] if host.startswith("www.") else host


def load_robots(start_url: str, session: requests.Session, timeout: int) -> Protego:
    """Fetch and parse /robots.txt once per crawl.

    Uses protego (the parser behind Scrapy) instead of urllib.robotparser because the
    stdlib one does not understand wildcards (``Disallow: /*/pulse``), which most real
    sites use. Fetched with our own User-Agent (urllib's default gets 403 on many sites).
    A missing or unreadable robots.txt means everything is allowed, which is the convention.
    """
    parts = urlsplit(start_url)
    robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
    try:
        response = session.get(robots_url, timeout=timeout)
        if response.status_code == 200:
            return Protego.parse(response.text)
    except requests.RequestException:
        pass
    return Protego.parse("")  # no rules: allow all


def crawl(
    start_url: str,
    max_pages: int = DEFAULT_MAX_PAGES,
    timeout: int = DEFAULT_TIMEOUT,
    on_page: PageCallback | None = None,
) -> list[CrawledPage]:
    """Breadth-first crawl of `start_url` limited to its domain and to `max_pages` pages.

    `on_page` is invoked right after each successful fetch so the caller can persist it
    (one commit per page, so a partial crawl stays consistent if the worker dies).
    """
    start = normalize_url(start_url)
    if start is None:
        raise ValueError(f"Not a crawlable http(s) URL: {start_url}")

    site = _site_key(start)
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    robots = load_robots(start, session, timeout)

    queue: deque[tuple[str, int]] = deque([(start, 0)])
    seen: set[str] = {start}  # visited OR already queued: guarantees no repeats
    crawled: list[CrawledPage] = []

    while queue and len(crawled) < max_pages:
        url, depth = queue.popleft()

        if not robots.can_fetch(url, USER_AGENT):
            print(f"[explorer] blocked by robots.txt: {url}")
            continue
        try:
            validate_public_url(url)
        except UnsafeURLError as exc:
            print(f"[explorer] unsafe, skipped: {url} ({exc})")
            continue

        try:
            response = session.get(url, timeout=timeout, allow_redirects=True)
        except requests.RequestException as exc:  # Timeout, ConnectionError, TooManyRedirects...
            print(f"[explorer] skipped {url}: {exc.__class__.__name__}")
            continue

        content_type = response.headers.get("Content-Type", "")
        if response.status_code >= 400 or "html" not in content_type:
            print(f"[explorer] skipped {url}: status {response.status_code}, type {content_type!r}")
            continue

        # Redirects may land on another domain (http -> https is fine, example.com -> other.com is not)
        final_url = normalize_url(response.url) or url
        if _site_key(final_url) != site:
            print(f"[explorer] redirected off-site, skipped: {url} -> {final_url}")
            continue
        if final_url != url:
            seen.add(final_url)

        soup = BeautifulSoup(response.content, "lxml")
        raw_title = soup.title.string if soup.title and soup.title.string else None
        title = " ".join(raw_title.split()) if raw_title else None  # collapse newlines/indentation
        page = CrawledPage(url=final_url, title=title, status_code=response.status_code, depth=depth)
        crawled.append(page)
        if on_page is not None:
            on_page(page)

        for anchor in soup.find_all("a", href=True):
            href = anchor.get("href")
            if not isinstance(href, str):
                continue
            link = normalize_url(urljoin(final_url, href))
            if link is None or link in seen or _site_key(link) != site:
                continue
            seen.add(link)
            queue.append((link, depth + 1))

    return crawled
