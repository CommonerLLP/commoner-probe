"""Tests for scripts/source_registry/state_legal.py: the date functions and record mappers.

Every test uses canned data; none touches the network.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

from commoner_probe.assembly_portal import StateAssemblyCrawler
from commoner_probe.audit_pdf_index import CAGAccountsProbe, parse_finance_accounts_tab
from commoner_probe.dmft.mines import MinesDmftProbe
from commoner_probe.dspace import LegacyDSpaceProbe
from commoner_probe.go_issue_register import GoIssueRegister, GoRow
from commoner_probe.statute_dspace import IndiaCodeProbe
from tests.conftest import load_script
from tests.test_cag import ACCOUNTS_HTML, GJ
from tests.test_indiacode import ACT_DETAIL_HTML, BROWSE_PAGE_HTML

cs = load_script("check_sources")

_SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from source_registry import SOURCES, state_legal  # noqa: E402

BY_ID = {s.id: s for s in SOURCES}


@pytest.fixture(autouse=True)
def _clear_caches():
    cached = (state_legal._neva, state_legal._neva_depth, state_legal._indiacode_probe,
              state_legal._indiacode_act, state_legal._myneta_probe,
              state_legal._myneta_constituencies, state_legal._dspace_probe,
              state_legal._dspace_items)
    for fn in cached:
        fn.cache_clear()
    yield
    for fn in cached:
        fn.cache_clear()


def test_every_assigned_entry_is_registered():
    assert {"state-assembly-neva", "state-assembly-probe-neva", "indiacode", "go-register-ap",
            "cag-finance-accounts", "myneta-ls2024", "legacy-dspace-assam-ala",
            "mines-dmft-ministry", "mines-dmft-odisha"} <= BY_ID.keys()


# -- NeVA -------------------------------------------------------------------


def test_neva_date_reads_a_gujarati_label():
    assert state_legal._neva_date("શુક્રવાર, ૧૧ સપ્ટેમ્બર, ૨૦૨૬") == "2026-09-11"
    assert state_legal._neva_date("બુધવાર, ૨૫ માર્ચ, ૨૦૨૬") == "2026-03-25"


def test_neva_date_ignores_a_sitting_marker():
    label = "મંગળવાર, ૧૭ માર્ચ, ૨૦૨૬ (બીજી બેઠક) 2nd sitting"
    assert state_legal._neva_date(label) == "2026-03-17"


def test_neva_date_reads_english_month_names():
    assert state_legal._neva_date("Friday, 11 September, 2026") == "2026-09-11"


@pytest.mark.parametrize("label", ["", None, "no date here", "૧૧ અજ્ઞાત, ૨૦૨૬", "૩૧ સપ્ટેમ્બર, ૨૦૨૬"])
def test_neva_date_returns_empty_when_unreadable(label):
    assert state_legal._neva_date(label) == ""


class _FakeCrawler:
    def get_sessions(self, assembly):
        return [{"SessionCode": 9}] if assembly == 15 else []

    def get_dates(self, assembly, session):
        # Oldest first, to show that the entry doesn't trust the portal's order.
        return [{"SessionDate": "બુધવાર, ૯ સપ્ટેમ્બર, ૨૦૨૬", "SessionDateId": 1},
                {"SessionDate": "શુક્રવાર, ૧૧ સપ્ટેમ્બર, ૨૦૨૬", "SessionDateId": 3},
                {"SessionDate": "ગુરૂવાર, ૧૦ સપ્ટેમ્બર, ૨૦૨૬", "SessionDateId": 2}]

    def fetch_questions_for_date(self, assembly, session, date_id, seen):
        if date_id == 3:  # the newest sitting has no questions
            return []
        return [{"key": f"GJ|q|{assembly}|{session}|{date_id}|1", "question_number": "1",
                 "pdf_urls": ["https://cms.neva.gov.in/q.pdf"]}]


def test_neva_questions_take_the_date_of_their_sitting(monkeypatch):
    monkeypatch.setattr(state_legal, "_neva", lambda: _FakeCrawler())
    rows = state_legal._neva_questions()
    assert [r["sitting_date"] for r in rows] == ["2026-09-10"]
    assert rows[0]["pdf_url"] == "https://cms.neva.gov.in/q.pdf"
    assert rows[0]["key"].endswith("|2|1")


def test_neva_questions_return_nothing_when_no_sitting_has_questions(monkeypatch):
    class Empty(_FakeCrawler):
        def fetch_questions_for_date(self, *args):
            return []

    monkeypatch.setattr(state_legal, "_neva", lambda: Empty())
    assert state_legal._neva_questions() == []


def test_neva_depth_blanks_zero_counts(monkeypatch):
    result = {"latest_assembly": 15, "sessions_found": 9, "dates_found": 3, "members_count": 0,
              "questions_sample": 0, "portal_code": "gujarat"}
    monkeypatch.setattr(StateAssemblyCrawler, "probe_depth", lambda self, **kw: dict(result))
    depth = state_legal._neva_depth()
    assert depth["members_count"] == ""
    assert depth["questions_sample"] == 0  # a sample can be legitimately empty
    # The contract check rejects the blank, so a portal with no members is broken.
    source = BY_ID["state-assembly-probe-neva"]
    assert not all(depth.get(f) not in (None, "") for f in source.required)


# -- India Code ---------------------------------------------------------------


def _indiacode_get(self, path):
    if "/browse" in path:
        return BROWSE_PAGE_HTML
    assert path == "/handle/123456789/14547"
    return ACT_DETAIL_HTML


def test_indiacode_sentinel_is_the_act_record(monkeypatch):
    monkeypatch.setattr(IndiaCodeProbe, "_get", _indiacode_get)
    sentinel = state_legal._indiacode_sentinel()
    assert sentinel["instrument_type"] == "act"
    assert "Public Libraries" in sentinel["short_title"]
    rule = BY_ID["indiacode"].freshness[0]
    assert rule.title_of(sentinel) == sentinel["short_title"]


def test_indiacode_records_carry_the_required_fields_and_total(monkeypatch):
    monkeypatch.setattr(IndiaCodeProbe, "_get", _indiacode_get)
    source = BY_ID["indiacode"]
    records = source.fetch()
    assert any(all(r.get(f) not in (None, "") for f in source.required) for r in records)
    assert source.total() == 2


# -- Government orders -----------------------------------------------------------


def test_go_orders_map_the_first_document_of_each_row(monkeypatch):
    rows = [GoRow(cells=("MS-84", "24/12/2021"), files=(("511027", "E"), ("511028", "T"))),
            GoRow(cells=("RT-2", "23/12/2021"), files=(("511030", "E"),))]
    monkeypatch.setattr(GoIssueRegister, "run_control", lambda self, control=None: None)
    monkeypatch.setattr(GoIssueRegister, "search", lambda self, query: rows)
    orders = state_legal._go_orders()
    assert orders[0] == {"go_no": "MS-84", "order_date": "2021-12-24",
                         "document_url": "https://goir.ap.gov.in/dgo.ashx?gid=511027&fileType=E"}
    assert orders[1]["go_no"] == "RT-2"


def test_go_orders_run_the_control_first(monkeypatch):
    calls = []
    monkeypatch.setattr(GoIssueRegister, "run_control", lambda self, control=None: calls.append("control"))
    monkeypatch.setattr(GoIssueRegister, "search", lambda self, query: calls.append("search") or [])
    state_legal._go_orders()
    assert calls == ["control", "search"]


# -- CAG --------------------------------------------------------------------------


def test_cag_edition_names_the_fiscal_year_due_in_a_cycle():
    assert state_legal._cag_edition(2026) == "2023-24"
    assert state_legal._cag_edition(2001) == "1998-99"


def _edition_rule():
    return BY_ID["cag-finance-accounts"].freshness[0]


def _cag_result(today: date, years: list[str]):
    ctx = cs.Context(today=today, results={}, records={}, state={}, geo_fenced=frozenset())
    records = [{"year": y} for y in years]
    return _edition_rule().evaluate(source_id="cag-finance-accounts", records=records,
                                    newest="", count=None, ctx=ctx)


def test_cag_rule_expects_fy_2023_24_on_2026_10_05():
    # The adapter docstring (live-verified 2026-07-23) has FY 2023-24 as the latest Vol-II.
    assert _cag_result(date(2026, 10, 5), ["2023-24"]).ok
    missing = _cag_result(date(2026, 10, 5), ["2022-23"])
    assert not missing.ok and "2023-24" in missing.reason


def test_cag_rule_expects_fy_2024_25_from_april_2027():
    assert _cag_result(date(2027, 3, 30), ["2023-24"]).ok
    assert _cag_result(date(2027, 3, 31), ["2023-24"]).ok  # allowed until 1 April
    assert not _cag_result(date(2027, 4, 1), ["2023-24"]).ok
    assert _cag_result(date(2027, 4, 2), ["2024-25", "2023-24"]).ok
    late = _cag_result(date(2027, 4, 2), ["2023-24"])
    assert not late.ok and "2024-25" in late.reason


def test_cag_records_come_back_newest_first_and_vol_ii_only(monkeypatch):
    monkeypatch.setattr(CAGAccountsProbe, "discover",
                        lambda self, state: parse_finance_accounts_tab(ACCOUNTS_HTML, GJ))
    records = state_legal._cag_accounts()
    assert [r["year"] for r in records] == ["2024-25", "2023-24"]
    assert {r["volume"] for r in records} == {"II"}
    source = BY_ID["cag-finance-accounts"]
    assert all(r.get(f) not in (None, "") for r in records for f in source.required)


# -- Legacy DSpace -----------------------------------------------------------------


def test_dspace_item_gets_an_absolute_bitstream_url(monkeypatch):
    item = {"handle_id": "2263", "title": "Economic Survey Assam 2023-24",
            "bitstream_paths": ["/bitstream/123456789/2263/1/ecosurvey_2023-24.pdf"]}
    monkeypatch.setattr(LegacyDSpaceProbe, "fetch_item", lambda self, handle: item)
    got = state_legal._dspace_item("2263")
    assert got["bitstream_url"] == (
        "https://aladigitallibrary.in/bitstream/123456789/2263/1/ecosurvey_2023-24.pdf")


def test_dspace_item_without_a_bitstream_has_an_empty_url(monkeypatch):
    item = {"handle_id": "546", "title": "An Act", "bitstream_paths": []}
    monkeypatch.setattr(LegacyDSpaceProbe, "fetch_item", lambda self, handle: item)
    assert state_legal._dspace_item("546")["bitstream_url"] == ""


# -- Mines DMFT -----------------------------------------------------------------------


@pytest.mark.parametrize(("value", "expected"), [
    ("2026-09-30T11:36:47Z", "2026-09-30"),
    ("Wed, 30 Sep 2026 11:36:47 GMT", ""),  # the adapter returns an unparsable header as it came
    (None, ""),
    ("", ""),
])
def test_last_modified_date_reads_the_iso_prefix(value, expected):
    assert state_legal._last_modified_date({"source_last_modified": value}) == expected


def test_dmft_skips_report_pages_and_keeps_the_download_record(monkeypatch):
    seen = []

    def fake_download(self, opener, endpoint, *, dry_run):
        seen.append(endpoint.filename)
        return {"key": endpoint.filename, "filename": endpoint.filename, "url": endpoint.url,
                "source_last_modified": "2026-06-19T05:46:16Z"}

    monkeypatch.setattr(MinesDmftProbe, "_build_opener", lambda self: object())
    monkeypatch.setattr(MinesDmftProbe, "download_endpoint", fake_download)
    records = state_legal._dmft("odisha")
    assert seen == ["state_summary_data.json", "district_summary_data.json"]
    assert len(records) == 2
