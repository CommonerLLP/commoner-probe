"""Tests for the Lok Sabha member-attendance probe.

Fixtures mirror the live sansad.in API contract verified 2026-07-08:
AllLoksabhaAndSessionDates for session enumeration, and
getMemberAttendanceMemberWise for per-session attendance rows.
"""

from __future__ import annotations

import json

from commoner_probe.attendance import AttendanceProbe

CATALOG = [
    {"loksabha": 18, "sessions": [
        {"sessionNo": 1, "dates": ["24/06/2024"]},
        {"sessionNo": 5, "dates": ["22/07/2024", "26/07/2024"]},
    ]},
]

# Real shape verified live 2026-07-08 (session 1: leadership rows all 0;
# session 5: non-uniform real values).
ATTENDANCE_SESSION_1 = [
    {"mpsno": 4589, "memberName": "Narendra Modi", "constituency": "Varanasi",
     "state": "Uttar Pradesh", "stateCode": "UP", "signedDaysCount": 0, "division": "1"},
    {"mpsno": 4268, "memberName": "Rajnath Singh", "constituency": "Lucknow",
     "state": "Uttar Pradesh", "stateCode": "UP", "signedDaysCount": 0, "division": "2"},
]
ATTENDANCE_SESSION_5 = [
    {"mpsno": 4455, "memberName": "Sanjay Jaiswal", "constituency": "Paschim Champaran",
     "state": "Bihar", "stateCode": "BR", "signedDaysCount": 19, "division": "17"},
    {"mpsno": 4324, "memberName": "Nishikant Dubey", "constituency": "Godda",
     "state": "Jharkhand", "stateCode": "JH", "signedDaysCount": 21, "division": "23"},
]


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, *, params=None, **kwargs):
        self.calls.append((url, params or {}))
        if "AllLoksabhaAndSessionDates" in url:
            return FakeResponse(CATALOG)
        if "getMemberAttendanceMemberWise" in url:
            session = params.get("session")
            if session == 1:
                return FakeResponse(ATTENDANCE_SESSION_1)
            if session == 5:
                return FakeResponse(ATTENDANCE_SESSION_5)
            return FakeResponse([])
        raise AssertionError(f"unrouted url: {url}")


def _probe(tmp_path, **kw):
    probe = AttendanceProbe(tmp_path, sleep=0, **kw)
    probe.session = FakeSession()
    return probe


def test_probe_records_session_attendance(tmp_path):
    probe = _probe(tmp_path, loksabhas=[18], sessions={1})
    records = probe.probe()

    assert len(records) == 2
    rec = records[0]
    assert rec["key"] == "ATTENDANCE|18|1|4589"
    assert rec["house"] == "Lok Sabha"
    assert rec["loksabha"] == 18
    assert rec["session_no"] == 1
    assert rec["member_name"] == "Narendra Modi"
    assert rec["signed_days_count"] == 0

    manifest = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert manifest == records


def test_nonzero_attendance_values_pass_through(tmp_path):
    probe = _probe(tmp_path, loksabhas=[18], sessions={5})
    records = probe.probe()
    values = {r["member_name"]: r["signed_days_count"] for r in records}
    assert values == {"Sanjay Jaiswal": 19, "Nishikant Dubey": 21}


def test_session_filter(tmp_path):
    probe = _probe(tmp_path, loksabhas=[18])  # all sessions in the catalog
    records = probe.probe()
    sessions_seen = {r["session_no"] for r in records}
    assert sessions_seen == {1, 5}


def test_dry_run_lists_windows_without_fetching_attendance(tmp_path):
    probe = _probe(tmp_path, loksabhas=[18], sessions={1})
    records = probe.probe(dry_run=True)
    assert len(records) == 1
    assert records[0]["status"] == "dry_run"
    assert not any("getMemberAttendanceMemberWise" in u for u, _ in probe.session.calls)
    assert not (tmp_path / "manifest.jsonl").exists()


def test_dedup_on_rerun(tmp_path):
    _probe(tmp_path, loksabhas=[18], sessions={1}).probe()
    assert _probe(tmp_path, loksabhas=[18], sessions={1}).probe() == []


def test_max_records_brake(tmp_path):
    probe = _probe(tmp_path, loksabhas=[18], sessions={5})
    records = probe.probe(max_records=1)
    assert len(records) == 1


def test_schema_bundled_and_validates(tmp_path):
    import pytest

    pytest.importorskip("jsonschema")
    from commoner_probe import schemas
    from commoner_probe.validate import validate_corpus

    assert "manifest_attendance" in schemas.list_all()
    record = {
        "key": "ATTENDANCE|18|1|4589",
        "kind": "attendance",
        "record_type": "attendance",
        "source": "sansad.in/api_ls/member/getMemberAttendanceMemberWise",
        "house": "Lok Sabha",
        "loksabha": 18,
        "session_no": 1,
        "mpsno": 4589,
        "member_name": "Narendra Modi",
        "constituency": "Varanasi",
        "state": "Uttar Pradesh",
        "state_code": "UP",
        "signed_days_count": 0,
        "division": "1",
        "probed_at": "2026-07-08T18:00:00Z",
    }
    (tmp_path / "manifest.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    assert validate_corpus(tmp_path, log=lambda _: None)


def test_corpus_streams_attendance(tmp_path):
    from commoner_probe import Corpus

    record = {
        "key": "ATTENDANCE|18|1|4589",
        "kind": "attendance",
        "record_type": "attendance",
        "source": "sansad.in/api_ls/member/getMemberAttendanceMemberWise",
        "house": "Lok Sabha",
        "loksabha": 18,
        "session_no": 1,
        "mpsno": 4589,
        "member_name": "Narendra Modi",
        "probed_at": "2026-07-08T18:00:00Z",
    }
    (tmp_path / "manifest.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    records = list(Corpus(tmp_path).manifest_attendance())
    assert len(records) == 1
    assert records[0].member_name == "Narendra Modi"
