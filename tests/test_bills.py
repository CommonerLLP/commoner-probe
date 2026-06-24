"""Tests for the bills / legislation probe.

The live sansad.in bills contract is unconfirmed (see commoner_probe/bills.py
PROVISIONAL note / bead sansad-crawler-4xd). These tests pin record-shaping,
pagination, dedup, dry-run, and error handling against the assumed committee-style
envelope via a fake session — NOT the live contract. Swap the fixtures for the
real shape once a response is captured.
"""

from __future__ import annotations

import json

from commoner_probe.bills import BillsProbe, bill_key


class FakeResponse:
    def __init__(self, payload: dict | None = None, status: int = 200):
        self._payload = payload or {}
        self.status_code = status

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


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


def _raw(no: int, **overrides) -> dict:
    rec = {
        "billNumber": no,
        "billName": f"The Demo (Amendment) Bill, 20{no:02d}",
        "billType": "Government Bills",
        "ministry": "Finance",
        "introducedDate": "2026-02-01",
        "status": "Pending",
    }
    rec.update(overrides)
    return rec


def _probe(tmp_path, routes, **kw):
    probe = BillsProbe(tmp_path, sleep=0, **kw)
    probe.session = FakeSession(routes)
    return probe


def test_bill_key_prefers_bill_number_else_hash():
    assert bill_key("ls", {"billNumber": 42}) == "BILL|ls|42"
    k = bill_key("rs", {"name": "no number here"})
    assert k.startswith("BILL|rs|") and len(k.split("|")[-1]) == 12


def test_probe_emits_bill_records(tmp_path):
    page1 = {"_metadata": {"totalPages": 1}, "records": [_raw(1), _raw(2)]}
    probe = _probe(tmp_path, {"legislation/bills": page1}, houses=["ls"])
    records = probe.probe()

    assert len(records) == 2
    for r in records:
        assert r["kind"] == "bill_record"
        assert r["record_type"] == "bill_record"
        assert r["house"] == "ls"
        assert r["fetch_status"] == "ok"
        assert r["source"] == "sansad.in/legislation"
    assert {r["key"] for r in records} == {"BILL|ls|1", "BILL|ls|2"}
    assert records[0]["bill_name"].startswith("The Demo")

    manifest = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert manifest == records


def test_probe_dedup_on_rerun(tmp_path):
    page1 = {"_metadata": {"totalPages": 1}, "records": [_raw(1)]}
    probe = _probe(tmp_path, {"legislation/bills": page1}, houses=["ls"])
    probe.probe()
    probe2 = _probe(tmp_path, {"legislation/bills": page1}, houses=["ls"])
    assert probe2.probe() == []  # same key already in manifest


def test_dry_run_emits_plan_records_without_fetching(tmp_path):
    probe = BillsProbe(tmp_path, sleep=0, houses=["ls", "rs"])

    class BoomSession:
        def get(self, *a, **k):
            raise AssertionError("dry-run must not hit network")

    probe.session = BoomSession()
    records = probe.probe(dry_run=True)
    assert len(records) == 2
    assert all(r["fetch_status"] == "dry_run" for r in records)
    assert {r["house"] for r in records} == {"ls", "rs"}
    assert not (tmp_path / "manifest.jsonl").exists()


def test_provisional_endpoint_failure_is_recorded(tmp_path):
    class BoomSession:
        def get(self, *a, **k):
            raise RuntimeError("HTTP 404")

    probe = BillsProbe(tmp_path, sleep=0, houses=["ls"])
    probe.session = BoomSession()
    records = probe.probe()
    assert len(records) == 1
    assert records[0]["fetch_status"] == "fetch_error"
    assert "404" in records[0]["error"]


def test_bills_cli_dry_run(tmp_path, capsys):
    from commoner_probe.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["bills", "--out", str(tmp_path), "--house", "ls", "--dry-run"])
    args.func(args)
    lines = capsys.readouterr().out.splitlines()
    assert lines
    rec = json.loads(lines[0])
    assert rec["kind"] == "bill_record"
    assert rec["fetch_status"] == "dry_run"
    assert not (tmp_path / "manifest.jsonl").exists()


def test_schema_bundled_and_validates(tmp_path):
    import pytest

    pytest.importorskip("jsonschema")
    from commoner_probe import schemas
    from commoner_probe.validate import validate_corpus

    assert "manifest_bill" in schemas.list_all()
    record = {
        "key": "BILL|ls|42",
        "kind": "bill_record",
        "record_type": "bill_record",
        "source": "sansad.in/legislation",
        "house": "ls",
        "bill_no": 42,
        "bill_name": "The Demo Bill, 2026",
        "status": "Pending",
        "fetch_status": "ok",
        "fetched_at": "2026-06-24T10:00:00Z",
        "probed_at": "2026-06-24T10:00:00Z",
    }
    (tmp_path / "manifest.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    assert validate_corpus(tmp_path, log=lambda _: None)


def test_corpus_streams_bills(tmp_path):
    from commoner_probe import Corpus

    record = {
        "key": "BILL|ls|42",
        "kind": "bill_record",
        "record_type": "bill_record",
        "source": "sansad.in/legislation",
        "house": "ls",
        "fetch_status": "ok",
        "probed_at": "2026-06-24T10:00:00Z",
    }
    (tmp_path / "manifest.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    records = list(Corpus(tmp_path).manifest_bills())
    assert len(records) == 1
    assert records[0].house == "ls"
    assert records[0].fetch_status == "ok"
