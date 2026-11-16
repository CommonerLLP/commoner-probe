"""SEC EDGAR submissions adapter. No test touches the network."""

from __future__ import annotations

import hashlib
import json

import pytest

from commoner_probe import http_client as hc
from commoner_probe.sec_edgar_submissions import fetch_submissions

CONTACT = "research@commoner.ca"
BODY = {"cik": "49938", "name": "IMPERIAL OIL LTD", "filings": {"recent": {}}}


class FakeResponse:
    def __init__(self, body: bytes):
        self._body = body
        self.status_code = 200
        self.headers: dict = {}

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=16384):
        yield self._body


class StubSession:
    def __init__(self, body: bytes | None = None):
        self.body = json.dumps(BODY).encode() if body is None else body
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.body)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(hc.time, "sleep", lambda s: None)


def test_cik_is_zero_padded(tmp_path):
    session = StubSession()
    fetch_submissions(session, "49938", tmp_path, contact=CONTACT)
    assert session.calls[0][0] == "https://data.sec.gov/submissions/CIK0000049938.json"


def test_missing_contact_raises(tmp_path):
    session = StubSession()
    for bad in ("", "   ", None):
        with pytest.raises(ValueError, match="contact"):
            fetch_submissions(session, "49938", tmp_path, contact=bad)
    assert session.calls == []


def test_user_agent_carries_contact(tmp_path):
    session = StubSession()
    fetch_submissions(session, "49938", tmp_path, contact=CONTACT)
    ua = session.calls[0][1]["headers"]["User-Agent"]
    assert ua.startswith("commoner-probe/")
    assert ua.endswith(f" ({CONTACT})")


def test_non_numeric_cik_raises(tmp_path):
    with pytest.raises(ValueError, match="CIK"):
        fetch_submissions(StubSession(), "../x", tmp_path, contact=CONTACT)


def test_result_and_manifest_record(tmp_path):
    result = fetch_submissions(StubSession(), "0000049938", tmp_path, contact=CONTACT)
    assert result["name"] == "IMPERIAL OIL LTD"
    rec = json.loads((tmp_path / "manifest.jsonl").read_text().splitlines()[0])
    assert rec["kind"] == "sec_edgar_submissions"
    assert rec["cik"] == "0000049938"
    assert rec["status"] == "downloaded"
    with open(rec["dest"], "rb") as f:
        stored = f.read()
    assert hashlib.sha256(stored).hexdigest() == rec["sha256"]


def test_rerun_keeps_earlier_file_and_hash_valid(tmp_path):
    fetch_submissions(StubSession(), "49938", tmp_path, contact=CONTACT)
    changed = dict(BODY, name="CHANGED")
    fetch_submissions(StubSession(json.dumps(changed).encode()), "49938", tmp_path, contact=CONTACT)
    recs = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert len(recs) == 2 and recs[0]["dest"] != recs[1]["dest"]
    for rec in recs:
        with open(rec["dest"], "rb") as f:
            assert hashlib.sha256(f.read()).hexdigest() == rec["sha256"]


def test_non_json_body_writes_no_record(tmp_path):
    with pytest.raises(ValueError):
        fetch_submissions(StubSession(b"<html>blocked</html>"), "49938", tmp_path, contact=CONTACT)
    assert not (tmp_path / "manifest.jsonl").exists()


def test_body_without_name_raises(tmp_path):
    with pytest.raises(ValueError):
        fetch_submissions(StubSession(b'{"a": 1}'), "49938", tmp_path, contact=CONTACT)
    assert not (tmp_path / "manifest.jsonl").exists()
