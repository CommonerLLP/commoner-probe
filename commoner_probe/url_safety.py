"""Single SSRF-guard surface used by every outbound HTTP caller in the
scraper (`fetch.py` for HTML, `pdf_extractor.py` for PDF downloads).

Lives in its own module so there's exactly one place to read, audit, and
test the network policy. External auditors of this anti-caste public-
interest project should be able to verify the host-allowlist behaviour
without grepping across two callers — that's the threat-model rationale,
not just code-cleanliness.

Policy enforced:
  * scheme must be http or https
  * URL must parse to a hostname
  * every resolved IP must be public — `is_private`, `is_loopback`,
    `is_link_local`, `is_reserved`, `is_multicast`, or `is_unspecified`
    rejects (covers IPv4 RFC1918, IPv6 ULA `fc00::/7`, IPv6 loopback
    `::1`, IANA-reserved ranges, IPv4 `224.0.0.0/4` and IPv6 `ff00::/8`
    multicast, and `0.0.0.0` / `::` unspecified)
  * multi-A-record hostnames are evaluated pessimistically: if any
    returned IP is unsafe, the whole URL is rejected

Residual risks (documented in TECHDEBT.md):
  * DNS rebinding: an attacker who controls a DNS response can serve a
    public IP at resolution time and a private IP a moment later. The
    realistic mitigation here is the request timeout + per-host rate
    limit in `fetch.py`, which keeps the rebinding window short.
  * TOCTOU: socket.getaddrinfo and the eventual TCP connect happen
    separately; a fast-flux DNS could differ between the two. Same
    mitigation as above.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


def is_safe_url(url: str) -> bool:
    """Return True if `url` is safe to fetch under the SSRF policy.

    Rejects non-http(s) schemes, missing hostnames, and any URL whose
    DNS resolution touches private, loopback, link-local, or reserved
    address space (IPv4 and IPv6).
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    if not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
    return True
