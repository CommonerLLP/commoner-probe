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
