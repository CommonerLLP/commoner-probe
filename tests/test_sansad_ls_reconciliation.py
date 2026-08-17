"""The LS pager reconciles its read against the total the portal declares.

`totalRecordSize` is a SESSION total. Measured live 2026-08-17 against
sansad.in: lkNo 18 session 8 declares 4,500 at pageSize 1, 100 and 1000, and
page 1 and page 2 both declare it; lkNo 13 session 8 declares 5,082 and session
9 declares 8,628. It does not echo the page size, and an earlier note in
`parliament_qa_api` said it did.

That makes it the completeness check this pager never had. Two truncations look
exactly like a finished session without it:

* a portal that stops serving mid-session — the walk sees an empty page and
  calls the short read done;
* a portal that ignores `pageNo` and re-serves page one — the rows pile up and
  the walk never ends.

No network.
"""

from __future__ import annotations

import json

from commoner_probe.parliament_qa_api import SansadProbe

PAGE_SIZE = 100


class FakeResponse:
    def __init__(self, payload=None, *, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _row(qno: int) -> dict:
    return {"quesNo": qno, "type": "STARRED", "date": "23.07.2026", "sessionNo": "8"}


class TruncatingSession:
    """Declares `declared` records and serves only `serves` of them.

    The live shape this mirrors: the portal answers a page, then answers the
    next one empty, while its own envelope still claims the full session.
    """

    def __init__(self, *, declared: int, serves: int):
        self.declared = declared
        self.serves = serves
        self.pages_requested: list[int] = []

    def get(self, url, params=None, headers=None, timeout=None, **kwargs):
        page_no = int((params or {})["pageNo"])
        page_size = int((params or {})["pageSize"])
        self.pages_requested.append(page_no)
        start = (page_no - 1) * page_size
        rows = [_row(q) for q in range(start + 1, min(start + page_size, self.serves) + 1)]
        return FakeResponse([{"listOfQuestions": rows, "totalRecordSize": self.declared}])


class PageIgnoringSession:
    """Ignores `pageNo` and serves page one forever, as this API is known to
    mishandle the parameter (`pageNo=0` answers HTTP 500)."""

    def __init__(self, *, declared: int = 1000, page_size: int = PAGE_SIZE):
        self.declared = declared
        self.page_size = page_size
        self.pages_requested: list[int] = []

    def get(self, url, params=None, headers=None, timeout=None, **kwargs):
        self.pages_requested.append(int((params or {})["pageNo"]))
        rows = [_row(q) for q in range(1, self.page_size + 1)]
        return FakeResponse([{"listOfQuestions": rows, "totalRecordSize": self.declared}])


class PageIgnoringNoTotalSession(PageIgnoringSession):
    """Repeats page one AND declares no total, which is the older envelope."""

    def get(self, url, params=None, headers=None, timeout=None, **kwargs):
        self.pages_requested.append(int((params or {})["pageNo"]))
        rows = [_row(q) for q in range(1, self.page_size + 1)]
        return FakeResponse([{"listOfQuestions": rows}])


class NoTotalSession:
    """Serves rows and omits `totalRecordSize` — an older shape, and not the
    same statement as a declared zero."""

    def __init__(self, *, serves: int = 100):
        self.serves = serves
        self.pages_requested: list[int] = []

    def get(self, url, params=None, headers=None, timeout=None, **kwargs):
        page_no = int((params or {})["pageNo"])
        page_size = int((params or {})["pageSize"])
        self.pages_requested.append(page_no)
        start = (page_no - 1) * page_size
        rows = [_row(q) for q in range(start + 1, min(start + page_size, self.serves) + 1)]
        return FakeResponse([{"listOfQuestions": rows}])


def _probe(tmp_path, session):
    probe = SansadProbe(None, tmp_path / "out", sleep=0)
    probe.session = session
    return probe


class TestDeclaredTotal:
    def test_totals_carries_the_declared_session_total(self, tmp_path):
        session = TruncatingSession(declared=250, serves=250)
        probe = _probe(tmp_path, session)
        totals: dict = {}
        rows = list(probe.paginate_ls_question_list(18, 8, PAGE_SIZE, totals=totals))
        assert len(rows) == 250
        assert totals["declared"] == 250
        assert totals["yielded"] == 250
        assert totals["complete"] is True

    def test_a_short_read_is_reported_as_incomplete(self, tmp_path):
        """The portal claims 250 and serves 100. Without this the walk sees an
        empty page and reports a finished session."""
        session = TruncatingSession(declared=250, serves=100)
        probe = _probe(tmp_path, session)
        totals: dict = {}
        rows = list(probe.paginate_ls_question_list(18, 8, PAGE_SIZE, totals=totals))
        assert len(rows) == 100
        assert totals["declared"] == 250
        assert totals["yielded"] == 100
        assert totals["complete"] is False

    def test_an_omitted_total_is_not_read_as_zero(self, tmp_path):
        """A missing field is not a claim. It must not make a good read look short."""
        session = NoTotalSession(serves=100)
        probe = _probe(tmp_path, session)
        totals: dict = {}
        rows = list(probe.paginate_ls_question_list(18, 8, PAGE_SIZE, totals=totals))
        assert len(rows) == 100
        assert totals["declared"] is None
        assert totals["complete"] is None, "no total means no verdict, not a pass and not a fail"

    def test_totals_is_optional(self, tmp_path):
        session = TruncatingSession(declared=100, serves=100)
        probe = _probe(tmp_path, session)
        assert len(list(probe.paginate_ls_question_list(18, 8, PAGE_SIZE))) == 100


class TestRepeatedPageGuard:
    def test_a_portal_that_ignores_pageno_stops_the_walk(self, tmp_path):
        """Page two returns page one's rows. The walk must stop rather than
        yield the same page until it reaches the declared total."""
        session = PageIgnoringSession(declared=1000)
        probe = _probe(tmp_path, session)
        totals: dict = {}
        rows = list(probe.paginate_ls_question_list(18, 8, PAGE_SIZE, totals=totals))
        assert len(rows) == PAGE_SIZE, "only the first page is real"
        assert len(session.pages_requested) <= 2, "stop on the first repeat, not later"
        assert totals["complete"] is False
        assert totals["repeated_page"] is True

    def test_a_repeat_is_incomplete_even_with_no_declared_total(self, tmp_path):
        """A repeat is direct evidence of truncation. Reading `complete` as None
        because the envelope declared no total files a knowingly truncated
        window as finished, and a finished window is skipped on every later run."""
        session = PageIgnoringNoTotalSession()
        probe = _probe(tmp_path, session)
        totals: dict = {}
        rows = list(probe.paginate_ls_question_list(18, 8, PAGE_SIZE, totals=totals))
        assert len(rows) == PAGE_SIZE
        assert totals["declared"] is None
        assert totals["repeated_page"] is True
        assert totals["complete"] is False

    def test_a_repeat_with_no_total_leaves_the_window_suspect(self, tmp_path):
        probe = _enumerating_probe(tmp_path, PageIgnoringNoTotalSession())
        probe.probe_ls_sessions(
            set(), loksabha=18, sessions=[8], from_date=None, to_date=None,
            qtype_filter=None, max_records=None, download=False, page_size=PAGE_SIZE)
        assert _windows(probe.out_dir)[-1]["status"] == "suspect"

    def test_distinct_pages_are_not_mistaken_for_a_repeat(self, tmp_path):
        session = TruncatingSession(declared=300, serves=300)
        probe = _probe(tmp_path, session)
        totals: dict = {}
        rows = list(probe.paginate_ls_question_list(18, 8, PAGE_SIZE, totals=totals))
        assert len(rows) == 300
        assert totals.get("repeated_page") is False


class FakeRoster:
    def lookup(self, name):
        return None


def _windows(out) -> list[dict]:
    path = out / "_windows.jsonl"
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _enumerating_probe(tmp_path, session) -> SansadProbe:
    probe = SansadProbe(None, tmp_path / "out", sleep=0, enumerate_all=True)
    probe.session = session
    probe._roster = FakeRoster()
    return probe


class TestTheWindowCarriesTheShortfall:
    """A truncated session must not be filed `complete`.

    This is the whole point of reconciling. A window recorded complete is
    skipped on every later run, so a short read that looks finished is
    permanent.
    """

    def _run(self, probe):
        return probe.probe_ls_sessions(
            set(), loksabha=18, sessions=[8], from_date=None, to_date=None,
            qtype_filter=None, max_records=None, download=False, page_size=PAGE_SIZE)

    def test_a_short_session_is_left_suspect(self, tmp_path):
        session = TruncatingSession(declared=250, serves=100)
        probe = _enumerating_probe(tmp_path, session)
        added = self._run(probe)
        assert added == 100
        window = _windows(probe.out_dir)[-1]
        assert window["status"] == "suspect"

    def test_a_complete_session_is_filed_complete(self, tmp_path):
        session = TruncatingSession(declared=100, serves=100)
        probe = _enumerating_probe(tmp_path, session)
        assert self._run(probe) == 100
        assert _windows(probe.out_dir)[-1]["status"] == "complete"

    def test_a_session_declaring_no_total_is_filed_complete(self, tmp_path):
        """No declared total is not a shortfall. Older fixtures and older
        sessions omit the field, and they are not all truncated."""
        session = NoTotalSession(serves=100)
        probe = _enumerating_probe(tmp_path, session)
        assert self._run(probe) == 100
        assert _windows(probe.out_dir)[-1]["status"] == "complete"
