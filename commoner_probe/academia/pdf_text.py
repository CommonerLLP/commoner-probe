# SPDX-License-Identifier: MIT
"""PDF text + field-extraction helpers for academic recruitment ads.

Ported from the origin project's scraper/pdf_extractor.py (the subset the migrated
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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..base import safe_filename_segment
from ..http_client import read_capped_response
from ..textparse import extract_pdf_text, term_pattern

#: `Applications` with the dropped `ti` ligature allowed. IIT Hyderabad's
#: recruitment PDFs render "Applica ons Invited for Post-Doctoral Research
#: Fellowship", so a regex anchored on the correct spelling reads a document
#: that states a deadline and reports none. `last date` and `deadline` carry no
#: `ti` and survive it. See `dopo_catalogue` TRAP 2 for the same font failure.
_APPLICATIONS = term_pattern("applications").pattern

# Lower bound on accepted deadline years (see origin constants.py).
_HARD_FLOOR_DEADLINE_YEAR = 2020


def same_site(url: str, other: str) -> bool:
    """True when two URLs sit on one site, ignoring a leading ``www.``.

    A career page on ``www.example.ac.in`` links its PDFs from
    ``example.ac.in``. robots.txt is per host, so the two are separate
    origins to the parser and one site to the institution.
    """
    a = urlparse(url).netloc.lower().removeprefix("www.")
    b = urlparse(other).netloc.lower().removeprefix("www.")
    return bool(a) and a == b


class Fetcher:
    """Per-run network helper handed to parsers that need to fetch beyond the
    listing page (PDF transcripts, per-position sub-pages). Routes through the
    probe session (SSRF guard / robots / rate-limit). ``None`` is passed instead
    when download is disabled, and parsers degrade to listing-page-only output.
    """

    def __init__(self, session: Any, pdf_dir: Path, out_dir: Path,
                 robots_override_for: str | None = None) -> None:
        self.session = session
        self.pdf_dir = pdf_dir
        self.out_dir = out_dir
        #: The career-page URL whose site a registry `robots_override` covers,
        #: or None. A host that refuses /robots.txt to every User-Agent reads
        #: as disallow-all, and that verdict reaches the annexure PDF as well
        #: as the listing page. An override that stopped at the listing would
        #: emit ads with `pdf_path: null` and no error. The scope is the
        #: institution's own site, so a third-party link off the page still
        #: obeys robots.
        self.robots_override_for = robots_override_for

    def _overrides(self, url: str) -> bool:
        return bool(self.robots_override_for) and same_site(url, self.robots_override_for)

    def _get(self, url: str, **kwargs: Any) -> Any:
        if self._overrides(url):
            kwargs["respect_robots"] = False
        return self.session.get(url, **kwargs)

    def get_html(self, url: str, *, timeout: float = 45.0) -> str | None:
        try:
            r = self._get(url, timeout=timeout)
            r.raise_for_status()
            return r.text
        except Exception:
            return None

    def download(self, url: str) -> Path | None:
        return download_pdf(self.session, url, self.pdf_dir,
                            robots_override=self._overrides(url))

    def rel(self, path: Path) -> str:
        return str(path.relative_to(self.out_dir))

    def pdf_text(self, url: str) -> tuple[str | None, str | None]:
        """Download a PDF and extract its text. Returns (rel_path, text)."""
        path = self.download(url)
        if not path:
            return None, None
        return self.rel(path), extract_text(path)


def _pdf_basename(url: str) -> str:
    """The on-disk name for a PDF URL.

    ``strip=False`` keeps this caller's existing mapping: it squashed runs of
    disallowed characters and never trimmed underscores, so trimming now would
    rename every ``_advt_.pdf`` already downloaded and fetch it again under the
    new name.
    """
    basename = url.split("?")[0].split("/")[-1]
    name = safe_filename_segment(basename, collapse=True, strip=False)[:200] if basename else "doc.pdf"
    return name if name.lower().endswith(".pdf") else name + ".pdf"


def download_pdf(session: Any, url: str, dest_dir: Path, *, timeout: float = 60.0,
                 robots_override: bool = False) -> Path | None:
    """Download a PDF via the probe session. Returns the local path or None.

    Filename is the sanitized basename; the probe session already enforces the
    SSRF guard, so no separate url-safety check is needed here.

    ``robots_override`` carries the institution's registry opt-in to this
    request. The caller decides the scope; this function only forwards it.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / _pdf_basename(url)
    if path.exists() and path.stat().st_size > 0:
        return path
    kwargs: dict[str, Any] = {"timeout": timeout, "stream": True}
    if robots_override:
        kwargs["respect_robots"] = False
    try:
        r = session.get(url, **kwargs)
    except Exception:
        return None
    status = getattr(r, "status_code", 200)
    if status != 200:
        return None
    # Unconditionally through the capped reader. `.content` is always present
    # on a requests response, so a fallback-only cap was never reached on the
    # path that ships — and reading it is itself the unbounded allocation.
    try:
        content = read_capped_response(r)
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


def _extract(pdf_path: Path, *, layout: bool) -> str | None:
    """The shared pdftotext -> pdfminer chain, with this package's noise strip.

    A second copy of that chain used to live here. It drifted: the shared one
    grew an OCR rung and learned to tell a missing toolchain from a wordless
    PDF, and this one did not. These parsers want ``None`` where the shared
    chain raises or returns "", because a recruitment ad with no readable text
    still yields a record — just without an excerpt.
    """
    try:
        text = extract_pdf_text(pdf_path, layout=layout)
    except Exception:  # includes PdfTextUnavailable: no backend is also no excerpt
        return None
    return _strip_pagination_noise(text) if text and text.strip() else None


def extract_text(pdf_path: Path) -> str | None:
    """Layout-preserved text of a PDF, or None — best for the tabular ads."""
    return _extract(pdf_path, layout=True)


def extract_text_flow(pdf_path: Path) -> str | None:
    """Reading-order text, for annexures that -layout column-mashes (IIT Madras)."""
    return _extract(pdf_path, layout=False)


# --- field extraction (regexes verbatim from origin pdf_extractor.py) --------

DEADLINE_RES = [
    re.compile(
        rf"(?:{_APPLICATIONS}?|complete[d]?\s+{_APPLICATIONS}?|submitted)"
        r"[^\n]{0,300}?(?:on\s+or\s+before|deadline[:\s]+|last\s+date[^\n]{0,20}?)"
        r"\s+(?P<date>[A-Z][a-z]+\s+\d{1,2},?\s+\d{4})",
        re.I | re.S,
    ),
    re.compile(
        r"(?:on\s+or\s+before|deadline\s+is|last\s+date\s+(?:for|of))"
        r"\s+(?P<date>[A-Z][a-z]+\s+\d{1,2},?\s+\d{4})",
        re.I | re.S,
    ),
    re.compile(rf"{_APPLICATIONS}?\s+Last\s+Date[^\d]{{0,40}}?(?P<date>\d{{1,2}}/\d{{1,2}}/\d{{4}})", re.I),
    # "The deadline for applications is 5:00 pm, 22/04/2026" (IITH, verbatim).
    # `deadline` already reached a month-name date and never a numeric one. A
    # clock time can sit between the two, and it cannot match a d/m/y shape.
    re.compile(r"deadline[^\n]{0,40}?(?P<date>\d{1,2}[./-]\d{1,2}[./-]\d{2,4})", re.I),
    # "Application Deadline: 24th August 2026" and "Last Date to Apply 26th
    # July 2026" (IITH, verbatim). The ordinal suffix and the day-first order
    # were both unreadable: every month-name pattern above wants "August 24,
    # 2026". Measured on the live corpus 2026-08-23, this was the single most
    # common missed shape.
    re.compile(
        r"(?:deadline|last\s+date(?:\s+to\s+apply)?)"
        r"[^\n]{0,40}?(?P<date>\d{1,2}(?:st|nd|rd|th)?\s+[A-Z][a-z]+,?\s+\d{4})",
        re.I,
    ),
    # "should apply by email to agupta@phy.iith.ac.in by 25-08-2026" (IITH,
    # verbatim). Anchored on `apply by` and never on a bare `by`, because a
    # bare `by` also introduces a START date. The optional middle carries the
    # address in the observed shape and is capped, so a date further down the
    # page cannot be pulled up into an unrelated sentence.
    re.compile(
        r"apply\s+by\s+(?:[^\n]{0,120}?\bby\s+)?"
        r"(?P<date>\d{1,2}[./-]\d{1,2}[./-]\d{2,4})",
        re.I,
    ),
    # "reviewed on a rolling basis and accepted until 31 December 2026". A
    # rolling REVIEW can still carry a closing date, so the date has to be
    # readable or the rolling wording wins and denies it.
    re.compile(
        r"accepted\s+un(?:ti|\s)?l"
        r"[^\n]{0,40}?(?P<date>\d{1,2}(?:st|nd|rd|th)?\s+[A-Z][a-z]+,?\s+\d{4})",
        re.I,
    ),
    re.compile(
        r"accepted\s+un(?:ti|\s)?l"
        r"[^\n]{0,40}?(?P<date>[A-Z][a-z]+\s+\d{1,2},?\s+\d{4})",
        re.I,
    ),
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


#: A call that states it has no closing date. Sourced from the wording IITH
#: uses, e.g. "This is a rolling advertisement; the PI will evaluate and
#: shortlist the applications". Kept narrow on purpose: a false "rolling"
#: asserts that no deadline exists, which is worse than reading none.
#:
#: `rolling basis` is deliberately absent. It describes the review cadence and
#: says nothing about a closing date. "reviewed on a rolling basis and accepted
#: until 31 December 2026" states both, and reading it as rolling denies a date
#: the document prints.
ROLLING_RES = [
    re.compile(r"\brolling\s+(?:advertisement|call|recruitment|mode)\b", re.I),
    re.compile(r"\b(?:open|accepted)\s+until\s+(?:the\s+)?(?:position|post)s?\s+(?:is|are)\s+filled\b", re.I),
    re.compile(r"\bthere\s+is\s+no\s+(?:last\s+date|closing\s+date|deadline)\b", re.I),
    # "Post-Doctoral Research Fellowship (Rolling)" — IITH marks the whole call
    # in its title and says nothing else about it.
    re.compile(r"\(\s*rolling\s*\)", re.I),
]

#: What the closing_date field means when it is null.
#:
#: `read`          a date was extracted from the document
#: `rolling`       the document states it has no closing date
#: `not_found`     the document was read and stated neither
#: `not_examined`  no document was opened
#:
#: The last two are the point. A null closing_date used to mean both, so a
#: consumer could not tell an expired posting from one nobody read. Do not
#: collapse them into a boolean.
DEADLINE_STATUSES = ("read", "rolling", "not_found", "not_examined")


def find_rolling(text: str) -> bool:
    """True when the text states the call has no closing date."""
    text = re.sub(r"[ \t]+", " ", text)
    return any(r.search(text) for r in ROLLING_RES)


def read_deadline(text: str | None) -> tuple[str | None, str]:
    """Return ``(raw_deadline, status)`` for one document's text.

    ``text`` is None when no document was opened. That is a different fact from
    a document that carries no date, and this function keeps them apart.
    """
    if not text or not text.strip():
        return None, "not_examined"
    raw = find_deadline(text)
    if raw:
        return raw, "read"
    if find_rolling(text):
        return None, "rolling"
    return None, "not_found"


def parse_deadline_iso(raw: str | None) -> str | None:
    """Best-effort coercion of a deadline string to ISO yyyy-mm-dd, or None."""
    if not raw:
        return None
    raw = raw.strip().rstrip(".")
    # "24th August 2026" -> "24 August 2026". The ordinal suffix is the source's
    # style and strptime reads no directive for it.
    raw = re.sub(r"(?<=\d)(?:st|nd|rd|th)\b", "", raw, flags=re.I)
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
                "%d %B %Y", "%d %b %Y", "%d %B, %Y", "%d %b, %Y"):
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
