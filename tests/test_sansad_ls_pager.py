"""Tests for the LS portal question-list pager (REQ-0040).

Fixtures mirror the live-verified contract (2026-07-23, zero-hour):

* ``pageNo`` is 1-indexed — ``pageNo=0`` answers HTTP 500, not an empty list.
* ``lkNo=18``/``sessionNumber=8``/``pageSize=100``: pages 1-10 return 100 rows
  each, page 11 returns 0 — 1,000 rows, reconciling with the envelope's
  ``totalRecordSize``.

No network.
"""

from __future__ import annotations

import pytest

from commoner_probe.sansad import SansadProbe

TOTAL_RECORD_SIZE = 1000
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
    return {
        "quesNo": qno,
        "type": "STARRED",
        "date": "23.07.2026",
        "member": ["Dr. Shashi Tharoor"],
        "sessionNo": "8",
        "questionText": None,
        "answerText": None,
        "questionsFilePath": f"https://sansad.in/q/{qno}.pdf",
    }


class FakeQuestionListSession:
    """Serves the recorded session: 10 full pages, then an empty one.

    Rejects ``pageNo=0`` the way the live portal does, so a missing guard
    surfaces as the same server error the caller would really hit.
    """

    def __init__(self, total: int = TOTAL_RECORD_SIZE):
        self.total = total
        self.pages_requested: list[int] = []

    def get(self, url, params=None, headers=None, timeout=None, **kwargs):
        assert "qetAllQuestions" in url, f"unrouted url: {url}"
        page_no = int((params or {})["pageNo"])
        page_size = int((params or {})["pageSize"])
        self.pages_requested.append(page_no)
        if page_no < 1:
            return FakeResponse({"status": 500, "error": "Internal Server Error"}, status=500)
        start = (page_no - 1) * page_size
        rows = [_row(qno) for qno in range(start + 1, min(start + page_size, self.total) + 1)]
        return FakeResponse([{"listOfQuestions": rows, "totalRecordSize": self.total}])


def _probe(tmp_path, session):
    probe = SansadProbe(None, tmp_path / "out", sleep=0)
    probe.session = session
    return probe


class TestPageIndexGuard:
    @pytest.mark.parametrize("page_no", [0, -1])
    def test_sub_one_page_raises_before_any_request(self, tmp_path, page_no):
        session = FakeQuestionListSession()
        probe = _probe(tmp_path, session)
        with pytest.raises(ValueError) as excinfo:
            probe.ls_question_list_page(18, 8, page_no)
        assert "1-indexed" in str(excinfo.value)
        assert session.pages_requested == [], "guard must reject before hitting the portal"

    def test_page_one_is_accepted(self, tmp_path):
        session = FakeQuestionListSession()
        probe = _probe(tmp_path, session)
        rows = probe.ls_question_list_page(18, 8, 1)
        assert len(rows) == PAGE_SIZE
        assert session.pages_requested == [1]


class TestPaginate:
    def test_walks_every_page_and_stops_on_the_empty_one(self, tmp_path):
        session = FakeQuestionListSession()
        probe = _probe(tmp_path, session)
        rows = list(probe.paginate_ls_question_list(18, 8, PAGE_SIZE))
        assert len(rows) == TOTAL_RECORD_SIZE
        assert session.pages_requested == list(range(1, 12)), "pages 1-10 full, 11 empty, then stop"
        assert [r["quesNo"] for r in rows] == list(range(1, TOTAL_RECORD_SIZE + 1))

    def test_starts_at_one_not_zero(self, tmp_path):
        session = FakeQuestionListSession(total=1)
        probe = _probe(tmp_path, session)
        list(probe.paginate_ls_question_list(18, 8, PAGE_SIZE))
        assert session.pages_requested[0] == 1

    def test_is_lazy(self, tmp_path):
        """A generator, so a caller with --max-records stops paging early."""
        session = FakeQuestionListSession()
        probe = _probe(tmp_path, session)
        pager = probe.paginate_ls_question_list(18, 8, PAGE_SIZE)
        assert session.pages_requested == []
        next(pager)
        assert session.pages_requested == [1]
