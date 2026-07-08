# SPDX-License-Identifier: MIT
"""Floor-debate probe (Lok Sabha and Rajya Sabha verbatim debate PDFs).

Roadmap source (README "Upcoming"): the day-by-day record of Lok Sabha floor
proceedings. The live API serves one PDF transcript per *sitting day* (not the
structured per-speaker JSON the README imagined) — so this is a per-day document
acquisition (sibling of the dmft/budget "source file" probes), topic-less, with
the verbatim-text/speaker extraction left to a downstream Layer-1 step.

Lok Sabha contract (captured live via the debates page's network calls):

    # 1. enumerate sitting dates per Lok Sabha / session:
    GET https://sansad.in/api_ls/business/AllLoksabhaAndSessionDates
        -> [{"loksabha": 18, "sessions": [{"sessionNo": 7,
              "dates": ["28/01/2026", ...]}]}, ...]   # dates are DD/MM/YYYY

    # 2. fetch one day's transcript PDF:
    GET https://sansad.in/api_ls/debate/text-of-debate
        ?loksabha=18&sessionNo=7&debateDate=1/28/2026&locale=en   # date is M/D/YYYY
        -> {"pdfUrl": "https://sansad.in/getFile/dms/fetch/...?source=dsp2"}
           (or {} when no transcript exists for that day)

Rajya Sabha contract (captured live via the debates/verbatim page's network
calls):

    # 1. enumerate sitting dates per Rajya Sabha session:
    GET https://sansad.in/api_rs/business/sessionDates
        -> [{"session": 270, "sittingDates": ["28/01/2026", ...]}, ...]

    # 2. fetch one day's verbatim transcript PDFs:
    GET https://rsdoc.nic.in/business/BusinessVerbatim
        ?ses_no=270&ses_dt=28/01/2026
        -> [{"FileUrl": ".../Full Day//28.01 Full Day.pdf", "Time": "Full Day"}, ...]
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import urlencode

from .base import safe_filename_segment
from .http_client import make_session
from .runlog import RunLog
from .sansad import date_in_range

LS_DEBATE_API = "https://sansad.in/api_ls/debate/text-of-debate"
LS_SESSION_DATES_API = "https://sansad.in/api_ls/business/AllLoksabhaAndSessionDates"
SESSION_DATES_API = LS_SESSION_DATES_API
RS_DEBATE_API = "https://rsdoc.nic.in/business/BusinessVerbatim"
RS_SESSION_DATES_API = "https://sansad.in/api_rs/business/sessionDates"

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "commoner-probe/0.5.0 (github.com/CommonerLLP/commoner-probe; public-interest research; rate-limited)",
    "Referer": "https://sansad.in/ls/debates/text-of-debates",
}

# rsdoc.nic.in/cms.rajyasabha.nic.in returns 406 Not Acceptable for PDF
# requests sent with Accept: application/json (verified live 2026-07-08);
# the LS PDF host (sansad.in/getFile/dms/fetch) does not enforce this, but
# the binary download must not reuse the JSON-API Accept header regardless.
PDF_HEADERS = {k: v for k, v in HEADERS.items() if k != "Accept"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _ddmmyyyy_parts(value: str) -> tuple[int, int, int]:
    d, m, y = (int(x) for x in value.strip().split("/"))
    return y, m, d


def date_to_iso(value: str) -> str:
    """'28/01/2026' (DD/MM/YYYY, from the catalog) -> '2026-01-28'."""
    y, m, d = _ddmmyyyy_parts(value)
    return f"{y:04d}-{m:02d}-{d:02d}"


def date_to_mdy(value: str) -> str:
    """'28/01/2026' -> '1/28/2026' (M/D/YYYY, what the debate API expects)."""
    y, m, d = _ddmmyyyy_parts(value)
    return f"{m}/{d}/{y}"


class DebateProbe:
    """Acquire per-day debate-transcript PDFs (topic-less)."""

    def __init__(
        self,
        out_dir: Path,
        *,
        sleep: float = 0.5,
        loksabhas: list[int] | None = None,
        sessions: list[int] | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        house: str = "ls",
        api_url: str = LS_DEBATE_API,
        sessions_api: str = LS_SESSION_DATES_API,
        rs_api_url: str = RS_DEBATE_API,
        rs_sessions_api: str = RS_SESSION_DATES_API,
    ) -> None:
        self.out_dir = out_dir
        self.sleep = sleep
        self.loksabhas = loksabhas or [18]
        self.sessions = set(sessions) if sessions else None
        self.from_date = from_date
        self.to_date = to_date
        if house not in {"ls", "rs", "both"}:
            raise ValueError("house must be one of: ls, rs, both")
        self.house = house
        self.api_url = api_url
        self.sessions_api = sessions_api
        self.rs_api_url = rs_api_url
        self.rs_sessions_api = rs_sessions_api
        self.manifest = out_dir / "manifest.jsonl"
        self.pdf_dir = out_dir / "pdfs" / "debates"
        self.session = make_session()
        self.runlog = RunLog(out_dir)

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

    def append_manifest(self, record: dict) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def session_catalog(self) -> list[dict]:
        r = self.session.get(self.sessions_api, headers=HEADERS, timeout=45)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    def rs_session_catalog(self) -> list[dict]:
        r = self.session.get(self.rs_sessions_api, headers=HEADERS, timeout=45)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    def iter_sitting_dates(self, catalog: list[dict], loksabha: int) -> Iterator[tuple[int, str]]:
        """Yield (session_no, date_ddmmyyyy) for a Lok Sabha, honouring the
        session filter and from/to date range."""
        entry = next((e for e in catalog if e.get("loksabha") == loksabha), None)
        if not entry:
            return
        for sess in entry.get("sessions", []):
            sno = sess.get("sessionNo")
            if self.sessions and sno not in self.sessions:
                continue
            for raw_date in sess.get("dates", []):
                try:
                    iso = date_to_iso(raw_date)
                except (ValueError, AttributeError):
                    continue
                if not date_in_range(iso, self.from_date, self.to_date):
                    continue
                yield sno, raw_date

    def debate_pdf_url(self, loksabha: int, session_no: int, date_mdy: str) -> str | None:
        params = {
            "loksabha": loksabha,
            "sessionNo": session_no,
            "debateDate": date_mdy,
            "locale": "en",
        }
        url = f"{self.api_url}?{urlencode(params)}"
        r = self.session.get(url, headers=HEADERS, timeout=45)
        r.raise_for_status()
        data = r.json()
        return (data or {}).get("pdfUrl") or None

    def rs_debate_pdfs(self, session_no: int, date_ddmmyyyy: str) -> list[dict]:
        params = {"ses_no": session_no, "ses_dt": date_ddmmyyyy}
        url = f"{self.rs_api_url}?{urlencode(params)}"
        r = self.session.get(url, headers=HEADERS, timeout=45)
        r.raise_for_status()
        data = r.json()
        rows = data if isinstance(data, list) else []
        pdfs = [row for row in rows if row.get("FileUrl")]
        full_day = [
            row for row in pdfs
            if "full day" in str(row.get("Time") or row.get("Name") or "").lower()
        ]
        return full_day or pdfs

    def _ls_record(
        self,
        loksabha: int,
        session_no: int,
        date_iso: str,
        *,
        pdf_url: str | None,
        status: str,
        run_id: str,
    ) -> dict:
        now = _now_iso()
        return {
            "key": f"DEBATE|{loksabha}|{session_no}|{date_iso}",
            "run_id": run_id,
            "kind": "floor_debate",
            "record_type": "floor_debate",
            "source": "sansad.in/api_ls/debate/text-of-debate",
            "house": "Lok Sabha",
            "loksabha": loksabha,
            "session_no": session_no,
            "date": date_iso,
            "pdf_url": pdf_url,
            "pdf_path": None,
            "sha256": None,
            "fetch_status": status,
            "fetched_at": now,
            "probed_at": now,
        }

    def _rs_record(
        self,
        session_no: int,
        date_iso: str,
        *,
        pdf_url: str | None,
        status: str,
        run_id: str,
        segment: str | None = None,
    ) -> dict:
        now = _now_iso()
        suffix = f"|{safe_filename_segment(segment)}" if segment else ""
        return {
            "key": f"DEBATE|RS|{session_no}|{date_iso}{suffix}",
            "run_id": run_id,
            "kind": "floor_debate",
            "record_type": "floor_debate",
            "source": "rsdoc.nic.in/business/BusinessVerbatim",
            "house": "Rajya Sabha",
            "loksabha": None,
            "session_no": session_no,
            "date": date_iso,
            "segment": segment,
            "pdf_url": pdf_url,
            "pdf_path": None,
            "sha256": None,
            "fetch_status": status,
            "fetched_at": now,
            "probed_at": now,
        }

    def _download_pdf(self, url: str, dest: Path) -> tuple[str | None, str | None]:
        """Download the transcript PDF. Returns (rel_path, sha256) or (None, None)."""
        if dest.exists() and dest.stat().st_size > 1000:
            body = dest.read_bytes()
            return str(dest.relative_to(self.out_dir)), hashlib.sha256(body).hexdigest()
        try:
            r = self.session.get(url, headers=PDF_HEADERS, timeout=120)
            r.raise_for_status()
            body = r.content
        except Exception as exc:  # noqa: BLE001
            self.runlog.record_error(f"download:{url}", exc)
            return None, None
        if not body:
            return None, None
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        return str(dest.relative_to(self.out_dir)), hashlib.sha256(body).hexdigest()

    def _iter_rs_sitting_dates(self, catalog: list[dict]) -> Iterator[tuple[int, str]]:
        for entry in catalog:
            session_no = entry.get("session")
            if self.sessions and session_no not in self.sessions:
                continue
            for raw_date in entry.get("sittingDates", []):
                try:
                    iso = date_to_iso(raw_date)
                except (ValueError, AttributeError):
                    continue
                if not date_in_range(iso, self.from_date, self.to_date):
                    continue
                yield session_no, raw_date

    def _start_run(self, *, dry_run: bool) -> str:
        return self.runlog.start(
            kind="floor_debate",
            scope={
                "house": self.house,
                "loksabhas": self.loksabhas,
                "sessions": sorted(self.sessions) if self.sessions else None,
                "from_date": self.from_date,
                "to_date": self.to_date,
                "dry_run": dry_run,
            },
            topic_name="",
            topic_path=None,
            classifier_mode="",
            classifier_config={},
        )

    def probe(self, *, max_records: int | None = None, download: bool = False, dry_run: bool = False) -> list[dict]:
        seen = self.load_seen()
        run_id = self._start_run(dry_run=dry_run)
        out: list[dict] = []
        added = 0
        if self.house in {"ls", "both"}:
            try:
                catalog = self.session_catalog()
            except Exception as exc:  # noqa: BLE001
                self.runlog.record_error("ls_session_catalog", exc)
                out.append({
                    "key": "DEBATE|LS|_catalog|_fetch_error",
                    "run_id": run_id,
                    "kind": "floor_debate",
                    "record_type": "floor_debate",
                    "source": "sansad.in/api_ls/business/AllLoksabhaAndSessionDates",
                    "house": "Lok Sabha",
                    "fetch_status": "fetch_error",
                    "error": str(exc)[:500],
                    "probed_at": _now_iso(),
                })
            else:
                for loksabha in self.loksabhas:
                    for session_no, raw_date in self.iter_sitting_dates(catalog, loksabha):
                        date_iso = date_to_iso(raw_date)
                        key = f"DEBATE|{loksabha}|{session_no}|{date_iso}"
                        if key in seen:
                            continue
                        if dry_run:
                            out.append(self._ls_record(
                                loksabha, session_no, date_iso, pdf_url=None, status="dry_run", run_id=run_id
                            ))
                            seen.add(key)
                            continue
                        try:
                            pdf_url = self.debate_pdf_url(loksabha, session_no, date_to_mdy(raw_date))
                        except Exception as exc:  # noqa: BLE001
                            self.runlog.record_error(f"ls:{loksabha}:{session_no}:{date_iso}", exc)
                            continue
                        if not pdf_url:
                            continue
                        rec = self._ls_record(loksabha, session_no, date_iso, pdf_url=pdf_url, status="ok", run_id=run_id)
                        if download:
                            fname = f"ls{loksabha}_s{session_no}_{safe_filename_segment(date_iso)}.pdf"
                            rel, sha = self._download_pdf(pdf_url, self.pdf_dir / fname)
                            rec["pdf_path"], rec["sha256"] = rel, sha
                        self.append_manifest(rec)
                        seen.add(key)
                        out.append(rec)
                        added += 1
                        if max_records is not None and added >= max_records:
                            break
                        if self.sleep:
                            time.sleep(self.sleep)
                    if max_records is not None and added >= max_records:
                        break

        if self.house in {"rs", "both"} and (max_records is None or added < max_records):
            try:
                rs_catalog = self.rs_session_catalog()
            except Exception as exc:  # noqa: BLE001
                self.runlog.record_error("rs_session_catalog", exc)
                out.append({
                    "key": "DEBATE|RS|_catalog|_fetch_error",
                    "run_id": run_id,
                    "kind": "floor_debate",
                    "record_type": "floor_debate",
                    "source": "sansad.in/api_rs/business/sessionDates",
                    "house": "Rajya Sabha",
                    "fetch_status": "fetch_error",
                    "error": str(exc)[:500],
                    "probed_at": _now_iso(),
                })
            else:
                for session_no, raw_date in self._iter_rs_sitting_dates(rs_catalog):
                    date_iso = date_to_iso(raw_date)
                    key_prefix = f"DEBATE|RS|{session_no}|{date_iso}"
                    if dry_run:
                        key = key_prefix
                        if key in seen:
                            continue
                        out.append(self._rs_record(session_no, date_iso, pdf_url=None, status="dry_run", run_id=run_id))
                        seen.add(key)
                        continue
                    try:
                        pdfs = self.rs_debate_pdfs(session_no, raw_date)
                    except Exception as exc:  # noqa: BLE001
                        self.runlog.record_error(f"rs:{session_no}:{date_iso}", exc)
                        continue
                    for row in pdfs:
                        segment = str(row.get("Time") or row.get("Name") or "").strip() or None
                        key = key_prefix + (f"|{safe_filename_segment(segment)}" if segment else "")
                        if key in seen:
                            continue
                        rec = self._rs_record(
                            session_no,
                            date_iso,
                            pdf_url=row.get("FileUrl"),
                            status="ok",
                            run_id=run_id,
                            segment=segment,
                        )
                        if download and rec["pdf_url"]:
                            suffix = safe_filename_segment(segment or "full-day")
                            fname = f"rs_s{session_no}_{safe_filename_segment(date_iso)}_{suffix}.pdf"
                            rel, sha = self._download_pdf(rec["pdf_url"], self.pdf_dir / fname)
                            rec["pdf_path"], rec["sha256"] = rel, sha
                        self.append_manifest(rec)
                        seen.add(key)
                        out.append(rec)
                        added += 1
                        if max_records is not None and added >= max_records:
                            break
                        if self.sleep:
                            time.sleep(self.sleep)
                    if max_records is not None and added >= max_records:
                        break
        self.runlog.finish(added=added)
        return out
