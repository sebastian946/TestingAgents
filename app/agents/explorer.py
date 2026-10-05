"""Explorer: same-domain BFS crawler with a page limit (WTA-10) and element extraction (WTA-11).

Deterministic by design (ADR-01): no LLM calls here. The crawler visits the user's site
breadth-first so the pages closest to the home page (the most important ones) are covered
before going deep, and stops after `max_pages`.

Guardrails:
- Same domain only (``www.`` is ignored when comparing hosts).
- Every URL goes through the anti-SSRF check before being fetched (a site can link to
  http://192.168.1.1/ and the worker runs inside our infrastructure).
- robots.txt is honoured and we identify ourselves with a stable User-Agent.
- Per-page timeout: a slow page is skipped, it never blocks the whole crawl.

Two fetchers share the same BFS loop:
- ``RequestsFetcher`` (default): plain HTTP, fast, no JavaScript. Fine for static sites
  and for tests.
- ``PlaywrightFetcher`` (``use_browser=True``): one headless Chromium for the whole crawl.
  Renders JavaScript (SPAs) and extracts the ``PageInfo`` inventory of forms, buttons and
  navigation that the Designer needs. Images, fonts and media are blocked to save time.

URL normalization decision: the fragment (``#section``) and the query string
(``?page=2``) are dropped. Two URLs that only differ in those almost always render the
same template, and with an 8-page budget we would rather spend it on distinct pages.
"""
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from protego import Protego

from agents.page_info import EXTRACT_JS, PageInfo
from security.ssrf import UnsafeURLError, validate_public_url

USER_AGENT = "WebTestAgent/0.1"
DEFAULT_MAX_PAGES = 8
DEFAULT_TIMEOUT = 15  # seconds, per page
SKIPPED_SCHEMES = {"mailto", "tel", "javascript", "data"}
BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}  # not needed to read the DOM


@dataclass
class CrawledPage:
    url: str
    title: str | None
    status_code: int
    depth: int  # 0 = start URL, 1 = linked from it, ...
    elements: dict[str, Any] | None = None  # PageInfo.to_dict(); only with use_browser=True
    links: list[str] = field(default_factory=list, repr=False)  # absolute hrefs found on the page


# Called after each page is fetched; the worker uses it to persist the page immediately
PageCallback = Callable[[CrawledPage], None]


@dataclass
class FetchResult:
    final_url: str
    status_code: int
    content_type: str
    title: str | None
    links: list[str]
    elements: dict[str, Any] | None = None


class Fetcher(Protocol):
    def fetch(self, url: str, timeout: int) -> FetchResult | None:
        """Return the page data, or None if it could not be fetched (timeout, network error)."""


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


def _clean_title(raw: str | None) -> str | None:
    return " ".join(raw.split()) if raw and raw.strip() else None  # collapse newlines/indentation


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


# ----------------------------------------------------------------------------- fetchers

class RequestsFetcher:
    """Plain HTTP fetcher: title and links parsed from the static HTML."""

    def __init__(self, session: requests.Session):
        self.session = session

    def fetch(self, url: str, timeout: int) -> FetchResult | None:
        try:
            response = self.session.get(url, timeout=timeout, allow_redirects=True)
        except requests.RequestException as exc:  # Timeout, ConnectionError, TooManyRedirects...
            print(f"[explorer] skipped {url}: {exc.__class__.__name__}")
            return None
        content_type = response.headers.get("Content-Type", "")
        if response.status_code >= 400 or "html" not in content_type:
            return FetchResult(response.url, response.status_code, content_type, None, [])

        soup = BeautifulSoup(response.content, "lxml")
        title = _clean_title(soup.title.string if soup.title and soup.title.string else None)
        links = [
            urljoin(response.url, href)
            for a in soup.find_all("a", href=True)
            if isinstance(href := a.get("href"), str)
        ]
        return FetchResult(response.url, response.status_code, content_type, title, links)


class PlaywrightFetcher:
    """Headless Chromium fetcher: renders JavaScript and extracts the PageInfo inventory.

    Use as a context manager so the browser is launched once per crawl and always closed.
    """

    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self._context = None

    def __enter__(self) -> "PlaywrightFetcher":
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._context = self._browser.new_context(user_agent=USER_AGENT, java_script_enabled=True)
        self._context.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in BLOCKED_RESOURCE_TYPES
            else route.continue_(),
        )
        return self

    def __exit__(self, *_exc_info) -> None:
        if self._context:
            self._context.close()
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()

    def fetch(self, url: str, timeout: int) -> FetchResult | None:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        assert self._context is not None, "use PlaywrightFetcher inside a `with` block"
        page = self._context.new_page()
        try:
            # domcontentloaded is enough to read the DOM; networkidle can hang on sites with
            # long polling, so it is only awaited briefly as a best effort for SPAs
            response = page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=3000)
            except PlaywrightTimeoutError:
                pass
            if response is None:
                return None
            content_type = response.headers.get("content-type", "")
            if response.status >= 400 or "html" not in content_type:
                return FetchResult(page.url, response.status, content_type, None, [])

            title = _clean_title(page.title())
            links = page.eval_on_selector_all("a[href]", "els => els.map(a => a.href)")
            raw = page.evaluate(EXTRACT_JS)
            info = PageInfo.from_evaluate(page.url, title, raw)
            return FetchResult(page.url, response.status, content_type, title, links, info.to_dict())
        except PlaywrightTimeoutError:
            print(f"[explorer] skipped {url}: timeout after {timeout}s")
            return None
        except PlaywrightError as exc:
            print(f"[explorer] skipped {url}: {exc.__class__.__name__}: {str(exc).splitlines()[0]}")
            return None
        finally:
            page.close()


# ---------------------------------------------------------------------------------- crawl

def crawl(
    start_url: str,
    max_pages: int = DEFAULT_MAX_PAGES,
    timeout: int = DEFAULT_TIMEOUT,
    on_page: PageCallback | None = None,
    use_browser: bool = False,
) -> list[CrawledPage]:
    """Breadth-first crawl of `start_url` limited to its domain and to `max_pages` pages.

    `on_page` is invoked right after each successful fetch so the caller can persist it
    (one commit per page, so a partial crawl stays consistent if the worker dies).
    With `use_browser=True` pages are rendered in Chromium and `CrawledPage.elements`
    holds the PageInfo inventory (forms, buttons, navigation).
    """
    start = normalize_url(start_url)
    if start is None:
        raise ValueError(f"Not a crawlable http(s) URL: {start_url}")

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    robots = load_robots(start, session, timeout)

    if use_browser:
        with PlaywrightFetcher() as fetcher:
            return _bfs(start, fetcher, robots, max_pages, timeout, on_page)
    return _bfs(start, RequestsFetcher(session), robots, max_pages, timeout, on_page)


def _bfs(
    start: str,
    fetcher: Fetcher,
    robots: Protego,
    max_pages: int,
    timeout: int,
    on_page: PageCallback | None,
) -> list[CrawledPage]:
    site = _site_key(start)
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

        result = fetcher.fetch(url, timeout)
        if result is None:
            continue
        if result.status_code >= 400 or "html" not in result.content_type:
            print(f"[explorer] skipped {url}: status {result.status_code}, type {result.content_type!r}")
            continue

        # Redirects may land on another domain (http -> https is fine, example.com -> other.com is not)
        final_url = normalize_url(result.final_url) or url
        if _site_key(final_url) != site:
            print(f"[explorer] redirected off-site, skipped: {url} -> {final_url}")
            continue
        if final_url != url:
            seen.add(final_url)

        page = CrawledPage(
            url=final_url,
            title=result.title,
            status_code=result.status_code,
            depth=depth,
            elements=result.elements,
            links=result.links,
        )
        crawled.append(page)
        if on_page is not None:
            on_page(page)

        for href in result.links:
            link = normalize_url(href)
            if link is None or link in seen or _site_key(link) != site:
                continue
            seen.add(link)
            queue.append((link, depth + 1))

    return crawled
