"""Tests for scripts/source_registry/data_publications.py: the date functions and record mappers.

Every test uses canned data; none touches the network. Record shapes come from
the adapters' own test fixtures.
"""

from __future__ import annotations

import functools
import json
import sys
from datetime import date
from pathlib import Path

import pytest

from commoner_probe.academia.parsers import PARSERS
from commoner_probe.annual_report_index import NitiAnnualReportProbe, parse_listing
from commoner_probe.budget.probe import BudgetProbe
from commoner_probe.catalogue_search_api import AbhilekhPatalProbe
from commoner_probe.csr.dpe import DpeCsrProbe
from commoner_probe.csr.mca import McaCsrProbe
from commoner_probe.koha import KohaProbe
from commoner_probe.ministry_pdf_index import MinistryDDGProbe, parse_ddg_listing_card
from commoner_probe.nada import NadaClient, NadaProbe
from commoner_probe.pay_report_index import DoePayAllowancesProbe
from commoner_probe.rest_dataset_api import MospiClient
from commoner_probe.shrug_catalogue_api import ShrugTable
from commoner_probe.spa_jwt_api import UdiseDocumentProbe
from commoner_probe.wayback_recover import Capture
from tests.conftest import load_script
from tests.test_abhilekh_patal import FakeSession as NaiSession
from tests.test_budget import _RBI_HTML
from tests.test_dchb_town import FIX as DCHB_FIXTURE
from tests.test_doe_pay_allowances import LISTING_HTML as DOE_LISTING
from tests.test_koha import FakeSession as KohaSession
from tests.test_koha import _item as koha_item
from tests.test_ministry_ddg import LISTING_HTML as DEA_LISTING
from tests.test_mospi import FakeMospiSession
from tests.test_nada import _fx as nada_fixture
from tests.test_niti import LISTING_HTML as NITI_LISTING
from tests.test_udise_documents import _Portal as UdisePortal

cs = load_script("check_sources")

_SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from source_registry import SOURCES  # noqa: E402
from source_registry import data_publications as dp  # noqa: E402

BY_ID = {s.id: s for s in SOURCES}

ASSIGNED_IDS = (
    "budget-union", "budget-rbi", "nada", "dchb-town", "mospi-udise", "shrug",
    "doe-pay-allowances", "ministry-ddg-dea", "ministry-ddg-mha", "ministry-ddg-dst",
    "niti-annual-report", "dpe-csr", "mca-csr", "udise-docs", "koha-niti", "abhilekh-patal",
    "wayback-recover",
)


@pytest.fixture(autouse=True)
def _clear_caches(tmp_path, monkeypatch):
    # A fresh scratch directory per test, because the probes resume from what they wrote.
    monkeypatch.setattr(dp, "SCRATCH", tmp_path)
    cached = (dp._budget_probe, dp._nada_client, dp._nada_search, dp._dchb_rows, dp._mospi_rows,
              dp._shrug_tables, dp._doe_reports, dp._koha_result, dp._nai_probe,
              dp._wayback_captures)
    for fn in cached:
        fn.cache_clear()
    yield
    for fn in cached:
        fn.cache_clear()


def _ctx(today: date):
    return cs.Context(today=today, results={}, records={}, state={}, geo_fenced=frozenset())


def _evaluate(rule, records, today: date, newest: str = ""):
    return rule.evaluate(source_id="x", records=records, newest=newest, count=len(records),
                         ctx=_ctx(today))


def _complete(source, record: dict) -> bool:
    """Would `check_source` count this record as satisfying the contract?"""
    return all(record.get(field) not in (None, "") for field in source.required)


def _edition_rule(source_id: str):
    return next(r for r in BY_ID[source_id].freshness if isinstance(r, cs.ExpectedEdition))


def test_every_assigned_entry_is_registered_and_resolves():
    assert set(ASSIGNED_IDS) <= BY_ID.keys()
    assert len(cs.order_sources(SOURCES)) == len(SOURCES)


def test_every_academia_parser_family_has_an_entry_except_the_one_no_institution_uses():
    families = {i["parser"] for i in dp.load_registry() if i.get("parser")} | {"generic"}
    covered = {parser for parser, _ in dp._ACADEMIA}
    assert covered == families
    assert families | {"jnu"} == set(PARSERS)
    for parser, _ in dp._ACADEMIA:
        assert f"academic-jobs-{parser.replace('_', '-')}" in BY_ID


# -- Union Budget and RBI ---------------------------------------------------


def test_budget_edition_names_the_fiscal_year_that_starts_in_the_cycle():
    assert dp._budget_edition(2026) == "2026-27"
    assert dp._budget_edition(2099) == "2099-00"


def test_union_budget_records_list_every_year_newest_first_without_the_network():
    records = dp._union_budget_records()
    assert [r["fiscal_year"] for r in records][:2] == ["2026-27", "2025-26"]
    assert records[0]["url"] == "https://www.indiabudget.gov.in/doc/eb/sbe101.xlsx"
    assert all(_complete(BY_ID["budget-union"], r) for r in records)


def test_union_budget_goes_stale_when_the_table_lacks_the_new_budget():
    rule = _edition_rule("budget-union")
    records = dp._union_budget_records()
    assert _evaluate(rule, records, date(2026, 10, 5)).ok
    assert _evaluate(rule, records, date(2027, 3, 2)).ok  # inside the grace period
    late = _evaluate(rule, records, date(2027, 3, 3))
    assert not late.ok and "2027-28" in late.reason


@pytest.mark.parametrize("section,expected", [
    ("Jan 23, 2026", "2026-01-23"),
    ("Dec 5, 2024", "2024-12-05"),
    ("Chapters", ""),
    ("Appendix Tables: 2025-26", ""),
    ("Foo 31, 2026", ""),
    ("", ""),
    (None, ""),
])
def test_rbi_release_date_reads_only_a_date_heading(section, expected):
    assert dp._rbi_release_date({"section": section, "url": "https://x/main.pdf"}) == expected


def test_rbi_release_date_skips_the_main_report_xls():
    # The main report's row links an XLS file before its PDF. The document check
    # fetches the first record with the newest date and expects a PDF.
    heading = "Jan 23, 2026"
    assert dp._rbi_release_date({"section": heading, "url": "https://x/main.xlsx"}) == ""
    assert dp._rbi_release_date({"section": heading, "url": "https://x/MAIN.PDF"}) == "2026-01-23"
    assert dp._rbi_release_date({"section": heading}) == ""


_RBI_RELEASE_HTML = _RBI_HTML.replace(
    '<tr><td class="tableheader">Statements</td></tr>',
    '<tr><td class="tableheader">Jan 23, 2026</td></tr>'
    '<tr><td style="x">State Finances: A Study of Budgets of 2025-26</td><td></td>'
    '<td><a target="_blank" href="/pdffiles/main.pdf">PDF</a></td></tr>'
    '<tr><td class="tableheader">Chapters</td></tr>',
)


def test_rbi_records_carry_the_release_date_of_the_main_report_only(monkeypatch):
    pytest.importorskip("lxml")
    monkeypatch.setattr(BudgetProbe, "_fetch_text", lambda self, opener, url: _RBI_RELEASE_HTML)
    records = dp._rbi_records()
    source = BY_ID["budget-rbi"]
    assert all(_complete(source, r) for r in records)
    assert cs.newest_date(records, source.record_date, date(2026, 10, 5)) == "2026-01-23"
    assert [source.record_date(r) for r in records].count("") == len(records) - 1


def test_rbi_edition_passes_until_one_year_and_85_days_after_its_release():
    rule = next(r for r in BY_ID["budget-rbi"].freshness if isinstance(r, cs.MaxAge))
    assert _evaluate(rule, [], date(2027, 4, 18), newest="2026-01-23").ok
    assert not _evaluate(rule, [], date(2027, 4, 19), newest="2026-01-23").ok


# -- NADA and the Census Town Release ---------------------------------------


def test_nada_returns_the_search_rows_and_the_catalogue_total(monkeypatch):
    payload = json.loads(nada_fixture("search_nss.json"))["result"]
    monkeypatch.setattr(NadaClient, "search",
                        lambda self, **kw: (payload["rows"], int(payload["found"])))
    source = BY_ID["nada"]
    rows = source.fetch()
    assert all(_complete(source, r) for r in rows)
    assert source.total() == 129


def test_nada_sentinel_reads_the_study_title(monkeypatch):
    dataset = json.loads(nada_fixture("study_1.json"))["dataset"]
    monkeypatch.setattr(NadaClient, "study", lambda self, idno: dataset)
    sentinel = next(r for r in BY_ID["nada"].freshness if isinstance(r, cs.Sentinel))
    assert _evaluate(sentinel, [], date(2026, 10, 5)).ok


def _fake_town_release(monkeypatch, resources):
    monkeypatch.setattr(NadaClient, "study", lambda self, idno: {"id": "1301", "idno": idno})
    monkeypatch.setattr(NadaClient, "resources", lambda self, catalog_id: (resources, "ok", None))

    def acquire(self, idno, slug, catalog_id, resource, allow):
        target = self.out_dir / "docs" / resource["filename"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(DCHB_FIXTURE.read_bytes())
        return {"fetch_status": "downloaded", "filename": resource["filename"],
                "path": str(target.relative_to(self.out_dir)), "error": None}

    monkeypatch.setattr(NadaProbe, "_acquire_resource", acquire)


def test_dchb_town_reads_the_workbook_it_downloads(monkeypatch):
    _fake_town_release(monkeypatch, [
        {"resource_id": "1", "filename": "DH_2011_1301_PART_A_DCHB_MON.pdf", "url": "u1"},
        {"resource_id": "2", "filename": "DH_2011_DCHB_Town_Release_1300.xlsx", "url": "u2"},
    ])
    source = BY_ID["dchb-town"]
    rows = source.fetch()
    assert len(rows) == 3 and source.total() == 3
    assert all(_complete(source, r) for r in rows)
    assert "Naginimora" in dp._dchb_sentinel()["town_name"]
    sentinel = next(r for r in source.freshness if isinstance(r, cs.Sentinel))
    assert _evaluate(sentinel, rows, date(2026, 10, 5)).ok


def test_dchb_town_fails_when_the_study_stops_shipping_a_town_release(monkeypatch):
    _fake_town_release(monkeypatch, [{"resource_id": "1", "filename": "other.pdf", "url": "u1"}])
    with pytest.raises(LookupError, match="Town Release"):
        BY_ID["dchb-town"].fetch()


# -- MoSPI and SHRUG ---------------------------------------------------------


def test_mospi_returns_the_dropout_rows_and_the_indicator_count(monkeypatch):
    monkeypatch.setattr(dp, "MospiClient", functools.partial(MospiClient, session=FakeMospiSession()))
    source = BY_ID["mospi-udise"]
    rows = source.fetch()
    assert rows and all(_complete(source, r) for r in rows)
    assert dp._mospi_label(rows[0]) == "Dropout Rate Gujarat 2024-25"
    assert source.total() == 1
    sentinel = next(r for r in source.freshness if isinstance(r, cs.Sentinel))
    assert _evaluate(sentinel, rows, date(2026, 10, 5)).ok


def test_shrug_maps_a_table_with_no_readable_link_to_an_empty_url(monkeypatch):
    tables = {
        "2011 Population Census Village Directory": ShrugTable(
            "Population Census", "2011 Population Census Village Directory", "DTA", "", "https://s3/x.zip", None),
        "Constituency keys": ShrugTable("Core keys", "Constituency keys", "DTA", "", None, None),
    }
    monkeypatch.setattr(dp, "catalogue", lambda: tables)
    source = BY_ID["shrug"]
    rows = source.fetch()
    assert [r["url"] for r in rows] == ["https://s3/x.zip", ""]
    assert [_complete(source, r) for r in rows] == [True, False]
    assert source.total() == 2
    assert dp._shrug_sentinel()["table_label"] == "2011 Population Census Village Directory"


# -- Listings of per-year documents ------------------------------------------


def test_doe_records_one_row_per_year(monkeypatch):
    monkeypatch.setattr(
        DoePayAllowancesProbe, "discover",
        lambda self: self.parse_listing(DOE_LISTING))
    source = BY_ID["doe-pay-allowances"]
    records = source.fetch()
    assert [r["year"] for r in records] == ["2023-24", "2022-23", "2016-17"]
    assert all(_complete(source, r) for r in records)
    assert source.total() == 3
    assert dp._doe_sentinel()["year"] == "2016-17"


def _fake_ddg(monkeypatch):
    monkeypatch.setattr(
        MinistryDDGProbe, "discover",
        lambda self: parse_ddg_listing_card(DEA_LISTING, self.portal.listing_url))


def test_ddg_records_one_row_per_edition(monkeypatch):
    _fake_ddg(monkeypatch)
    source = BY_ID["ministry-ddg-dea"]
    records = source.fetch()
    assert [r["year"] for r in records] == ["2026-27", "2022-23"]
    assert all(_complete(source, r) for r in records)


@pytest.mark.parametrize("source_id,last_ok,first_stale", [
    ("ministry-ddg-dea", date(2027, 6, 30), date(2027, 7, 1)),
    ("ministry-ddg-dst", date(2027, 6, 30), date(2027, 7, 1)),
    ("ministry-ddg-mha", date(2027, 3, 31), date(2027, 4, 1)),
])
def test_ddg_goes_stale_when_the_next_edition_is_missing_after_the_deadline(
        monkeypatch, source_id, last_ok, first_stale):
    _fake_ddg(monkeypatch)
    rule = _edition_rule(source_id)
    records = BY_ID["ministry-ddg-dea"].fetch()
    assert _evaluate(rule, records, last_ok).ok
    late = _evaluate(rule, records, first_stale)
    assert not late.ok and "2027-28" in late.reason


def test_niti_edition_names_the_fiscal_year_that_ended_in_the_cycle():
    assert dp._niti_edition(2026) == "2025-26"
    assert dp._niti_edition(2100) == "2099-00"


def test_niti_records_keep_the_english_reports_and_the_edition_rule_reads_them(monkeypatch):
    monkeypatch.setattr(
        NitiAnnualReportProbe, "discover",
        lambda self, **kw: [r for r in parse_listing(NITI_LISTING) if r["language"] == "english"])
    source = BY_ID["niti-annual-report"]
    records = source.fetch()
    assert {r["report_year"] for r in records} == {"2025-26", "2024-25", "2022-23"}
    assert all(_complete(source, r) for r in records)
    rule = _edition_rule("niti-annual-report")
    assert _evaluate(rule, records, date(2026, 10, 5)).ok
    assert _evaluate(rule, records, date(2027, 6, 30)).ok
    missing = _evaluate(rule, records, date(2027, 7, 1))
    assert not missing.ok and "2026-27" in missing.reason


# -- CSR ---------------------------------------------------------------------


@pytest.mark.parametrize("record,expected", [
    ({"date": "2026-01-01T00:00:00"}, "2026-01-01"),
    ({"date": "2026-01-01T00:00:00Z"}, "2026-01-01"),
    ({"date": ""}, ""),
    ({}, ""),
])
def test_dpe_date_is_the_day_part_of_the_wordpress_timestamp(record, expected):
    assert dp._dpe_date(record) == expected


def test_dpe_documents_map_media_items_and_skip_empty_ones(monkeypatch):
    items = [
        {"id": 456, "date": "2026-01-01T00:00:00", "title": {"rendered": "CSR Document 2025"},
         "source_url": "https://dpe.gov.in/wp-content/uploads/2026/01/doc.pdf"},
        {"id": 457, "date": "2025-01-01T00:00:00", "title": {"rendered": "No file"}},
    ]
    monkeypatch.setattr(DpeCsrProbe, "fetch_page", lambda self, page, per_page=100, search="csr": items)
    source = BY_ID["dpe-csr"]
    records = source.fetch()
    assert [r["key"] for r in records] == ["DPE_CSR|456"]
    assert _complete(source, records[0])
    assert cs.newest_date(records, source.record_date, date(2026, 10, 5)) == "2026-01-01"


def test_mca_csr_exports_the_year_the_adapter_documents(monkeypatch):
    seen = []
    record = {"key": "MCA_CSR|FY 2022-23", "financial_year": "FY 2022-23",
              "filename": "mca_csr_company_spend_2022-23.csv", "sha256": "ab" * 32}
    monkeypatch.setattr(McaCsrProbe, "probe_years", lambda self, years: seen.append(years) or [record])
    source = BY_ID["mca-csr"]
    assert source.fetch() == [record]
    assert seen == [["2022-23"]]
    assert _complete(source, record)
    assert not _complete(source, {**record, "sha256": None})


# -- UDISE+, Koha, Abhilekh Patal, Wayback -----------------------------------


def test_udise_docs_records_a_real_document_through_the_adapter(monkeypatch):
    monkeypatch.setattr(dp, "UdiseDocumentProbe", functools.partial(UdiseDocumentProbe, session=UdisePortal()))
    source = BY_ID["udise-docs"]
    records = source.fetch()
    assert len(records) == 1 and records[0]["fetch_status"] == "ok"
    assert _complete(source, records[0])


def test_udise_docs_record_has_no_hash_when_the_body_is_not_a_pdf(monkeypatch):
    portal = UdisePortal(gone={"DCF0112"})
    monkeypatch.setattr(dp, "UdiseDocumentProbe", functools.partial(UdiseDocumentProbe, session=portal))
    records = BY_ID["udise-docs"].fetch()
    assert records[0]["fetch_status"] == "not_pdf"
    assert not _complete(BY_ID["udise-docs"], records[0])


def test_koha_items_carry_the_biblio_title_and_the_portal_total(monkeypatch):
    session = KohaSession({1: [koha_item(1), koha_item(2)]}, total=103970)
    monkeypatch.setattr(dp, "KohaProbe", functools.partial(KohaProbe, session=session))
    source = BY_ID["koha-niti"]
    items = source.fetch()
    assert [i["title"] for i in items] == ["Held title 1", "Held title 2"]
    assert all(_complete(source, i) for i in items)
    assert source.total() == 103970


class _NaiProbe(AbhilekhPatalProbe):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session = NaiSession()


def test_abhilekh_patal_returns_five_records_and_the_result_total(monkeypatch):
    monkeypatch.setattr(dp, "AbhilekhPatalProbe", _NaiProbe)
    source = BY_ID["abhilekh-patal"]
    records = source.fetch()
    assert len(records) == 5
    assert all(_complete(source, r) for r in records)
    assert source.total() == 25


def _captures():
    url = "https://dsel.education.gov.in/sites/default/files/2019-05/AN_PAB_2018_2019.pdf"
    return {"dsel.education.gov.in/x": [
        Capture(url, "20250517032756", "200", 5242957),
        Capture(url, "20220121062121", "200", 14561108),
        Capture(url, "20231015155748", "200", 14561045),
    ]}


def test_wayback_records_list_captures_largest_first_with_their_replay_urls(monkeypatch):
    monkeypatch.setattr(dp, "host_captures", lambda prefix, **kw: _captures())
    source = BY_ID["wayback-recover"]
    records = source.fetch()
    assert [r["timestamp"] for r in records] == ["20220121062121", "20231015155748", "20250517032756"]
    assert records[0]["replay_url"].startswith("https://web.archive.org/web/20220121062121id_/https://dsel.")
    assert all(_complete(source, r) for r in records)
    assert source.total() == 3
    sentinel = next(r for r in source.freshness if isinstance(r, cs.Sentinel))
    assert _evaluate(sentinel, records, date(2026, 10, 5)).ok


def test_wayback_sentinel_is_missing_when_that_capture_is_gone(monkeypatch):
    only_newest = {"k": [Capture("https://dsel.education.gov.in/a/AN_PAB_2018_2019.pdf",
                                 "20250517032756", "200", 5242957)]}
    monkeypatch.setattr(dp, "host_captures", lambda prefix, **kw: only_newest)
    assert dp._wayback_sentinel() is None


# -- Academic jobs -----------------------------------------------------------


def _ad(**kw):
    return {"fetch_status": "ok", "key": "ACAD|x|1", "title": "Faculty", "original_url": "https://x/y",
            **kw}


def test_academic_ads_drop_the_status_records_the_probe_adds(monkeypatch):
    monkeypatch.setattr(dp.AcademicJobsProbe, "probe", lambda self, **kw: [_ad(), _ad(fetch_status="no_ads")])
    assert [a["fetch_status"] for a in dp._academic_ads("iit-kanpur")] == ["ok"]


def test_academic_ads_raise_with_the_status_when_a_page_yields_none(monkeypatch):
    status = _ad(fetch_status="fetch_error", error="http_error (HTTP 404)")
    monkeypatch.setattr(dp.AcademicJobsProbe, "probe", lambda self, **kw: [status])
    with pytest.raises(ValueError, match=r"iit-indore yielded no ads: fetch_error http_error \(HTTP 404\)"):
        dp._academic_ads("iit-indore")


def test_only_the_pdf_parser_downloads_its_listing_documents(monkeypatch):
    calls = {}
    monkeypatch.setattr(dp.AcademicJobsProbe, "probe",
                        lambda self, **kw: calls.setdefault(sorted(self._institutions_filter)[0], kw) and [_ad()])
    for parser, institution in dp._ACADEMIA:
        BY_ID[f"academic-jobs-{parser.replace('_', '-')}"].fetch()
        assert calls[institution]["download"] is (parser == "iit_rolling"), parser


def test_academic_entries_take_their_host_from_the_bundled_registry():
    assert BY_ID["academic-jobs-iit-kanpur"].host == "iitk.ac.in"
    assert BY_ID["academic-jobs-private-university"].host == "careers.ashoka.edu.in"
    assert all(BY_ID[f"academic-jobs-{p.replace('_', '-')}"].freshness == () for p, _ in dp._ACADEMIA)
