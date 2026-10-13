"""Anti-SSRF validation (WTA-5), parametrized (WTA-23).

`validate_public_url` is what stops a job (or a link found while crawling) from making the
worker, which runs inside our network, visit an internal address. DNS is replaced by a
table so the tests never touch the network and can model tricks such as a domain that
resolves to a public and a private address at the same time.
"""
import ipaddress
import socket

import pytest

from security import ssrf
from security.ssrf import UnsafeURLError, validate_public_url

# hostname -> the addresses the fake DNS answers with
FAKE_DNS = {
    "example.com": ["93.184.215.14"],
    "www.saucedemo.com": ["104.21.32.1", "2606:4700:3030::6815:2001"],
    "shop.example": ["8.8.4.4"],
    "internal.corp": ["10.0.0.5"],
    "router.home": ["192.168.1.1"],
    "metadata.cloud": ["169.254.169.254"],
    "rebind.example": ["8.8.8.8", "127.0.0.1"],  # one public + one loopback address
    "ipv6-loopback.example": ["::1"],
}


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    def getaddrinfo(host, port, *args, **kwargs):
        try:
            addresses = [str(ipaddress.ip_address(host.strip("[]")))]  # IP literals resolve to themselves
        except ValueError:
            if host not in FAKE_DNS:
                raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
            addresses = FAKE_DNS[host]
        return [
            (socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port))
            for a in addresses
        ]

    monkeypatch.setattr(ssrf.socket, "getaddrinfo", getaddrinfo)


@pytest.mark.parametrize("url", [
    "https://example.com",
    "http://example.com/",
    "https://www.saucedemo.com/inventory.html?sort=az#top",
    "HTTPS://EXAMPLE.COM/Path",  # scheme and host are case-insensitive
    "https://example.com:8443/app",  # a custom port is fine on a public host
    "  https://example.com/  ",  # surrounding spaces are trimmed
    "https://8.8.8.8/",  # a public IP literal
])
def test_public_urls_are_accepted(url):
    assert validate_public_url(url) == url.strip()


@pytest.mark.parametrize("url, reason", [
    # only http and https
    ("ftp://example.com/file", "Only http and https"),
    ("file:///etc/passwd", "Only http and https"),
    ("javascript:alert(1)", "Only http and https"),
    ("gopher://example.com:70/", "Only http and https"),
    # loopback, in every spelling
    ("http://localhost/", "could not be resolved"),  # not in the fake DNS, like a missing host
    ("http://127.0.0.1/", "disallowed address"),
    ("http://127.1.2.3:8000/admin", "disallowed address"),
    ("http://[::1]/", "disallowed address"),
    ("http://[::ffff:127.0.0.1]/", "disallowed address"),  # loopback disguised as IPv6
    ("http://0.0.0.0/", "disallowed address"),
    # private networks
    ("http://10.0.0.1/", "disallowed address"),
    ("http://172.16.5.4/", "disallowed address"),
    ("http://192.168.1.1/", "disallowed address"),
    ("http://100.64.0.1/", "disallowed address"),  # carrier-grade NAT
    ("http://[fd00::1]/", "disallowed address"),  # IPv6 unique local
    # link-local, including the cloud metadata endpoint
    ("http://169.254.169.254/latest/meta-data/", "disallowed address"),
    ("http://[fe80::1]/", "disallowed address"),
    # multicast
    ("http://224.0.0.1/", "disallowed address"),
    # names that resolve to internal addresses
    ("https://internal.corp/", "disallowed address"),
    ("https://router.home/", "disallowed address"),
    ("https://metadata.cloud/", "disallowed address"),
    ("https://ipv6-loopback.example/", "disallowed address"),
    ("https://rebind.example/", "disallowed address"),  # one bad address is enough
    # malformed
    ("https://", "not valid"),
    ("https://example.com:99999/", "not valid"),  # port out of range
    ("not a url", "Only http and https"),
    ("https://does-not-exist.invalid/", "could not be resolved"),
])
def test_unsafe_urls_are_rejected_with_a_clear_reason(url, reason):
    with pytest.raises(UnsafeURLError, match=reason):
        validate_public_url(url)


def test_the_error_message_does_not_leak_the_resolved_address():
    """The message reaches API clients: it must not reveal internal IPs."""
    with pytest.raises(UnsafeURLError) as info:
        validate_public_url("https://internal.corp/")
    assert "10.0.0.5" not in str(info.value)


def test_unsafe_url_error_is_a_value_error():
    """Callers that only know ValueError still stop on it."""
    assert issubclass(UnsafeURLError, ValueError)
