"""Tests for the DoE Pay & Allowances annual-report fetcher.

The listing HTML mirrors the live doe.gov.in table (verified 2026-07-08):
one <tr> per report year, a serial cell, a title cell, and a Download
link into /files/annual_reports_documents/. No network.
"""

from __future__ import annotations

import hashlib
import json

from commoner_probe.doe import DoePayAllowancesProbe

LISTING_HTML = """
<html><body>
<a href="https://doe.gov.in/files/circulars_document/GFRupdatedupto31012026.pdf">GFR</a>
<table><tbody>
<tr><td>1</td><td>Annual Report on Pay and Allowances for the year 2023-24</td>
<td><a href="/files/annual_reports_documents/AnnualReportonPayAllowance202324.pdf" type="application/pdf">Download (5.53 MB)</a></td></tr>
<tr><td>2</td><td>Annual Report on Pay and Allowances For The Year 2022-23</td>
<td><a href="/files/annual_reports_documents/Annual_Report_on_Pay_and_Allowances_For_The_Year_2022_23_.pdf" type="application/pdf">Download (5.58 MB)</a></td></tr>
<tr><td>8</td><td>ANNUAL REPORT ON PAY &amp; ALLOWANCES FOR THE YEAR 2016-17</td>
<td><a href="/files/annual_reports_documents/PayAllowance2016-17%28English%29.pdf" type="application/pdf">Download (1.33 MB)</a></td></tr>
</tbody></table>
</body></html>
"""

PDF_WITH_TEXT = b"%PDF-1.7 born digital " + b"x" * 2000
PDF_SCANNED = b"%PDF-1.5 flattened scan " + b"y" * 2000


class FakeResponse:
    def __init__(self, *, text=None, content=None, status=200, content_type=None):
        self.text = text
        self.content = content
        self.status_code = status
        self.headers = {"Content-Type": content_type} if content_type else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, *, pdf_for_url=None, fail_listing=False):
        self.pdf_for_url = pdf_for_url or {}
        self.fail_listing = fail_listing
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if "annual-report-pay-and-allowances" in url:
            if self.fail_listing:
                return FakeResponse(status=503)
            return FakeResponse(text=LISTING_HTML)
        if url in self.pdf_for_url:
            return FakeResponse(content=self.pdf_for_url[url], content_type="application/pdf")
        raise AssertionError(f"unrouted url: {url}")


URL_2324 = "https://doe.gov.in/files/annual_reports_documents/AnnualReportonPayAllowance202324.pdf"
URL_2223 = "https://doe.gov.in/files/annual_reports_documents/Annual_Report_on_Pay_and_Allowances_For_The_Year_2022_23_.pdf"
URL_1617 = "https://doe.gov.in/files/annual_reports_documents/PayAllowance2016-17%28English%29.pdf"


def _probe(tmp_path, **kw):
    probe = DoePayAllowancesProbe(tmp_path, sleep=0)
    probe.session = FakeSession(**kw)
    return probe


def test_parse_listing_enumerates_years_and_titles(tmp_path):
    probe = _probe(tmp_path)
    reports = probe.discover()
    assert [(r["year"], r["url"]) for r in reports] == [
        ("2023-24", URL_2324),
        ("2022-23", URL_2223),
        ("2016-17", URL_1617),
    ]
    assert reports[0]["title"] == "Annual Report on Pay and Allowances for the year 2023-24"
    # Non-report PDFs elsewhere on the page must not be enumerated.
    assert not any("circulars_document" in r["url"] for r in reports)


def test_probe_downloads_with_provenance_and_text_layer(tmp_path, monkeypatch):
    from commoner_probe import doe as doe_mod
    monkeypatch.setattr(
        doe_mod, "extract_pdf_text",
        lambda p: "extracted text " * 50 if "202324" in str(p) else "",
    )
    probe = _probe(tmp_path, pdf_for_url={
        URL_2324: PDF_WITH_TEXT,
        URL_2223: PDF_SCANNED,
    })
    records = probe.probe(years=["2023-24", "2022-23"])
    assert [r["status"] for r in records] == ["downloaded", "downloaded"]
    by_year = {r["year"]: r for r in records}
    assert by_year["2023-24"]["text_layer"] is True
    assert by_year["2022-23"]["text_layer"] is False
    assert by_year["2022-23"]["sha256"] == hashlib.sha256(PDF_SCANNED).hexdigest()
    assert by_year["2022-23"]["key"] == "DOE_PAY_ALLOWANCES|2022-23"
    assert by_year["2022-23"]["kind"] == "doe_pay_allowances_report"
    assert (tmp_path / by_year["2022-23"]["filename"]).read_bytes() == PDF_SCANNED
    manifest = [
        json.loads(line)
        for line in (tmp_path / "manifest.jsonl").read_text().splitlines()
    ]
    assert len(manifest) == 2


def test_probe_skips_existing_file(tmp_path, monkeypatch):
    from commoner_probe import doe as doe_mod
    monkeypatch.setattr(doe_mod, "extract_pdf_text", lambda p: "")
    probe = _probe(tmp_path, pdf_for_url={URL_2223: PDF_SCANNED})
    first = probe.probe(years=["2022-23"])
    second = probe.probe(years=["2022-23"])
    assert first[0]["status"] == "downloaded"
    assert second[0]["status"] == "skipped_exists"
    assert second[0]["sha256"] == first[0]["sha256"]
    # One download call total; listing fetched on both runs.
    assert probe.session.calls.count(URL_2223) == 1


def test_probe_dry_run_lists_without_downloading(tmp_path):
    probe = _probe(tmp_path)
    records = probe.probe(dry_run=True)
    assert [r["year"] for r in records] == ["2023-24", "2022-23", "2016-17"]
    assert all(r["status"] == "dry_run" for r in records)
    assert not (tmp_path / "manifest.jsonl").exists()
    assert probe.session.calls == [probe.listing_url]


def test_non_pdf_body_is_recorded_as_error_not_written(tmp_path):
    probe = _probe(tmp_path, pdf_for_url={
        URL_2223: b"<html>WAF interstitial</html>",
    })
    records = probe.probe(years=["2022-23"])
    assert records[0]["status"] == "error"
    assert "sha256" not in records[0]
    assert not (tmp_path / records[0]["filename"]).exists()


def test_records_validate_against_schema(tmp_path, monkeypatch):
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        import pytest
        pytest.skip("jsonschema not installed")
    from commoner_probe import doe as doe_mod
    from commoner_probe import schemas
    monkeypatch.setattr(doe_mod, "extract_pdf_text", lambda p: "")
    schema = schemas.load("manifest_doe_pay_allowances")
    probe = _probe(tmp_path, pdf_for_url={URL_2223: PDF_SCANNED})
    for record in probe.probe(years=["2022-23"]):
        Draft202012Validator(schema).validate(record)
