# SPDX-License-Identifier: MIT
"""Anna University recruitment parser.

Targets the events/recruitment page, whose Recruitment tab publishes a table of
notices with department and last-date columns. HTML-only — linked PDFs are
recorded but not parsed here.

Probe-native port of academiaindia/scraper/parsers/anna_university.py (bs4 lazy;
NavigableString/Tag isinstance checks replaced with duck-typing).
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable
from urllib.parse import urljoin

from ..ad_factory import make_ad, stable_id

DATE_RE = re.compile(
    r"\b(?P<day>\d{1,2})\s+"
    r"(?P<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"[a-z]*\s+(?P<year>20\d{2})\b",
    re.I,
)
DOT_DATE_RE = re.compile(r"\b(?P<day>\d{1,2})\.(?P<month>\d{1,2})\.(?P<year>20\d{2})\b")
MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
GENERIC_LINK_RE = re.compile(r"^(click here|download|view|pdf|notification|advertisement)$", re.I)
FACULTY_RE = re.compile(
    r"\b(assistant professor|associate professor|professor|faculty|teaching)\b", re.I)
RESEARCH_RE = re.compile(
    r"\b(jrf|junior research fellow|srf|senior research fellow|research|"
    r"project assistant|project associate|project scientist|post[- ]?doctoral|"
    r"post doctoral|fellowship|scientist)\b",
    re.I,
)
ADMIN_RE = re.compile(
    r"\b(registrar|controller|finance officer|clerk|typist|driver|office "
    r"assistant|administrative|superintendent|technician|accountant)\b",
    re.I,
)
UNIT_RE = re.compile(
    r"\b([A-Z]{2,}(?:\s+[A-Z]{2,}){0,3}|"
    r"(?:Department|Centre|Center|School)\s+of\s+[A-Za-z&,\- ]+)\b"
)
CONTRACT_RE = re.compile(r"\b(contract|contractual|temporary|project)\b", re.I)


def _text_of(node) -> str:
    if node is None:
        return ""
    if hasattr(node, "get_text"):  # a bs4 Tag
        return " ".join(node.get_text(" ", strip=True).split())
    return " ".join(str(node).split())  # NavigableString / str


def _context_pieces(a) -> list[str]:
    parent = a.find_parent(["li", "tr", "div", "p"]) or a.parent or a
    out: list[str] = []
    seen: set[str] = set()
    for piece in (_text_of(parent), _text_of(a)):
        cleaned = " ".join(piece.split()).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out


def _extract_date(parts: Iterable[str]) -> str | None:
    for part in parts:
        match = DATE_RE.search(part)
        if not match:
            continue
        day = int(match.group("day"))
        month = MONTHS[match.group("month").lower()[:3]]
        year = int(match.group("year"))
        return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def _extract_dot_date(text: str) -> str | None:
    match = DOT_DATE_RE.search(text or "")
    if not match:
        return None
    return f"{int(match.group('year')):04d}-{int(match.group('month')):02d}-{int(match.group('day')):02d}"


def _classify_scope(text: str) -> str | None:
    has_faculty = bool(FACULTY_RE.search(text))
    has_research = bool(RESEARCH_RE.search(text))
    has_admin = bool(ADMIN_RE.search(text))
    if not has_faculty and not has_research:
        return None
    if has_admin and not has_research and not has_faculty:
        return None
    return "Faculty" if has_faculty and not has_research else "Research"


def _title_for(a, context_parts: list[str]) -> str:
    link_text = _text_of(a)
    if link_text and not GENERIC_LINK_RE.match(link_text):
        return link_text
    for part in context_parts:
        if part != link_text and (FACULTY_RE.search(part) or RESEARCH_RE.search(part)):
            return part
    return link_text


def _extract_unit(context_parts: list[str], title: str) -> str | None:
    for part in context_parts:
        hay = part.replace(title, " ", 1) if title and title in part else part
        match = UNIT_RE.search(hay)
        if match:
            unit = match.group(1).strip(" -:")
            if unit.lower() != title.lower():
                return unit
    return None


def _parse_table_rows(soup, url: str, fetched_at: Any) -> list[dict]:
    ads: list[dict] = []
    seen_urls: set[str] = set()
    for tr in soup.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 4:
            continue
        title_link = cells[1].find("a", href=True)
        if not title_link:
            continue
        title = _text_of(cells[1]).strip(" -|")
        if len(title) < 5:
            continue
        department = _text_of(cells[2]).strip(" -:") or None
        row_text = " ".join(_text_of(cell) for cell in cells)
        post_type = _classify_scope(f"{title} {department or ''} {row_text}")
        if post_type is None:
            continue
        href = (title_link.get("href") or "").strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        abs_url = urljoin(url, href)
        if abs_url in seen_urls:
            continue
        seen_urls.add(abs_url)
        closing_date = _extract_dot_date(_text_of(cells[3]))
        contract_status = "Contractual" if CONTRACT_RE.search(row_text) else "Unknown"
        parse_confidence = 0.93 if closing_date and department else 0.88
        ads.append(make_ad(
            id=stable_id("anna-university", abs_url, title, closing_date or ""),
            title=title[:250],
            original_url=abs_url,
            snapshot_fetched_at=fetched_at,
            department=department,
            discipline=department if department and len(department) > 4 else None,
            post_type=post_type,
            contract_status=contract_status,
            closing_date=closing_date,
            parse_confidence=parse_confidence,
            raw_text_excerpt=row_text[:500],
            info_url=url,
        ))
    return ads


def parse(html: str, url: str, fetched_at: Any, pdf: Callable | None = None) -> list[dict]:
    from bs4 import BeautifulSoup  # lazy: bs4 is the `academia` extra

    soup = BeautifulSoup(html or "", "html.parser")
    table_ads = _parse_table_rows(soup, url, fetched_at)
    if table_ads:
        return table_ads

    ads: list[dict] = []
    seen_urls: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        abs_url = urljoin(url, href)
        if abs_url in seen_urls:
            continue
        context_parts = _context_pieces(a)
        if not context_parts:
            continue
        title = _title_for(a, context_parts).strip(" -|")
        if len(title) < 5:
            continue
        scope_text = " ".join(context_parts)
        post_type = _classify_scope(f"{title} {scope_text}")
        if post_type is None:
            continue
        seen_urls.add(abs_url)
        publication_date = _extract_date(context_parts)
        unit = _extract_unit(context_parts, title)
        parse_confidence = 0.87 if publication_date else 0.78
        contract_status = "Contractual" if CONTRACT_RE.search(scope_text) else "Unknown"
        ads.append(make_ad(
            id=stable_id("anna-university", abs_url, title, publication_date or ""),
            title=title[:250],
            original_url=abs_url,
            snapshot_fetched_at=fetched_at,
            publication_date=publication_date,
            department=unit,
            discipline=unit if unit and len(unit) > 4 else None,
            post_type=post_type,
            contract_status=contract_status,
            parse_confidence=parse_confidence,
            raw_text_excerpt=scope_text[:500],
            info_url=url,
        ))
    return ads
