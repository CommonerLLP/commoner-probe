"""Tests for scripts/source_registry/construction.py: the Karnataka RERA freshness entry.

Every test uses the trimmed fixture and a fake session; none touches the network.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from commoner_probe.http_client import StdlibResponse
from tests.conftest import load_script

cs = load_script("check_sources")

_SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from source_registry import SOURCES, construction  # noqa: E402

BY_ID = {s.id: s for s in SOURCES}
FIXTURE = Path(__file__).parent / "fixtures" / "rera_karnataka" / "project_view_details_trimmed.html"
HTML = FIXTURE.read_text(encoding="utf-8")


class _Session:
    def __init__(self, status=200, body=HTML.encode("utf-8")):
        self.status, self.body, self.calls = status, body, []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return StdlibResponse(url, self.status, self.body, {"Content-Type": "text/html"})


def test_the_entry_is_registered_with_the_planned_rules():
    source = BY_ID["rera-karnataka"]
    assert source.host == "rera.karnataka.gov.in"
    assert source.required == ("key", "promoter", "project_name", "district")
    assert source.document is None
    assert source.freshness == (cs.MaxAge(days=30), cs.CountFloor(0.95))


def test_fetch_posts_bengaluru_urban_and_parses_the_listing(monkeypatch):
    session = _Session()
    monkeypatch.setattr(construction, "make_session", lambda: session)
    rows = construction._rera_karnataka_rows()
    assert len(rows) == 20
    (url, kwargs), = session.calls
    assert url == "https://rera.karnataka.gov.in/projectViewDetails"
    form = parse_qs(kwargs["data"], keep_blank_values=True)
    assert form["district"] == ["Bengaluru Urban"]
    assert form["btn1"] == ["Search"]


def test_fetch_raises_a_message_the_checker_reads_as_http_error(monkeypatch):
    monkeypatch.setattr(construction, "make_session", lambda: _Session(status=503, body=b""))
    with pytest.raises(RuntimeError) as exc:
        construction._rera_karnataka_rows()
    assert cs.classify_exception(exc.value)[:2] == (cs.HTTP_ERROR, 503)


def test_record_date_is_the_approved_on_date():
    source = BY_ID["rera-karnataka"]
    rows = construction.parse_listing(HTML)
    newest = cs.newest_date(rows, source.record_date, date(2026, 11, 11))
    assert newest and newest <= "2026-11-11"
    assert source.record_date({"approved_on": None}) == ""


# --- Haryana ---------------------------------------------------------------

HR_FIXTURE = Path(__file__).parent / "fixtures" / "rera_haryana" / "registered_projects_2_trimmed.html"
HR_HTML = HR_FIXTURE.read_text(encoding="utf-8")


class _GetSession:
    def __init__(self, status=200, body=HR_HTML.encode("utf-8")):
        self.status, self.body, self.calls = status, body, []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return StdlibResponse(url, self.status, self.body, {"Content-Type": "text/html"})


def test_the_haryana_entry_is_registered_with_the_planned_rules():
    source = BY_ID["rera-haryana"]
    assert source.host == "haryanarera.gov.in"
    assert source.required == ("key", "promoter", "project_name", "district")
    assert source.document is None
    assert source.freshness == (cs.MaxAge(days=60), cs.CountFloor(0.95))


def test_haryana_fetch_gets_the_gurugram_listing_and_parses_it(monkeypatch):
    session = _GetSession()
    monkeypatch.setattr(construction, "make_session", lambda: session)
    rows = construction._rera_haryana_rows()
    assert len(rows) == 12
    (url, _), = session.calls
    assert url == "https://haryanarera.gov.in/admincontrol/registered_projects/2"


def test_haryana_fetch_requests_no_quarterly_progress_link(monkeypatch):
    session = _GetSession()
    monkeypatch.setattr(construction, "make_session", lambda: session)
    construction._rera_haryana_rows()
    assert all("print_quarterly_schedules" not in url for url, _ in session.calls)


def test_haryana_fetch_raises_a_message_the_checker_reads_as_http_error(monkeypatch):
    monkeypatch.setattr(construction, "make_session", lambda: _GetSession(status=503, body=b""))
    with pytest.raises(RuntimeError) as exc:
        construction._rera_haryana_rows()
    assert cs.classify_exception(exc.value)[:2] == (cs.HTTP_ERROR, 503)


def test_haryana_record_date_is_the_certificate_date():
    source = BY_ID["rera-haryana"]
    rows = construction.haryana.parse_listing(HR_HTML)
    newest = cs.newest_date(rows, source.record_date, date(2026, 11, 11))
    assert newest == "2026-07-03"
    assert source.record_date({"approved_on": None}) == ""


# --- Tamil Nadu ------------------------------------------------------------

TN_FIXTURE = Path(__file__).parent / "fixtures" / "rera_tamil_nadu" / "registered_building_tn_trimmed.html"
TN_HTML = TN_FIXTURE.read_text(encoding="utf-8")


def test_the_tamil_nadu_entry_is_registered_with_the_planned_rules():
    source = BY_ID["rera-tamil-nadu"]
    assert source.host == "rera.tn.gov.in"
    assert source.required == ("key", "promoter", "approved_on")
    assert source.document is None
    # No CountFloor: the list holds one year, so its count restarts every January.
    assert source.freshness == (cs.MaxAge(days=30),)


def test_tamil_nadu_fetch_gets_the_current_year_building_list(monkeypatch):
    session = _GetSession(body=TN_HTML.encode("utf-8"))
    monkeypatch.setattr(construction, "make_session", lambda: session)
    rows = construction._rera_tamil_nadu_rows()
    assert len(rows) == 10
    (url, _), = session.calls
    assert url == "https://rera.tn.gov.in/registered-building/tn"


def test_tamil_nadu_fetch_requests_no_pdf_or_row_link(monkeypatch):
    session = _GetSession(body=TN_HTML.encode("utf-8"))
    monkeypatch.setattr(construction, "make_session", lambda: session)
    construction._rera_tamil_nadu_rows()
    assert all(not url.lower().endswith(".pdf") and "/uploads/" not in url for url, _ in session.calls)


def test_tamil_nadu_fetch_raises_a_message_the_checker_reads_as_http_error(monkeypatch):
    monkeypatch.setattr(construction, "make_session", lambda: _GetSession(status=503, body=b""))
    with pytest.raises(RuntimeError) as exc:
        construction._rera_tamil_nadu_rows()
    assert cs.classify_exception(exc.value)[:2] == (cs.HTTP_ERROR, 503)


def test_tamil_nadu_record_date_is_the_registration_date():
    source = BY_ID["rera-tamil-nadu"]
    rows = construction.tamil_nadu.parse_listing(TN_HTML)
    newest = cs.newest_date(rows, source.record_date, date(2026, 11, 11))
    assert newest == "2026-11-10"
    assert source.record_date({"approved_on": None}) == ""
