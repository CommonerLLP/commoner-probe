"""Tests for the robots.txt fetch in http_client.

Focus: the bounded-timeout fix for _get_robot_parser. urllib.robotparser's
RobotFileParser.read() calls urlopen() with no timeout and hangs against a
non-responding host; we fetch robots.txt ourselves with ROBOTS_TIMEOUT_SEC.

All tests monkeypatch urlopen — no network, no real robots.txt fetch.
"""

from __future__ import annotations

import socket
import urllib.error

import pytest

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


def test_robot_parser_is_cached_per_domain_and_user_agent(monkeypatch):
    """Regression for the Codex PR#41 finding: the cache was keyed by domain
    only, so the first UA's robots result stuck for every later session with
    a different UA against the same host — e.g. a WAF 403 for the default UA
    (read as disallow-all) would also block a scheme-free UA that the WAF
    would have let through."""
    _clear_cache()
    calls: list[str] = []

    def waf_sensitive_urlopen(req, timeout=None):
        ua = req.headers.get("User-agent")
        calls.append(ua)
        if ua == hc.USER_AGENT:
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", hdrs=None, fp=None)
        return _FakeResp(b"User-agent: *\nDisallow:\n")

    monkeypatch.setattr(hc.urllib.request, "urlopen", waf_sensitive_urlopen)

    rp_default = hc._get_robot_parser("https://multi-ua.example/page")
    assert rp_default.can_fetch(hc.USER_AGENT, "https://multi-ua.example/page") is False

    # Pre-fix this returned the cached disallow-all parser for the domain.
    rp_override = hc._get_robot_parser("https://multi-ua.example/page", user_agent="scheme-free/1.0")
    assert rp_override.can_fetch("scheme-free/1.0", "https://multi-ua.example/page") is True
    assert len(calls) == 2  # robots.txt fetched per (domain, UA), not once per domain

    # The first UA's cached parser is untouched by the second's.
    rp_default_again = hc._get_robot_parser("https://multi-ua.example/page")
    assert rp_default_again.can_fetch(hc.USER_AGENT, "https://multi-ua.example/page") is False
    assert len(calls) == 2


def test_stdlib_response_exposes_requests_compatible_content():
    """Regression for the Codex PR#41 finding: ddg/doe/dspace/debates read
    ``r.content`` for PDF bytes, but StdlibResponse exposed only .text and
    .iter_content — the zero-dependency fallback crashed with AttributeError
    on every document download."""
    body = b"%PDF-1.7 binary \x00\x01\x02 bytes"
    resp = hc.StdlibResponse("https://x.example/f.pdf", 200, body)
    assert resp.content == body
    assert b"".join(resp.iter_content()) == body
    resp.raise_for_status()  # 200 — must not raise


def test_stdlib_response_raise_for_status_on_error():
    import pytest

    resp = hc.StdlibResponse("https://x.example/missing", 404, b"nope")
    with pytest.raises(RuntimeError):
        resp.raise_for_status()


def test_post_is_guarded_like_get():
    """POST used to fall through __getattr__ to the bare requests.Session.

    That path skips the SSRF guard, the per-domain rate limit and the 5xx
    backoff — every discipline this wrapper exists to apply. api.indiankanoon.org
    is POST-only, so the gap became reachable.
    """
    session = hc.make_session()
    if not hasattr(session, "_session"):
        pytest.skip("stdlib fallback session has no SSRF guard by contract")
    with pytest.raises(ValueError, match="SSRF"):
        session.post("http://169.254.169.254/latest/meta-data/")


def test_stdlib_session_supports_post(monkeypatch):
    """The zero-dependency fallback must not AttributeError on POST."""
    session = hc.StdlibSession()
    assert callable(getattr(session, "post", None))
    captured = {}

    class FakeResp:
        status = 200

        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout=None):
        captured["method"] = req.get_method()
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        return FakeResp()

    monkeypatch.setattr(hc.urllib.request, "urlopen", fake_urlopen)
    r = session.post("https://api.example.org/search/", headers={"Authorization": "Token x"})
    assert captured["method"] == "POST"
    assert captured["auth"] == "Token x"
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Retry policy: 429, Retry-After, and backoff jitter.
#
# The loop backed off on 5xx and network errors only, so a 429 returned to the
# caller like a success and the next request went out on the ordinary schedule.
# 429 is the portal asking for a slower rate; ignoring it is how a polite
# crawler becomes a blocked one.
# ---------------------------------------------------------------------------

# Base the marker on RetrySession itself, NOT on `hc.requests is None`: the
# stdlib-fallback branch rebinds `requests` to a types.SimpleNamespace, so that
# condition is False in exactly the environment where RetrySession was never
# defined (Codex, PR #97).
requires_requests = pytest.mark.skipif(
    not hasattr(hc, "RetrySession"),
    reason="RetrySession only exists when requests is installed",
)


class _StubHttpResp:
    def __init__(self, status_code: int, headers: dict) -> None:
        self.status_code = status_code
        self.headers = headers


class _StubTransport:
    """Returns a scripted sequence of responses and counts the calls."""

    def __init__(self, statuses, headers=None) -> None:
        self._statuses = list(statuses)
        self._headers = list(headers or [{}] * len(statuses))
        self.calls = 0
        self.headers: dict = {}

    def request(self, method, url, **kwargs):
        i = self.calls
        self.calls += 1
        return _StubHttpResp(self._statuses[i], self._headers[i])


def _scripted_session(monkeypatch, statuses, headers=None):
    """A RetrySession with a scripted transport and recorded sleeps.

    The SSRF guard resolves hostnames for real, so it is stubbed here — these
    tests are about the retry loop, and the guard has its own tests above.
    """
    slept: list[float] = []
    monkeypatch.setattr(hc.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(hc, "is_safe_url", lambda url: True)
    session = hc.RetrySession(rate_limit_sec=0)
    session._session = _StubTransport(statuses, headers)
    return session, slept


@requires_requests
def test_429_is_retried_not_returned_as_success(monkeypatch):
    session, slept = _scripted_session(monkeypatch, [429, 200])
    resp = session.get("https://api.example.org/x", respect_robots=False)
    assert resp.status_code == 200
    assert session._session.calls == 2
    assert slept, "a 429 must back off before retrying"


@requires_requests
def test_retry_after_seconds_is_honoured(monkeypatch):
    session, slept = _scripted_session(
        monkeypatch, [429, 200], headers=[{"Retry-After": "7"}, {}]
    )
    session.get("https://api.example.org/x", respect_robots=False)
    assert slept[0] == 7.0


@requires_requests
def test_retry_after_beyond_the_cap_raises_instead_of_blocking(monkeypatch):
    """A portal asking for ten minutes is telling us to stop, not to sleep
    through it holding the process."""
    session, slept = _scripted_session(
        monkeypatch, [429], headers=[{"Retry-After": "600"}]
    )
    with pytest.raises(RuntimeError, match="Retry-After"):
        session.get("https://api.example.org/x", respect_robots=False)
    assert not slept


@requires_requests
def test_retry_after_http_date_form_falls_back_to_backoff(monkeypatch):
    """Retry-After may be an HTTP-date. Unparseable as seconds must degrade to
    the ordinary backoff, not crash the request."""
    session, slept = _scripted_session(
        monkeypatch,
        [429, 200],
        headers=[{"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, {}],
    )
    resp = session.get("https://api.example.org/x", respect_robots=False)
    assert resp.status_code == 200
    assert slept and 0.5 <= slept[0] <= 1.0


def test_backoff_is_jittered():
    """Deterministic 2**attempt makes every client retry in lockstep."""
    delays = {hc._retry_delay(2, None) for _ in range(50)}
    assert len(delays) > 1, "backoff must be jittered"
    assert all(2.0 <= d <= 4.0 for d in delays), delays


@requires_requests
def test_non_429_client_errors_are_not_retried(monkeypatch):
    """Regression guard: a 404 must still come straight back to the caller."""
    session, _ = _scripted_session(monkeypatch, [404, 200])
    resp = session.get("https://api.example.org/x", respect_robots=False)
    assert resp.status_code == 404
    assert session._session.calls == 1


@requires_requests
def test_persistent_429_raises_after_max_retries(monkeypatch):
    session, _ = _scripted_session(monkeypatch, [429] * hc.MAX_RETRIES)
    with pytest.raises(RuntimeError, match="429"):
        session.get("https://api.example.org/x", respect_robots=False)


@requires_requests
def test_no_sleep_after_the_final_attempt(monkeypatch):
    """A persistent 429 with Retry-After: 30 made the caller wait an extra 30s
    after the retry budget was already spent, with no request left to make
    (Codex, PR #97)."""
    session, slept = _scripted_session(
        monkeypatch,
        [429] * hc.MAX_RETRIES,
        headers=[{"Retry-After": "5"}] * hc.MAX_RETRIES,
    )
    with pytest.raises(RuntimeError):
        session.get("https://api.example.org/x", respect_robots=False)
    assert len(slept) == hc.MAX_RETRIES - 1, "the last attempt must not sleep"


@requires_requests
def test_no_sleep_after_the_final_network_error(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(hc.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(hc, "is_safe_url", lambda url: True)
    session = hc.RetrySession(rate_limit_sec=0)

    class _AlwaysFails:
        headers: dict = {}

        def request(self, *a, **k):
            raise hc.requests.RequestException("boom")

    session._session = _AlwaysFails()
    with pytest.raises(hc.requests.RequestException):
        session.get("https://api.example.org/x", respect_robots=False)
    assert len(slept) == hc.MAX_RETRIES - 1


@requires_requests
def test_5xx_backoff_still_works(monkeypatch):
    """The behaviour that already existed must survive the change."""
    session, slept = _scripted_session(monkeypatch, [503, 200])
    resp = session.get("https://api.example.org/x", respect_robots=False)
    assert resp.status_code == 200
    assert session._session.calls == 2
    assert slept
