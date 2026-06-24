# SPDX-License-Identifier: MIT
"""PDF text + field-extraction helpers for academic recruitment ads.

Ported from academiaindia/scraper/pdf_extractor.py (the subset the migrated
parsers need). Two differences from the origin:

* Downloads route through the probe HTTP session (SSRF guard / robots / rate-
  limit already enforced there) instead of a bare ``requests.get`` + the origin
  ``url_safety`` duplicate.
* ``extract_text`` prefers Poppler ``pdftotext -layout`` (best for the tabular
  rolling-ad PDFs) but falls back to ``pdfminer.six`` (the ``pdf`` extra) when
  Poppler is not on PATH, instead of hard-failing. Returns ``None`` if neither
  is available — callers degrade to excerpt-less records.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PDFTOTEXT = shutil.which("pdftotext")

# Lower bound on accepted deadline years (see origin constants.py).
_HARD_FLOOR_DEADLINE_YEAR = 2020


def has_pdftotext() -> bool:
    return _PDFTOTEXT is not None


class Fetcher:
    """Per-run network helper handed to parsers that need to fetch beyond the
    listing page (PDF transcripts, per-position sub-pages). Routes through the
    probe session (SSRF guard / robots / rate-limit). ``None`` is passed instead
    when download is disabled, and parsers degrade to listing-page-only output.
    """

    def __init__(self, session: Any, pdf_dir: Path, out_dir: Path) -> None:
        self.session = session
        self.pdf_dir = pdf_dir
        self.out_dir = out_dir

    def get_html(self, url: str, *, timeout: float = 45.0) -> str | None:
        try:
            r = self.session.get(url, timeout=timeout)
            r.raise_for_status()
            return r.text
        except Exception:
            return None

    def download(self, url: str) -> Path | None:
        return download_pdf(self.session, url, self.pdf_dir)

    def rel(self, path: Path) -> str:
        return str(path.relative_to(self.out_dir))

    def pdf_text(self, url: str) -> tuple[str | None, str | None]:
        """Download a PDF and extract its text. Returns (rel_path, text)."""
        path = self.download(url)
        if not path:
            return None, None
        return self.rel(path), extract_text(path)


def download_pdf(session: Any, url: str, dest_dir: Path, *, timeout: float = 60.0) -> Path | None:
    """Download a PDF via the probe session. Returns the local path or None.

    Filename is the sanitized basename; the probe session already enforces the
    SSRF guard, so no separate url-safety check is needed here.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", url.split("?")[0].split("/")[-1])[:200] or "doc.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    path = dest_dir / name
    if path.exists() and path.stat().st_size > 0:
        return path
    try:
        r = session.get(url, timeout=timeout)
    except Exception:
        return None
    status = getattr(r, "status_code", 200)
    if status != 200:
        return None
    content = getattr(r, "content", None)
    if content is None:
        try:
            content = b"".join(r.iter_content(16384))
        except Exception:
            return None
    if not content or not content.startswith(b"%PDF"):
        return None
    path.write_bytes(content)
    return path


# --- text extraction --------------------------------------------------------

_PAGINATION_PATTERNS = [
    re.compile(r"\f"),
    re.compile(r"[ \t]*Page\s+\d+\s+of\s+\d+[ \t]*", re.IGNORECASE),
    re.compile(r"^[ \t]*Page\s+\d+[ \t]*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^[ \t]*-\s*\d+\s*-[ \t]*$", re.MULTILINE),
]


def _strip_pagination_noise(text: str) -> str:
    if not text:
        return text
    for pat in _PAGINATION_PATTERNS:
        text = pat.sub(" ", text)
    return text


def _run_pdftotext(args: list[str], pdf_path: Path) -> str | None:
    if not has_pdftotext():
        return None
    cmd = [_PDFTOTEXT, *args, str(pdf_path), "-"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120)
        return _strip_pagination_noise(out.stdout)
    except Exception:
        return None


def extract_text(pdf_path: Path) -> str | None:
    """Layout-preserved text of a PDF, or None. pdftotext -> pdfminer fallback."""
    text = _run_pdftotext(["-layout"], pdf_path)
    if text and text.strip():
        return text
    try:
        from pdfminer.high_level import extract_text as _pdfminer_extract  # type: ignore
    except ImportError:
        return None
    try:
        return _strip_pagination_noise(_pdfminer_extract(str(pdf_path)))
    except Exception:
        return None


def extract_text_flow(pdf_path: Path) -> str | None:
    """Reading-order text (no -layout) — used when -layout column-mashes a
    two-column annexure (IIT Madras). pdftotext -> pdfminer fallback."""
    text = _run_pdftotext([], pdf_path)
    if text and text.strip():
        return text
    try:
        from pdfminer.high_level import extract_text as _pdfminer_extract  # type: ignore
    except ImportError:
        return None
    try:
        return _strip_pagination_noise(_pdfminer_extract(str(pdf_path)))
    except Exception:
        return None


# --- field extraction (regexes verbatim from origin pdf_extractor.py) --------

DEADLINE_RES = [
    re.compile(
        r"(?:application[s]?|complete[d]?\s+application|submitted)"
        r"[^\n]{0,300}?(?:on\s+or\s+before|deadline[:\s]+|last\s+date[^\n]{0,20}?)"
        r"\s+(?P<date>[A-Z][a-z]+\s+\d{1,2},?\s+\d{4})",
        re.I | re.S,
    ),
    re.compile(
        r"(?:on\s+or\s+before|deadline\s+is|last\s+date\s+(?:for|of))"
        r"\s+(?P<date>[A-Z][a-z]+\s+\d{1,2},?\s+\d{4})",
        re.I | re.S,
    ),
    re.compile(r"Application\s+Last\s+Date[^\d]{0,40}?(?P<date>\d{1,2}/\d{1,2}/\d{4})", re.I),
    re.compile(r"last\s+date[^\n]{0,40}?(?P<date>\d{1,2}[./-]\d{1,2}[./-]\d{2,4})", re.I),
    re.compile(r"last\s+date[^\n]{0,40}?(?P<date>[A-Z][a-z]+\s+\d{1,2},?\s+\d{4})", re.I),
]


def find_deadline(text: str) -> str | None:
    floor_year = max(_HARD_FLOOR_DEADLINE_YEAR, datetime.now(timezone.utc).year - 1)
    text = re.sub(r"[ \t]+", " ", text)
    for r in DEADLINE_RES:
        for m in r.finditer(text):
            raw = m.group("date").strip()
            yr_m = re.search(r"(20\d{2})$", raw) or re.search(r"/(20\d{2})$", raw)
            if yr_m and int(yr_m.group(1)) < floor_year:
                continue
            return raw
    return None


def parse_deadline_iso(raw: str | None) -> str | None:
    """Best-effort coercion of a deadline string to ISO yyyy-mm-dd, or None."""
    if not raw:
        return None
    raw = raw.strip().rstrip(".")
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    return None


CATEGORY_COUNT_RE = re.compile(
    r"\b(UR|GEN|NC[-\s]?OBC|OBC(?:[-\s]?NCL)?|SC|ST|EWS|PwBD|PwD)\s*[-:–—\s]\s*(\d+)\b",
    re.I,
)


def find_category_breakdown(text: str) -> dict | None:
    """Return {UR, SC, ST, OBC, EWS, PwBD: int} for an explicit roster, else None."""
    text = re.sub(r"[ \t]+", " ", text)
    matches = list(CATEGORY_COUNT_RE.finditer(text))
    if len(matches) < 3:
        return None
    best: dict | None = None
    for m in matches:
        window = (m.start(), m.start() + 200)
        cluster = [mm for mm in matches if window[0] <= mm.start() <= window[1]]
        if len(cluster) < 3:
            continue
        out: dict = {}
        for mm in cluster:
            cat_raw = mm.group(1).upper().replace(" ", "").replace("-", "")
            if cat_raw == "GEN":
                key = "UR"
            elif "OBC" in cat_raw:
                key = "OBC"
            elif cat_raw in ("PWD", "PWBD"):
                key = "PwBD"
            else:
                key = cat_raw
            try:
                out[key] = int(mm.group(2))
            except ValueError:
                continue
        if any(v > 50 for v in out.values()):
            continue
        if len(out) >= 3 and (best is None or len(out) > len(best)):
            best = out
    return best


PUBS_RES = [
    re.compile(
        r"(minimum of\s+(?:THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|TEN|\d+)[^.]{10,400}?(?:journals?|publications?|conferences?)\.?)",
        re.I | re.S,
    ),
    re.compile(
        r"(at least\s+(?:THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|TEN|\d+)[^.]{10,400}?(?:journals?|publications?|conferences?)\.?)",
        re.I | re.S,
    ),
]


def find_publications(text: str) -> str | None:
    matches: list[str] = []
    for r in PUBS_RES:
        for m in r.finditer(text):
            matches.append(re.sub(r"\s+", " ", m.group(1).strip()))
    if not matches:
        return None
    seen, dedup = set(), []
    for s in matches:
        if s in seen:
            continue
        seen.add(s)
        dedup.append(s)
    return " | ".join(dedup[:3])


RESERVATION_NOTE_RES = [
    re.compile(
        r"(?:extent of reservation[^\n]{0,40}?(?:as follows)?\s*[:\-]?\s*)?"
        r"(SC[-\s]\s*\d+(?:\.\d+)?%[^.\n]{0,200}"
        r"(?:ST|OBC|EWS|PwBD|PwD|NCL)[^\n]{0,40}%)",
        re.I,
    ),
    re.compile(
        r"((?:SC|ST|OBC|EWS|PwBD|PwD)[-\s]\s*\d+(?:\.\d+)?%"
        r"(?:\s*[;,&]\s*(?:SC|ST|OBC|EWS|PwBD|PwD|NCL)[^\n]{0,30}%){2,})",
        re.I,
    ),
]


def find_reservation_note(text: str) -> str | None:
    """The institute-wide CEI(RTC) Act 2019 reservation percentage spread."""
    text = re.sub(r"\s+", " ", text)
    for r in RESERVATION_NOTE_RES:
        m = r.search(text)
        if m:
            return m.group(1).strip().rstrip(".,;")
    return None


GENERAL_ELIGIBILITY_RES = [
    re.compile(r"(Ph\.?D\.?[^\n]{0,30}?(?:first class|equivalent)[^.\n]{20,400}\.)", re.I),
]


def find_general_eligibility(text: str) -> str | None:
    """The institute-wide PhD-and-experience preamble."""
    text = re.sub(r"\s+", " ", text)
    for r in GENERAL_ELIGIBILITY_RES:
        m = r.search(text)
        if m:
            return m.group(1).strip()
    return None


# --- rolling-ad PDF unit splitting (used by iit_rolling) ---------------------

_NAME_CONTINUATION_LOOKBACK = 12  # origin NAME_CONTINUATION_LOOKBACK
_UNIT_NAME_MAX_CHARS = 80  # origin UNIT_NAME_MAX_CHARS


@dataclass
class UnitBlock:
    """One academic-unit block extracted from a rolling-ad PDF."""

    unit_num: int
    unit_name: str
    text: str


def split_into_units_flow(text: str, dept_names: list[str]) -> dict[str, str]:
    """Split reading-order text into per-department blocks using a known list of
    department names as anchors. Returns {dept_name: body_text}."""
    if not text or not dept_names:
        return {}
    by_pos: list[tuple[int, str]] = []
    for name in dept_names:
        flexible = re.escape(name).replace(r"\ ", r"\s+")
        pattern = re.compile(r"(?:^|\n)\s*(?:\d{1,2}\s*\n\s*)?" + flexible + r"\s*\n", re.IGNORECASE)
        m = pattern.search(text)
        if m:
            by_pos.append((m.end(), name))
    by_pos.sort()
    out: dict[str, str] = {}
    for i, (pos, name) in enumerate(by_pos):
        end = by_pos[i + 1][0] if i + 1 < len(by_pos) else len(text)
        body = text[pos:end].strip()
        if body:
            out[name] = body
    return out


UNIT_HEADER_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<num>\d{1,2})[ \t]{2,}(?P<rest>[A-Z][^\n]{2,400})$", re.MULTILINE,
)


def _split_name_from_areas(rest: str) -> str:
    """Isolate the unit-name column (ends at the first >=2-space gap)."""
    m = re.match(r"(.+?)[ \t]{2,}\S", rest)
    return (m.group(1) if m else rest).strip()


def split_into_units(text: str) -> list[UnitBlock]:
    """Split a rolling-ad PDF's text into per-unit blocks. Keeps header lines with
    monotonically-increasing unit numbers, then carves body text between them."""
    raw_matches = list(UNIT_HEADER_RE.finditer(text))
    if not raw_matches:
        return []

    toc_double = re.compile(r"\d{1,2}[ \t]{2,}[A-Z][^\n]{2,30}[ \t]{2,}\d{1,2}[ \t]{2,}[A-Z]")
    matches = []
    for m in raw_matches:
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_end = text.find("\n", m.start())
        if line_end == -1:
            line_end = len(text)
        if toc_double.search(text[line_start:line_end]):
            continue  # two unit headers on one line -> TOC
        matches.append(m)
    if not matches:
        return []

    by_num: dict[int, re.Match] = {}
    for m in matches:
        by_num[int(m.group("num"))] = m
    matches = sorted(by_num.values(), key=lambda x: x.start())

    kept: list[re.Match] = []
    last_num = 0
    for m in matches:
        num = int(m.group("num"))
        if last_num == 0 and num <= 3:
            kept.append(m)
            last_num = num
        elif num == last_num + 1:
            kept.append(m)
            last_num = num
        elif last_num < num <= last_num + 3:
            kept.append(m)
            last_num = num
    if not kept:
        return []

    blocks: list[UnitBlock] = []
    for i, m in enumerate(kept):
        start = m.start()
        end = kept[i + 1].start() if i + 1 < len(kept) else len(text)
        body = text[start:end].rstrip()
        name = _split_name_from_areas(m.group("rest"))

        name_col = len(m.group("indent")) + len(m.group("num"))
        nl = text.find("\n", m.start())
        line0 = text[m.start():nl if nl != -1 else end]
        gap_m = re.match(r"^\s*\d{1,2}(\s+)", line0)
        if gap_m:
            name_col += len(gap_m.group(1))

        body_lines = body.splitlines()
        gathered_after_break = False
        for j in range(1, min(_NAME_CONTINUATION_LOOKBACK, len(body_lines))):
            line = body_lines[j]
            stripped = line.lstrip()
            if not stripped:
                continue
            indent = len(line) - len(stripped)
            if abs(indent - name_col) > 4:
                gathered_after_break = True
                continue
            frag_m = re.match(r"^([A-Za-z][\w &/(),.\-]{1,40}?)(?:[ \t]{2,}\S|[ \t]*$)", stripped)
            if not frag_m:
                break
            frag = frag_m.group(1).strip()
            if len(frag.split()) > 6:
                break
            if gathered_after_break and len(name) > 60:
                break
            name += " " + frag

        blocks.append(UnitBlock(
            unit_num=int(m.group("num")),
            unit_name=re.sub(r"\s+", " ", name).strip()[:_UNIT_NAME_MAX_CHARS],
            text=body,
        ))
    return blocks
