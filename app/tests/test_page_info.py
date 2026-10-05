"""Acceptance tests for WTA-11 (element extraction with Playwright) against a local site.

Needs the Chromium that `playwright install chromium` downloads; no DB or internet.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

import pytest

from agents import explorer
from agents.explorer import PlaywrightFetcher, crawl
from agents.page_info import PageInfo

PAGES = {
    "/": """<html><head><title>Acme</title><style>.x{color:red}</style></head><body>
        <nav><a href="/contact">Contact us</a><a href="/login">Sign in</a><a href="/">Home</a></nav>
        <h1>Welcome to Acme</h1>
        <button onclick="x()">Get started</button>
        <button style="display:none">Hidden button</button>
        <div hidden><button>Also hidden</button></div>
        <script>function x(){}</script>
        </body></html>""",
    "/contact": """<html><head><title>Contact</title></head><body>
        <h1>Contact us</h1>
        <form action="/send" method="post">
          <input type="hidden" name="csrf" value="abc">
          <label for="n">Your name</label><input id="n" name="name" type="text" required>
          <label>Email <input name="email" type="email" placeholder="you@example.com" required></label>
          <select name="topic"><option>Sales</option><option>Support</option></select>
          <textarea name="message" aria-label="Message"></textarea>
          <input type="checkbox" name="newsletter"> <input type="text" name="honeypot" style="display:none">
          <button type="submit">Send message</button>
        </form></body></html>""",
    "/login": """<html><head><title>Login</title></head><body>
        <form action="/session" method="post">
          <input name="username" type="text" placeholder="Username">
          <input name="password" type="password" placeholder="Password">
          <input type="submit" value="Log in">
        </form></body></html>""",
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path not in PAGES:
            self.send_response(404)
            self.end_headers()
            return
        data = PAGES[path].encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture(scope="module")
def site():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with patch.object(explorer, "validate_public_url", lambda url: url):  # local test site
        yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture(scope="module")
def fetched(site):
    with PlaywrightFetcher() as fetcher:
        return {path: fetcher.fetch(f"{site}{path}", timeout=10) for path in PAGES}


def test_contact_form_lists_fields_with_types(fetched):
    info = fetched["/contact"].elements
    assert len(info["forms"]) == 1
    form = info["forms"][0]
    assert form["method"] == "post" and form["action"].endswith("/send")
    assert form["submit_text"] == "Send message"
    fields = {f["name"]: f for f in form["fields"]}
    assert set(fields) == {"name", "email", "topic", "message", "newsletter"}  # no csrf (hidden), no honeypot (display:none)
    assert fields["name"]["type"] == "text" and fields["name"]["required"] and fields["name"]["label"] == "Your name"
    assert fields["email"]["type"] == "email" and fields["email"]["placeholder"] == "you@example.com"
    assert fields["topic"]["tag"] == "select" and fields["topic"]["options"] == ["Sales", "Support"]
    assert fields["message"]["tag"] == "textarea" and fields["message"]["label"] == "Message"
    assert fields["newsletter"]["type"] == "checkbox"


def test_login_page_detects_password_input(fetched):
    info = fetched["/login"].elements
    types = [f["type"] for f in info["forms"][0]["fields"]]
    assert "password" in types
    assert info["forms"][0]["submit_text"] == "Log in"


def test_noise_is_ignored_and_nav_buttons_headings_extracted(fetched):
    info = fetched["/"].elements
    assert info["buttons"] == ["Get started"]  # hidden ones dropped
    assert info["headings"] == ["Welcome to Acme"]
    assert [l["text"] for l in info["nav_links"]] == ["Contact us", "Sign in", "Home"]
    assert "forms" not in info  # empty lists are dropped by to_dict
    assert "script" not in json.dumps(info) and "color:red" not in json.dumps(info)


def test_page_info_is_small(fetched):
    for path, result in fetched.items():
        size = len(json.dumps(result.elements))
        assert size < 2048, f"{path} inventory is {size} bytes"


def test_crawl_with_browser_attaches_elements_and_follows_links(site):
    pages = crawl(site, max_pages=5, timeout=10, use_browser=True)
    by_url = {p.url: p for p in pages}
    assert set(by_url) == {f"{site}/", f"{site}/contact", f"{site}/login"}
    assert by_url[f"{site}/"].title == "Acme"
    assert all(p.elements is not None for p in pages)
    assert PageInfo.from_evaluate("u", "t", {"forms": by_url[f"{site}/login"].elements["forms"]}).has_password_field
