"""Acceptance tests for WTA-10 against a local HTTP server (no internet, no DB).

The fake site has: a robots.txt that blocks /private, an external link, duplicate links
(fragment / query / trailing slash), a slow page and a non-HTML file.
"""
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

import pytest

from agents import explorer
from agents.explorer import crawl, normalize_url

ROBOTS = "User-agent: *\nDisallow: /private\nDisallow: /*/hidden\n"

PAGES = {
    "/": """<title>Home</title>
        <a href="/about">About</a> <a href="/about#team">About (fragment)</a>
        <a href="/products?page=2">Products</a> <a href="/products/">Products slash</a>
        <a href="https://twitter.com/x">External</a> <a href="mailto:a@b.c">Mail</a>
        <a href="/private/secret">Private</a> <a href="/slow">Slow</a> <a href="/file.pdf">PDF</a>""",
    "/about": "<title>About</title><a href='/'>home</a><a href='/contact'>c</a>",
    "/products": "<title>Products</title><a href='/p1'>1</a><a href='/p2'>2</a><a href='/p3'>3</a><a href='/p4'>4</a>",
    "/contact": "<title>Contact</title>",
    "/private/secret": "<title>SECRET</title>",
    "/p1": "<title>P1</title>", "/p2": "<title>P2</title>", "/p3": "<title>P3</title>",
    "/p4": "<title>P4</title><a href='/p4/hidden'>wildcard-blocked</a>",
    "/p4/hidden": "<title>HIDDEN</title>",
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep pytest output clean
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/robots.txt":
            body, ctype = ROBOTS, "text/plain"
        elif path == "/slow":
            time.sleep(3)  # longer than the test timeout
            body, ctype = "<title>Slow</title>", "text/html"
        elif path == "/file.pdf":
            body, ctype = "%PDF", "application/pdf"
        elif path in PAGES:
            body, ctype = PAGES[path], "text/html"
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = body.encode()
        try:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except ConnectionError:
            pass  # the crawler gave up on /slow and closed the socket: expected


@pytest.fixture(scope="module")
def site():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # 127.0.0.1 is rightly rejected by the SSRF check; bypass it only for the local test site
    with patch.object(explorer, "validate_public_url", lambda url: url):
        yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_normalize_url_drops_fragment_query_and_trailing_slash():
    assert normalize_url("HTTP://Example.com/a/?x=1#top") == "http://example.com/a"
    assert normalize_url("http://example.com") == "http://example.com/"
    assert normalize_url("mailto:a@b.c") is None
    assert normalize_url("javascript:void(0)") is None


def test_bfs_respects_max_pages_and_never_repeats(site):
    pages = crawl(site, max_pages=4, timeout=2)
    urls = [p.url for p in pages]
    assert len(urls) == 4
    assert len(set(urls)) == 4
    # BFS order: home first, then every reachable depth-1 page before any depth-2 page
    assert urls[0] == f"{site}/" and pages[0].title == "Home"
    depths = [p.depth for p in pages]
    assert depths == sorted(depths)
    assert {f"{site}/about", f"{site}/products"} <= set(urls)   # the only crawlable depth-1 links


def test_stays_on_domain_and_dedupes_variants(site):
    pages = crawl(site, max_pages=20, timeout=2)
    urls = {p.url for p in pages}
    assert not any("twitter.com" in u for u in urls)
    assert sum(1 for u in urls if u.endswith("/about")) == 1      # /about#team deduped
    assert sum(1 for u in urls if u.endswith("/products")) == 1   # ?page=2 and trailing slash deduped
    assert f"{site}/file.pdf" not in urls                           # non-HTML skipped


def test_robots_txt_blocks_private_path_and_wildcards(site):
    pages = crawl(site, max_pages=20, timeout=2)
    urls = {p.url for p in pages}
    assert all("/private" not in u for u in urls)
    assert all("/hidden" not in u for u in urls)      # wildcard rule, unsupported by urllib.robotparser


def test_slow_page_does_not_block_the_crawl(site):
    started = time.monotonic()
    pages = crawl(site, max_pages=20, timeout=1)
    assert f"{site}/slow" not in {p.url for p in pages}
    assert len(pages) >= 7                      # everything else was still crawled
    assert time.monotonic() - started < 10      # we did not hang on /slow


def test_on_page_callback_is_called_per_page(site):
    seen = []
    crawl(site, max_pages=3, timeout=2, on_page=lambda p: seen.append(p.url))
    assert len(seen) == 3
