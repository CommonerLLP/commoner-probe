# SPDX-License-Identifier: MIT
"""Shared utilities for HEI recruitment page parsers.

All parsers face the same core sub-problems:
  - Is this anchor a recruitment link or navigation chrome?
  - What post-type is this position?
  - What department does the title name?
  - When a link says "Click here", what should the card title be?
  - When is a date a deadline vs a publication date?

This module owns those answers. Individual parsers import from here
instead of re-implementing the logic in isolation.

Ported verbatim from academiaindia's parked `feat/parser-dry-layer` branch
(never merged to academiaindia `main`) — pure regex/string logic with no
coupling to the old Pydantic/`ad_factory` layer, so no adaptation was
required beyond this header.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

# ── Recruitment-link detection ─────────────────────────────────────────────

RECRUITMENT_KEYWORDS = [
    r"recruit",
    r"vacanc",
    r"advert",
    r"non[- ]?teaching",
    r"ministerial",
    r"scientist",
    r"engagement",
    r"walk[- ]?in",
    r"अधिसूचना",
    r"भर्ती",
    r"विज्ञापन",
    r"रिक्ति",
]

RECRUITMENT_LINK_RE = re.compile(
    r"\b(advertisement|advt|recruitment|vacancy|position|opening|job|jobs|"
    r"fellow|jrf|srf|post[- ]?doctoral|research\s+associate|project\s+scientist|"
    r"professor|faculty\s+recruitment)\b",
    re.I,
)

GENERIC_NAV_TEXT_RE = re.compile(
    r"^\s*(home|about|about us|academics?|admissions?|programs?|departments?|"
    r"calendars?|career(s)?|donate(\s+to\s+\w+)?|visit\s+\w+|search|faculty|"
    r"staff|students?|directory|registrar|director|apply\s+now|"
    r"office\s+of\s+.*|centre\s+for\s+continuing\s+education)\s*$",
    re.I,
)

CAREERS_JOB_URL_RE = re.compile(
    r"/(career|careers|jobs?)/(staff/)?[^/]*\.(pdf|docx?)$|/(career|careers|jobs?)/jobs?/\d+",
    re.I,
)

# Generic link labels where the surrounding context is a better title.
GENERIC_LINK_TEXT = frozenset({
    "view", "click here", "click", "download", "pdf", "read more",
    "view advertisement", "here", "download advertisement pdf", "download pdf",
    "apply here", "apply now", "apply",
})


def is_recruitment_link(link_text: str, href: str, surrounding_text: str) -> bool:
    """Return True if this anchor likely points to a recruitment advertisement."""
    link_hay = f"{link_text} {href}"
    context_hay = f"{link_hay} {surrounding_text}"
    if GENERIC_NAV_TEXT_RE.match(link_text):
        return False
    if RECRUITMENT_LINK_RE.search(link_hay) or CAREERS_JOB_URL_RE.search(href):
        return True
    for kw in RECRUITMENT_KEYWORDS:
        if re.search(kw, link_hay, re.IGNORECASE):
            return True
    return href.lower().endswith(".pdf") and any(
        re.search(k, context_hay, re.IGNORECASE)
        for k in ("advert", "recruit", "vacanc", "fellow", "position")
    )


def resolve_title(link_text: str, parent_text: str) -> str:
    """Return the best title for a link.

    When link_text is a generic label ("Click here", "Download PDF"), the
    parent element's text usually carries the actual advertisement title.
    Strip the generic label and any trailing PDF/download noise.
    """
    if link_text.lower() in GENERIC_LINK_TEXT and len(parent_text) > len(link_text) + 5:
        title = re.sub(r"\s+", " ", parent_text.replace(link_text, "")).strip(" -—·|.")
        title = re.sub(
            r"\.?\s*(download|view|click|pdf)\s*(advertisement|pdf)?\s*$",
            "", title, flags=re.IGNORECASE,
        ).strip(" -—·|.")
        return title or link_text
    return link_text


# ── Post-type classification ───────────────────────────────────────────────
# Checked on the ad title only (not full context) to prevent false positives
# from eligibility text like "postdoctoral experience required".

_POSTDOC_RE = re.compile(
    r"\bpost[- ]?doc|\bpostdoctoral\b|\bpost[- ]?doctoral\b|\bpdf\s+position\b",
    re.I,
)
_JRF_SRF_RE  = re.compile(r"\b(?:junior|senior)\s+research\s+fellow\b|\bJRF\b|\bSRF\b", re.I)
_RA_RE       = re.compile(r"\bresearch\s+associate\b|\bproject\s+(?:research\s+)?scientist\b", re.I)
_FACULTY_RE  = re.compile(r"\bprofessor\b|\blecturer\b|\breader\b|\bfaculty\s+recruit", re.I)
_NONFAC_RE   = re.compile(
    r"\bproject\s+(?:associate|officer|manager|assistant|engineer)\b"
    r"|\btechnician\b|\bweb\s+developer\b|\benergy\s+(?:manager|engineer)\b"
    r"|\bsecurity\s+(?:analyst|engineer)\b|\balumni\s+relations\b"
    r"|\bfire\s+safety\b|\boffice\s+executive\b|\bmedical\s+officer\b"
    r"|\bconsultant\b",
    re.I,
)
_CONTRACT_SIGNALS = re.compile(r"\bguest\b|\bad[- ]?hoc\b|\bcontractual\b|\bvisiting\b|\btenure[- ]track\b|\bregular\b|\bpermanent\b", re.I)


def classify_post_type(title: str) -> str:
    """Infer post-type from the ad title."""
    if _POSTDOC_RE.search(title):
        return "Postdoc"
    if _FACULTY_RE.search(title):
        return "Faculty"
    if _JRF_SRF_RE.search(title) or _RA_RE.search(title):
        return "Scientific"
    if _NONFAC_RE.search(title):
        return "NonFaculty"
    return "Unknown"


def classify_contract_status(context: str) -> str:
    """Infer contract status from ad text (title + surrounding context)."""
    lc = context.lower()
    if "guest" in lc:
        return "Guest"
    if "ad-hoc" in lc or "adhoc" in lc:
        return "AdHoc"
    if "contractual" in lc or "contract basis" in lc:
        return "Contractual"
    if "visiting" in lc:
        return "Visiting"
    if "tenure track" in lc or "tenure-track" in lc:
        return "TenureTrack"
    if "regular" in lc or "permanent" in lc:
        return "Regular"
    return "Unknown"


# ── Department extraction ──────────────────────────────────────────────────

_DEPT_EXPLICIT_RE = re.compile(
    r"\bDep(?:artment|t)\.?\s+of\s+([^,\n]+?)(?:,\s*IIT|,\s*IIM|,\s*IISER|\s*$)",
    re.I,
)
_DEPT_POSITION_RE = re.compile(
    r"\b(?:faculty|professor|lecturer|chair)\s+(?:positions?|posts?|openings?)\s+in\s+(.+?)(?:\s+[—–\-]{1,2}\s+|\s*$)",
    re.I,
)


def extract_department(title: str) -> str | None:
    """Extract department/area name from an ad title."""
    m = _DEPT_EXPLICIT_RE.search(title)
    if m:
        return m.group(1).strip()
    m = _DEPT_POSITION_RE.search(title)
    if m:
        return m.group(1).strip()
    return None


# ── Date extraction ────────────────────────────────────────────────────────

_DATE_RE = re.compile(
    r"(\d{1,2})[\s.\-/](Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|[0-1]?\d)[\s.\-/](20\d{2})",
    re.IGNORECASE,
)
_CLOSING_HINTS  = re.compile(r"(last|closing|deadline|apply\s*by)\s*(date)?", re.IGNORECASE)
_PUBLISHED_HINTS = re.compile(r"(advertise|publish|issued|dated)", re.IGNORECASE)

_MONTHS = {m: i for i, m in enumerate(
    ["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"], start=1,
)}


def _to_iso(day: str, month_s: str, year: str) -> str | None:
    try:
        d, y = int(day), int(year)
        ms = month_s.lower()[:3]
        mo = _MONTHS.get(ms) or (int(month_s) if month_s.isdigit() else None)
        if mo is None or not (1 <= mo <= 12 and 1 <= d <= 31 and 2015 <= y <= 2035):
            return None
        return f"{y:04d}-{mo:02d}-{d:02d}"
    except Exception:
        return None


def extract_dates(text: str) -> tuple[str | None, str | None]:
    """Return (publication_date, closing_date) as ISO strings."""
    pub: str | None = None
    close: str | None = None
    matches = list(_DATE_RE.finditer(text))
    for m in matches:
        ctx = text[max(0, m.start() - 60): m.start()]
        iso = _to_iso(*m.groups())
        if iso is None:
            continue
        if _CLOSING_HINTS.search(ctx):
            close = close or iso
        elif _PUBLISHED_HINTS.search(ctx):
            pub = pub or iso
    if close is None and len(matches) == 1:
        close = _to_iso(*matches[0].groups())
    return pub, close


# ── Ad-number extraction ───────────────────────────────────────────────────

_AD_NUMBER_RE = re.compile(
    r"(?:Advertisement|Advt\.?|Notification|Ref\.?|F\.?\s?No\.?)[\s:/No\.]*([A-Z0-9/\-\.\s]{3,40})",
    re.IGNORECASE,
)


def extract_ad_number(text: str) -> str | None:
    m = _AD_NUMBER_RE.search(text)
    return m.group(1).strip().rstrip(".,;:") if m else None


# ── Link iterator (shared soup traversal) ─────────────────────────────────

def iter_recruitment_links(soup, base_url: str):
    """Yield (abs_url, title, parent_text) for each recruitment-relevant anchor.

    Handles dedup, skips nav/javascript, resolves generic link labels.
    Callers do the final ad construction — this handles the traversal pattern
    that every parser was implementing independently.
    """
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue
        link_text = (a.get_text(" ", strip=True) or "").strip()
        if not link_text:
            continue

        parent_text = a.parent.get_text(" ", strip=True) if a.parent else link_text
        context = f"{link_text}  {parent_text}"

        if not is_recruitment_link(link_text, href, context):
            continue

        abs_url = urljoin(base_url, href)
        if abs_url in seen:
            continue
        seen.add(abs_url)

        title = resolve_title(link_text, parent_text)
        yield abs_url, title, parent_text
