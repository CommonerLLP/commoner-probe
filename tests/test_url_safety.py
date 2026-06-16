"""SSRF-guard policy tests for commoner_probe.url_safety.is_safe_url.

These mock socket.getaddrinfo so the suite never touches the network. The
load-bearing case is test_no_hostname_bypass: a host that previously sat on a
name allowlist must still be rejected when it resolves into private address
space. That allowlist early-return was an SSRF bypass (DNS rebinding) and has
been removed; this test is the regression guard against it returning.
"""

from __future__ import annotations

from unittest import mock

from commoner_probe import url_safety


def _addrinfo(*ips: str):
    """Build a getaddrinfo-shaped return value for the given IPs."""
    return [(2, 1, 6, "", (ip, 0)) for ip in ips]


def _patch_resolve(*ips: str):
    return mock.patch.object(
        url_safety.socket, "getaddrinfo", return_value=_addrinfo(*ips)
    )


def test_public_ip_allowed():
    with _patch_resolve("164.100.85.146"):
        assert url_safety.is_safe_url("https://elibrary.sansad.in/server/api") is True


def test_loopback_rejected():
    with _patch_resolve("127.0.0.1"):
        assert url_safety.is_safe_url("http://localhost.example/x") is False


def test_private_rfc1918_rejected():
    with _patch_resolve("10.0.0.5"):
        assert url_safety.is_safe_url("http://internal.example/x") is False


def test_ipv6_loopback_rejected():
    with _patch_resolve("::1"):
        assert url_safety.is_safe_url("http://v6.example/x") is False


def test_any_unsafe_record_rejects_whole_url():
    # Pessimistic: one private A-record among public ones rejects everything.
    with _patch_resolve("164.100.85.146", "192.168.1.1"):
        assert url_safety.is_safe_url("https://multi.example/x") is False


def test_no_hostname_bypass():
    """Regression: the removed elibrary.sansad.in allowlist must not bypass
    the IP check. If the name resolves private, the URL is unsafe."""
    with _patch_resolve("127.0.0.1"):
        assert url_safety.is_safe_url("https://elibrary.sansad.in/server/api") is False


def test_non_http_scheme_rejected():
    assert url_safety.is_safe_url("file:///etc/passwd") is False
    assert url_safety.is_safe_url("gopher://x/") is False


def test_missing_hostname_rejected():
    assert url_safety.is_safe_url("http:///nohostname") is False


def test_unresolvable_host_rejected():
    import socket

    with mock.patch.object(
        url_safety.socket, "getaddrinfo", side_effect=socket.gaierror
    ):
        assert url_safety.is_safe_url("https://nxdomain.invalid/x") is False


def test_hostname_normalized_before_resolution():
    """Trailing dot and case are normalized so they resolve canonically."""
    with mock.patch.object(
        url_safety.socket, "getaddrinfo", return_value=_addrinfo("164.100.85.146")
    ) as g:
        assert url_safety.is_safe_url("https://ELibrary.Sansad.IN./x") is True
        g.assert_called_once_with("elibrary.sansad.in", None)
