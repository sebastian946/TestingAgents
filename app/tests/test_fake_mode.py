"""The configuration switches the integration test relies on (WTA-24): the fake Designer,
the test-only SSRF allowlist and the configurable RQ queue. Fast unit tests, no services."""
import pytest

from agents import designer
from agents.designer import design_pages, design_scenarios
from agents.fake_designer import FAKE_MODEL, fake_scenarios
from security import ssrf
from security.ssrf import UnsafeURLError, validate_public_url


@pytest.fixture
def fake_mode(monkeypatch):
    monkeypatch.setattr(designer.settings, "designer_mode", "fake")


def test_fake_mode_designs_without_any_client(fake_mode, login_page):
    result = design_scenarios(login_page, client=None)  # no client, no key, no network
    assert result.model == FAKE_MODEL and result.input_tokens == result.output_tokens == 0
    titles = [s.title for s in result.scenarios]
    assert "A wrong password is rejected" in titles
    assert all(s.priority in {"critical", "high", "medium", "low"} for s in result.scenarios)


def test_fake_mode_is_deterministic_and_follows_the_inventory(contact_page, login_page):
    first = fake_scenarios(contact_page.url, contact_page.page_type, contact_page.elements)
    again = fake_scenarios(contact_page.url, contact_page.page_type, contact_page.elements)
    assert first == again
    titles = [s.title for s in first]
    assert "Your name is required" in titles and "Email is required" in titles  # required fields
    assert not any("Message is required" in t for t in titles)  # optional field: no scenario
    assert not any("password" in t.lower() for t in titles)  # no password field on this page


def test_fake_mode_speaks_both_languages(login_page):
    spanish = fake_scenarios(login_page.url, login_page.page_type, login_page.elements, "es")
    assert "Una contraseña incorrecta se rechaza" in [s.title for s in spanish]


def test_a_page_without_forms_still_gets_one_scenario():
    [only] = fake_scenarios("https://a.example/about", "content", {"headings": ["About us"]})
    assert only.title == "The page loads with its main content" and "About us" in only.expected_result


def test_design_pages_in_fake_mode_never_builds_a_real_client(fake_mode, login_page, monkeypatch):
    monkeypatch.setattr(designer, "_default_client", lambda: pytest.fail("a real client was created"))
    outcomes = design_pages([login_page])
    assert outcomes[0].result is not None


def test_the_default_mode_is_the_real_designer():
    from config.variables import Settings

    fresh = Settings(_env_file=None, db_user="u", db_password="p", db_name="d", endpoint="h")  # type: ignore[call-arg]
    assert fresh.designer_mode == "anthropic"
    assert fresh.ssrf_allowed_hosts == []
    assert fresh.rq_queue_name == "default"


# ----------------------------------------------------------- SSRF test-only allowlist

def test_an_allowed_host_skips_the_internal_address_check(monkeypatch):
    monkeypatch.setattr(ssrf.socket, "getaddrinfo", lambda *a, **k: pytest.fail("no DNS for an allowed host"))
    monkeypatch.setattr(designer.settings, "ssrf_allowed_hosts", ["127.0.0.1"])
    assert validate_public_url("http://127.0.0.1:8123/login") == "http://127.0.0.1:8123/login"


def test_the_allowlist_is_exact_and_does_not_open_other_internal_hosts(monkeypatch):
    monkeypatch.setattr(designer.settings, "ssrf_allowed_hosts", ["127.0.0.1"])
    for url in ("http://localhost/", "http://10.0.0.1/", "http://169.254.169.254/"):
        with pytest.raises(UnsafeURLError):
            validate_public_url(url)


def test_the_allowlist_does_not_allow_other_schemes(monkeypatch):
    monkeypatch.setattr(designer.settings, "ssrf_allowed_hosts", ["127.0.0.1"])
    with pytest.raises(UnsafeURLError, match="Only http and https"):
        validate_public_url("file://127.0.0.1/etc/passwd")


def test_without_an_allowlist_localhost_stays_blocked():
    with pytest.raises(UnsafeURLError, match="disallowed address"):
        validate_public_url("http://127.0.0.1:8123/")
