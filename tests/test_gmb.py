"""Offline unit tests for commoner_probe.gmb.

Fixtures use the exact filenames/content-ids documented as verified in
_org/requests/0006-gmb-and-gujarat-legislative-cross-level.md (the filing
spec for this adapter): Admin_Report_2023_24_English.pdf, ..._Gujarati.pdf,
inc_exp_2020_21_acs.pdf, and the Traffic Details / Traffic Highlight jetty
tables (GMB Jetty / Captive Jetty / Private Jetty / Private Port; named
ports Magdalla, Bedi, Jafrabad, Okha, Porbandar, Bhavnagar, Navlakhi,
Mundra(Old), Alang). No network — a fake session serves canned HTML.

Note: gmbports.org was unreachable from this development environment when
this adapter was finalised (DNS resolved but every TCP connection attempt
timed out) — these tests exercise the parsing/orchestration logic against
the documented contract, not a live-captured page. Live-verify the actual
page structure before a real acquisition run.
"""

from __future__ import annotations

import json

from commoner_probe.gmb import GMB_SOURCES, GmbProbe
from commoner_probe.gmb.probe import (
    discover_pdf_links,
    parse_traffic_tables,
)

ADMIN_REPORTS_PAGE_HTML = """
<html><body>
<h2>Publications</h2>
<ul>
  <li><a href="/assets/downloads/Admin_Report_2023_24_English.pdf">Administrative Report 2023-24 (English)</a></li>
  <li><a href="/assets/downloads/Admin_Report_2023_24_Gujarati.pdf">Administrative Report 2023-24 (Gujarati)</a></li>
  <li><a href="/assets/downloads/31_administrative_report_2012_13.pdf">Administrative Report 2012-13</a></li>
  <li><a href="/assets/downloads/Maritime_Horizon_Issue_12.pdf">Maritime Horizon Magazine Issue 12</a></li>
  <li><a href="/contact.aspx">Contact Us</a></li>
</ul>
</body></html>
"""

FINANCIALS_PAGE_HTML = """
<html><body>
<a href="/assets/downloads/inc_exp_2020_21_acs.pdf">Income Expenditure Account 2020-21</a>
</body></html>
"""

TRAFFIC_PAGE_HTML = """
<html><body>
<h3>Traffic Handled at GMB owned Ports</h3>
<table>
<tr><th>Port</th><th>2020-21</th><th>2021-22</th></tr>
<tr><td>Magdalla</td><td>45.2</td><td>50.1</td></tr>
<tr><td>Bedi</td><td>12,340</td><td>-</td></tr>
</table>
<h3>Traffic Handled at various Non-Major GMB ports</h3>
<table>
<tr><th>Class</th><th>2020-21</th><th>2021-22</th></tr>
<tr><td>Private Jetty</td><td>210.5</td><td>NA</td></tr>
<tr><td>Captive Jetty</td><td>88</td><td>91.3</td></tr>
</table>
</body></html>
"""


# ---------------------------------------------------------------------------
# GMB_SOURCES registry
# ---------------------------------------------------------------------------

class TestGmbSourcesRegistry:
    def test_all_ten_source_classes_present(self):
        names = {s.name for s in GMB_SOURCES}
        assert names == {
            "admin-reports", "publications-misc", "financials", "traffic",
            "tariff", "circulars", "tenders", "rti", "vision-2047", "news-articles",
        }

    def test_content_ids_match_spec(self):
        by_name = {s.name: s for s in GMB_SOURCES}
        assert by_name["admin-reports"].content_ids == ("56", "307")
        assert by_name["financials"].content_ids == ("50",)
        assert by_name["traffic"].content_ids == ("46", "504")
        assert by_name["tariff"].content_ids == ("212",)
        assert by_name["circulars"].content_ids == ("3208",)
        assert by_name["tenders"].content_ids == ("63",)
        assert by_name["rti"].content_ids == ("69",)
        assert by_name["vision-2047"].content_ids == ("30454",)
        assert by_name["news-articles"].content_ids == ("876",)

    def test_traffic_source_is_traffic_kind(self):
        by_name = {s.name: s for s in GMB_SOURCES}
        assert by_name["traffic"].kind == "traffic"
        assert by_name["admin-reports"].kind == "pdf_index"


# ---------------------------------------------------------------------------
# discover_pdf_links
# ---------------------------------------------------------------------------

class TestDiscoverPdfLinks:
    def test_admin_reports_keyword_filter_excludes_non_pdf_and_magazine(self):
        source = next(s for s in GMB_SOURCES if s.name == "admin-reports")
        endpoints = discover_pdf_links(
            ADMIN_REPORTS_PAGE_HTML, base_url="https://gmbports.org/showpage.aspx?contentid=56", source=source
        )
        filenames = {e.filename for e in endpoints}
        assert "Admin_Report_2023_24_English.pdf" in filenames
        assert "Admin_Report_2023_24_Gujarati.pdf" in filenames
        assert "31_administrative_report_2012_13.pdf" in filenames
        assert not any("Horizon" in f for f in filenames)  # publications-misc territory, not admin-reports

    def test_publications_misc_keyword_filter_picks_only_horizon(self):
        source = next(s for s in GMB_SOURCES if s.name == "publications-misc")
        endpoints = discover_pdf_links(
            ADMIN_REPORTS_PAGE_HTML, base_url="https://gmbports.org/showpage.aspx?contentid=56", source=source
        )
        filenames = {e.filename for e in endpoints}
        assert filenames == {"Maritime_Horizon_Issue_12.pdf"}

    def test_financials_no_keyword_filter_picks_all_pdfs(self):
        source = next(s for s in GMB_SOURCES if s.name == "financials")
        endpoints = discover_pdf_links(
            FINANCIALS_PAGE_HTML, base_url="https://gmbports.org/showpage.aspx?contentid=50", source=source
        )
        assert len(endpoints) == 1
        assert endpoints[0].filename == "inc_exp_2020_21_acs.pdf"
        assert endpoints[0].url == "https://gmbports.org/assets/downloads/inc_exp_2020_21_acs.pdf"
        assert endpoints[0].document_class == "financial"

    def test_fiscal_year_and_language_parsed_from_filename(self):
        source = next(s for s in GMB_SOURCES if s.name == "admin-reports")
        endpoints = discover_pdf_links(
            ADMIN_REPORTS_PAGE_HTML, base_url="https://gmbports.org/showpage.aspx?contentid=56", source=source
        )
        by_name = {e.filename: e for e in endpoints}
        eng = by_name["Admin_Report_2023_24_English.pdf"]
        assert eng.fiscal_year == "2023-24"
        assert eng.language == "en"
        guj = by_name["Admin_Report_2023_24_Gujarati.pdf"]
        assert guj.language == "gu"

    def test_dedupes_repeated_links(self):
        html = ADMIN_REPORTS_PAGE_HTML + ADMIN_REPORTS_PAGE_HTML
        source = next(s for s in GMB_SOURCES if s.name == "admin-reports")
        endpoints = discover_pdf_links(html, base_url="https://gmbports.org/showpage.aspx?contentid=56", source=source)
        assert len({e.url for e in endpoints}) == len(endpoints)

    def test_no_pdfs_found(self):
        source = next(s for s in GMB_SOURCES if s.name == "financials")
        assert discover_pdf_links("<html><body>no links here</body></html>", base_url="https://gmbports.org/", source=source) == []


# ---------------------------------------------------------------------------
# parse_traffic_tables
# ---------------------------------------------------------------------------

class TestParseTrafficTables:
    def test_extracts_gmb_owned_and_non_major_sections(self):
        rows = parse_traffic_tables(TRAFFIC_PAGE_HTML, source_url="https://gmbports.org/showpage.aspx?contentid=46")
        sections = {r["table_section"] for r in rows}
        assert "Traffic Handled at GMB owned Ports" in sections
        assert "Traffic Handled at various Non-Major GMB ports" in sections

    def test_gmb_owned_rows_are_tidy_long_format(self):
        rows = parse_traffic_tables(TRAFFIC_PAGE_HTML, source_url="https://gmbports.org/")
        magdalla_2021 = next(
            r for r in rows if r["port_or_class"] == "Magdalla" and r["fiscal_year"] == "2020-21"
        )
        assert magdalla_2021["tonnage_lakh_tonnes"] == "45.2"
        assert magdalla_2021["operator_class"] == "GMB Owned Port"

    def test_comma_separated_numbers_are_cleaned(self):
        rows = parse_traffic_tables(TRAFFIC_PAGE_HTML, source_url="https://gmbports.org/")
        bedi = next(r for r in rows if r["port_or_class"] == "Bedi")
        assert bedi["tonnage_lakh_tonnes"] == "12340"

    def test_dash_and_na_cells_are_dropped_not_zeroed(self):
        rows = parse_traffic_tables(TRAFFIC_PAGE_HTML, source_url="https://gmbports.org/")
        # Bedi 2021-22 was "-", Private Jetty 2021-22 was "NA" — neither should appear as a row.
        assert not any(r["port_or_class"] == "Bedi" and r["fiscal_year"] == "2021-22" for r in rows)
        assert not any(r["port_or_class"] == "Private Jetty" and r["fiscal_year"] == "2021-22" for r in rows)

    def test_non_major_operator_class_is_the_jetty_label_itself(self):
        rows = parse_traffic_tables(TRAFFIC_PAGE_HTML, source_url="https://gmbports.org/")
        private_jetty = next(r for r in rows if r["port_or_class"] == "Private Jetty")
        assert private_jetty["operator_class"] == "Private Jetty"

    def test_empty_html_yields_no_rows(self):
        assert parse_traffic_tables("<html><body>no tables</body></html>", source_url="x") == []


# ---------------------------------------------------------------------------
# GmbProbe — fake session, no network
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, text: str = "", content: bytes | None = None, status: int = 200, headers: dict | None = None):
        self.text = text
        self.content = content if content is not None else text.encode("utf-8")
        self.status_code = status
        self.headers = headers or {}


class FakeSession:
    def __init__(self, routes: dict[str, FakeResponse]):
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append(url)
        for needle, resp in self.routes.items():
            if needle in url:
                return resp
        raise AssertionError(f"FakeSession had no route matching: {url}")


def _probe(tmp_path, routes):
    probe = GmbProbe(tmp_path, sleep=0)
    probe.session = FakeSession(routes)
    return probe


class TestGmbProbeSelectedSources:
    def test_all_selects_everything(self, tmp_path):
        probe = _probe(tmp_path, {})
        assert len(probe.selected_sources(["all"])) == len(GMB_SOURCES)

    def test_specific_names(self, tmp_path):
        probe = _probe(tmp_path, {})
        selected = probe.selected_sources(["financials", "rti"])
        assert {s.name for s in selected} == {"financials", "rti"}

    def test_unknown_name_yields_nothing(self, tmp_path):
        probe = _probe(tmp_path, {})
        assert probe.selected_sources(["not-a-real-source"]) == []


class TestGmbProbeDryRun:
    def test_dry_run_no_network_no_manifest_write(self, tmp_path):
        probe = _probe(tmp_path, {})
        records = probe.probe_sources(["financials"], dry_run=True)
        assert len(records) == 1
        assert records[0]["status"] == "dry_run"
        assert probe.session.calls == []
        assert not (tmp_path / "manifest.jsonl").exists()

    def test_dry_run_traffic_emits_page_and_csv_records(self, tmp_path):
        probe = _probe(tmp_path, {})
        records = probe.probe_sources(["traffic"], dry_run=True)
        # 2 content_ids x (1 page + 1 derived-csv placeholder) = 4
        assert len(records) == 4
        assert all(r["status"] == "dry_run" for r in records)


class TestGmbProbeFullRun:
    def test_admin_reports_downloads_pdfs_and_writes_manifest(self, tmp_path):
        routes = {
            "contentid=56": FakeResponse(ADMIN_REPORTS_PAGE_HTML),
            "contentid=307": FakeResponse("<html><body>no links</body></html>"),
            "Admin_Report_2023_24_English.pdf": FakeResponse(content=b"%PDF-fake-en"),
            "Admin_Report_2023_24_Gujarati.pdf": FakeResponse(content=b"%PDF-fake-gu"),
            "31_administrative_report_2012_13.pdf": FakeResponse(content=b"%PDF-fake-old"),
        }
        probe = _probe(tmp_path, routes)
        records = probe.probe_sources(["admin-reports"])

        pdf_records = [r for r in records if r["document_class"] == "admin-report" and r["media_type"] == "application/pdf"]
        assert len(pdf_records) == 3
        assert all(r["status"] == "downloaded" for r in pdf_records)
        assert all(len(r["sha256"]) == 64 for r in pdf_records)

        manifest_lines = (tmp_path / "manifest.jsonl").read_text().splitlines()
        assert len(manifest_lines) == len(records)
        for line in manifest_lines:
            json.dumps(json.loads(line))  # round-trips

    def test_traffic_source_derives_csv_not_pdfs(self, tmp_path):
        routes = {
            "contentid=46": FakeResponse(TRAFFIC_PAGE_HTML),
            "contentid=504": FakeResponse("<html><body></body></html>"),
        }
        probe = _probe(tmp_path, routes)
        records = probe.probe_sources(["traffic"])
        # One derived CSV per content_id (46 and 504) — empty pages still get
        # a header-only CSV for provenance that the page was checked.
        csv_records = [r for r in records if r["document_class"] == "traffic-table" and r["status"] == "derived"]
        assert len(csv_records) == 2
        with_data = next(r for r in csv_records if "contentid46" in r["filename"])
        dest = tmp_path / "traffic" / with_data["filename"]
        assert dest.exists()
        body = dest.read_text()
        assert "Magdalla" in body
        assert "table_section,operator_class,port_or_class,fiscal_year,tonnage_lakh_tonnes" in body

    def test_rerun_skips_already_downloaded_files(self, tmp_path):
        routes = {
            "contentid=50": FakeResponse(FINANCIALS_PAGE_HTML),
            "inc_exp_2020_21_acs.pdf": FakeResponse(content=b"%PDF-fake"),
        }
        probe = _probe(tmp_path, routes)
        probe.probe_sources(["financials"])

        probe2 = _probe(tmp_path, routes)
        records2 = probe2.probe_sources(["financials"])
        pdf_rec = next(r for r in records2 if r["document_class"] == "financial")
        assert pdf_rec["status"] == "skipped_exists"
        # The page itself is also skip-on-exists (page HTML was saved to disk too).
        assert "inc_exp_2020_21_acs.pdf" not in probe2.session.calls[-1] if probe2.session.calls else True

    def test_fetch_error_is_recorded_not_raised(self, tmp_path):
        routes = {"contentid=63": FakeResponse(status=500)}
        probe = _probe(tmp_path, routes)
        records = probe.probe_sources(["tenders"])
        assert records[0]["status"] == "fetch_error"
        assert records[0]["http_status"] == 500

    def test_network_exception_is_recorded_not_raised(self, tmp_path):
        class BoomSession:
            def get(self, url, **kwargs):
                raise RuntimeError("dns exploded")

        probe = GmbProbe(tmp_path, sleep=0)
        probe.session = BoomSession()
        records = probe.probe_sources(["rti"])
        assert records[0]["status"] == "fetch_error"
        assert "dns exploded" in records[0]["error"]

    def test_last_modified_header_captured(self, tmp_path):
        routes = {
            "contentid=50": FakeResponse(
                FINANCIALS_PAGE_HTML, headers={"Last-Modified": "Wed, 01 Jan 2025 00:00:00 GMT"}
            ),
            "inc_exp_2020_21_acs.pdf": FakeResponse(content=b"%PDF-fake"),
        }
        probe = _probe(tmp_path, routes)
        records = probe.probe_sources(["financials"])
        page_rec = next(r for r in records if r["document_class"] == "financial" and r["media_type"] == "text/html")
        assert page_rec["source_last_modified"] == "2025-01-01T00:00:00Z"
