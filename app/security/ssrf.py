"""Anti-SSRF validation for the URLs the worker will visit (WTA-5).

Used when creating the job (POST /jobs), and must be repeated in the worker right before
navigating: DNS can change between the two moments (DNS rebinding).
"""
import ipaddress
import socket
from urllib.parse import urlsplit

ALLOWED_SCHEMES = {"http", "https"}


class UnsafeURLError(ValueError):
    """The URL must not be visited. The message is safe to show to the client."""


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # ::ffff:127.0.0.1 is loopback disguised as IPv6: evaluate it as IPv4
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    # is_global excludes private, loopback, link-local (169.254.169.254, cloud metadata),
    # reserved, 0.0.0.0 and 100.64.0.0/10; multicast is rejected separately
    return ip.is_global and not ip.is_multicast


def validate_public_url(url: str) -> str:
    """Return the URL if it points to a public http/https host; otherwise raise UnsafeURLError."""
    try:
        parts = urlsplit(url.strip())
        hostname = parts.hostname
        port = parts.port  # raises ValueError if the port is invalid
    except ValueError:
        raise UnsafeURLError("The URL is not valid.")

    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURLError("Only http and https URLs are allowed.")
    if not hostname:
        raise UnsafeURLError("The URL is not valid.")

    try:
        infos = socket.getaddrinfo(hostname, port or 443, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError):
        raise UnsafeURLError("The URL's domain could not be resolved.")

    # Check ALL IPs: a single internal one is enough to reject (a domain can
    # resolve to a public and a private address at the same time)
    for *_, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0].split("%", 1)[0])  # strip the IPv6 scope id
        if not _is_public_ip(ip):
            raise UnsafeURLError("The URL points to a disallowed address.")

    return url.strip()
