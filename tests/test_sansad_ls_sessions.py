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
from commoner_probe.sansad import SansadProbe


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
