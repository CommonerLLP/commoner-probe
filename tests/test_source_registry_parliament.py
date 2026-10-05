"""Tests for the Parliament registry entries: their date functions and record mappers.

Every test uses canned data; none touches the network. Record shapes come from
the adapters' own test fixtures and live responses.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from commoner_probe.attendance_json_api import AttendanceProbe
from commoner_probe.parliament_qa_api import SansadProbe, SessionEntry
from tests.conftest import load_script

cs = load_script("check_sources")

_SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from source_registry import SOURCES, parliament  # noqa: E402

BY_ID = {s.id: s for s in SOURCES}

NEW_IDS = (
    "sessions-rs", "sansad-ls", "sansad-rs", "sansad-tabled", "questions-list-ls",
    "questions-list-rs", "debates-ls", "debates-rs", "bills-ls", "bills-rs",
    "attendance-ls", "committees-members-ls", "prs-report-summaries", "prs-vital-stats",
    "prs-bill-track", "prs-mp-track-ls", "prs-mp-track-rs",
)


class FrozenDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 10, 5)


def _entry(house: str, session: int, dates: list[str], loksabha: int | None = None) -> SessionEntry:
    return SessionEntry(house=house, loksabha=loksabha, session=session, periods=[],
                        sitting_dates=dates)


def _ctx(today: date, results=None, records=None):
    return cs.Context(today=today, results=results or {}, records=records or {},
                      state={}, geo_fenced=frozenset())


def test_new_entries_are_registered_and_resolve():
    assert set(NEW_IDS) <= BY_ID.keys()
    assert len(cs.order_sources(SOURCES)) == len(SOURCES)


def test_second_house_entries_trail_their_first_house():
    pairs = {"sansad-rs": "sansad-ls", "questions-list-rs": "questions-list-ls",
             "debates-rs": "debates-ls", "bills-rs": "bills-ls",
             "prs-mp-track-rs": "prs-mp-track-ls"}
    for second, first in pairs.items():
        lags = [r for r in BY_ID[second].freshness if isinstance(r, cs.SiblingLag)]
        assert [(r.other_id, r.max_days) for r in lags] == [(first, 60)], second


def test_sessions_rs_uses_a_200_day_age_limit():
    assert BY_ID["sessions-rs"].freshness == (cs.MaxAge(200),)


def test_recent_sittings_are_newest_first_and_skip_today_and_later():
    entries = [
        _entry("ls", 7, ["2026-01-28", "2026-01-29"], loksabha=18),
        _entry("ls", 8, ["2026-07-20", "2026-10-05", "2026-10-06"], loksabha=18),
    ]
    got = parliament._recent_sittings(entries, "2026-10-05", 3)
    assert [(e.session, d) for e, d in got] == [(8, "2026-07-20"), (7, "2026-01-29"), (7, "2026-01-28")]
    assert len(parliament._recent_sittings(entries, "2026-10-05", 2)) == 2


def test_session_rows_skip_sessions_without_sittings():
    rows = parliament._session_rows([
        _entry("rs", 270, ["2026-01-28", "2026-04-18"]),
        _entry("rs", 271, []),
    ])
    assert rows == [{"session": 270, "first_sitting": "2026-01-28", "last_sitting": "2026-04-18"}]


def test_sessions_rs_reads_the_last_five_sessions(monkeypatch):
    entries = [_entry("rs", n, [f"2026-0{n % 9 + 1}-01"]) for n in range(1, 8)]
    monkeypatch.setattr(SansadProbe, "session_catalog", lambda self, house, loksabha=None: entries)
    rows = parliament._sessions_rs()
    assert [r["session"] for r in rows] == [3, 4, 5, 6, 7]


def test_bill_date_reads_the_introduction_date_and_tolerates_null():
    assert parliament._bill_date({"introduced_date": "2026-08-10"}) == "2026-08-10"
    assert parliament._bill_date({"introduced_date": None}) == ""


def test_dmy_to_iso_accepts_calendar_dates_only():
    assert parliament._dmy_to_iso("28/01/2026") == "2026-01-28"
    assert parliament._dmy_to_iso("31/02/2026") == ""
    assert parliament._dmy_to_iso("2026-01-28") == ""
    assert parliament._dmy_to_iso(None) == ""


def test_mptrack_date_reads_the_period_end_from_the_note():
    # The note text is copied from the MP Track CSV fixture in tests/test_prs.py.
    row = {"mp_note": "Data corresponds to the period from 01-06-2019 to 10-02-2024."}
    assert parliament._mptrack_date(row) == "2024-02-10"
    assert parliament._mptrack_date({"mp_note": None}) == ""
    assert parliament._mptrack_date({"mp_note": "No data"}) == ""


def test_committee_edition_is_the_formation_year():
    assert parliament._committee_edition({"committeeFormationDate": "2025-09-26"}) == "2025"
    assert parliament._committee_edition({}) == ""


def test_committee_membership_expects_the_new_edition_after_the_grace_period():
    rule = BY_ID["committees-members-ls"].freshness[0]
    records = [{"committeeFormationDate": "2025-09-26"}]
    kwargs = {"source_id": "committees-members-ls", "records": records, "newest": "2025-09-26",
              "count": None}
    # Before the end of November 2026 the 2025 committees are still current.
    assert rule.evaluate(ctx=_ctx(date(2026, 10, 5)), **kwargs).ok
    # After it, the 2026 committees are overdue.
    result = rule.evaluate(ctx=_ctx(date(2026, 12, 15)), **kwargs)
    assert not result.ok and "2026" in result.reason


def test_attendance_dates_each_row_by_the_sessions_last_past_sitting(monkeypatch):
    # Catalogue and row shapes copied from tests/test_attendance.py.
    catalog = [{"loksabha": 18, "sessions": [
        {"sessionNo": 5, "dates": ["22/07/2025", "26/07/2025"]},
        {"sessionNo": 8, "dates": ["20/07/2026", "13/08/2026", "01/11/2026"]},
        {"sessionNo": 9, "dates": ["01/12/2026"]},
    ]}]
    rows = [{"mpsno": 4455, "memberName": "Sanjay Jaiswal", "constituency": "Paschim Champaran",
             "state": "Bihar", "stateCode": "BR", "signedDaysCount": 19, "division": "17"}]
    asked = []

    def fake_rows(self, loksabha, session_no):
        asked.append((loksabha, session_no))
        return rows if session_no == 5 else []

    monkeypatch.setattr(parliament, "date", FrozenDate)
    monkeypatch.setattr(AttendanceProbe, "session_catalog", lambda self: catalog)
    monkeypatch.setattr(AttendanceProbe, "fetch_session_attendance", fake_rows)
    records = parliament._attendance_ls()
    # Session 9 hasn't started and session 8 has no register yet, so it falls back to 5.
    assert asked == [(18, 8), (18, 5)]
    assert [r["register_to"] for r in records] == ["2025-07-26"]
    assert records[0]["key"] == "ATTENDANCE|18|5|4455"
    assert BY_ID["attendance-ls"].record_date(records[0]) == "2025-07-26"


def test_sansad_ls_falls_back_to_the_previous_session_when_a_session_has_no_rows(monkeypatch):
    entries = [_entry("ls", 7, ["2026-01-28"], loksabha=18), _entry("ls", 8, ["2026-07-20"], loksabha=18)]
    row = {"quesNo": 360, "subjects": "Nuclear Power Generation", "member": ["Dr. Prabha Mallikarjun"],
           "ministry": "ATOMIC ENERGY", "type": "STARRED", "date": "12.08.2026",
           "questionsFilePath": "https://sansad.in/getFile/lsapps/loksabhaquestions/annex/188/AS360.pdf"}
    asked = []

    def fake_page(self, loksabha, session_number, page_no, page_size=100):
        asked.append((loksabha, session_number))
        return [row] if session_number == 7 else []

    monkeypatch.setattr(parliament, "date", FrozenDate)
    monkeypatch.setattr(SansadProbe, "session_catalog", lambda self, house, loksabha=None: entries)
    monkeypatch.setattr(SansadProbe, "ls_portal_terms", lambda self: [18])
    monkeypatch.setattr(SansadProbe, "ls_question_list_page", fake_page)
    records = parliament._sansad_ls()
    assert asked == [(18, 8), (18, 7)]
    assert records[0]["date"] == "2026-08-12"
    assert records[0]["pdf_url"].endswith("AS360.pdf")


def test_sansad_rs_maps_rows_through_the_adapter(monkeypatch):
    entries = [_entry("rs", 271, ["2026-07-20", "2026-08-13"])]
    row = {"mp_code": 74, "qslno": 328139, "qtitle": "Ayushman Bharat", "qtype": "STARRED   ",
           "ans_date": "11.08.2026", "qno": 245.0, "name": "Kapil Sibal",
           "min_name": "HEALTH AND FAMILY WELFARE", "files": "https://rsdoc.nic.in/Question/q.pdf"}
    monkeypatch.setattr(parliament, "date", FrozenDate)
    monkeypatch.setattr(SansadProbe, "session_catalog", lambda self, house, loksabha=None: entries)
    monkeypatch.setattr(SansadProbe, "rs_search_session", lambda self, ses, ministry: [row])
    records = parliament._sansad_rs()
    assert BY_ID["sansad-rs"].record_date(records[0]) == "2026-08-11"
    assert records[0]["pdf_url"] == "https://rsdoc.nic.in/Question/q.pdf"
    assert records[0]["key"]
