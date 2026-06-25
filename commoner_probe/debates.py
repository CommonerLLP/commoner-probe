# SPDX-License-Identifier: MIT
"""Floor-debate probe (Lok Sabha "text of debate").

Roadmap source (README "Upcoming"): the day-by-day record of Lok Sabha floor
proceedings. The live API serves one PDF transcript per *sitting day* (not the
structured per-speaker JSON the README imagined) — so this is a per-day document
acquisition (sibling of the dmft/budget "source file" probes), topic-less, with
the verbatim-text/speaker extraction left to a downstream Layer-1 step.

Contract (captured live via the debates page's network calls):

    # 1. enumerate sitting dates per Lok Sabha / session:
    GET https://sansad.in/api_ls/business/AllLoksabhaAndSessionDates
        -> [{"loksabha": 18, "sessions": [{"sessionNo": 7,
              "dates": ["28/01/2026", ...]}]}, ...]   # dates are DD/MM/YYYY

    # 2. fetch one day's transcript PDF:
    GET https://sansad.in/api_ls/debate/text-of-debate
        ?loksabha=18&sessionNo=7&debateDate=1/28/2026&locale=en   # date is M/D/YYYY
        -> {"pdfUrl": "https://sansad.in/getFile/dms/fetch/...?source=dsp2"}
           (or {} when no transcript exists for that day)
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
from .sansad import date_in_range

LS_DEBATE_API = "https://sansad.in/api_ls/debate/text-of-debate"
SESSION_DATES_API = "https://sansad.in/api_ls/business/AllLoksabhaAndSessionDates"

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "commoner-probe/0.5.0 (github.com/CommonerLLP/commoner-probe; public-interest research; rate-limited)",
    "Referer": "https://sansad.in/ls/debates/text-of-debates",
}


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
    """Acquire Lok Sabha per-day debate-transcript PDFs (topic-less)."""

    def __init__(
        self,
        out_dir: Path,
        *,
        sleep: float = 0.5,
        loksabhas: list[int] | None = None,
        sessions: list[int] | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        api_url: str = LS_DEBATE_API,
        sessions_api: str = SESSION_DATES_API,
    ) -> None:
        self.out_dir = out_dir
        self.sleep = sleep
        self.loksabhas = loksabhas or [18]
        self.sessions = set(sessions) if sessions else None
        self.from_date = from_date
        self.to_date = to_date
        self.api_url = api_url
        self.sessions_api = sessions_api
        self.manifest = out_dir / "manifest.jsonl"
        self.pdf_dir = out_dir / "pdfs" / "debates"
        self.session = make_session()

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

    def _record(self, loksabha: int, session_no: int, date_iso: str, *, pdf_url: str | None, status: str) -> dict:
        now = _now_iso()
        return {
            "key": f"DEBATE|{loksabha}|{session_no}|{date_iso}",
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

    def _download_pdf(self, url: str, dest: Path) -> tuple[str | None, str | None]:
        """Download the transcript PDF. Returns (rel_path, sha256) or (None, None)."""
        if dest.exists() and dest.stat().st_size > 1000:
            body = dest.read_bytes()
            return str(dest.relative_to(self.out_dir)), hashlib.sha256(body).hexdigest()
        try:
            r = self.session.get(url, headers=HEADERS, timeout=120)
            r.raise_for_status()
            body = r.content
        except Exception:
            return None, None
        if not body:
            return None, None
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        return str(dest.relative_to(self.out_dir)), hashlib.sha256(body).hexdigest()

    def probe(self, *, max_records: int | None = None, download: bool = False, dry_run: bool = False) -> list[dict]:
        seen = self.load_seen()
        out: list[dict] = []
        try:
            catalog = self.session_catalog()
        except Exception as exc:  # noqa: BLE001
            out.append({
                "key": "DEBATE|_catalog|_fetch_error",
                "kind": "floor_debate",
                "record_type": "floor_debate",
                "source": "sansad.in/api_ls/business/AllLoksabhaAndSessionDates",
                "house": "Lok Sabha",
                "fetch_status": "fetch_error",
                "error": str(exc)[:500],
                "probed_at": _now_iso(),
            })
            return out

        for loksabha in self.loksabhas:
            added = 0
            for session_no, raw_date in self.iter_sitting_dates(catalog, loksabha):
                date_iso = date_to_iso(raw_date)
                key = f"DEBATE|{loksabha}|{session_no}|{date_iso}"
                if key in seen:
                    continue
                if dry_run:
                    out.append(self._record(loksabha, session_no, date_iso, pdf_url=None, status="dry_run"))
                    seen.add(key)
                    continue
                try:
                    pdf_url = self.debate_pdf_url(loksabha, session_no, date_to_mdy(raw_date))
                except Exception:  # noqa: BLE001 — one bad date shouldn't abort the crawl
                    continue
                if not pdf_url:
                    continue  # no transcript published for that sitting day
                rec = self._record(loksabha, session_no, date_iso, pdf_url=pdf_url, status="ok")
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
        return out
