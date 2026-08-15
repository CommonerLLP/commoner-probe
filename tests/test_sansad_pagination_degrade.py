"""Page-size degradation in paginate_ls_question_list.

Written after LS 13 lost six sessions to an oversized page request, and after
the first fix for that looped 59 times on one offset.
"""
import pytest

from commoner_probe.sansad import SansadProbe, _halve_to_multiple


class _Portal:
    """A portal that refuses pages above `max_size` and can hold a dead range."""

    def __init__(self, total, max_size, dead=()):
        self.total, self.max_size, self.dead = total, max_size, set(dead)
        self.calls = []

    def page(self, loksabha, session_number, page_no, page_size):
        self.calls.append((page_no, page_size))
        if len(self.calls) > 500:
            raise AssertionError("runaway: the walk is not making forward progress")
        if page_size > self.max_size:
            raise RuntimeError("HTTP 500 https://sansad.in/api_ls/question/qetAllQuestions")
        start = (page_no - 1) * page_size
        if start in self.dead:
            raise RuntimeError("HTTP 500 https://sansad.in/api_ls/question/qetAllQuestions")
        return [{"quesNo": start + i} for i in range(page_size) if start + i < self.total]


def _walk(portal, page_size=500, **kw):
    p = SansadProbe.__new__(SansadProbe)
    p.sleep = 0
    p.ls_question_list_page = portal.page
    p.log = lambda *_a, **_k: None
    return list(p.paginate_ls_question_list(13, 9, page_size=page_size, **kw))


def test_halving_stays_on_multiples_of_the_floor():
    """Plain // 2 gives 62 and 31, which no coarser page boundary lands on."""
    s, seen = 500, []
    for _ in range(6):
        s = _halve_to_multiple(s, 25)
        seen.append(s)
    assert seen == [250, 125, 50, 25, 25, 25]
    assert all(x % 25 == 0 for x in seen)


def test_it_degrades_until_the_portal_answers():
    """The LS 13 failure: the session serves at 125 and 500s above it."""
    portal = _Portal(total=600, max_size=125)
    rows = _walk(portal)
    assert len(rows) == 600
    assert max(sz for _, sz in portal.calls if sz <= 125) == 125


def test_a_dead_page_is_skipped_not_fatal():
    """LS 13 session 8 served page 1 and page 3 with a 500 between them.
    Stopping at the first failure stored 1,000 rows of a 5,060-row session."""
    portal = _Portal(total=200, max_size=25, dead={50})
    skipped = []
    rows = _walk(portal, page_size=25, skipped=skipped)
    assert skipped == [(50, 74)]
    got = {r["quesNo"] for r in rows}
    assert got == set(range(0, 50)) | set(range(75, 200)), "everything except the dead page"


def test_the_offset_never_moves_backward():
    """The regression. Restoring the page size by flooring the page number put
    the offset back inside the page that had just failed, and the walk span on
    one offset until it was killed."""
    portal = _Portal(total=2000, max_size=500, dead={550})
    skipped = []
    rows = _walk(portal, page_size=500, skipped=skipped)
    assert len(rows) == 2000 - 25 * len(skipped)
    starts = [(pn - 1) * sz for pn, sz in portal.calls]
    assert len(set(starts)) > len(starts) - 20, "the same offset is being retried in a loop"


def test_a_healthy_session_never_degrades():
    portal = _Portal(total=1500, max_size=10_000)
    assert len(_walk(portal)) == 1500
    assert {sz for _, sz in portal.calls} == {500}, "no degradation without a failure"


def test_a_non_5xx_error_is_not_swallowed():
    class Boom:
        def page(self, *a, **k):
            raise RuntimeError("HTTP 403 forbidden")
    with pytest.raises(RuntimeError, match="403"):
        _walk(Boom())


class _FlakyPortal(_Portal):
    """Fails a given offset the first `flaky_times` times, then serves it.

    LS 13 session 9 behaved exactly this way: twenty-five offsets reported as
    permanently unavailable all returned their rows a minute later.
    """

    def __init__(self, total, max_size, flaky_at, flaky_times):
        super().__init__(total, max_size)
        self.flaky_at, self.flaky_times, self.seen = flaky_at, flaky_times, {}

    def page(self, loksabha, session_number, page_no, page_size):
        start = (page_no - 1) * page_size
        if start == self.flaky_at:
            n = self.seen.get(start, 0)
            self.seen[start] = n + 1
            if n < self.flaky_times:
                self.calls.append((page_no, page_size))
                raise RuntimeError("HTTP 500 https://sansad.in/api_ls/question/qetAllQuestions")
        return super().page(loksabha, session_number, page_no, page_size)


def test_a_transient_floor_failure_is_retried_not_skipped():
    portal = _FlakyPortal(total=200, max_size=25, flaky_at=50, flaky_times=2)
    skipped = []
    rows = _walk(portal, page_size=25, floor_retries=4, skipped=skipped)
    assert skipped == [], "a page that recovers on retry must not be recorded as a hole"
    assert len(rows) == 200


def test_retries_are_bounded_and_then_it_skips():
    portal = _FlakyPortal(total=200, max_size=25, flaky_at=50, flaky_times=99)
    skipped = []
    rows = _walk(portal, page_size=25, floor_retries=2, skipped=skipped)
    assert skipped == [(50, 74)]
    assert len(rows) == 175


def test_the_retry_budget_resets_between_pages():
    """Otherwise one flaky page early on spends the budget for the whole walk."""
    portal = _FlakyPortal(total=300, max_size=25, flaky_at=50, flaky_times=2)
    rows = _walk(portal, page_size=25, floor_retries=2, skipped=[])
    assert len(rows) == 300
