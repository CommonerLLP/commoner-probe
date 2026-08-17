"""Tests for session discovery (commoner-probe sansad sessions).

Fixtures mirror the two live shapes (2026-08-14):

* LS `AllLoksabhaAndSessionDates` nests sessions under a `loksabha` block, with
  `sessionNo`, `sessionPeriod` (a LIST) and `dates` in dd/mm/yyyy. Live, it
  knows Lok Sabhas 13-18 — 1999 to 2026.
* RS `sessionDates` is a flat list of `session` + `sittingDates`, 271 entries,
  session 1 (1952) to 271 (2026).

No network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from commoner_probe.parliament_qa_api import SansadProbe


class FakeResponse:
    def __init__(self, payload=None, *, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


LS_PAYLOAD = [
    {"loksabha": "17", "sessions": [
        {"sessionNo": 1, "sessionPeriod": ["17/06/2019 to 06/08/2019"],
         "dates": ["17/06/2019", "18/06/2019", "06/08/2019"]},
        # The budget session is routinely split into two parts so committees
        # can examine the Demands for Grants in the recess. It happens every
        # year, not only in 2020, and a consumer reading sessionPeriod[0]
        # silently loses the second half.
        {"sessionNo": 3, "sessionPeriod": ["31/01/2020 to 11/02/2020",
                                           "02/03/2020 to 23/03/2020"],
         "dates": ["31/01/2020", "02/03/2020", "23/03/2020"]},
    ]},
    {"loksabha": "18", "sessions": [
        {"sessionNo": 1, "sessionPeriod": ["24/06/2024 to 02/07/2024"],
         "dates": ["24/06/2024", "02/07/2024"]},
    ]},
]

RS_PAYLOAD = [
    {"session": 271, "sittingDates": ["20/07/2026", "13/08/2026"]},
    {"session": 1, "sittingDates": ["13/05/1952", "14/08/1952"]},
]


class FakeSessionsSession:
    def __init__(self, ls=LS_PAYLOAD, rs=RS_PAYLOAD):
        self.ls, self.rs = ls, rs
        self.urls: list[str] = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        if "api_rs" in url:
            return FakeResponse(self.rs)
        return FakeResponse(self.ls)


def _probe(tmp_path: Path, session=None) -> SansadProbe:
    probe = SansadProbe(None, tmp_path, sleep=0)
    probe.session = session or FakeSessionsSession()
    return probe


def test_ls_sessions_are_scoped_to_a_term(tmp_path):
    got = _probe(tmp_path).session_catalog("ls", loksabha=17)
    assert [e.session for e in got] == [1, 3]
    assert all(e.loksabha == 17 for e in got)


def test_omitting_the_term_lists_every_lok_sabha(tmp_path):
    got = _probe(tmp_path).session_catalog("ls")
    assert {(e.loksabha, e.session) for e in got} == {(17, 1), (17, 3), (18, 1)}


def test_a_split_session_keeps_both_periods(tmp_path):
    """sessionPeriod is a list; the budget session is split most years."""
    got = _probe(tmp_path).session_catalog("ls", loksabha=17)
    split = next(e for e in got if e.session == 3)
    assert len(split.periods) == 2
    assert split.first_sitting == "2020-01-31"
    assert split.last_sitting == "2020-03-23"


def test_dates_are_normalised_to_iso_and_sorted(tmp_path):
    got = _probe(tmp_path).session_catalog("ls", loksabha=17)
    first = next(e for e in got if e.session == 1)
    assert first.sitting_dates == ["2019-06-17", "2019-06-18", "2019-08-06"]
    assert first.sittings == 3


def test_rs_has_no_term_and_is_numbered_continuously(tmp_path):
    got = _probe(tmp_path).session_catalog("rs")
    assert all(e.loksabha is None for e in got)
    assert [e.session for e in got] == [1, 271]
    assert next(e for e in got if e.session == 1).first_sitting == "1952-05-13"


def test_both_houses_share_one_output_schema(tmp_path):
    probe = _probe(tmp_path)
    ls = probe.session_catalog("ls", loksabha=17)[0].as_dict()
    rs = probe.session_catalog("rs")[0].as_dict()
    assert set(ls) == set(rs)


def test_an_unparseable_date_is_dropped_not_guessed(tmp_path):
    payload = [{"loksabha": "17", "sessions": [
        {"sessionNo": 1, "sessionPeriod": [], "dates": ["17/06/2019", "not-a-date", ""]}]}]
    got = _probe(tmp_path, FakeSessionsSession(ls=payload)).session_catalog("ls", loksabha=17)
    assert got[0].sitting_dates == ["2019-06-17"]


def test_terms_come_from_the_catalogue_not_a_hardcoded_range(tmp_path):
    assert _probe(tmp_path).ls_portal_terms() == [17, 18]


def test_a_term_with_no_block_returns_nothing_rather_than_another_terms_sessions(tmp_path):
    """The endpoint returns every term; selecting a missing one must not fall through."""
    assert _probe(tmp_path).session_catalog("ls", loksabha=13) == []


@pytest.mark.parametrize("house", ["ls", "rs"])
def test_the_catalogue_is_not_a_coverage_claim(tmp_path, house):
    """Documented invariant: entries carry dates, never a record count.

    RS 264 is in the live catalogue with sitting dates and returns zero
    questions. Nothing here may be read as "questions exist for this session".
    """
    got = _probe(tmp_path).session_catalog(house)
    assert got
    assert all(not hasattr(e, "records") for e in got)
    assert all("records" not in e.as_dict() for e in got)
