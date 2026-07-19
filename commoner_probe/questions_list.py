# SPDX-License-Identifier: MIT
"""Pre-admission List of Questions and Bulletin document acquisition.

Live Sansad contracts captured 2026-07-18 from the LS/RS frontend chunks:

Lok Sabha item-wise business page:
    GET https://sansad.in/api_ls/question/questionListUrl
        ?quesDay=20&quesMonth=7&quesYear=2026&locale=en
        -> {"name": "Questions List", "date": "20/07/2026", "url": "..."}
    GET https://sansad.in/api_ls/business/bulletin1Url
        ?bull1Day=20&bull1Month=7&bull1Year=2026&locale=en
    GET https://sansad.in/api_ls/business/bulletin2Url
        ?bull2Day=20&bull2Month=7&bull2Year=2026&locale=en

Rajya Sabha item-wise business page:
    GET https://sansad.in/api_rs/business/questionUrls
        ?quesDay=20&quesMonth=7&quesYear=2026&locale=en
        -> [{"name": "Starred Questions", "type": "Starred", "url": "..."}, ...]
    GET https://sansad.in/api_rs/business/bulletin1Url ...
    GET https://sansad.in/api_rs/business/bulletin2Url ...
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlencode

from .base import safe_filename_segment
from .debates import date_to_iso
from .http_client import make_session
from .runlog import RunLog
from .sansad import date_in_range
from .textparse import extract_pdf_text

LS_SESSION_DATES_API = "https://sansad.in/api_ls/business/AllLoksabhaAndSessionDates"
RS_SESSION_DATES_API = "https://sansad.in/api_rs/business/sessionDates"

LS_QUESTION_LIST_API = "https://sansad.in/api_ls/question/questionListUrl"
LS_BULLETIN1_API = "https://sansad.in/api_ls/business/bulletin1Url"
LS_BULLETIN2_API = "https://sansad.in/api_ls/business/bulletin2Url"

RS_QUESTION_LIST_API = "https://sansad.in/api_rs/business/questionUrls"
RS_BULLETIN1_API = "https://sansad.in/api_rs/business/bulletin1Url"
RS_BULLETIN2_API = "https://sansad.in/api_rs/business/bulletin2Url"

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "commoner-probe/0.5.0 (github.com/CommonerLLP/commoner-probe; public-interest research; rate-limited)",
    "Referer": "https://sansad.in/ls/business/agenda",
}
PDF_HEADERS = {k: v for k, v in HEADERS.items() if k != "Accept"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _iso_to_dmy(value: str) -> tuple[int, int, int]:
    y, m, d = (int(part) for part in value.split("-"))
    return d, m, y


def _date_range(from_date: str, to_date: str) -> Iterator[str]:
    start = datetime.strptime(from_date, "%Y-%m-%d").date()
    end = datetime.strptime(to_date, "%Y-%m-%d").date()
    if end < start:
        raise ValueError("--to-date must be on or after --from-date")
    cur = start
    while cur <= end:
        yield cur.isoformat()
        cur += timedelta(days=1)


def _doc_url(row: Any) -> str | None:
    if not isinstance(row, dict):
        return None
    url = row.get("url") or row.get("pdfUrl") or row.get("pdf_url")
    return str(url).strip() if url else None


def _normalise_docs(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict) and _doc_url(row)]
    if isinstance(data, dict) and _doc_url(data):
        return [data]
    return []


def _record_type_for(kind: str, raw_type: str | None = None) -> str:
    if kind != "question_list":
        return kind
    if raw_type:
        value = raw_type.lower()
        if "unstar" in value:
            return "question_list_unstarred"
        if "star" in value:
            return "question_list_starred"
    return "question_list"


_QUESTION_ROW_RE = re.compile(
    r"^\s*(?P<qno>\d{1,5})\s+"
    r"(?P<subject>[A-Z][A-Z0-9 ,/&().:'\"-]{4,}?)\s{2,}"
    r"(?P<ministry>[A-Z][A-Z0-9 ,/&().:'\"-]{2,})\s*$"
)
_QUESTION_START_RE = re.compile(
    # LS heads read "*1." / "†*3." with a dot; RS heads read "553 Name:" with no
    # dot. A "#" marker may precede the name in RS lists. Heads always end with
    # ":" - requiring it keeps bare numbers (page numbers, index lines) out.
    r"^\s*[†\u2020]?\s*\*?\s*(?P<qno>\d{1,5})\.?\s*#?\s*"
    r"(?P<asker>(?:Shri|Smt\.?|Shrimati|Sushri|Dr\.?|Prof\.?|Adv\.?|Kumari|Kunwar|"
    r"Ms\.?|Miss|Mr\.?|Mrs\.?|Md\.?|Mohd\.?|Thiru|Thirumathi|Selvi|Km\.?|Kum\.?|"
    r"Sardar|Er\.?|Sant|Baba|Ch\.?|Com\.?)\b.+?):\s*$",
    re.IGNORECASE,
)
_MINISTER_RE = re.compile(
    # RS lists put "be pleased to state:" inline on the minister line; LS lists
    # put it on its own line. The suffix is never part of the ministry name.
    r"^\s*Will the Minister of (?P<ministry>.+?)(?:\s+be\s+pleased\s+to\s+state\s*:?)?\s*$",
    re.IGNORECASE,
)
_NOISE_LINES = {
    "LOK SABHA",
    "RAJYA SABHA",
    "be pleased to state:",
}
_WRITTEN_HEADER_RE = re.compile(r"List of Questions for WRITTEN ANSWERS", re.IGNORECASE)
_CORRIGENDA_HEADER_RE = re.compile(r"^\s*CORRIGEND(?:UM|A)", re.IGNORECASE)
_STATED_TOTAL_RE = re.compile(r"Total\s+number\s+of\s+questions\s*[-–—:]+\s*(\d+)", re.IGNORECASE)


def stated_totals(text: str) -> list[int]:
    """The per-section "Total Number of Questions - N" declarations, in order."""
    return [int(m.group(1)) for m in _STATED_TOTAL_RE.finditer(text)]


def corrigenda_present(text: str) -> bool:
    return any(_CORRIGENDA_HEADER_RE.match(line) for line in text.splitlines())


def _sections(lines: list[str], list_type: str) -> list[tuple[list[str], str]]:
    """Split a list PDF into parseable sections with their list_type.

    The LS daily PDF carries BOTH the oral (starred, *1-20) and written
    (unstarred, 1-230) sections, whose question numbers overlap - they must be
    parsed as separate numbering spaces or written 1-20 vanish as duplicates
    of the starred section. CORRIGENDUM/CORRIGENDA sections (which may sit
    between the two or at the tail, and restate question numbers) are excluded
    from row parsing. RS lists arrive pre-split per type and pass through
    whole, truncated at any corrigenda.
    """
    written_at = next((i for i, line in enumerate(lines) if _WRITTEN_HEADER_RE.search(line)), None)
    corr = [i for i, line in enumerate(lines) if _CORRIGENDA_HEADER_RE.match(line)]
    if list_type == "question_list" and written_at is not None:
        oral_end = min([i for i in corr if i < written_at] + [written_at])
        written_end = min([i for i in corr if i > written_at] + [len(lines)])
        return [
            (lines[:oral_end], "question_list_starred"),
            (lines[written_at:written_end], "question_list_unstarred"),
        ]
    end = min(corr + [len(lines)])
    return [(lines[:end], list_type)]


def _parse_block_segment(
    lines: list[str], *, house: str, sitting_date: str, list_type: str, source_pdf: str
) -> list[dict[str, Any]]:
    """Block-structured rows from one section: subject heading, ``*qno. asker``
    (LS) or ``qno asker:`` (RS), optional more askers, ``Will the Minister of
    ...``, body. Question numbers are a per-section namespace."""
    rows: list[dict[str, Any]] = []
    seen_qnos: set[str] = set()
    i = 0
    while i < len(lines):
        m = _QUESTION_START_RE.match(lines[i])
        if not m:
            i += 1
            continue
        qno = m.group("qno")
        subject = ""
        for prev in range(i - 1, max(-1, i - 8), -1):
            candidate = re.sub(r"\s+", " ", lines[prev]).strip()
            if not candidate or candidate.isdigit() or candidate in _NOISE_LINES:
                continue
            if candidate.lower().startswith(("list of questions", "monday,", "total number")):
                continue
            subject = candidate
            break
        askers = [re.sub(r"\s+", " ", m.group("asker")).strip().rstrip(":")]
        ministry = ""
        body: list[str] = []
        j = i + 1
        while j < len(lines):
            next_start = _QUESTION_START_RE.match(lines[j])
            if next_start:
                break
            minister = _MINISTER_RE.match(lines[j])
            if minister:
                ministry = re.sub(r"\s+", " ", minister.group("ministry")).strip()
                j += 1
                continue
            clean = re.sub(r"\s+", " ", lines[j]).strip()
            if clean:
                if not ministry and clean.endswith(":") and not clean.lower().startswith(("be pleased", "list of")):
                    askers.append(clean.rstrip(":"))
                elif ministry and clean not in _NOISE_LINES and not clean.isdigit():
                    body.append(clean)
            j += 1
        if qno not in seen_qnos:
            rows.append({
                "key": f"QUESTION_ROW|{house}|{sitting_date}|{list_type}|{qno}",
                "kind": "question_list_row",
                "house": house,
                "sitting_date": sitting_date,
                "list_type": list_type,
                "qno": qno,
                "askers": [a for a in askers if a],
                "subject": subject,
                "ministry": ministry,
                "text": "\n".join(body),
                "source_pdf": source_pdf,
                "extractor": "commoner_probe.questions_list.parse_question_rows.v2",
                "extracted_at": _now_iso(),
            })
            seen_qnos.add(qno)
        i = max(j, i + 1)
    return rows


def parse_question_rows(text: str, *, house: str, sitting_date: str, list_type: str, source_pdf: str) -> list[dict[str, Any]]:
    """Extract rows from observed Sansad question-list PDF text.

    Sections are parsed independently (see ``_sections``): the LS daily PDF
    holds overlapping starred and unstarred numbering spaces, so rows carry the
    section's list_type, not the whole document's. The table-line fallback is
    kept for older or alternate layouts and only runs when no block rows parse.
    """
    all_lines = [line.rstrip() for line in text.splitlines()]
    rows: list[dict[str, Any]] = []
    for seg_lines, seg_type in _sections(all_lines, list_type):
        rows.extend(
            _parse_block_segment(
                seg_lines, house=house, sitting_date=sitting_date,
                list_type=seg_type, source_pdf=source_pdf,
            )
        )
    if rows:
        return rows
    seen_qnos: set[str] = set()
    for line in all_lines:
        m = _QUESTION_ROW_RE.match(line)
        if not m:
            continue
        if m.group("qno") in seen_qnos:
            continue
        subject = re.sub(r"\s+", " ", m.group("subject")).strip(" -")
        ministry = re.sub(r"\s+", " ", m.group("ministry")).strip(" -")
        if not subject or not ministry:
            continue
        rows.append({
            "key": f"QUESTION_ROW|{house}|{sitting_date}|{list_type}|{m.group('qno')}",
            "kind": "question_list_row",
            "house": house,
            "sitting_date": sitting_date,
            "list_type": list_type,
            "qno": m.group("qno"),
            "askers": [],
            "subject": subject,
            "ministry": ministry,
            "text": "",
            "source_pdf": source_pdf,
            "extractor": "commoner_probe.questions_list.parse_question_rows.v2",
            "extracted_at": _now_iso(),
        })
        seen_qnos.add(m.group("qno"))
    return rows

class QuestionsListProbe:
    """Acquire daily question-list and Bulletin PDFs for Sansad sitting dates."""

    _TERMINAL_STATUSES = frozenset({"downloaded", "not_published"})

    def __init__(
        self,
        out_dir: Path,
        *,
        house: str = "both",
        loksabhas: list[int] | None = None,
        sessions: list[int] | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        sleep: float = 0.25,
    ) -> None:
        if house not in {"ls", "rs", "both"}:
            raise ValueError("house must be one of: ls, rs, both")
        self.out_dir = out_dir
        self.house = house
        self.loksabhas = loksabhas or [18]
        self.sessions = set(sessions) if sessions else None
        self.from_date = from_date
        self.to_date = to_date
        self.sleep = sleep
        self.manifest = out_dir / "manifest.jsonl"
        self.questions_path = out_dir / "questions_list.jsonl"
        self.pdf_dir = out_dir / "pdfs" / "questions-list"
        self.session = make_session()
        self.runlog = RunLog(out_dir)

    def load_seen(self) -> dict[str, str]:
        seen: dict[str, str] = {}
        if not self.manifest.exists():
            return seen
        with self.manifest.open(encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("kind") == "question_list" and rec.get("key"):
                    seen[str(rec["key"])] = self._resume_status(rec)
        return seen

    @staticmethod
    def _resume_status(rec: dict[str, Any]) -> str:
        # A downloaded PDF whose parsed rows failed to reconcile against the
        # list's own stated total must stay retryable: a later parser fix has
        # to be able to re-extract it.
        if rec.get("parse_status") == "count_mismatch":
            return "count_mismatch"
        return str(rec.get("fetch_status") or "")

    def append_manifest(self, record: dict[str, Any]) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def replace_question_rows(self, source_pdf: str, rows: list[dict[str, Any]]) -> None:
        """Drop any prior rows for ``source_pdf``, then write the fresh parse.

        Reprocessing after a ``count_mismatch`` must not duplicate the rows the
        failed parse already appended.
        """
        kept: list[str] = []
        if self.questions_path.exists():
            for line in self.questions_path.read_text(encoding="utf-8").splitlines():
                try:
                    prior = json.loads(line)
                except json.JSONDecodeError:
                    kept.append(line)
                    continue
                if prior.get("source_pdf") != source_pdf:
                    kept.append(line)
        if not kept and not rows:
            return
        self.out_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.questions_path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for line in kept:
                f.write(line + "\n")
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        tmp.replace(self.questions_path)

    def ls_session_catalog(self) -> list[dict[str, Any]]:
        r = self.session.get(LS_SESSION_DATES_API, headers=HEADERS, timeout=45)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    def rs_session_catalog(self) -> list[dict[str, Any]]:
        r = self.session.get(RS_SESSION_DATES_API, headers=HEADERS, timeout=45)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    def iter_ls_sitting_dates(self, catalog: list[dict[str, Any]]) -> Iterator[tuple[int, int, str]]:
        for loksabha in self.loksabhas:
            entry = next((e for e in catalog if e.get("loksabha") == loksabha), None)
            if not entry:
                continue
            for sess in entry.get("sessions", []):
                session_no = sess.get("sessionNo")
                if self.sessions and session_no not in self.sessions:
                    continue
                for raw_date in sess.get("dates", []):
                    try:
                        iso = date_to_iso(raw_date)
                    except (ValueError, AttributeError):
                        continue
                    if date_in_range(iso, self.from_date, self.to_date):
                        yield loksabha, int(session_no), iso

    def iter_rs_sitting_dates(self, catalog: list[dict[str, Any]]) -> Iterator[tuple[int, str]]:
        for entry in catalog:
            session_no = entry.get("session")
            if self.sessions and session_no not in self.sessions:
                continue
            for raw_date in entry.get("sittingDates", []):
                try:
                    iso = date_to_iso(raw_date)
                except (ValueError, AttributeError):
                    continue
                if date_in_range(iso, self.from_date, self.to_date):
                    yield int(session_no), iso

    def fetch_daily_docs(self, api_url: str, *, date_iso: str, day_key: str, month_key: str, year_key: str) -> list[dict[str, Any]]:
        d, m, y = _iso_to_dmy(date_iso)
        params = {day_key: d, month_key: m, year_key: y, "locale": "en"}
        r = self.session.get(f"{api_url}?{urlencode(params)}", headers=HEADERS, timeout=45)
        r.raise_for_status()
        if not r.text.strip():
            return []
        return _normalise_docs(r.json())

    def _download_pdf(self, url: str, dest: Path) -> tuple[str | None, str | None]:
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

    def _record(
        self,
        *,
        key: str,
        source: str,
        house: str,
        sitting_date: str,
        session_no: int,
        document_kind: str,
        document_type: str,
        pdf_url: str | None,
        run_id: str,
        loksabha: int | None = None,
        status: str = "metadata_only",
    ) -> dict[str, Any]:
        now = _now_iso()
        return {
            "key": key,
            "run_id": run_id,
            "kind": "question_list",
            "record_type": document_type,
            "source": source,
            "house": house,
            "loksabha": loksabha,
            "session_no": session_no,
            "sitting_date": sitting_date,
            "document_kind": document_kind,
            "document_type": document_type,
            "pdf_url": pdf_url,
            "pdf_path": None,
            "sha256": None,
            "fetch_status": status,
            "fetched_at": now,
            "probed_at": now,
        }

    def _start_run(self, *, dry_run: bool) -> str:
        return self.runlog.start(
            kind="question_list",
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

    def _handle_doc(
        self,
        *,
        seen: dict[str, str],
        run_id: str,
        source: str,
        house: str,
        session_no: int,
        sitting_date: str,
        document_kind: str,
        row: dict[str, Any],
        download: bool,
        loksabha: int | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any] | None:
        pdf_url = _doc_url(row)
        raw_type = str(row.get("type") or row.get("name") or document_kind)
        document_type = _record_type_for(document_kind, raw_type)
        scope = f"LS{loksabha}" if loksabha else "RS"
        key = f"QUESTION_LIST|{scope}|{session_no}|{sitting_date}|{safe_filename_segment(document_type)}"
        prior = seen.get(key)
        if prior in self._TERMINAL_STATUSES or (prior is not None and not download):
            return None
        rec = self._record(
            key=key,
            source=source,
            house=house,
            loksabha=loksabha,
            sitting_date=sitting_date,
            session_no=session_no,
            document_kind=document_kind,
            document_type=document_type,
            pdf_url=pdf_url,
            run_id=run_id,
            status="metadata_only",
        )
        if download and pdf_url:
            fname = f"{scope.lower()}_s{session_no}_{safe_filename_segment(sitting_date)}_{safe_filename_segment(document_type)}.pdf"
            rel, sha = self._download_pdf(pdf_url, self.pdf_dir / fname)
            rec["pdf_path"], rec["sha256"] = rel, sha
            rec["fetch_status"] = "downloaded" if rel else "download_error"
            if rel and document_kind == "question_list":
                text = extract_pdf_text(self.out_dir / rel)
                rows = parse_question_rows(text, house=house, sitting_date=sitting_date, list_type=document_type, source_pdf=rel)
                self.replace_question_rows(rel, rows)
                rec["question_rows_extracted"] = len(rows)
                expected = stated_totals(text)
                rec["question_rows_expected"] = sum(expected) if expected else None
                rec["corrigenda_present"] = corrigenda_present(text)
                if not expected:
                    rec["parse_status"] = "no_stated_total"
                elif sum(expected) == len(rows):
                    rec["parse_status"] = "reconciled"
                else:
                    # the list itself declares its size; a mismatch is a parse
                    # failure, not noise
                    rec["parse_status"] = "count_mismatch"
                    self.runlog.record_error(
                        f"parse:{rel}",
                        ValueError(
                            f"stated totals {expected} (sum {sum(expected)}) != "
                            f"{len(rows)} parsed rows"
                        ),
                    )
        if dry_run:
            # Preview only: nothing may land in the manifest or the seen-set,
            # or a later real run would skip the document as already recorded.
            return rec
        self.append_manifest(rec)
        seen[key] = self._resume_status(rec)
        return rec

    def probe(self, *, max_records: int | None = None, download: bool = True, dry_run: bool = False) -> list[dict[str, Any]]:
        if not self.from_date or not self.to_date:
            raise ValueError("--from-date and --to-date are required")
        list(_date_range(self.from_date, self.to_date))
        seen = self.load_seen()
        run_id = self._start_run(dry_run=dry_run)
        records: list[dict[str, Any]] = []
        added = 0

        if self.house in {"ls", "both"}:
            catalog = self.ls_session_catalog()
            for loksabha, session_no, date_iso in self.iter_ls_sitting_dates(catalog):
                for api_url, kind, keys in [
                    (LS_QUESTION_LIST_API, "question_list", ("quesDay", "quesMonth", "quesYear")),
                    (LS_BULLETIN1_API, "bulletin1", ("bull1Day", "bull1Month", "bull1Year")),
                    (LS_BULLETIN2_API, "bulletin2", ("bull2Day", "bull2Month", "bull2Year")),
                ]:
                    rows = [] if dry_run else self.fetch_daily_docs(api_url, date_iso=date_iso, day_key=keys[0], month_key=keys[1], year_key=keys[2])
                    if dry_run:
                        rows = [{"name": kind, "type": kind, "url": None}]
                    for row in rows:
                        rec = self._handle_doc(
                            seen=seen,
                            run_id=run_id,
                            source=api_url,
                            house="Lok Sabha",
                            loksabha=loksabha,
                            session_no=session_no,
                            sitting_date=date_iso,
                            document_kind=kind,
                            row=row,
                            download=download and not dry_run,
                            dry_run=dry_run,
                        )
                        if rec:
                            records.append(rec)
                            added += 1
                            # one API response may carry several documents (RS
                            # returns starred+unstarred together) - the brake
                            # must bite per document, not per endpoint
                            if max_records is not None and added >= max_records:
                                break
                    if max_records is not None and added >= max_records:
                        break
                    if self.sleep:
                        time.sleep(self.sleep)
                if max_records is not None and added >= max_records:
                    break

        if self.house in {"rs", "both"} and (max_records is None or added < max_records):
            catalog = self.rs_session_catalog()
            for session_no, date_iso in self.iter_rs_sitting_dates(catalog):
                for api_url, kind, keys in [
                    (RS_QUESTION_LIST_API, "question_list", ("quesDay", "quesMonth", "quesYear")),
                    (RS_BULLETIN1_API, "bulletin1", ("bull1Day", "bull1Month", "bull1Year")),
                    (RS_BULLETIN2_API, "bulletin2", ("bull2Day", "bull2Month", "bull2Year")),
                ]:
                    rows = [] if dry_run else self.fetch_daily_docs(api_url, date_iso=date_iso, day_key=keys[0], month_key=keys[1], year_key=keys[2])
                    if dry_run:
                        rows = [{"name": kind, "type": kind, "url": None}]
                    for row in rows:
                        rec = self._handle_doc(
                            seen=seen,
                            run_id=run_id,
                            source=api_url,
                            house="Rajya Sabha",
                            session_no=session_no,
                            sitting_date=date_iso,
                            document_kind=kind,
                            row=row,
                            download=download and not dry_run,
                            dry_run=dry_run,
                        )
                        if rec:
                            records.append(rec)
                            added += 1
                            if max_records is not None and added >= max_records:
                                break
                    if max_records is not None and added >= max_records:
                        break
                    if self.sleep:
                        time.sleep(self.sleep)
                if max_records is not None and added >= max_records:
                    break

        self.runlog.finish(added=added)
        return records
