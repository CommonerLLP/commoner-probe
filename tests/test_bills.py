"""Tests for the bills / legislation probe.

Fixtures mirror the live api_rs/legislation/getBills shape captured from the
bills page: a committee-style {records, _metadata.totalPages} envelope with
bill* field names. No network — a fake session serves the canned payload.
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


def _raw(no: int, year: int = 2026, **overrides) -> dict:
    rec = {
        "billNumber": str(no),
        "billName": f"THE DEMO (AMENDMENT) BILL, {year}",
        "billType": "Government",
        "billCategory": "Financial Bill",
        "ministryName": "FINANCE",
        "billYear": year,
        "billIntroducedInHouse": "Lok Sabha",
        "billIntroducedBy": None,
        "billIntroducedDate": "2026-02-01 00:00:00.0",
        "billIntroducedFile": f"https://sansad.in/getFile/bill{no}.pdf",
        "billPassedInLSDate": None,
        "billPassedInLSFile": None,
        "status": "Pending",
    }
    rec.update(overrides)
    return rec


def _probe(tmp_path, routes, **kw):
    probe = BillsProbe(tmp_path, sleep=0, **kw)
    probe.session = FakeSession(routes)
    return probe


def test_bill_key_uses_house_year_number_else_hash():
    assert bill_key("ls", {"billNumber": "42", "billYear": 2026}) == "BILL|ls|2026|42"
    k = bill_key("rs", {"billName": "no number here"})
    assert k.startswith("BILL|rs|") and len(k.split("|")[-1]) == 12


def test_probe_emits_bill_records_with_real_fields(tmp_path):
    page1 = {"_metadata": {"totalPages": 1}, "records": [_raw(1), _raw(2)]}
    probe = _probe(tmp_path, {"getBills": page1}, houses=["ls"])
    records = probe.probe()

    assert len(records) == 2
    for r in records:
        assert r["kind"] == "bill_record"
        assert r["house"] == "ls"
        assert r["fetch_status"] == "ok"
        assert r["source"] == "sansad.in/api_rs/legislation/getBills"
    by_key = {r["key"]: r for r in records}
    assert "BILL|ls|2026|1" in by_key
    rec = by_key["BILL|ls|2026|1"]
    assert rec["bill_name"].startswith("THE DEMO")
    assert rec["bill_category"] == "Financial Bill"
    assert rec["ministry"] == "FINANCE"
    assert rec["introduced_date"] == "2026-02-01"  # trimmed from "...00:00:00.0"
    assert rec["introduced_file"].endswith("bill1.pdf")
    assert rec["status"] == "Pending"

    manifest = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert manifest == records


def test_house_param_is_mapped(tmp_path):
    page1 = {"_metadata": {"totalPages": 1}, "records": [_raw(1)]}
    probe = _probe(tmp_path, {"getBills": page1}, houses=["ls"])
    probe.probe()
    # The fake session records the requested URL — house must be the sansad value.
    assert any("house=Lok+Sabha" in u or "house=Lok%20Sabha" in u for u in probe.session.calls)


def test_probe_paginates(tmp_path):
    # Two pages; the fake matches both calls to the same payload by page param.
    p1 = {"_metadata": {"totalPages": 2}, "records": [_raw(1)]}
    p2 = {"_metadata": {"totalPages": 2}, "records": [_raw(2)]}
    probe = BillsProbe(tmp_path, sleep=0, houses=["ls"])

    class PagedSession:
        def __init__(self):
            self.calls = []

        def get(self, url, **kw):
            self.calls.append(url)
            return FakeResponse(p2 if "page=2" in url else p1)

    probe.session = PagedSession()
    records = probe.probe()
    assert {r["key"] for r in records} == {"BILL|ls|2026|1", "BILL|ls|2026|2"}


def test_probe_dedup_on_rerun(tmp_path):
    page1 = {"_metadata": {"totalPages": 1}, "records": [_raw(1)]}
    _probe(tmp_path, {"getBills": page1}, houses=["ls"]).probe()
    assert _probe(tmp_path, {"getBills": page1}, houses=["ls"]).probe() == []


def test_max_records_brake(tmp_path):
    page1 = {"_metadata": {"totalPages": 1}, "records": [_raw(1), _raw(2), _raw(3)]}
    probe = _probe(tmp_path, {"getBills": page1}, houses=["ls"])
    records = probe.probe(max_records=2)
    assert len(records) == 2


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


def test_fetch_error_is_recorded(tmp_path):
    class BoomSession:
        def get(self, *a, **k):
            raise RuntimeError("HTTP 500")

    probe = BillsProbe(tmp_path, sleep=0, houses=["ls"])
    probe.session = BoomSession()
    records = probe.probe()
    assert len(records) == 1
    assert records[0]["fetch_status"] == "fetch_error"
    assert "500" in records[0]["error"]


def test_bills_cli_dry_run(tmp_path, capsys):
    from commoner_probe.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["bills", "--out", str(tmp_path), "--house", "ls", "--dry-run"])
    args.func(args)
    rec = json.loads(capsys.readouterr().out.splitlines()[0])
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
        "key": "BILL|ls|2026|42",
        "kind": "bill_record",
        "record_type": "bill_record",
        "source": "sansad.in/api_rs/legislation/getBills",
        "house": "ls",
        "bill_no": "42",
        "bill_name": "THE DEMO BILL, 2026",
        "bill_category": "Financial Bill",
        "bill_year": 2026,
        "introduced_date": "2026-02-01",
        "introduced_file": "https://sansad.in/getFile/x.pdf",
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
        "key": "BILL|ls|2026|42",
        "kind": "bill_record",
        "record_type": "bill_record",
        "source": "sansad.in/api_rs/legislation/getBills",
        "house": "ls",
        "bill_year": 2026,
        "fetch_status": "ok",
        "probed_at": "2026-06-24T10:00:00Z",
    }
    (tmp_path / "manifest.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    records = list(Corpus(tmp_path).manifest_bills())
    assert len(records) == 1
    assert records[0].house == "ls"
    assert records[0].bill_year == 2026
