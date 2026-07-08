"""Tests for the floor-debate probe.

The live sources serve transcript PDFs for sitting days. Fixtures mirror the
captured endpoints: Lok Sabha session/date catalog + text-of-debate, and Rajya
Sabha sessionDates + BusinessVerbatim. No network.
"""

from __future__ import annotations

import hashlib
import json
from urllib.parse import parse_qs, urlparse

from commoner_probe.debates import DebateProbe, date_to_iso, date_to_mdy

CATALOG = [
    {"loksabha": 18, "sessions": [
        {"sessionNo": 7, "dates": ["28/01/2026", "29/01/2026", "01/02/2026"]},
        {"sessionNo": 6, "dates": ["01/12/2025"]},
    ]},
    {"loksabha": 17, "sessions": [{"sessionNo": 1, "dates": ["17/06/2019"]}]},
]

# Keyed by the M/D/YYYY value the debate API receives. Missing => no transcript.
PDF_FOR = {
    "1/28/2026": "https://sansad.in/getFile/dms/fetch/abc?source=dsp2",
    "1/29/2026": "https://sansad.in/getFile/dms/fetch/def?source=dsp2",
    "12/1/2025": "https://sansad.in/getFile/dms/fetch/ghi?source=dsp2",
}
RS_CATALOG = [
    {"session": 270, "sittingDates": ["28/01/2026", "29/01/2026"]},
    {"session": 269, "sittingDates": ["01/12/2025"]},
]
RS_PDFS_FOR = {
    ("270", "28/01/2026"): [
        {
            "FileUrl": "https://cms.rajyasabha.nic.in/UploadedFiles/Debates/VerbatimDebates/270/full.pdf",
            "Time": "Full Day",
            "Name": "28.01 Full Day.pdf",
        },
        {
            "FileUrl": "https://cms.rajyasabha.nic.in/UploadedFiles/Debates/VerbatimDebates/270/12-1.pdf",
            "Time": "12:00-01:00 PM",
            "Name": "12-1.pdf",
        },
    ],
    ("269", "01/12/2025"): [
        {
            "FileUrl": "https://cms.rajyasabha.nic.in/UploadedFiles/Debates/VerbatimDebates/269/11-12.pdf",
            "Time": "11:00-12:00 Noon",
            "Name": "11-12.pdf",
        },
    ],
}
PDF_BODY = b"%PDF-1.4 fake debate transcript body that is over one thousand bytes " + b"x" * 1100


class FakeResponse:
    def __init__(self, payload=None, *, content=None, status=200):
        self._payload = payload
        self.content = content
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, *, catalog=CATALOG, rs_catalog=RS_CATALOG, fail_catalog=False):
        self.catalog = catalog
        self.rs_catalog = rs_catalog
        self.fail_catalog = fail_catalog
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if "AllLoksabhaAndSessionDates" in url:
            if self.fail_catalog:
                return FakeResponse(status=503)
            return FakeResponse(self.catalog)
        if "sessionDates" in url:
            if self.fail_catalog:
                return FakeResponse(status=503)
            return FakeResponse(self.rs_catalog)
        if "text-of-debate" in url:
            date = parse_qs(urlparse(url).query).get("debateDate", [""])[0]
            pdf = PDF_FOR.get(date)
            return FakeResponse({"pdfUrl": pdf} if pdf else {})
        if "BusinessVerbatim" in url:
            query = parse_qs(urlparse(url).query)
            session_no = query.get("ses_no", [""])[0]
            date = query.get("ses_dt", [""])[0]
            return FakeResponse(RS_PDFS_FOR.get((session_no, date), []))
        if "getFile" in url:  # PDF download
            return FakeResponse(content=PDF_BODY)
        if "cms.rajyasabha.nic.in" in url:
            # Live rsdoc.nic.in/cms.rajyasabha.nic.in returns 406 Not Acceptable
            # for PDF requests sent with Accept: application/json (verified
            # 2026-07-08). Mirror that here so a regression that reintroduces
            # the JSON Accept header on the binary download fails this test.
            if kwargs.get("headers", {}).get("Accept") == "application/json":
                return FakeResponse(status=406)
            return FakeResponse(content=PDF_BODY)
        raise AssertionError(f"unrouted url: {url}")


def _probe(tmp_path, **kw):
    probe = DebateProbe(tmp_path, sleep=0, **kw)
    probe.session = FakeSession()
    return probe


def test_date_converters():
    assert date_to_iso("28/01/2026") == "2026-01-28"
    assert date_to_mdy("28/01/2026") == "1/28/2026"
    assert date_to_mdy("01/12/2025") == "12/1/2025"


def test_probe_records_days_with_transcripts_only(tmp_path):
    probe = _probe(tmp_path, loksabhas=[18], sessions=[7])
    records = probe.probe()

    # session 7 has 3 dates; only 28th and 29th have a pdfUrl (1 Feb -> {} -> skipped)
    assert len(records) == 2
    keys = {r["key"] for r in records}
    assert keys == {"DEBATE|18|7|2026-01-28", "DEBATE|18|7|2026-01-29"}
    r = next(r for r in records if r["key"].endswith("2026-01-28"))
    assert r["kind"] == "floor_debate"
    assert r["run_id"]
    assert r["house"] == "Lok Sabha"
    assert r["loksabha"] == 18 and r["session_no"] == 7
    assert r["date"] == "2026-01-28"
    assert r["pdf_url"].endswith("abc?source=dsp2")
    assert r["fetch_status"] == "ok"
    assert r["pdf_path"] is None  # not downloaded by default

    manifest = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert manifest == records


def test_session_filter_and_multiple_loksabhas(tmp_path):
    probe = _probe(tmp_path, loksabhas=[18])  # all sessions
    records = probe.probe()
    # session 7: 28th + 29th; session 6: 1 Dec 2025 -> 12/1/2025 has a pdf
    assert {r["key"] for r in records} == {
        "DEBATE|18|7|2026-01-28", "DEBATE|18|7|2026-01-29", "DEBATE|18|6|2025-12-01",
    }


def test_date_range_filter(tmp_path):
    probe = _probe(tmp_path, loksabhas=[18], from_date="2026-01-01", to_date="2026-01-28")
    records = probe.probe()
    assert {r["key"] for r in records} == {"DEBATE|18|7|2026-01-28"}


def test_dry_run_lists_candidates_without_fetching_pdfs(tmp_path):
    probe = _probe(tmp_path, loksabhas=[18], sessions=[7])
    records = probe.probe(dry_run=True)
    # all 3 candidate dates are listed (not just the ones with transcripts)
    assert len(records) == 3
    assert all(r["fetch_status"] == "dry_run" for r in records)
    assert all(r["pdf_url"] is None for r in records)
    # catalog fetched, but no per-day text-of-debate calls
    assert any("AllLoksabhaAndSessionDates" in u for u in probe.session.calls)
    assert not any("text-of-debate" in u for u in probe.session.calls)
    assert not (tmp_path / "manifest.jsonl").exists()


def test_download_writes_pdf_and_sha256(tmp_path):
    probe = _probe(tmp_path, loksabhas=[18], sessions=[7])
    records = probe.probe(download=True, max_records=1)
    assert len(records) == 1
    rec = records[0]
    assert rec["pdf_path"] == "pdfs/debates/ls18_s7_2026-01-28.pdf"
    assert rec["sha256"] == hashlib.sha256(PDF_BODY).hexdigest()
    assert (tmp_path / rec["pdf_path"]).read_bytes() == PDF_BODY


def test_probe_records_rajya_sabha_full_day_transcripts(tmp_path):
    probe = _probe(tmp_path, house="rs", sessions=[270])
    records = probe.probe()

    assert len(records) == 1
    rec = records[0]
    assert rec["key"] == "DEBATE|RS|270|2026-01-28|Full_Day"
    assert rec["run_id"]
    assert rec["house"] == "Rajya Sabha"
    assert rec["loksabha"] is None
    assert rec["session_no"] == 270
    assert rec["date"] == "2026-01-28"
    assert rec["segment"] == "Full Day"
    assert rec["pdf_url"].endswith("/full.pdf")
    assert rec["fetch_status"] == "ok"


def test_probe_records_rajya_sabha_segments_when_full_day_absent(tmp_path):
    probe = _probe(tmp_path, house="rs", sessions=[269])
    records = probe.probe()

    assert len(records) == 1
    assert records[0]["key"] == "DEBATE|RS|269|2025-12-01|11_00-12_00_Noon"
    assert records[0]["segment"] == "11:00-12:00 Noon"


def test_rajya_sabha_download_writes_pdf_and_sha256(tmp_path):
    probe = _probe(tmp_path, house="rs", sessions=[270])
    records = probe.probe(download=True, max_records=1)

    assert len(records) == 1
    rec = records[0]
    assert rec["pdf_path"] == "pdfs/debates/rs_s270_2026-01-28_Full_Day.pdf"
    assert rec["sha256"] == hashlib.sha256(PDF_BODY).hexdigest()
    assert (tmp_path / rec["pdf_path"]).read_bytes() == PDF_BODY


def test_pdf_download_omits_json_accept_header(tmp_path):
    """Regression for the 2026-07-08 live-verified bug: the RS PDF host
    (rsdoc.nic.in/cms.rajyasabha.nic.in) returns 406 Not Acceptable for a
    request carrying Accept: application/json, but fetch_status stayed "ok"
    with pdf_path/sha256 silently null because the JSON-API HEADERS dict was
    reused for the binary download. FakeSession enforces the same 406 here.
    """
    probe = _probe(tmp_path, house="rs", sessions=[270])
    records = probe.probe(download=True, max_records=1)
    rec = records[0]
    assert rec["fetch_status"] == "ok"
    assert rec["pdf_path"] is not None
    assert rec["sha256"] == hashlib.sha256(PDF_BODY).hexdigest()


def test_download_failure_is_recorded_in_runlog(tmp_path):
    probe = _probe(tmp_path, house="rs", sessions=[270])

    class FailingDownloadSession(FakeSession):
        def get(self, url, **kwargs):
            if "cms.rajyasabha.nic.in" in url:
                return FakeResponse(status=500)
            return super().get(url, **kwargs)

    probe.session = FailingDownloadSession()
    records = probe.probe(download=True, max_records=1)
    rec = records[0]
    assert rec["pdf_path"] is None
    assert rec["sha256"] is None

    run_row = json.loads((tmp_path / "_runs.jsonl").read_text().splitlines()[-1])
    assert any("download:" in e["where"] for e in run_row["errors"])


def test_dedup_on_rerun(tmp_path):
    _probe(tmp_path, loksabhas=[18], sessions=[7]).probe()
    assert _probe(tmp_path, loksabhas=[18], sessions=[7]).probe() == []


def test_catalog_fetch_error_is_recorded(tmp_path):
    probe = DebateProbe(tmp_path, sleep=0, loksabhas=[18])
    probe.session = FakeSession(fail_catalog=True)
    records = probe.probe()
    assert len(records) == 1
    assert records[0]["fetch_status"] == "fetch_error"


def test_schema_bundled_and_validates(tmp_path):
    import pytest

    pytest.importorskip("jsonschema")
    from commoner_probe import schemas
    from commoner_probe.validate import validate_corpus

    assert "manifest_floor_debate" in schemas.list_all()
    record = {
        "key": "DEBATE|18|7|2026-01-28",
        "run_id": "abcdef1234567890abcdef1234567890",
        "kind": "floor_debate",
        "record_type": "floor_debate",
        "source": "sansad.in/api_ls/debate/text-of-debate",
        "house": "Lok Sabha",
        "loksabha": 18,
        "session_no": 7,
        "date": "2026-01-28",
        "pdf_url": "https://sansad.in/getFile/dms/fetch/abc?source=dsp2",
        "pdf_path": None,
        "sha256": None,
        "fetch_status": "ok",
        "fetched_at": "2026-06-24T10:00:00Z",
        "probed_at": "2026-06-24T10:00:00Z",
    }
    (tmp_path / "manifest.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    assert validate_corpus(tmp_path, log=lambda _: None)


def test_corpus_streams_floor_debates(tmp_path):
    from commoner_probe import Corpus

    record = {
        "key": "DEBATE|18|7|2026-01-28",
        "run_id": "abcdef1234567890abcdef1234567890",
        "kind": "floor_debate",
        "record_type": "floor_debate",
        "source": "sansad.in/api_ls/debate/text-of-debate",
        "house": "Lok Sabha",
        "loksabha": 18,
        "session_no": 7,
        "date": "2026-01-28",
        "fetch_status": "ok",
        "probed_at": "2026-06-24T10:00:00Z",
    }
    (tmp_path / "manifest.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    records = list(Corpus(tmp_path).manifest_floor_debates())
    assert len(records) == 1
    assert records[0].loksabha == 18 and records[0].session_no == 7
