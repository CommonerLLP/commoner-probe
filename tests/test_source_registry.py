"""Tests for scripts/source_registry: the registry entries and their helpers.

Every test uses canned data; none touches the network.
"""

from __future__ import annotations

import sys
from pathlib import Path

from commoner_probe.committee_report_api import CommitteeProbe
from tests.conftest import load_script

cs = load_script("check_sources")

_SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from source_registry import SOURCES, parliament  # noqa: E402

BY_ID = {s.id: s for s in SOURCES}


def test_ids_are_unique_and_order_resolves():
    ids = [s.id for s in SOURCES]
    assert len(ids) == len(set(ids))
    assert len(cs.order_sources(SOURCES)) == len(SOURCES)


def test_parliament_entries_sort_calendar_first():
    assert {"sessions-ls", "committees-ls", "committees-rs"} <= BY_ID.keys()
    order = [s.id for s in cs.order_sources(SOURCES)]
    assert order.index("sessions-ls") < order.index("committees-ls") < order.index("committees-rs")


def test_only_committees_rs_skips_robots_for_its_document():
    assert BY_ID["committees-rs"].document_respect_robots is False
    assert all(s.document_respect_robots for s in SOURCES if s.id != "committees-rs")


def test_ls_date_falls_back_in_order():
    assert parliament._ls_date({"dateOfPresentation": "17-Mar-2026"}) == "2026-03-17"
    both = {"PresentedInLS": "18-Mar-2026", "dateOfPresentation": "17-Mar-2026"}
    assert parliament._ls_date(both) == "2026-03-18"
    assert parliament._ls_date({}) == ""


def test_rs_record_date_reads_presentation_then_adoption():
    record_date = BY_ID["committees-rs"].record_date
    assert record_date({"dateOfPresentation": "07/08/2026"}) == "2026-08-07"
    assert record_date({"dateOfAdoption": "09/08/2026"}) == "2026-08-09"
    assert record_date({}) == ""


def test_committees_rs_concatenates_pages(monkeypatch):
    calls = []

    def fake_rs_page(self, mst, page, size=200):
        calls.append((mst, page, size))
        return {"records": [{"reportNo": mst}]} if mst != 15 else {"records": None}

    monkeypatch.setattr(CommitteeProbe, "rs_page", fake_rs_page)
    assert parliament._committees_rs() == [{"reportNo": 14}, {"reportNo": 19}]
    assert calls == [(14, 1, 5), (15, 1, 5), (19, 1, 5)]


def test_committees_ls_concatenates_pages(monkeypatch):
    calls = []

    def fake_ls_page(self, code, page, size=200):
        calls.append((code, page, size))
        return {"records": [{"reportNo": code}]}

    monkeypatch.setattr(CommitteeProbe, "ls_page", fake_ls_page)
    assert parliament._committees_ls() == [{"reportNo": 12}, {"reportNo": 7}, {"reportNo": 11}]
    assert calls == [(12, 1, 5), (7, 1, 5), (11, 1, 5)]
