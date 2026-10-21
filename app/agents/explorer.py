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

page_type (WTA-13): in browser mode every page is classified by ``agents.page_classifier``
(login / signup / checkout / product / listing / form / content) from the same inventory.

Screenshots (WTA-12): with ``screenshot_dir`` set (browser mode only), a full-page PNG is
taken right after the DOM is read, while the page is still open, so no second browser or
second fetch is needed. A failed screenshot is logged and the page is kept without one.
"""
import hashlib
import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from protego import Protego

from agents.page_classifier import classify_page
from agents.page_info import EXTRACT_JS, PageInfo
from security.credentials import LoginSpec
from security.ssrf import UnsafeURLError, validate_public_url

USER_AGENT = "WebTestAgent/0.1"
DEFAULT_MAX_PAGES = 8
DEFAULT_TIMEOUT = 15  # seconds, per page
SKIPPED_SCHEMES = {"mailto", "tel", "javascript", "data"}
BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}  # not needed to read the DOM
# When the crawler is logged in it acts as a real user, so links whose path suggests a
# state change are never followed (WTA-29). A GET to /account/delete is harmless for an
# anonymous visitor and real for a logged-in one on a badly built site.
UNSAFE_LINK_WORDS = frozenset({
    "logout", "signout", "delete", "remove", "destroy", "cancel", "unsubscribe", "deactivate",
    "confirm", "submit", "approve", "reject", "transfer", "withdraw", "send", "archive", "reset",
    "revoke", "disable",
})
# Viewing a cart or a checkout page is a GET like any other, so those links ARE followed
# (WTA-35): the Designer needs to see them to write purchase scenarios. Buying is a POST the
# crawler never makes; whether the Executor may complete it is its own guardrail.

# Link texts / hrefs that lead to the login page, when the customer gave us the home page
LOGIN_LINK_WORDS = ("log in", "login", "sign in", "signin", "iniciar sesión", "iniciar sesion", "acceder",
                    "entrar", "ingresar", "my account", "mi cuenta", "account")
LOGIN_LINK_JS = """(words) => {
  const norm = (s) => (s || '').trim().toLowerCase().replace(/\\s+/g, ' ');
  for (const a of document.querySelectorAll('a[href], button')) {
    const t = norm(a.innerText || a.getAttribute('aria-label'));
    const h = norm(a.getAttribute('href'));
    if (!t && !h) continue;
    if (words.some(w => t === w || t.startsWith(w + ' ') || t.endsWith(' ' + w) ||
                        (h && h.replace(/[^a-z]/g, '').includes(w.replace(/[^a-z]/g, '')) && w.length > 4)))
      return a.href || t;
  }
  return null;
}"""


@dataclass
class CrawledPage:
    url: str
    title: str | None
    status_code: int
    depth: int  # 0 = start URL, 1 = linked from it, ...
    elements: dict[str, Any] | None = None  # PageInfo.to_dict(); only with use_browser=True
    page_type: str | None = None  # heuristic PageType value (WTA-13); only with use_browser=True
    screenshot_path: str | None = None  # full-page PNG; only with use_browser + screenshot_dir
    links: list[str] = field(default_factory=list, repr=False)  # absolute hrefs found on the page


# Called after each page is fetched; the worker uses it to persist the page immediately
PageCallback = Callable[[CrawledPage], None]


@dataclass
class LoginResult:
    """Outcome of `PlaywrightFetcher.login`. `message` is safe to store in job.error."""

    ok: bool
    message: str


def _looks_unsafe(url: str) -> bool:
    """True when a path segment or word of the URL is an action word (see UNSAFE_LINK_WORDS).

    "log-out" and "sign_out" count too: separators are dropped before matching."""
    words = re.split(r"[^a-z]+", urlsplit(url).path.lower())
    joined = {w for w in words if w} | {a + b for a, b in zip(words, words[1:])}
    return bool(joined & UNSAFE_LINK_WORDS)


@dataclass
class FetchResult:
    final_url: str
    status_code: int
    content_type: str
    title: str | None
    links: list[str]
    elements: dict[str, Any] | None = None
    page_type: str | None = None
    screenshot_path: str | None = None


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


def same_site(a: str, b: str) -> bool:
    """True when two URLs belong to the same registrable domain (app.example.com and
    auth.example.com), which is where a login page may legitimately live. Two-label
    approximation: fine for .com/.co, over-generous for example.co.uk (accepted)."""
    def key(url: str) -> str:
        host = (urlsplit(url).hostname or "").lower()
        if host.replace(".", "").isdigit() or host == "localhost":
            return host
        parts = host.split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else host
    return key(a) == key(b)


def _clean_title(raw: str | None) -> str | None:
    return " ".join(raw.split()) if raw and raw.strip() else None  # collapse newlines/indentation


def screenshot_filename(url: str) -> str:
    """Predictable, filesystem-safe name derived from the URL path.

    ``https://shop.com/`` -> ``index-a1b2c3.png``; ``/catalog/Shoes`` -> ``catalog-shoes-d4e5f6.png``.
    The short hash keeps two paths that slug to the same text from overwriting each other.
    """
    path = urlsplit(url).path
    slug = re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-") or "index"
    digest = hashlib.sha1(url.encode()).hexdigest()[:6]
    return f"{slug[:60]}-{digest}.png"


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
    With ``screenshot_dir`` each fetched page is also saved there as a full-page PNG.
    """

    def __init__(self, screenshot_dir: Path | None = None) -> None:
        self.screenshot_dir = screenshot_dir
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
            page_type = classify_page(info).value
            screenshot_path = self._screenshot(page) if self.screenshot_dir else None
            return FetchResult(
                page.url, response.status, content_type, title, links,
                elements=info.to_dict(), page_type=page_type, screenshot_path=screenshot_path,
            )
        except PlaywrightTimeoutError:
            print(f"[explorer] skipped {url}: timeout after {timeout}s")
            return None
        except PlaywrightError as exc:
            print(f"[explorer] skipped {url}: {exc.__class__.__name__}: {str(exc).splitlines()[0]}")
            return None
        finally:
            page.close()

    def clear_session(self) -> None:
        """Forget the cookies (log out everywhere) without closing the browser (WTA-35)."""
        assert self._context is not None, "use PlaywrightFetcher inside a `with` block"
        self._context.clear_cookies()

    def _settle(self, page, timeout_ms: int = 3000) -> None:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        try:
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except PlaywrightTimeoutError:
            pass

    def _find_login_form(self, page, spec: LoginSpec, timeout: int):
        """Locate the visible password field, trying in order (WTA-35):
        1. the page as given (a login page, or a private page that redirected to it);
        2. the site's own login link, when the customer gave us the home page;
        3. a two-step form (identifier first, password on the next screen).
        Returns (password_locator, two_step) or (None, False)."""
        password = page.locator("input[type=password]:visible").first
        if password.count() > 0:
            return password, False

        target = page.evaluate(LOGIN_LINK_JS, list(LOGIN_LINK_WORDS))
        if target:
            if target.startswith("http"):
                if same_site(target, spec.login_url):
                    page.goto(target, timeout=timeout * 1000, wait_until="domcontentloaded")
            else:  # a button that opens the form in place
                page.get_by_text(target, exact=False).first.click(timeout=5000)
            self._settle(page)
            password = page.locator("input[type=password]:visible").first
            if password.count() > 0:
                return password, False

        identifier = page.locator("input:visible").filter(
            has_not=page.locator("[type=hidden], [type=checkbox], [type=radio], [type=submit], [type=button], [type=search]")
        ).first
        if identifier.count() > 0 and (identifier.get_attribute("type") or "text") in ("text", "email", "tel"):
            identifier.fill(spec.username)
            identifier.press("Enter")
            self._settle(page, timeout * 1000)
            password = page.locator("input[type=password]:visible").first
            if password.count() > 0:
                return password, True
        return None, False

    def login(self, spec: LoginSpec, timeout: int) -> LoginResult:
        """Sign in once; the session cookies then apply to every page of the crawl.

        Deterministic, no LLM (ADR-01): the login form is the one with a visible password
        field (found directly, behind the site's login link, or after a first identifier
        step); the username field is the visible text/email input before it; submit is the
        form's submit button, or Enter. Success = no password field is visible afterwards.
        Any failure is reported, never raised:
        the crawl then continues as an anonymous visitor and the job says so.
        """
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        assert self._context is not None, "use PlaywrightFetcher inside a `with` block"
        page = self._context.new_page()
        try:
            page.goto(spec.login_url, timeout=timeout * 1000, wait_until="domcontentloaded")
            self._settle(page)
            password, two_step = self._find_login_form(page, spec, timeout)
            if password is None:
                return LoginResult(False, f"No password field found on {spec.login_url}, behind its login link, or after entering the username.")
            username = password.locator(
                "xpath=preceding::input[not(@type) or @type='text' or @type='email' or @type='tel'][1]"
            )
            if username.count() == 0 and not two_step:
                return LoginResult(False, f"No username field found before the password field on {page.url}.")
            if username.count() > 0:
                username.fill(spec.username)
            password.fill(spec.password)
            submit = password.locator("xpath=ancestor::form[1]//*[(self::button or self::input) and @type='submit']").first
            if submit.count() == 0:
                submit = password.locator("xpath=ancestor::form[1]//button[not(@type) or @type='button']").first
            if submit.count() > 0:
                submit.click(timeout=5000)
            else:
                password.press("Enter")
            try:
                page.wait_for_load_state("networkidle", timeout=timeout * 1000)
            except PlaywrightTimeoutError:
                pass
            # A visible password field after submitting means the form came back, whatever
            # the URL: many sites POST elsewhere and re-render the login page on failure
            if page.locator("input[type=password]:visible").count() > 0:
                return LoginResult(False, "Login failed: the login form is still shown (wrong credentials?).")
            return LoginResult(True, f"Logged in as {spec.username} at {spec.login_url}.")
        except PlaywrightTimeoutError:
            return LoginResult(False, f"Login failed: {spec.login_url} timed out after {timeout}s.")
        except PlaywrightError as exc:
            return LoginResult(False, f"Login failed: {exc.__class__.__name__}: {str(exc).splitlines()[0]}")
        finally:
            page.close()

    def new_page(self):
        """A page in the crawl's context (same cookies, same blocked resources); the Executor
        (WTA-33) drives one for a whole run."""
        assert self._context is not None, "use PlaywrightFetcher inside a `with` block"
        return self._context.new_page()

    def _screenshot(self, page) -> str | None:
        """Save a full-page PNG; a failure here must never fail the page itself."""
        from playwright.sync_api import Error as PlaywrightError

        assert self.screenshot_dir is not None
        target = self.screenshot_dir / screenshot_filename(page.url)
        try:
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(target), full_page=True, timeout=10_000)
            return str(target)
        except (PlaywrightError, OSError) as exc:  # huge page, closed page, disk full...
            print(f"[explorer] screenshot failed for {page.url}: {exc.__class__.__name__}")
            return None


# ---------------------------------------------------------------------------------- crawl

def crawl(
    start_url: str,
    max_pages: int = DEFAULT_MAX_PAGES,
    timeout: int = DEFAULT_TIMEOUT,
    on_page: PageCallback | None = None,
    use_browser: bool = False,
    screenshot_dir: str | Path | None = None,
    login: LoginSpec | None = None,
    on_login: Callable[[LoginResult], None] | None = None,
) -> list[CrawledPage]:
    """Breadth-first crawl of `start_url` limited to its domain and to `max_pages` pages.

    `on_page` is invoked right after each successful fetch so the caller can persist it
    (one commit per page, so a partial crawl stays consistent if the worker dies).
    With `use_browser=True` pages are rendered in Chromium and `CrawledPage.elements`
    holds the PageInfo inventory (forms, buttons, navigation); `screenshot_dir` (browser
    mode only) additionally saves a full-page PNG per page into that folder.
    With `login` (browser mode only, WTA-29) the crawler signs in first and reports the
    outcome through `on_login`; while logged in, links that look like actions (logout,
    delete, checkout...) are not followed. A failed login still crawls, anonymously.
    """
    start = normalize_url(start_url)
    if start is None:
        raise ValueError(f"Not a crawlable http(s) URL: {start_url}")

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    robots = load_robots(start, session, timeout)

    if use_browser:
        shots = Path(screenshot_dir) if screenshot_dir else None
        with PlaywrightFetcher(screenshot_dir=shots) as fetcher:
            logged_in = False
            if login is not None:
                outcome = fetcher.login(login, timeout)
                print(f"[explorer] {outcome.message}")
                if on_login is not None:
                    on_login(outcome)
                logged_in = outcome.ok
            return _bfs(start, fetcher, robots, max_pages, timeout, on_page, skip_unsafe=logged_in)
    return _bfs(start, RequestsFetcher(session), robots, max_pages, timeout, on_page)


def _bfs(
    start: str,
    fetcher: Fetcher,
    robots: Protego,
    max_pages: int,
    timeout: int,
    on_page: PageCallback | None,
    skip_unsafe: bool = False,
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
            page_type=result.page_type,
            screenshot_path=result.screenshot_path,
            links=result.links,
        )
        crawled.append(page)
        if on_page is not None:
            on_page(page)

        for href in result.links:
            link = normalize_url(href)
            if link is None or link in seen or _site_key(link) != site:
                continue
            if skip_unsafe and _looks_unsafe(link):
                print(f"[explorer] logged in, not following action-like link: {link}")
                seen.add(link)
                continue
            seen.add(link)
            queue.append((link, depth + 1))

    return crawled
