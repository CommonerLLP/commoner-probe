"""Tests for the full-corpus enumeration mode (commoner-probe sansad --all).

Fixtures mirror the two live-verified enumeration contracts (2026-07-08):

* LS: the eLibrary DSpace discover API accepts an empty query plus a
  ``f.dateIssued=[YYYY-MM-DD TO YYYY-MM-DD],equals`` range facet and a
  ``sort=dc.date.issued,ASC`` option (one day = 250 items = the LS
  procedural cap, paginated cleanly).
* RS: a bare ``whereclause=ses_no=N`` returns the entire session in one
  JSON list (session 267 = 4,371 rows), no pagination.

No network.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from commoner_probe.cli import build_parser
from commoner_probe.sansad import SansadProbe, month_windows


class FakeResponse:
    def __init__(self, payload=None, *, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _ls_item(uuid: str, qno: str, date: str, qtype: str = "Starred", title: str = "Q") -> dict:
    return {"_embedded": {"indexableObject": {
        "uuid": uuid,
        "handle": f"123456789/{uuid}",
        "metadata": {
            "dc.title": [{"value": title}],
            "dc.date.issued": [{"value": date}],
            "dc.identifier.questiontype": [{"value": qtype}],
            "dc.identifier.questionnumber": [{"value": qno}],
            "dc.identifier.sessionnumber": [{"value": "7"}],
            "dc.identifier.loksabhanumber": [{"value": "18"}],
            "dc.relation.ministry": [{"value": "CULTURE"}],
            "dc.contributor.members": [{"value": "Test MP"}],
            "dc.identifier.uri": [{"value": f"http://hdl.handle.net/123456789/{uuid}"}],
        },
    }}}


class FakeLSSession:
    """Routes DSpace discover URLs by their f.dateIssued window."""

    def __init__(self, items_by_window: dict[str, list[dict]], *, fail_windows=(), page_size: int = 2):
        self.items_by_window = items_by_window
        self.fail_windows = set(fail_windows)
        self.page_size = page_size
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        assert "/discover/search/objects" in url, f"unrouted url: {url}"
        q = parse_qs(urlparse(url).query)
        m = re.match(r"\[(\S+) TO (\S+)\]", q["f.dateIssued"][0])
        window = f"{m.group(1)}..{m.group(2)}"
        if window in self.fail_windows:
            return FakeResponse(status=503)
        items = self.items_by_window.get(window, [])
        page = int(q.get("page", ["0"])[0])
        chunk = items[page * self.page_size:(page + 1) * self.page_size]
        return FakeResponse({"_embedded": {"searchResult": {
            "_embedded": {"objects": chunk},
            "page": {
                "number": page,
                "totalPages": math.ceil(len(items) / self.page_size),
                "totalElements": len(items),
            },
        }}})


class FakeRSSession:
    def __init__(self, rows_by_session: dict[int, list[dict]], *, fail_sessions=()):
        self.rows_by_session = rows_by_session
        self.fail_sessions = set(fail_sessions)
        self.whereclauses: list[str] = []

    def get(self, url, params=None, **kwargs):
        where = (params or {}).get("whereclause", "")
        self.whereclauses.append(where)
        m = re.search(r"ses_no=(\d+)", where)
        ses_no = int(m.group(1))
        if ses_no in self.fail_sessions:
            return FakeResponse(status=503)
        return FakeResponse(self.rows_by_session.get(ses_no, []))


class FakeRoster:
    def lookup(self, name):
        return None


def _rs_row(qno: str, ses_no: int, *, qtype: str = "UNSTARRED", ans_date: str = "02.01.2026") -> dict:
    return {
        "qslno": qno,
        "ses_no": ses_no,
        "qtitle": f"Question {qno}",
        "ans_date": ans_date,
        "qtype": qtype,
        "qno": qno,
        "min_name": "Culture",
        "name": "MP One",
        "qn_text": "Question text",
        "ans_text": "Answer text",
        "files": "",
        "hindifiles": "",
        "status": "Answered",
    }


def _all_probe(out: Path, session) -> SansadProbe:
    probe = SansadProbe(None, out, sleep=0, enumerate_all=True)
    probe.session = session
    probe._roster = FakeRoster()
    return probe


def _manifest(out: Path) -> list[dict]:
    path = out / "manifest.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _windows(out: Path) -> list[dict]:
    path = out / "_windows.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _called_windows(session: FakeLSSession) -> list[str]:
    out = []
    for url in session.calls:
        q = parse_qs(urlparse(url).query)
        m = re.match(r"\[(\S+) TO (\S+)\]", q["f.dateIssued"][0])
        out.append(f"{m.group(1)}..{m.group(2)}")
    return out


def test_month_windows_splits_and_clips():
    assert month_windows("2024-07-22", "2024-07-26") == [("2024-07-22", "2024-07-26")]
    assert month_windows("2024-11-15", "2025-01-10") == [
        ("2024-11-15", "2024-11-30"),
        ("2024-12-01", "2024-12-31"),
        ("2025-01-01", "2025-01-10"),
    ]
    assert month_windows("2024-02-01", "2024-02-29") == [("2024-02-01", "2024-02-29")]


JAN = "2026-01-01..2026-01-31"
FEB = "2026-02-01..2026-02-28"

JAN_ITEMS = [
    _ls_item("a1", "1", "2026-01-05"),
    _ls_item("a2", "2", "2026-01-05", qtype="Unstarred"),
    _ls_item("a3", "3", "2026-01-06"),
]
FEB_ITEMS = [_ls_item("b1", "9", "2026-02-02")]


def test_ls_all_enumerates_without_topic_and_marks_windows_complete(tmp_path):
    probe = _all_probe(tmp_path, FakeLSSession({JAN: JAN_ITEMS, FEB: FEB_ITEMS}))
    seen: set[str] = set()
    added = probe.probe_ls_all(
        seen, from_date="2026-01-01", to_date="2026-02-28",
        qtype_filter=None, max_records=None, download=False,
    )

    assert added == 4
    written = _manifest(tmp_path)
    assert [r["qno"] for r in written] == ["1", "2", "3", "9"]
    assert all(r["found_via_group"] == "all" and r["found_via_query"] == "" for r in written)
    assert all(r["asker_entity_ids"] == [None] for r in written)

    windows = _windows(tmp_path)
    assert [(w["window_id"], w["status"], w["kept"]) for w in windows] == [
        (f"ls:{JAN}", "complete", 3),
        (f"ls:{FEB}", "complete", 1),
    ]

    for url in probe.session.calls:
        q = parse_qs(urlparse(url).query)
        assert q.get("query", [""]) == [""] or "query" not in q
        assert q["sort"] == ["dc.date.issued,ASC"]
        assert q["f.category"] == ["Part 1(Questions And Answers),equals"]


def test_ls_all_window_rows_validate_against_windows_schema(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    from commoner_probe import schemas

    probe = _all_probe(tmp_path, FakeLSSession({JAN: JAN_ITEMS}))
    probe.probe_ls_all(
        set(), from_date="2026-01-01", to_date="2026-01-31",
        qtype_filter=None, max_records=None, download=False,
    )
    schema = schemas.load("windows")
    for row in _windows(tmp_path):
        jsonschema.validate(instance=row, schema=schema)


def test_ls_all_marks_failed_window_suspect_and_continues(tmp_path):
    probe = _all_probe(tmp_path, FakeLSSession({JAN: JAN_ITEMS, FEB: FEB_ITEMS}, fail_windows={JAN}))
    added = probe.probe_ls_all(
        set(), from_date="2026-01-01", to_date="2026-02-28",
        qtype_filter=None, max_records=None, download=False,
    )

    assert added == 1  # FEB still crawled after JAN failed
    windows = {w["window_id"]: w for w in _windows(tmp_path)}
    assert windows[f"ls:{JAN}"]["status"] == "suspect"
    assert windows[f"ls:{JAN}"]["errors"] == 1
    assert windows[f"ls:{FEB}"]["status"] == "complete"


def test_ls_all_resume_skips_complete_recrawls_suspect(tmp_path):
    _all_probe(tmp_path, FakeLSSession({JAN: JAN_ITEMS, FEB: FEB_ITEMS}, fail_windows={JAN})).probe_ls_all(
        set(), from_date="2026-01-01", to_date="2026-02-28",
        qtype_filter=None, max_records=None, download=False,
    )

    probe = _all_probe(tmp_path, FakeLSSession({JAN: JAN_ITEMS, FEB: FEB_ITEMS}))
    seen = probe.load_seen()
    added = probe.probe_ls_all(
        seen, from_date="2026-01-01", to_date="2026-02-28",
        qtype_filter=None, max_records=None, download=False,
    )

    assert added == 3  # suspect JAN re-crawled; complete FEB skipped
    assert set(_called_windows(probe.session)) == {JAN}
    latest = {w["window_id"]: w for w in _windows(tmp_path)}
    assert latest[f"ls:{JAN}"]["status"] == "complete"


def test_ls_all_reset_window_forces_recrawl(tmp_path):
    session = FakeLSSession({JAN: JAN_ITEMS, FEB: FEB_ITEMS})
    _all_probe(tmp_path, session).probe_ls_all(
        set(), from_date="2026-01-01", to_date="2026-02-28",
        qtype_filter=None, max_records=None, download=False,
    )

    probe = _all_probe(tmp_path, FakeLSSession({JAN: JAN_ITEMS, FEB: FEB_ITEMS}))
    probe.probe_ls_all(
        probe.load_seen(), from_date="2026-01-01", to_date="2026-02-28",
        qtype_filter=None, max_records=None, download=False,
        reset_windows=frozenset({f"ls:{JAN}"}),
    )

    assert set(_called_windows(probe.session)) == {JAN}


def test_ls_all_qtype_scope_mismatch_recrawls(tmp_path):
    _all_probe(tmp_path, FakeLSSession({JAN: JAN_ITEMS})).probe_ls_all(
        set(), from_date="2026-01-01", to_date="2026-01-31",
        qtype_filter="starred", max_records=None, download=False,
    )
    assert [r["qtype"] for r in _manifest(tmp_path)] == ["Starred", "Starred"]

    probe = _all_probe(tmp_path, FakeLSSession({JAN: JAN_ITEMS}))
    added = probe.probe_ls_all(
        probe.load_seen(), from_date="2026-01-01", to_date="2026-01-31",
        qtype_filter=None, max_records=None, download=False,
    )
    assert added == 1  # the unstarred row the starred-only pass could not have kept


def test_ls_all_no_download_makes_no_pdf_requests(tmp_path):
    probe = _all_probe(tmp_path, FakeLSSession({JAN: JAN_ITEMS}))
    probe.probe_ls_all(
        set(), from_date="2026-01-01", to_date="2026-01-31",
        qtype_filter=None, max_records=None, download=False,
    )
    assert all("/discover/search/objects" in u for u in probe.session.calls)
    assert not (tmp_path / "pdfs").exists()


def test_ls_all_max_records_leaves_window_unrecorded(tmp_path):
    probe = _all_probe(tmp_path, FakeLSSession({JAN: JAN_ITEMS, FEB: FEB_ITEMS}))
    added = probe.probe_ls_all(
        set(), from_date="2026-01-01", to_date="2026-02-28",
        qtype_filter=None, max_records=2, download=False,
    )
    assert added == 2
    assert _windows(tmp_path) == []  # interrupted window must not read as done


def test_rs_all_uses_bare_whereclause_and_marks_complete(tmp_path):
    rows = [_rs_row("1", 267), _rs_row("2", 267, qtype="STARRED")]
    probe = _all_probe(tmp_path, FakeRSSession({267: rows}))
    added = probe.probe_rs_all(
        set(), sessions=[267], from_date=None, to_date=None,
        qtype_filter=None, max_records=None, download=False,
    )

    assert added == 2
    assert probe.session.whereclauses == ["ses_no=267"]
    written = _manifest(tmp_path)
    assert [r["qno"] for r in written] == ["1", "2"]
    assert all(r["found_via_query"] == "" for r in written)
    windows = _windows(tmp_path)
    assert [(w["window_id"], w["status"], w["ses_no"]) for w in windows] == [("rs:267", "complete", 267)]


def test_rs_all_marks_failed_session_suspect_and_continues(tmp_path):
    probe = _all_probe(tmp_path, FakeRSSession(
        {267: [_rs_row("1", 267)]}, fail_sessions={266},
    ))
    added = probe.probe_rs_all(
        set(), sessions=[266, 267], from_date=None, to_date=None,
        qtype_filter=None, max_records=None, download=False,
    )

    assert added == 1
    windows = {w["window_id"]: w for w in _windows(tmp_path)}
    assert windows["rs:266"]["status"] == "suspect"
    assert windows["rs:267"]["status"] == "complete"


def test_rs_all_resume_skips_complete_sessions(tmp_path):
    rows = {266: [_rs_row("1", 266)], 267: [_rs_row("2", 267)]}
    _all_probe(tmp_path, FakeRSSession(rows)).probe_rs_all(
        set(), sessions=[266, 267], from_date=None, to_date=None,
        qtype_filter=None, max_records=None, download=False,
    )

    probe = _all_probe(tmp_path, FakeRSSession(rows))
    added = probe.probe_rs_all(
        probe.load_seen(), sessions=[266, 267], from_date=None, to_date=None,
        qtype_filter=None, max_records=None, download=False,
    )
    assert added == 0
    assert probe.session.whereclauses == []


def test_rs_all_date_scope_mismatch_recrawls(tmp_path):
    rows = {267: [_rs_row("1", 267, ans_date="02.01.2026"), _rs_row("2", 267, ans_date="05.03.2026")]}
    _all_probe(tmp_path, FakeRSSession(rows)).probe_rs_all(
        set(), sessions=[267], from_date="2026-01-01", to_date="2026-01-31",
        qtype_filter=None, max_records=None, download=False,
    )
    assert len(_manifest(tmp_path)) == 1

    probe = _all_probe(tmp_path, FakeRSSession(rows))
    added = probe.probe_rs_all(
        probe.load_seen(), sessions=[267], from_date=None, to_date=None,
        qtype_filter=None, max_records=None, download=False,
    )
    assert added == 1  # narrower earlier pass must not satisfy the full pass


def _parse(argv: list[str]):
    return build_parser().parse_args(argv)


def test_cli_all_rejects_topic_member_and_missing_bounds(tmp_path):
    from commoner_probe.cli import sansad_cmd

    with pytest.raises(SystemExit, match="cannot be combined"):
        sansad_cmd(_parse(["sansad", "--all", "--topic", "t.json", "--out", str(tmp_path)]))
    with pytest.raises(SystemExit, match="from-date"):
        sansad_cmd(_parse(["sansad", "--all", "--house", "ls", "--out", str(tmp_path)]))
    with pytest.raises(SystemExit, match="sessions"):
        sansad_cmd(_parse(["sansad", "--all", "--house", "rs", "--out", str(tmp_path)]))


def test_cli_without_all_still_requires_topic_or_member(tmp_path):
    from commoner_probe.cli import sansad_cmd

    with pytest.raises(SystemExit, match="--topic is required"):
        sansad_cmd(_parse(["sansad", "--out", str(tmp_path)]))
