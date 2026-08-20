"""Tests for member-less LS enumeration through the portal question list.

Fixture mirrors the live-verified contract (2026-08-14, lkNo 18 session 8):
``qetAllQuestions`` honours ``pageSize`` up to at least 1000, is 1-indexed,
returns ``questionsFilePath`` on every row, and returns ``questionText`` and
``answerText`` as null. A whole session came back in five pages
(1000/1000/1000/1000/500, then empty) for 4,500 records.

The session-drift case is the one that matters for a historical backfill: the
endpoint silently ignores an unknown ``sessionNumber`` and answers from the
latest session instead, so enumerating a term that never had session N would
otherwise file current rows under it.

No network.
"""

from __future__ import annotations

import json
from pathlib import Path

from commoner_probe.cli import build_parser
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


def _row(qno: str, *, ses_no: int = 8, qtype: str = "UNSTARRED", date: str = "22.07.2026") -> dict:
    return {
        "quesNo": qno,
        "subjects": f"Subject {qno}",
        "lokNo": 18,
        "member": ["Shri Test Member"],
        "ministry": "CULTURE",
        "type": qtype,
        "date": date,
        "questionText": None,
        "answerText": None,
        "questionsFilePath": f"https://sansad.in/getFile/annex/188/AU{qno}.pdf?source=ls",
        "questionsFilePathHindi": f"https://sansad.in/getFile/qhindi/188/AU{qno}.pdf?source=ls",
        "sessionNo": str(ses_no),
    }


class FakePortalSession:
    """Pages the question list, honouring pageSize and 1-indexed pageNo."""

    def __init__(self, rows_by_session: dict[int, list[dict]], *, fail_sessions=()):
        self.rows_by_session = rows_by_session
        self.fail_sessions = set(fail_sessions)
        self.calls: list[tuple[int, int, int]] = []

    def get(self, url, params=None, **kwargs):
        p = params or {}
        ses = int(p["sessionNumber"])
        page = int(p["pageNo"])
        size = int(p["pageSize"])
        self.calls.append((ses, page, size))
        if ses in self.fail_sessions:
            return FakeResponse(status=503)
        rows = self.rows_by_session.get(ses, [])
        chunk = rows[(page - 1) * size: page * size]
        return FakeResponse([{"listOfQuestions": chunk}])


class FakeRoster:
    def lookup(self, name):
        return None


def _probe(out: Path, session) -> SansadProbe:
    probe = SansadProbe(None, out, sleep=0, enumerate_all=True)
    probe.session = session
    probe._roster = FakeRoster()
    return probe


def _manifest(out: Path) -> list[dict]:
    path = out / "manifest.jsonl"
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _windows(out: Path) -> list[dict]:
    path = out / "_windows.jsonl"
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _run(probe, **kw):
    defaults = dict(
        loksabha=18, sessions=[8], from_date=None, to_date=None,
        qtype_filter=None, max_records=None, download=False,
    )
    return probe.probe_ls_sessions(set(), **{**defaults, **kw})


def test_a_whole_session_enumerates_and_carries_the_pdf_url(tmp_path):
    """The reason this path exists: DSpace records carry no pdf_url at all."""
    session = FakePortalSession({8: [_row(str(100 + i)) for i in range(5)]})
    added = _run(_probe(tmp_path, session))
    rows = _manifest(tmp_path)
    assert added == 5
    assert len(rows) == 5
    assert all(r["pdf_url"] for r in rows)
    assert all(r["source"] == "sansad.in/api_ls/question" for r in rows)


def test_pagination_stops_on_the_first_empty_page(tmp_path):
    session = FakePortalSession({8: [_row(str(100 + i)) for i in range(7)]})
    _run(_probe(tmp_path, session), page_size=3)
    # 3 + 3 + 1 then one empty page to learn it is over
    assert [c[1] for c in session.calls] == [1, 2, 3, 4]
    assert all(c[2] == 3 for c in session.calls)


def test_page_numbering_starts_at_one(tmp_path):
    """pageNo=0 answers HTTP 500 on the live portal, so the loop must not use it."""
    session = FakePortalSession({8: [_row("101")]})
    _run(_probe(tmp_path, session))
    assert session.calls[0][1] == 1


def test_a_row_from_another_session_is_never_filed(tmp_path):
    """The endpoint falls back to the latest session for an unknown one."""
    rows = [_row("101"), _row("999", ses_no=7), _row("102")]
    session = FakePortalSession({8: rows})
    added = _run(_probe(tmp_path, session))
    assert added == 2
    assert {r["qno"] for r in _manifest(tmp_path)} == {"101", "102"}


def test_a_completed_window_is_skipped_on_rerun(tmp_path):
    session = FakePortalSession({8: [_row("101")]})
    _run(_probe(tmp_path, session))
    first = len(session.calls)
    probe = _probe(tmp_path, session)
    added = probe.probe_ls_sessions(
        {r["key"] for r in _manifest(tmp_path)},
        loksabha=18, sessions=[8], from_date=None, to_date=None,
        qtype_filter=None, max_records=None, download=False,
    )
    assert added == 0
    assert len(session.calls) == first, "a complete window must not be re-requested"


def test_a_failed_session_is_recorded_suspect_and_retried(tmp_path):
    session = FakePortalSession({8: [_row("101")]}, fail_sessions={8})
    _run(_probe(tmp_path, session))
    w = _windows(tmp_path)
    assert w[-1]["status"] == "suspect"
    assert w[-1]["window_id"] == "ls:18:8"


def test_the_window_id_carries_the_term(tmp_path):
    """Session numbers are LS-relative, so 18:8 and 17:8 are different windows."""
    session = FakePortalSession({8: [_row("101")]})
    _run(_probe(tmp_path, session))
    assert _windows(tmp_path)[-1]["window_id"] == "ls:18:8"
    assert _windows(tmp_path)[-1]["loksabha"] == 18


def test_member_less_enumeration_records_no_mp_code(tmp_path):
    """The question list carries member names and no code; 0 would be a lie."""
    session = FakePortalSession({8: [_row("101")]})
    _run(_probe(tmp_path, session))
    rec = _manifest(tmp_path)[0]
    assert rec["mp_code"] is None
    assert rec["found_via_query"] == "ls_session:18:8"


def test_qtype_and_date_filters_apply(tmp_path):
    rows = [
        _row("101", qtype="STARRED", date="22.07.2026"),
        _row("102", qtype="UNSTARRED", date="22.07.2026"),
        _row("103", qtype="STARRED", date="01.01.2020"),
    ]
    session = FakePortalSession({8: rows})
    added = _run(_probe(tmp_path, session), qtype_filter="starred",
                 from_date="2026-01-01", to_date="2026-12-31")
    assert added == 1
    assert _manifest(tmp_path)[0]["qno"] == "101"


def test_max_records_leaves_the_window_unrecorded(tmp_path):
    """An incomplete window must re-crawl cleanly rather than look done."""
    session = FakePortalSession({8: [_row(str(100 + i)) for i in range(5)]})
    added = _run(_probe(tmp_path, session), max_records=2)
    assert added == 2
    assert _windows(tmp_path) == []


def test_cli_rejects_loksabha_without_sessions():
    parser = build_parser()
    args = parser.parse_args(
        ["sansad", "--all", "--house", "ls", "--loksabha", "18", "--out", "x"]
    )
    assert args.loksabha == 18 and args.sessions is None


# ---- inline text on older sessions -------------------------------------------

def _row_with_text(qno: str, *, ses_no: int = 10) -> dict:
    """A pre-2015 row. The portal carried question and answer text until
    LS 16 session 4 (May 2015); every session after returns null on both."""
    r = _row(qno, ses_no=ses_no)
    r["questionText"] = f"<p>(a) whether question {qno} was asked?</p>"
    r["answerText"] = f"<p>THE MINISTER: (a) Yes, for {qno}.</p>"
    r["answerTextHindi"] = None
    return r


def test_inline_text_is_carried_not_dropped(tmp_path):
    """The mapper used to emit 18 fields and none of them the text.

    Harmless while the only caller was the per-member path on the current
    term, where both fields are always null. Extended to historical sessions
    it silently discarded the entire value of the record.
    """
    session = FakePortalSession({10: [_row_with_text("101")]})
    _run(_probe(tmp_path, session), sessions=[10])
    rec = _manifest(tmp_path)[0]
    assert rec["question_text"].startswith("<p>(a) whether")
    assert rec["answer_text"].startswith("<p>THE MINISTER")
    assert rec["answer_text_hindi"] is None


def test_the_hindi_answer_survives_the_typed_reader(tmp_path):
    """`ManifestQaRecord.from_dict` drops any key the dataclass does not name.

    The raw manifest carried `answer_text_hindi` from the day inline text was
    carried; the schema never declared it and the dataclass never named it, so
    every consumer reading this corpus through `Corpus` lost the Hindi answer
    with nothing to indicate a loss. The schema has no `additionalProperties`
    bar, so `validate` passed too — silent at all three layers.
    """
    from commoner_probe.corpus import Corpus

    row = _row_with_text("101")
    row["answerTextHindi"] = "<p>मंत्री: (क) जी हाँ।</p>"
    session = FakePortalSession({10: [row]})
    _run(_probe(tmp_path, session), sessions=[10])

    assert _manifest(tmp_path)[0]["answer_text_hindi"] == "<p>मंत्री: (क) जी हाँ।</p>"
    rec = list(Corpus(tmp_path).manifest_qa())[0]
    assert rec.answer_text_hindi == "<p>मंत्री: (क) जी हाँ।</p>"


def test_a_modern_row_keeps_the_keys_with_null_values(tmp_path):
    """Absent text must be visible as null, never an absent field: a consumer
    has to be able to tell 'this session served no text' from 'we dropped it'."""
    session = FakePortalSession({8: [_row("101")]})
    _run(_probe(tmp_path, session))
    rec = _manifest(tmp_path)[0]
    assert "question_text" in rec and rec["question_text"] is None
    assert "answer_text" in rec and rec["answer_text"] is None


# ---- pagination that fails mid-session ---------------------------------------

class FailAfterPage(FakePortalSession):
    """Answers page 1, then raises — the LS 15 session 10 shape."""

    def __init__(self, rows_by_session, *, fail_from_page: int = 2):
        super().__init__(rows_by_session)
        self.fail_from_page = fail_from_page

    def get(self, url, params=None, **kwargs):
        if int((params or {})["pageNo"]) >= self.fail_from_page:
            raise RuntimeError("HTTP 500 https://sansad.in/api_ls/question/qetAllQuestions")
        return super().get(url, params=params, **kwargs)


def test_a_failing_page_keeps_earlier_records(tmp_path):
    session = FailAfterPage({10: [_row(str(100 + i), ses_no=10) for i in range(4)]})
    added = _run(_probe(tmp_path, session), sessions=[10], page_size=2)
    assert added == 2, "records from the pages that did work must be kept"


def test_a_failing_page_does_NOT_mark_the_window_complete(tmp_path):
    """A 500 is not proof the session ended. Calling it done would silently
    truncate the session and report success on the next resume."""
    session = FailAfterPage({10: [_row(str(100 + i), ses_no=10) for i in range(4)]})
    _run(_probe(tmp_path, session), sessions=[10], page_size=2)
    w = _windows(tmp_path)[-1]
    assert w["status"] == "suspect"
    assert w["window_id"] == "ls:18:10"


def test_a_suspect_window_is_recrawled_on_the_next_run(tmp_path):
    session = FailAfterPage({10: [_row(str(100 + i), ses_no=10) for i in range(4)]})
    probe = _probe(tmp_path, session)
    _run(probe, sessions=[10], page_size=2)
    before = len(session.calls)
    probe2 = _probe(tmp_path, session)
    probe2.probe_ls_sessions(
        {r["key"] for r in _manifest(tmp_path)},
        loksabha=18, sessions=[10], from_date=None, to_date=None,
        qtype_filter=None, max_records=None, download=False, page_size=2,
    )
    assert len(session.calls) > before, "a suspect window must be retried, not skipped"


def test_failure_on_page_one_still_raises(tmp_path):
    """Nothing was fetched, so there is no partial result to preserve."""
    session = FailAfterPage({10: [_row("101", ses_no=10)]}, fail_from_page=1)
    _run(_probe(tmp_path, session), sessions=[10])
    assert _manifest(tmp_path) == []
    assert _windows(tmp_path)[-1]["status"] == "suspect"


# ---- the enumeration path degrades like the pager ------------------------------

class TooBigForThisSession(FakePortalSession):
    """Serves pages up to `max_size` and 500s above it — the LS 13 shape.

    Measured 2026-08-15: LS 13 session 9 serves page 1 at page_size 500 and
    returns HTTP 500 at 1000. Because `LS_PORTAL_PAGE_SIZE` is 1000, a
    term-wide enumeration recorded six sessions — February 2002 to February
    2004 — as unavailable while the portal held them and would serve them.
    """

    def __init__(self, rows_by_session, *, max_size):
        super().__init__(rows_by_session)
        self.max_size = max_size

    def get(self, url, params=None, **kwargs):
        if int((params or {})["pageSize"]) > self.max_size:
            self.calls.append((int((params or {})["sessionNumber"]),
                               int((params or {})["pageNo"]),
                               int((params or {})["pageSize"])))
            raise RuntimeError("HTTP 500 https://sansad.in/api_ls/question/qetAllQuestions")
        return super().get(url, params=params, **kwargs)


def test_an_oversized_page_degrades_instead_of_losing_the_session(tmp_path):
    """`paginate_ls_question_list` exists for this and had no caller.

    The CLI enumeration ran its own loop, so the degrade, the floor retry,
    the skip and the climb-back reached nothing a user could invoke.
    """
    rows = [_row(str(100 + i), ses_no=9) for i in range(60)]
    session = TooBigForThisSession({9: rows}, max_size=50)
    added = _run(_probe(tmp_path, session), sessions=[9], page_size=100)
    assert added == 60, "every row the portal will serve must be collected"
    assert min(c[2] for c in session.calls) <= 50, "the walk must have degraded"


def test_a_degraded_session_that_completes_is_not_suspect(tmp_path):
    rows = [_row(str(100 + i), ses_no=9) for i in range(60)]
    session = TooBigForThisSession({9: rows}, max_size=50)
    _run(_probe(tmp_path, session), sessions=[9], page_size=100)
    assert _windows(tmp_path)[-1]["status"] == "complete"


def test_a_skipped_page_leaves_the_window_suspect(tmp_path):
    """A hole is not a completed window, however many rows came back."""
    session = FailAfterPage({10: [_row(str(100 + i), ses_no=10) for i in range(8)]},
                            fail_from_page=2)
    _run(_probe(tmp_path, session), sessions=[10], page_size=2)
    w = _windows(tmp_path)[-1]
    assert w["status"] == "suspect"
    assert w["errors"] == 1


# ---- the question-number guard, end to end -------------------------------------

def _runs(out: Path) -> list[dict]:
    path = out / "_runs.jsonl"
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_an_inline_answer_naming_another_question_is_recorded_not_dropped(tmp_path):
    """The document is real and belongs to SOME question. Dropping it would be
    harder to notice than flagging it."""
    row = _row_with_text("1982", ses_no=10)
    row["answerText"] = ("<p>GOVERNMENT OF INDIA<br>LOK SABHA<br>UNSTARRED QUESTION NO.1882"
                         "<br>ANSWERED ON 07.03.2000<br>OFFICIAL LANGUAGE</p>")
    row["questionsFilePath"] = None
    session = FakePortalSession({10: [row]})
    added = _run(_probe(tmp_path, session), sessions=[10])

    assert added == 1, "a flagged record is kept"
    rec = _manifest(tmp_path)[0]
    assert rec["qno"] == "1982"
    assert rec["document_qno"] == "1882"
    assert rec["document_qno_status"] == "mismatch"


def test_the_run_log_carries_the_mismatch_count(tmp_path):
    """A consumer reads the rate off the run rather than inferring it."""
    row = _row_with_text("1982", ses_no=10)
    row["answerText"] = "<p>LOK SABHA UNSTARRED QUESTION NO.1882</p>"
    row["questionsFilePath"] = None
    session = FakePortalSession({10: [row]})
    _run(_probe(tmp_path, session), sessions=[10])
    bucket = _runs(tmp_path)[-1]["bucket_attempts"][-1]
    assert bucket["qno_mismatches"] == 1


def test_an_inline_answer_that_agrees_is_verified(tmp_path):
    row = _row_with_text("1982", ses_no=10)
    row["answerText"] = "<p>LOK SABHA UNSTARRED QUESTION NO.1982</p>"
    row["questionsFilePath"] = None
    session = FakePortalSession({10: [row]})
    _run(_probe(tmp_path, session), sessions=[10])
    rec = _manifest(tmp_path)[0]
    assert (rec["document_qno"], rec["document_qno_status"]) == ("1982", "verified")


def test_an_answer_that_prints_no_number_is_unreadable_not_flagged(tmp_path):
    """85% of inline answers state no number of their own. A check that flags
    those gets switched off within a week."""
    row = _row_with_text("1982", ses_no=10)
    row["answerText"] = "<p>(a) and (b): The Government has taken several steps.</p>"
    row["questionsFilePath"] = None
    session = FakePortalSession({10: [row]})
    _run(_probe(tmp_path, session), sessions=[10])
    rec = _manifest(tmp_path)[0]
    assert (rec["document_qno"], rec["document_qno_status"]) == (None, "unreadable")
    assert _runs(tmp_path)[-1]["bucket_attempts"][-1]["qno_mismatches"] == 0


def test_a_flagged_record_still_validates(tmp_path):
    """`validate` must accept the new fields, and must accept a corpus written
    before they existed."""
    from commoner_probe.validate import validate_corpus

    row = _row_with_text("1982", ses_no=10)
    row["answerText"] = "<p>LOK SABHA UNSTARRED QUESTION NO.1882</p>"
    row["questionsFilePath"] = None
    _run(_probe(tmp_path, FakePortalSession({10: [row]})), sessions=[10])
    assert validate_corpus(tmp_path, log=lambda _: None)

    legacy = _manifest(tmp_path)[0]
    del legacy["document_qno"], legacy["document_qno_status"]
    (tmp_path / "manifest.jsonl").write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    assert validate_corpus(tmp_path, log=lambda _: None), "a corpus written before the check must still validate"


def test_the_typed_reader_carries_the_verdict(tmp_path):
    from commoner_probe.corpus import Corpus

    row = _row_with_text("1982", ses_no=10)
    row["answerText"] = "<p>LOK SABHA UNSTARRED QUESTION NO.1882</p>"
    row["questionsFilePath"] = None
    _run(_probe(tmp_path, FakePortalSession({10: [row]})), sessions=[10])
    rec = list(Corpus(tmp_path).manifest_qa())[0]
    assert (rec.document_qno, rec.document_qno_status) == ("1882", "mismatch")


# ---- space-padded dates on older rows -----------------------------------------

def test_a_space_padded_month_parses(tmp_path):
    """`25. 4.2001` is 25 April 2001, space-padded instead of zero-padded.

    Eight rows of Lok Sabha 13 carry it. Unparsed, the raw string reached
    `stable_key` and produced `LS|U|5669|25. 4.2001` — a permanently broken key.
    """
    from commoner_probe.parliament_qa_api import SansadProbe
    assert SansadProbe._ls_portal_date("25. 4.2001") == "2001-04-25"
    assert SansadProbe._ls_portal_date("07. 3.2001") == "2001-03-07"


def test_normal_dates_are_unaffected():
    from commoner_probe.parliament_qa_api import SansadProbe
    assert SansadProbe._ls_portal_date("12.08.2026") == "2026-08-12"
    assert SansadProbe._ls_portal_date("") == ""


def test_an_unreadable_date_stays_visibly_wrong():
    """Never guess a date. An unparseable value must not become a plausible one."""
    import re

    from commoner_probe.parliament_qa_api import SansadProbe
    for bad in ("not-a-date", "31.02.2001", "rubbish"):
        got = SansadProbe._ls_portal_date(bad)
        assert not re.fullmatch(r"\d{4}-\d{2}-\d{2}", got), (
            f"{bad!r} became a plausible ISO date {got!r} — an unreadable date must stay "
            "visibly wrong, never be guessed into something a consumer will trust"
        )
        assert got == bad[:10]


def test_the_broken_key_shape_cannot_recur(tmp_path):
    """End to end: a space-padded row must produce an ISO-dated key."""
    session = FakePortalSession({8: [dict(_row("5669"), date="25. 4.2001")]})
    _run(_probe(tmp_path, session))
    assert _manifest(tmp_path)[0]["key"] == "LS|U|5669|2001-04-25"


def test_the_run_log_carries_every_verdict_not_only_the_mismatches(tmp_path):
    """A mismatch count of zero describes a clean slice and a slice the guard
    could not read at all. One consumer run returned 25 of 25 unreadable on an
    unsupported layout, and `qno_mismatches: 0` said nothing about it.

    Every status appears, including the zeros. A key present only when
    non-zero makes "this bucket read nothing" and "this field is new"
    indistinguishable to whoever reads the run log later.
    """
    unreadable = _row_with_text("1982", ses_no=10)
    unreadable["answerText"] = "<p>(a) and (b): The Government has taken steps.</p>"
    unreadable["questionsFilePath"] = None
    agrees = _row_with_text("1983", ses_no=10)
    agrees["answerText"] = "<p>LOK SABHA UNSTARRED QUESTION NO.1983</p>"
    agrees["questionsFilePath"] = None

    session = FakePortalSession({10: [unreadable, agrees]})
    _run(_probe(tmp_path, session), sessions=[10])

    bucket = _runs(tmp_path)[-1]["bucket_attempts"][-1]
    assert bucket["qno_status"] == {"verified": 1, "mismatch": 0, "unreadable": 1}
