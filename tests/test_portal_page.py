"""portal_page raw fetch adapter. No test touches the network."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest

from commoner_probe import http_client as hc
from commoner_probe.http_client import ChallengeDetected
from commoner_probe.portal_page import fetch_page

HTML = "<html><body><h1>Notice 026-1015</h1> café</body></html>".encode()
JSON_BODY = json.dumps({"bill": "C-5", "title": "One Canadian Economy"}).encode()


class FakeResponse:
    def __init__(self, body: bytes, headers: dict | None = None):
        self._body = body
        self.status_code = 200
        self.headers = headers if headers is not None else {"Content-Type": "text/html; charset=utf-8"}

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=16384):
        yield self._body


class StubSession:
    def __init__(self, body: bytes, headers: dict | None = None):
        self.body, self.headers = body, headers
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return FakeResponse(self.body, self.headers)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(hc.time, "sleep", lambda s: None)


def _records(out):
    return [json.loads(line) for line in (out / "manifest.jsonl").read_text().splitlines()]


def test_html_body_stored_verbatim(tmp_path):
    rec = fetch_page(StubSession(HTML), "https://ero.ontario.ca/notice/026-1015", tmp_path, expect="html")
    with open(rec["dest"], "rb") as f:
        assert f.read() == HTML
    assert rec["sha256"] == hashlib.sha256(HTML).hexdigest()
    assert rec["content_type"] == "text/html; charset=utf-8"
    assert rec["kind"] == "portal_page"
    assert _records(tmp_path) == [rec]


def test_json_body_stored_verbatim(tmp_path):
    session = StubSession(JSON_BODY, {"Content-Type": "application/json"})
    rec = fetch_page(session, "https://www.parl.ca/legisinfo/en/bill/45-1/c-5/json", tmp_path, expect="json")
    with open(rec["dest"], "rb") as f:
        assert f.read() == JSON_BODY
    assert rec["dest"].endswith(".json")


def test_json_expectation_rejects_html_challenge_page(tmp_path):
    session = StubSession(b"<html><body>Request unsuccessful</body></html>")
    with pytest.raises(ChallengeDetected):
        fetch_page(session, "https://www.parl.ca/x/json", tmp_path, expect="json")
    assert not list(tmp_path.iterdir())


def test_waf_header_is_refused(tmp_path):
    session = StubSession(JSON_BODY, {"x-amzn-waf-action": "challenge"})
    with pytest.raises(ChallengeDetected):
        fetch_page(session, "https://www.parl.ca/x/json", tmp_path, expect="json")
    assert not list(tmp_path.iterdir())


def test_empty_body_refused(tmp_path):
    for expect in ("html", "json"):
        with pytest.raises(ChallengeDetected):
            fetch_page(StubSession(b""), "https://example.org/p", tmp_path, expect=expect)
    assert not list(tmp_path.iterdir())


def test_malformed_json_writes_nothing(tmp_path):
    with pytest.raises(ValueError):
        fetch_page(StubSession(b'{"a": '), "https://example.org/p", tmp_path, expect="json")
    assert not list(tmp_path.iterdir())


def test_bad_expect_raises_before_request(tmp_path):
    session = StubSession(HTML)
    with pytest.raises(ValueError, match="expect"):
        fetch_page(session, "https://example.org/p", tmp_path, expect="xml")
    assert session.calls == []


def test_rerun_keeps_earlier_file_and_hash_valid(tmp_path):
    url = "https://ero.ontario.ca/notice/026-1015"
    fetch_page(StubSession(HTML), url, tmp_path, expect="html")
    fetch_page(StubSession(HTML + b"<!-- changed -->"), url, tmp_path, expect="html")
    recs = _records(tmp_path)
    assert len(recs) == 2 and recs[0]["dest"] != recs[1]["dest"]
    for rec in recs:
        with open(rec["dest"], "rb") as f:
            assert hashlib.sha256(f.read()).hexdigest() == rec["sha256"]


def test_non_json_non_html_body_is_a_value_error_not_a_challenge(tmp_path):
    xml = b'<?xml version="1.0"?><feed><entry/></feed>'
    with pytest.raises(ValueError, match="not JSON") as exc:
        fetch_page(StubSession(xml), "https://example.org/p", tmp_path, expect="json")
    assert not isinstance(exc.value, ChallengeDetected)
    assert not (tmp_path / "manifest.jsonl").exists()


class _Clock(datetime):
    """A datetime whose now() moves one second per call, so two calls differ."""

    calls = 0

    @classmethod
    def now(cls, tz=None):
        cls.calls += 1
        return datetime(2026, 11, 16, 12, 0, cls.calls, tzinfo=tz)


def _stamp(fetched_at: str) -> str:
    return fetched_at.replace("-", "").replace(":", "").rstrip("Z")


def test_run_dir_and_fetched_at_share_one_timestamp(tmp_path, monkeypatch):
    from commoner_probe import portal_page

    _Clock.calls = 0
    monkeypatch.setattr(portal_page, "datetime", _Clock)
    rec = fetch_page(StubSession(HTML), "https://example.org/p", tmp_path, expect="html")
    run = Path(rec["dest"]).relative_to(tmp_path).parts[0]
    assert run.startswith("run-" + _stamp(rec["fetched_at"]))
