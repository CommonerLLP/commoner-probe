"""Tests for the robots.txt fetch in http_client.

Focus: the bounded-timeout fix for _get_robot_parser. urllib.robotparser's
RobotFileParser.read() calls urlopen() with no timeout and hangs against a
non-responding host; we fetch robots.txt ourselves with ROBOTS_TIMEOUT_SEC.

All tests monkeypatch urlopen — no network, no real robots.txt fetch.
"""

from __future__ import annotations

import socket
import urllib.error

import commoner_probe.http_client as hc


class _FakeResp:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._body


def _clear_cache():
    hc._robot_parsers.clear()


def test_robots_fetch_passes_bounded_timeout_and_parses_rules(monkeypatch):
    _clear_cache()
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["timeout"] = timeout
        captured["url"] = req.full_url
        return _FakeResp(b"User-agent: *\nDisallow: /private\n")

    monkeypatch.setattr(hc.urllib.request, "urlopen", fake_urlopen)
    rp = hc._get_robot_parser("https://robots-demo.example/page")

    # The fix: a bounded timeout is actually passed (was unbounded before).
    assert captured["timeout"] == hc.ROBOTS_TIMEOUT_SEC
    assert captured["url"].endswith("/robots.txt")
    # And the rules are honoured.
    assert rp.can_fetch(hc.USER_AGENT, "https://robots-demo.example/public") is True
    assert rp.can_fetch(hc.USER_AGENT, "https://robots-demo.example/private") is False


def test_robots_network_error_fails_open(monkeypatch):
    _clear_cache()

    def boom(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(hc.urllib.request, "urlopen", boom)
    rp = hc._get_robot_parser("https://neterr.example/x")
    assert rp.can_fetch(hc.USER_AGENT, "https://neterr.example/anything") is True


def test_robots_timeout_fails_open(monkeypatch):
    _clear_cache()

    def slow(req, timeout=None):
        # Simulate the timeout firing rather than hanging the test.
        raise socket.timeout("timed out")

    monkeypatch.setattr(hc.urllib.request, "urlopen", slow)
    rp = hc._get_robot_parser("https://slowhost.example/x")
    assert rp.can_fetch(hc.USER_AGENT, "https://slowhost.example/anything") is True


def test_robots_403_disallows_all(monkeypatch):
    _clear_cache()

    def forbidden(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", hdrs=None, fp=None)

    monkeypatch.setattr(hc.urllib.request, "urlopen", forbidden)
    rp = hc._get_robot_parser("https://forbid.example/x")
    assert rp.can_fetch(hc.USER_AGENT, "https://forbid.example/anything") is False


def test_robots_404_fails_open(monkeypatch):
    _clear_cache()

    def notfound(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", hdrs=None, fp=None)

    monkeypatch.setattr(hc.urllib.request, "urlopen", notfound)
    rp = hc._get_robot_parser("https://nofile.example/x")
    assert rp.can_fetch(hc.USER_AGENT, "https://nofile.example/anything") is True


def test_robot_parser_is_cached_per_domain(monkeypatch):
    _clear_cache()
    calls: list[str] = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        return _FakeResp(b"User-agent: *\nDisallow:\n")

    monkeypatch.setattr(hc.urllib.request, "urlopen", fake_urlopen)
    hc._get_robot_parser("https://cache.example/a")
    hc._get_robot_parser("https://cache.example/b")  # same domain, second path
    assert len(calls) == 1  # robots.txt fetched once per domain


def test_robots_fetch_uses_given_user_agent(monkeypatch):
    """The robots.txt fetch identity must match the identity that will
    actually request pages (2026-07-09, added while wiring the
    ministry-DDG adapter — see test_user_agent_override_avoids_waf_false_positive)."""
    _clear_cache()
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["user_agent"] = req.headers.get("User-agent")  # urllib title-cases header keys
        return _FakeResp(b"User-agent: *\nDisallow:\n")

    monkeypatch.setattr(hc.urllib.request, "urlopen", fake_urlopen)
    hc._get_robot_parser("https://ua-demo.example/page", user_agent="my-custom-agent/1.0")
    assert captured["user_agent"] == "my-custom-agent/1.0"


def test_user_agent_override_avoids_waf_false_positive(monkeypatch):
    """Reproduces the mha.gov.in bug: a WAF returns 403 (not 404) for
    commoner-probe's default URL-bearing User-Agent, which the fail-open
    design's 401/403 branch turns into a real 'disallow all' — even though
    the site has no robots.txt. A scheme-free override User-Agent clears the
    WAF and gets the true 404 fail-open response. Verified live against
    mha.gov.in 2026-07-09; this test pins the behaviour without network."""
    _clear_cache()

    def waf_sensitive_urlopen(req, timeout=None):
        ua = req.headers.get("User-agent")
        if ua == hc.USER_AGENT:  # default UA — WAF blocks it
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", hdrs=None, fp=None)
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", hdrs=None, fp=None)  # no robots.txt

    monkeypatch.setattr(hc.urllib.request, "urlopen", waf_sensitive_urlopen)

    rp_default = hc._get_robot_parser("https://waf.example/page")
    assert rp_default.can_fetch(hc.USER_AGENT, "https://waf.example/page") is False

    _clear_cache()
    rp_override = hc._get_robot_parser("https://waf.example/page", user_agent="scheme-free-agent/1.0")
    assert rp_override.can_fetch("scheme-free-agent/1.0", "https://waf.example/page") is True


def test_make_session_applies_user_agent_override():
    session = hc.make_session(user_agent="override-agent/2.0")
    assert session.headers["User-Agent"] == "override-agent/2.0"


def test_make_session_default_user_agent_unchanged():
    session = hc.make_session()
    assert session.headers["User-Agent"] == hc.USER_AGENT
