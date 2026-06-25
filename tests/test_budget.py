from __future__ import annotations

import hashlib
import json

import pytest


class FakeHeaders:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def get(self, name: str, default=None):
        return self._values.get(name, default)


class FakeResponse:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None) -> None:
        self._body = body
        self.headers = FakeHeaders(headers or {})

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._body


class FakeOpener:
    """Maps full URLs to FakeResponse; raising HTTPError for any 404 URL."""

    def __init__(self, responses: dict[str, FakeResponse], not_found: set[str] | None = None) -> None:
        self.responses = responses
        self.not_found = not_found or set()
        self.opened: list[str] = []

    def open(self, req, timeout):
        import urllib.error

        url = req.full_url
        self.opened.append(url)
        if url in self.not_found:
            raise urllib.error.HTTPError(url, 404, "Not Found", hdrs=None, fp=None)
        if url not in self.responses:
            raise urllib.error.HTTPError(url, 404, "Not Found", hdrs=None, fp=None)
        return self.responses[url]


# --------------------------------------------------------------------------- #
# Static endpoint table                                                       #
# --------------------------------------------------------------------------- #


def test_union_budget_endpoints_expand_years_x_demands():
    from commoner_probe.budget import union_budget_endpoints

    endpoints = union_budget_endpoints(["101", "1"])
    # 7 archive years × 2 demands = 14 endpoints
    assert len(endpoints) == 14

    current = next(e for e in endpoints if e.fiscal_year == "2026-27" and e.demand_no == "101")
    assert current.url == "https://www.indiabudget.gov.in/doc/eb/sbe101.xlsx"
    assert current.filename == "sbe101_2026-27.xlsx"
    assert current.source_name == "union-budget"
    assert current.document_type == "demand_for_grants"

    older = next(e for e in endpoints if e.fiscal_year == "2022-23" and e.demand_no == "1")
    assert older.url == "https://www.indiabudget.gov.in/budget2022-23/doc/eb/sbe1.xls"
    assert older.media_type == "application/vnd.ms-excel"


# --------------------------------------------------------------------------- #
# Dry-run (offline) for union-budget                                          #
# --------------------------------------------------------------------------- #


def test_budget_dry_run_emits_union_records_without_manifest(tmp_path):
    from commoner_probe.budget import BudgetProbe

    probe = BudgetProbe(tmp_path, sleep=0, demands=["101"])
    records = probe.probe_sources(["union-budget"], dry_run=True)

    assert len(records) == 7  # one per archive year
    assert all(r["kind"] == "budget_source_file" for r in records)
    assert all(r["status"] == "dry_run" for r in records)
    assert all(r["source_name"] == "union-budget" for r in records)
    keys = {r["key"] for r in records}
    assert "BUDGET|union-budget|2026-27|sbe101_2026-27.xlsx" in keys
    assert not (tmp_path / "manifest.jsonl").exists()


# --------------------------------------------------------------------------- #
# Download path: 200 + 404 handling, sha256, manifest                         #
# --------------------------------------------------------------------------- #


def test_budget_download_writes_files_and_records_not_found(tmp_path, monkeypatch):
    from commoner_probe.budget import BudgetProbe

    xls_body = b"PK\x03\x04 fake xlsx body"
    ok_url = "https://www.indiabudget.gov.in/doc/eb/sbe101.xlsx"
    responses = {
        ok_url: FakeResponse(
            xls_body,
            {"Last-Modified": "Thu, 11 Jun 2026 09:15:11 GMT", "Content-Type": "application/vnd.ms-excel"},
        ),
    }
    # Every other archived-year URL 404s (demand 101 not archived under those paths in this fixture).
    opener = FakeOpener(responses)
    probe = BudgetProbe(tmp_path, sleep=0, demands=["101"])
    monkeypatch.setattr(probe, "_build_opener", lambda: opener)

    records = probe.probe_sources(["union-budget"], dry_run=False)

    downloaded = [r for r in records if r["status"] == "downloaded"]
    not_found = [r for r in records if r["status"] == "not_found"]
    assert len(downloaded) == 1
    assert len(not_found) == 6

    dl = downloaded[0]
    assert dl["sha256"] == hashlib.sha256(xls_body).hexdigest()
    assert dl["source_last_modified"] == "2026-06-11T09:15:11Z"
    assert (tmp_path / "union-budget" / "sbe101_2026-27.xlsx").read_bytes() == xls_body

    # Manifest persists exactly the returned records (downloaded + not_found).
    manifest_records = [
        json.loads(line)
        for line in (tmp_path / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert manifest_records == records
    assert not_found[0]["http_status"] == 404


# --------------------------------------------------------------------------- #
# RBI discovery (offline, canned HTML)                                        #
# --------------------------------------------------------------------------- #


_RBI_HTML = """
<html><body>
<h2 class="page_title">State Finances: A Study of Budgets of 2024-25</h2>
<table class="tablebg">
  <tr><td class="tableheader">Statements</td></tr>
  <tr>
    <td style="x">Appendix: Combined Receipts</td>
    <td><a target="_blank" href="/pdffiles/appendix.xls">XLS</a></td>
    <td><a target="_blank" href="/pdffiles/appendix.pdf">PDF</a></td>
  </tr>
  <tr>
    <td style="x">Notes to Appendix</td>
    <td></td>
    <td><a target="_blank" href="http://www.rbi.org.in/notes.pdf">PDF</a></td>
  </tr>
</table>
</body></html>
"""


def test_parse_rbi_documents_offline():
    pytest.importorskip("lxml")
    from commoner_probe.budget import parse_rbi_documents

    endpoints = parse_rbi_documents(_RBI_HTML, base_url="https://www.rbi.org.in/scripts/AnnualPublications.aspx")

    # Appendix has both XLS + PDF; Notes has only PDF -> 3 endpoints.
    assert len(endpoints) == 3
    assert all(e.source_name == "rbi-state-finances" for e in endpoints)
    assert all(e.fiscal_year == "2024-25" for e in endpoints)
    assert all(e.section == "Statements" for e in endpoints)

    urls = {e.url for e in endpoints}
    # Relative links are joined against the base; http upgraded to https.
    assert "https://www.rbi.org.in/pdffiles/appendix.xls" in urls
    assert "https://www.rbi.org.in/notes.pdf" in urls
    xls = next(e for e in endpoints if e.url.endswith("appendix.xls"))
    assert xls.media_type == "application/vnd.ms-excel"
    assert xls.filename == "Appendix_ Combined Receipts.xls"


# --------------------------------------------------------------------------- #
# CLI + schema + corpus wiring                                                #
# --------------------------------------------------------------------------- #


def test_budget_cli_dry_run_emits_records_without_manifest(tmp_path, capsys):
    from commoner_probe.cli import build_parser

    parser = build_parser()
    args = parser.parse_args([
        "budget", "--out", str(tmp_path), "--sources", "union-budget", "--demands", "101", "--dry-run",
    ])
    args.func(args)

    lines = capsys.readouterr().out.splitlines()
    assert lines
    record = json.loads(lines[0])
    assert record["kind"] == "budget_source_file"
    assert record["source_name"] == "union-budget"
    assert record["status"] == "dry_run"
    assert not (tmp_path / "manifest.jsonl").exists()


def test_budget_manifest_schema_is_bundled_and_validates_record(tmp_path):
    pytest.importorskip("jsonschema")
    from commoner_probe import schemas
    from commoner_probe.validate import validate_corpus

    assert "manifest_budget" in schemas.list_all()

    record = {
        "key": "BUDGET|union-budget|2026-27|sbe101_2026-27.xlsx",
        "kind": "budget_source_file",
        "record_type": "budget_source_file",
        "source_family": "budget",
        "source_name": "union-budget",
        "publisher": "Ministry of Finance",
        "fiscal_year": "2026-27",
        "document_type": "demand_for_grants",
        "demand_no": "101",
        "section": None,
        "filename": "sbe101_2026-27.xlsx",
        "dest": str(tmp_path / "union-budget" / "sbe101_2026-27.xlsx"),
        "url": "https://www.indiabudget.gov.in/doc/eb/sbe101.xlsx",
        "status": "downloaded",
        "media_type": "application/vnd.ms-excel",
        "source_last_modified": "2026-06-11T09:15:11Z",
        "source_last_modified_raw": "Thu, 11 Jun 2026 09:15:11 GMT",
        "fetched_at": "2026-06-16T17:06:58Z",
        "probed_at": "2026-06-16T17:06:58Z",
        "sha256": "a" * 64,
    }
    (tmp_path / "manifest.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

    assert validate_corpus(tmp_path, log=lambda _: None)


def test_corpus_streams_budget_manifest_records(tmp_path):
    from commoner_probe import Corpus

    record = {
        "key": "BUDGET|rbi-state-finances|2024-25|Appendix.xls",
        "kind": "budget_source_file",
        "record_type": "budget_source_file",
        "source_family": "budget",
        "source_name": "rbi-state-finances",
        "publisher": "Reserve Bank of India",
        "fiscal_year": "2024-25",
        "document_type": "state_finances_study",
        "demand_no": None,
        "section": "Statements",
        "filename": "Appendix.xls",
        "dest": str(tmp_path / "rbi-state-finances" / "Appendix.xls"),
        "url": "https://www.rbi.org.in/pdffiles/appendix.xls",
        "status": "downloaded",
        "media_type": "application/vnd.ms-excel",
        "fetched_at": "2026-06-16T17:06:58Z",
        "probed_at": "2026-06-16T17:06:58Z",
        "sha256": "b" * 64,
    }
    (tmp_path / "manifest.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

    records = list(Corpus(tmp_path).manifest_budget())
    assert len(records) == 1
    assert records[0].source_name == "rbi-state-finances"
    assert records[0].section == "Statements"
    assert records[0].demand_no is None
