"""Tests for identity-safe per-member retrieval by member code (REQ-0028).

Fixtures mirror the live-verified contracts (2026-07-17):

* RS: ``whereclause=ses_no=N and mp_code=C`` returns only that member's rows,
  each echoing ``mp_code`` (session 267, mpCode 2372 = 33 rows, all code 2372).
* RS/LS rosters (``api_rs``/``api_ls`` ``member/member-list``) carry a stable
  ``mpCode``; the LS roster also carries ``lastLoksabha``. 26 LS roster names
  are shared by more than one mpCode — exact-name joins must warn.
* LS portal question list (``qetAllQuestions``): ``sessionNumber`` is the
  honored session param; ``sessionNo`` is silently IGNORED and the endpoint
  falls back to the latest session — rows carry member NAMES (no code),
  ``quesNo`` as an integer, dates as ``dd.mm.yyyy``, ``type`` upper case.

No network.
"""

from __future__ import annotations

import json

import pytest

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


LS_ROSTER = [
    {"mpCode": 4569, "mpName": "Dr. Shashi Tharoor", "lastLoksabha": 18, "party": "INC", "partyName": "Indian National Congress"},
    {"mpCode": 101, "mpName": "Smt. Veena Devi", "lastLoksabha": 17, "party": "X", "partyName": "X"},
    {"mpCode": 202, "mpName": "Smt. Veena Devi", "lastLoksabha": 18, "party": "Y", "partyName": "Y"},
]

RS_ROSTER = [
    {"mpCode": 2372, "mpName": "Shri Sanjay Singh", "partyCode": "AAP", "partyName": "Aam Aadmi Party"},
]

LS_CALENDAR = [
    {"loksabha": 17, "sessions": [{"sessionNo": 1, "sessionPeriod": "x", "dates": []}]},
    {"loksabha": 18, "sessions": [
        {"sessionNo": 2, "sessionPeriod": "y", "dates": []},
        {"sessionNo": 3, "sessionPeriod": "z", "dates": []},
    ]},
]


def _ls_q(qno: int, members: list[str], *, session="2", date="09.08.2024", qtype="STARRED"):
    return {
        "quesNo": qno,
        "type": qtype,
        "date": date,
        "ministry": "EXTERNAL AFFAIRS",
        "member": members,
        "subjects": f"Subject {qno}",
        "sessionNo": session,
        "questionsFilePath": f"https://sansad.in/q/{qno}.pdf",
        "questionsFilePathHindi": None,
    }


class FakePortalSession:
    """Routes the sansad.in roster/calendar/question-list endpoints."""

    def __init__(self, pages_by_session: dict[int, list[list[dict]]]):
        self.pages_by_session = pages_by_session
        self.question_calls: list[dict] = []

    def get(self, url, params=None, headers=None, timeout=None, **kwargs):
        if "member/member-list" in url:
            return FakeResponse(RS_ROSTER if "api_rs" in url else LS_ROSTER)
        if "AllLoksabhaAndSessionDates" in url:
            return FakeResponse(LS_CALENDAR)
        if "qetAllQuestions" in url:
            self.question_calls.append(dict(params or {}))
            assert "sessionNumber" in (params or {}), "must use the honored session param"
            assert "sessionNo" not in (params or {}), "sessionNo is silently ignored by the endpoint"
            pages = self.pages_by_session.get(int(params["sessionNumber"]), [])
            page_no = int(params["pageNo"])
            rows = pages[page_no - 1] if page_no <= len(pages) else []
            return FakeResponse([{"listOfQuestions": rows}])
        raise AssertionError(f"unrouted url: {url}")


class FakeRSSession:
    def __init__(self, rows_by_where: dict[str, list[dict]]):
        self.rows_by_where = rows_by_where
        self.whereclauses: list[str] = []

    def get(self, url, params=None, headers=None, timeout=None, **kwargs):
        if "member/member-list" in url:
            return FakeResponse(RS_ROSTER)
        where = (params or {}).get("whereclause", "")
        self.whereclauses.append(where)
        return FakeResponse(self.rows_by_where.get(where, []))


def _probe(tmp_path, session, **kwargs):
    probe = SansadProbe(None, tmp_path / "out", sleep=0, **kwargs)
    probe.session = session
    return probe


def _manifest(probe):
    path = probe.out_dir / "manifest.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


class TestInitGuards:
    def test_member_name_and_mp_code_are_mutually_exclusive(self, tmp_path):
        with pytest.raises(ValueError):
            SansadProbe(None, tmp_path / "out", member_name="X", mp_code=1)

    def test_name_mode_warns_identity_unsafe(self, tmp_path):
        probe = SansadProbe(None, tmp_path / "out", member_name="Mohd. Ali Khan")
        log_text = (probe.out_dir / "probe.log").read_text()
        assert "identity-UNSAFE" in log_text


class TestRSByCode:
    def _rs_row(self, mp_code: int, qno: int, name="Sanjay Singh"):
        return {
            "mp_code": mp_code,
            "name": name,
            "qno": f"{qno}.0",
            "qslno": qno,
            "ses_no": 267,
            "qtitle": f"T{qno}",
            "qtype": "UNSTARRED",
            "ans_date": "10/12/2024",
            "min_name": "HOME AFFAIRS",
            "qn_text": "q",
            "ans_text": "a",
            "files": None,
            "hindifiles": None,
            "status": "ANSWERED",
        }

    def test_whereclause_is_code_pinned(self, tmp_path):
        session = FakeRSSession({"ses_no=267 and mp_code=2372": [self._rs_row(2372, 1)]})
        probe = _probe(tmp_path, session, mp_code=2372)
        probe.rs_search_session(267, "", mp_code=2372)
        assert session.whereclauses == ["ses_no=267 and mp_code=2372"]

    def test_probe_rs_keeps_only_the_requested_code_and_stamps_provenance(self, tmp_path):
        rows = [self._rs_row(2372, 1), self._rs_row(9999, 2, name="Someone Else"), self._rs_row(2372, 3)]
        session = FakeRSSession({"ses_no=267 and mp_code=2372": rows})
        probe = _probe(tmp_path, session, mp_code=2372)
        added = probe.probe_rs(
            seen=set(), sessions=[267], from_date=None, to_date=None,
            qtype_filter=None, limit=None, max_buckets=None, max_records=None, download=False,
        )
        assert added == 2
        recs = [r for r in _manifest(probe) if r.get("kind") == "qa"]
        assert {r["qno"] for r in recs} == {"1", "3"}
        assert all(r["mp_code"] == 2372 for r in recs)
        assert all(r["found_via_query"] == "mp_code:2372" for r in recs)

    def test_corpus_api_round_trips_mp_code(self, tmp_path):
        # Codex PR#51 finding: ManifestQaRecord.from_dict drops unknown keys,
        # so the Corpus API must carry mp_code as a first-class field.
        from commoner_probe.corpus import Corpus

        session = FakeRSSession({"ses_no=267 and mp_code=2372": [self._rs_row(2372, 1)]})
        probe = _probe(tmp_path, session, mp_code=2372)
        probe.probe_rs(
            seen=set(), sessions=[267], from_date=None, to_date=None,
            qtype_filter=None, limit=None, max_buckets=None, max_records=None, download=False,
        )
        recs = list(Corpus(probe.out_dir).manifest_qa())
        assert recs and recs[0].mp_code == 2372

    def test_manifest_qa_record_keeps_existing_positional_order(self):
        from commoner_probe.records import ManifestQaRecord

        rec = ManifestQaRecord(
            "k",
            "qa",
            "Lok Sabha",
            "title",
            "2026-07-18",
            "UNSTARRED",
            "1",
            "MINISTRY",
            ["MP"],
            "elibrary.sansad.in",
            None,
            None,
            [],
            [],
            [],
            None,
            None,
            None,
            None,
            "uuid-1",
        )
        assert rec.uuid == "uuid-1"
        assert rec.mp_code is None

    def test_probe_rs_names_the_member_from_the_roster(self, tmp_path):
        session = FakeRSSession({"ses_no=267 and mp_code=2372": []})
        probe = _probe(tmp_path, session, mp_code=2372)
        probe.probe_rs(
            seen=set(), sessions=[267], from_date=None, to_date=None,
            qtype_filter=None, limit=None, max_buckets=None, max_records=None, download=False,
        )
        assert "Shri Sanjay Singh" in (probe.out_dir / "probe.log").read_text()

    def test_unknown_rs_code_raises(self, tmp_path):
        session = FakeRSSession({})
        probe = _probe(tmp_path, session, mp_code=555)
        with pytest.raises(KeyError):
            probe.resolve_rs_member(555)


class TestLSByCode:
    def test_resolve_ls_member(self, tmp_path):
        probe = _probe(tmp_path, FakePortalSession({}))
        member = probe.resolve_ls_member(4569)
        assert member["mpName"] == "Dr. Shashi Tharoor"
        with pytest.raises(KeyError):
            probe.resolve_ls_member(31337)

    def test_resolve_ls_member_warns_on_same_name_collision(self, tmp_path):
        probe = _probe(tmp_path, FakePortalSession({}))
        probe.resolve_ls_member(101)
        assert "cannot distinguish" in (probe.out_dir / "probe.log").read_text()

    def test_ls_portal_date(self):
        assert SansadProbe._ls_portal_date("09.08.2024") == "2024-08-09"
        assert SansadProbe._ls_portal_date(None) == ""
        assert SansadProbe._ls_portal_date("2024-08-09T00:00") == "2024-08-09"

    def test_probe_ls_mpcode_exact_name_join_and_session_scope(self, tmp_path):
        pages = {
            2: [[_ls_q(1, ["Dr. Shashi Tharoor"]), _ls_q(2, ["Shri Someone Else"])]],
            3: [
                [_ls_q(3, ["Dr. Shashi Tharoor", "Shri Someone Else"], session="3")],
                [_ls_q(4, ["Dr. Shashi Tharoorx"], session="3"), _ls_q(5, ["Dr. Shashi Tharoor"], session="7")],
            ],
        }
        session = FakePortalSession(pages)
        probe = _probe(tmp_path, session, mp_code=4569)
        added = probe.probe_ls_mpcode(
            set(), mp_code=4569, from_date=None, to_date=None,
            qtype_filter=None, max_records=None, download=False,
        )
        # q1 (exact name), q3 (multi-member row) kept; q2/q4 (other/prefix-extended
        # names) dropped; q5 dropped by the session drift guard.
        assert added == 2
        recs = [r for r in _manifest(probe) if r.get("kind") == "qa"]
        assert {r["qno"] for r in recs} == {"1", "3"}
        assert all(r["mp_code"] == 4569 for r in recs)
        assert all(r["source"] == "sansad.in/api_ls/question" for r in recs)
        assert all(r["found_via_query"] == "mp_code:4569" for r in recs)
        assert all(r["loksabhanumber"] == "18" for r in recs)
        # only lastLoksabha=18's sessions (2, 3) were crawled
        assert {int(c["sessionNumber"]) for c in session.question_calls} == {2, 3}
        assert "drift" in (probe.out_dir / "probe.log").read_text()

    def test_probe_ls_mpcode_records_validate_against_schema(self, tmp_path):
        jsonschema = pytest.importorskip("jsonschema")
        from commoner_probe import schemas

        session = FakePortalSession({2: [[_ls_q(1, ["Dr. Shashi Tharoor"])]], 3: []})
        probe = _probe(tmp_path, session, mp_code=4569)
        probe.probe_ls_mpcode(
            set(), mp_code=4569, from_date=None, to_date=None,
            qtype_filter=None, max_records=None, download=False,
        )
        recs = [r for r in _manifest(probe) if r.get("kind") == "qa"]
        assert recs
        schema = schemas.load("manifest_qa")
        for rec in recs:
            jsonschema.validate(rec, schema)


class TestCLIGuards:
    def _argv(self, *extra):
        return ["sansad", "--out", "x", *extra]

    def test_mp_code_flag_parses(self):
        args = build_parser().parse_args(self._argv("--mp-code", "2372", "--house", "rs"))
        assert args.mp_code == 2372

    def test_mp_code_rejects_house_both(self, capsys):
        from commoner_probe.cli import sansad_cmd

        args = build_parser().parse_args(self._argv("--mp-code", "2372"))
        with pytest.raises(SystemExit, match="separate numbering spaces"):
            sansad_cmd(args)

    def test_mp_code_rejects_member(self):
        from commoner_probe.cli import sansad_cmd

        args = build_parser().parse_args(self._argv("--mp-code", "1", "--house", "ls", "--member", "X"))
        with pytest.raises(SystemExit, match="--mp-code cannot be combined"):
            sansad_cmd(args)

    def test_mp_code_rejects_all(self):
        from commoner_probe.cli import sansad_cmd

        args = build_parser().parse_args(
            self._argv("--mp-code", "1", "--house", "rs", "--all", "--sessions", "260-267")
        )
        with pytest.raises(SystemExit, match="--all cannot be combined"):
            sansad_cmd(args)
