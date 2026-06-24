"""Tests for the floor-debate probe.

The live sansad.in debate contract is unconfirmed (see commoner_probe/debates.py
PROVISIONAL note / bead sansad-crawler-5ht). These tests pin the record-shaping,
pagination, dedup, and runlog behaviour against the assumed committee-style
envelope via a fake session — NOT the live contract. When a real response is
captured, swap the fixture payloads below for the real shape.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from commoner_probe.debates import DebateProbe, debate_key
from commoner_probe.topics import TopicProfile


class FakeResponse:
    def __init__(self, payload: dict | None = None, status: int = 200):
        self._payload = payload or {}
        self.status_code = status

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size: int = 16384):
        yield b""


class FakeSession:
    def __init__(self, routes: dict[str, dict]):
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append(url)
        for needle, payload in self.routes.items():
            if needle in url:
                return FakeResponse(payload)
        raise AssertionError(f"FakeSession had no route matching: {url}")


def _topic(**kw) -> TopicProfile:
    return TopicProfile(
        name=kw.get("name", "test"),
        description="",
        search_groups={},
        lok_sabha_ministries=[],
        rajya_sabha_ministry_likes=[],
        filter_fn=kw.get("filter_fn"),
    )


def _raw(seq: int, **overrides) -> dict:
    rec = {
        "debateDate": "17-Mar-2026",
        "businessType": "Zero Hour",
        "memberName": f"Member {seq}",
        "debateText": f"Verbatim text number {seq}.",
        "debateTitle": "Matter of urgent public importance",
        "loksabha": 18,
    }
    rec.update(overrides)
    return rec


def _probe(tmp, routes, **kw) -> DebateProbe:
    probe = DebateProbe(_topic(**kw), Path(tmp), sleep=0, lok_sabha_no=18, topic_path=None)
    probe.session = FakeSession(routes)
    return probe


def test_debate_key_is_stable_across_field_churn():
    raw = {"a": 1, "b": 2}
    k1 = debate_key(18, "2026-03-17", raw)
    k2 = debate_key(18, "2026-03-17", {"b": 2, "a": 1})  # key order differs
    assert k1 == k2
    assert k1.startswith("DEBATE|18|2026-03-17|")


def test_probe_emits_floor_debate_records():
    page1 = {"_metadata": {"totalPages": 1}, "records": [_raw(1), _raw(2)]}
    with tempfile.TemporaryDirectory() as tmp:
        probe = _probe(tmp, {"api_ls/debate": page1})
        added = probe.probe(set(), ls_no=18, max_records=None, download=False)
        assert added == 2
        records = [json.loads(line) for line in (Path(tmp) / "manifest.jsonl").read_text().splitlines()]

    for r in records:
        assert r["kind"] == "floor_debate"
        assert r["house"] == "Lok Sabha"
        assert r["ls_no"] == 18
        assert r["date"] == "2026-03-17"
        assert r["verbatim_text"].startswith("Verbatim text")
        assert r["source"] == "sansad.in/api_ls/debate"
        assert r["run_id"]


def test_probe_dedup_on_rerun():
    page1 = {"_metadata": {"totalPages": 1}, "records": [_raw(1)]}
    with tempfile.TemporaryDirectory() as tmp:
        probe = _probe(tmp, {"api_ls/debate": page1})
        probe.probe(set(), ls_no=18, download=False)
        seen = probe.load_seen()
        assert len(seen) == 1
        added2 = probe.probe(seen, ls_no=18, download=False)
        assert added2 == 0


def test_probe_topic_filter_fn_is_applied():
    page1 = {"_metadata": {"totalPages": 1}, "records": [
        _raw(1, debateText="mentions libraries and reading rooms"),
        _raw(2, debateText="about agriculture subsidies"),
    ]}
    # Keep only debates whose text mentions "librar".
    topic_filter = lambda title, text: "librar" in (text or "").lower()  # noqa: E731
    with tempfile.TemporaryDirectory() as tmp:
        probe = _probe(tmp, {"api_ls/debate": page1}, filter_fn=topic_filter)
        added = probe.probe(set(), ls_no=18, download=False)
        assert added == 1
        rec = json.loads((Path(tmp) / "manifest.jsonl").read_text().splitlines()[0])
        assert "librar" in rec["verbatim_text"].lower()


def test_provisional_endpoint_failure_is_recorded_not_raised():
    # An unverified endpoint may 4xx; the probe must finish cleanly with a run-log error.
    class BoomSession:
        def get(self, *a, **k):
            raise RuntimeError("HTTP 400")

    with tempfile.TemporaryDirectory() as tmp:
        probe = DebateProbe(_topic(), Path(tmp), sleep=0, topic_path=None)
        probe.session = BoomSession()
        added = probe.probe(set(), ls_no=18, download=False)
        assert added == 0
        run = json.loads((Path(tmp) / "_runs.jsonl").read_text().splitlines()[0])
        assert run["errors"]  # the failure was recorded, not raised


def test_schema_bundled_and_validates(tmp_path):
    import pytest

    pytest.importorskip("jsonschema")
    from commoner_probe import schemas
    from commoner_probe.validate import validate_corpus

    assert "manifest_floor_debate" in schemas.list_all()
    record = {
        "key": "DEBATE|18|2026-03-17|abcdef123456",
        "kind": "floor_debate",
        "house": "Lok Sabha",
        "ls_no": 18,
        "date": "2026-03-17",
        "business_type": "Zero Hour",
        "member_name": "Member 1",
        "verbatim_text": "Verbatim text.",
        "language_classified": ["en"],
        "source": "sansad.in/api_ls/debate",
        "run_id": "r1",
        "probed_at": "2026-06-24T10:00:00Z",
    }
    (tmp_path / "manifest.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    assert validate_corpus(tmp_path, log=lambda _: None)


def test_corpus_streams_floor_debates(tmp_path):
    from commoner_probe import Corpus

    record = {
        "key": "DEBATE|18|2026-03-17|abcdef123456",
        "kind": "floor_debate",
        "house": "Lok Sabha",
        "source": "sansad.in/api_ls/debate",
        "probed_at": "2026-06-24T10:00:00Z",
        "verbatim_text": "x",
    }
    (tmp_path / "manifest.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    records = list(Corpus(tmp_path).manifest_floor_debates())
    assert len(records) == 1
    assert records[0].house == "Lok Sabha"
