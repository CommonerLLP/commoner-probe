# SPDX-License-Identifier: MIT
"""Lok Sabha member-attendance acquisition (sansad.in native API).

Supersedes the REQ-0012 PRS-attendance want: this is a primary source
with no ToS ambiguity, discovered from the sansad.in JS config chunk and
smoke-tested live 2026-07-07 (per REQ-0012's own notes), re-verified live
2026-07-08. Session/date enumeration reuses the same
``AllLoksabhaAndSessionDates`` contract as ``commoner_probe.debates``.

Only the member-wise endpoint is acquired here (one row per member per
session — the grain a per-MP enrichment join needs). The API also exposes
date-wise/month-wise/by-mpsno views of the same underlying attendance
facts; those are redundant re-aggregations of what member-wise already
carries and are not separately acquired.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .http_client import make_session

SESSION_DATES_API = "https://sansad.in/api_ls/business/AllLoksabhaAndSessionDates"
ATTENDANCE_MEMBER_WISE_API = "https://sansad.in/api_ls/member/getMemberAttendanceMemberWise"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class AttendanceProbe:
    """Acquire Lok Sabha member-wise sitting attendance, per session."""

    def __init__(
        self,
        out_dir: Path,
        *,
        sleep: float = 0.5,
        loksabhas: list[int] | None = None,
        sessions: set[int] | None = None,
        api_url: str = ATTENDANCE_MEMBER_WISE_API,
        sessions_api: str = SESSION_DATES_API,
    ) -> None:
        self.out_dir = out_dir
        self.sleep = sleep
        self.loksabhas = loksabhas or [18]
        self.sessions = sessions
        self.api_url = api_url
        self.sessions_api = sessions_api
        self.manifest = out_dir / "manifest.jsonl"
        self.session = make_session(rate_limit_sec=sleep)

    def session_catalog(self) -> list[dict[str, Any]]:
        r = self.session.get(self.sessions_api, timeout=45)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    def iter_sessions(self, catalog: list[dict[str, Any]], loksabha: int) -> list[int]:
        entry = next((e for e in catalog if e.get("loksabha") == loksabha), None)
        if not entry:
            return []
        nos = [s.get("sessionNo") for s in entry.get("sessions", []) if s.get("sessionNo") is not None]
        if self.sessions:
            nos = [n for n in nos if n in self.sessions]
        return sorted(nos)

    def fetch_session_attendance(self, loksabha: int, session_no: int) -> list[dict[str, Any]]:
        params = {"loksabha": loksabha, "session": session_no, "locale": "en"}
        r = self.session.get(self.api_url, params=params, timeout=45)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    def _record(self, loksabha: int, session_no: int, row: dict[str, Any]) -> dict[str, Any]:
        mpsno = row.get("mpsno")
        now = _now()
        return {
            "key": f"ATTENDANCE|{loksabha}|{session_no}|{mpsno}",
            "kind": "attendance",
            "record_type": "attendance",
            "source": "sansad.in/api_ls/member/getMemberAttendanceMemberWise",
            "house": "Lok Sabha",
            "loksabha": loksabha,
            "session_no": session_no,
            "mpsno": mpsno,
            "member_name": row.get("memberName"),
            "constituency": row.get("constituency"),
            "state": row.get("state"),
            "state_code": row.get("stateCode"),
            "signed_days_count": row.get("signedDaysCount"),
            "division": row.get("division"),
            "probed_at": now,
        }

    def load_seen(self) -> set[str]:
        seen: set[str] = set()
        if not self.manifest.exists():
            return seen
        with self.manifest.open(encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("key"):
                    seen.add(rec["key"])
        return seen

    def append_manifest(self, record: dict[str, Any]) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def probe(self, *, max_records: int | None = None, dry_run: bool = False) -> list[dict[str, Any]]:
        seen = self.load_seen()
        catalog = self.session_catalog()
        records: list[dict[str, Any]] = []
        for loksabha in self.loksabhas:
            for session_no in self.iter_sessions(catalog, loksabha):
                key_prefix = f"ATTENDANCE|{loksabha}|{session_no}|"
                if dry_run:
                    records.append({
                        "key": f"{key_prefix}_all",
                        "loksabha": loksabha,
                        "session_no": session_no,
                        "status": "dry_run",
                    })
                    continue
                rows = self.fetch_session_attendance(loksabha, session_no)
                for row in rows:
                    record = self._record(loksabha, session_no, row)
                    if record["key"] in seen:
                        continue
                    self.append_manifest(record)
                    records.append(record)
                    seen.add(record["key"])
                    if max_records is not None and len(records) >= max_records:
                        return records
                if self.sleep:
                    time.sleep(self.sleep)
        return records
