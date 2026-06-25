# SPDX-License-Identifier: MIT
"""Site-specific parser for the JNU career page.

Target: https://www.jnu.ac.in/career

Notes / honesty
- The career page mixes initial notices (PDF links with "JNU_AdvtNo" in href)
  with follow-up process updates (shortlists, "no applications received", etc.).
  Only initial notices are emitted as ads.
- PDF parsing uses ``pdftotext -layout`` (via the injected probe ``Fetcher`` +
  ``extract_text``) to preserve column alignment. The table has four columns at
  fixed horizontal positions:
    col 0-5:   post number
    col 6-35:  school / centre name
    col 36-52: cadre
    col 53-68: category (OBC/SC/ST/UR/EWS/PwD)
    col 69+:   qualifications (captured as unit_eligibility)
  For each post-number line we scan a bounded window and extract category/cadre
  from their respective column bands.
- Parse confidence 0.78 — structure confirmed against RC/75/2026, 2026-05-20.

Probe-native port of academiaindia/scraper/parsers/jnu.py: PDF download goes
through the injected probe ``Fetcher`` (``pdf.download``); Pydantic ``JobAd``
output is rewritten to plain dicts via :func:`make_ad`. Without a Fetcher
(``--no-download``) or when a PDF can't be read, it degrades to one listing-level
record per notice.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin

from .._common import stable_id
from ..ad_factory import make_ad
from ..pdf_text import extract_text

_RC_NUMBER = re.compile(r"RC/\d+/\d{4}", re.IGNORECASE)
_INITIAL_HREF = re.compile(r"JNU_AdvtNo", re.IGNORECASE)
_UPDATE_HINTS = re.compile(
    r"shortlist|provisional\s+list|final\s+list|selection\s+list|waiting\s+list"
    r"|no\s+application|screened\s+candidate|interview\s+schedule|cancellation",
    re.IGNORECASE,
)

# Post number line: starts with 1-3 digits + period, at most 5 leading spaces.
# Allow both "N.   <text>" (2+ trailing spaces) and bare "N." (end of line).
# 3-digit support is required for advertisements with 100+ posts —
# RC/75/2026 has 122 posts.
_POST_LINE_RE = re.compile(r"^[ \t]{0,5}(\d{1,3})\.(?:[ \t]{2,}|[ \t]*$)", re.MULTILINE)

# Category keywords in the category column (col ~53-68)
_CAT_RE = re.compile(r"\b(OBC|SC|ST|EWS|UR|GEN|PwD|PwBD)\b", re.IGNORECASE)
_BACKLOG_RE = re.compile(r"\bBacklog\b", re.IGNORECASE)
_CADRE_RE = re.compile(r"\b(Associate\s+Professor|Assistant\s+Professor|Professor)\b", re.IGNORECASE)

# Column band boundaries (characters in a layout-mode line).
_CADRE_START = 35
_CADRE_END = 50
_CAT_START = 50
_CAT_END = 65


def parse(html: str, url: str, fetched_at: Any, pdf: Any = None) -> list[dict]:
    from bs4 import BeautifulSoup  # lazy: bs4 is the `academia` extra

    soup = BeautifulSoup(html or "", "html.parser")
    ads: list[dict] = []
    seen: set[str] = set()

    for p in soup.find_all("p"):
        for a in p.find_all("a", href=True):
            href = a["href"].strip()
            link_text = a.get_text(" ", strip=True)
            if not _INITIAL_HREF.search(href) or _UPDATE_HINTS.search(link_text):
                continue
            abs_url = urljoin(url, href)
            if abs_url in seen:
                continue
            seen.add(abs_url)

            context = p.get_text(" ", strip=True)
            rc_m = _RC_NUMBER.search(context)
            ad_number = rc_m.group(0) if rc_m else None

            # PDF table parse needs the injected Fetcher; degrade to a
            # listing-level record under --no-download or unreadable PDF.
            if pdf is not None:
                pdf_path = pdf.download(abs_url)
                if pdf_path:
                    posts = _parse_pdf_posts(pdf_path, abs_url, ad_number, url, fetched_at)
                    if posts:
                        ads.extend(posts)
                        continue

            ads.append(make_ad(
                id=stable_id(abs_url),
                ad_number=ad_number,
                title=(link_text[:250] or context[:250]) or "(untitled)",
                post_type="Faculty",
                contract_status="Unknown",
                original_url=abs_url,
                snapshot_fetched_at=fetched_at,
                parse_confidence=0.5,
                raw_text_excerpt=context[:500],
                info_url=url,
            ))

    return ads


def _col(line: str, start: int, end: int) -> str:
    """Return text from a fixed column band of a layout-mode line."""
    segment = line[start:end] if len(line) > start else ""
    return segment.strip()


def _school_from_line(line: str) -> str:
    """Extract school text from a layout line.

    The school column starts at col 7 in JNU's PDF and ends at the next 3+ space
    gap (column boundary) or end of line. Using a 3-space boundary instead of a
    fixed column lets us capture variable-width school names like
    "Centre for Historical Studies (CHS)" (35 chars) without bleeding into the
    cadre column for shorter names like "SCHOOL OF ARTS &" (16 chars).
    """
    if len(line) < 7:
        return ""
    m = re.match(r"^(.+?)(?:\s{3,}|$)", line[6:])
    if not m:
        return ""
    text = m.group(1).strip()
    if not text or _CAT_RE.fullmatch(text) or _BACKLOG_RE.search(text):
        return ""
    return text


def _qualifications_from_window(window_lines: list[str]) -> Optional[str]:
    """Extract the per-post qualifications/specialisation text from col 65+.

    The qualifications column is the rightmost column and wraps across multiple
    lines within the post's vertical extent. The col-65 boundary is approximate;
    for the FIRST post on a page some text appears visually above the post-number
    line and is not captured by the 1-line look-back used here (slightly truncated
    prefix on post 1 only). All other posts get the full text.
    """
    parts: list[str] = []
    for line in window_lines:
        if len(line) <= 65:
            continue
        text = line[65:].strip()
        if not text:
            continue
        if "Qualifications, Specialisation" in text:  # column-header echo at page breaks
            continue
        parts.append(text)
    if not parts:
        return None
    joined = re.sub(r"\s+", " ", " ".join(parts)).strip()
    return joined or None


def _parse_pdf_posts(
    pdf_path: Path,
    pdf_url: str,
    ad_number: Optional[str],
    listing_url: str,
    fetched_at: Any,
) -> list[dict]:
    text = extract_text(pdf_path)
    if not text:
        return []
    return _parse_posts_from_text(text, pdf_url, ad_number, listing_url, fetched_at)


def _parse_posts_from_text(
    text: str,
    pdf_url: str,
    ad_number: Optional[str],
    listing_url: str,
    fetched_at: Any,
) -> list[dict]:
    """Parse JNU advertisement table text (``pdftotext -layout`` output) into ad dicts.

    Separated from :func:`_parse_pdf_posts` so tests can feed known layout-text
    fixtures without a real PDF or a mocked extractor.
    """
    lines = text.splitlines()
    # Find post-number lines; skip false positives from numbered instruction
    # lists (those appear after "GENERAL INSTRUCTIONS").
    stop_marker = next(
        (i for i, line in enumerate(lines) if "GENERAL INSTRUCTIONS" in line.upper()),
        len(lines),
    )

    post_positions: list[tuple[int, int]] = []  # (line_index, post_num)
    for i, line in enumerate(lines[:stop_marker]):
        m = _POST_LINE_RE.match(line)
        if not m:
            continue
        num = int(m.group(1))
        # Only accept monotonically increasing numbers to filter TOC/noise.
        if post_positions and num <= post_positions[-1][1] and num != post_positions[-1][1] + 1:
            continue
        if post_positions and num == post_positions[-1][1]:
            continue
        post_positions.append((i, num))

    posts: list[dict] = []
    for idx, (line_idx, num) in enumerate(post_positions):
        prev_post_line = post_positions[idx - 1][0] if idx > 0 else 0
        next_post_line = post_positions[idx + 1][0] if idx + 1 < len(post_positions) else stop_marker
        window_start = max(prev_post_line + 1, line_idx - 4)
        window_end = min(next_post_line, line_idx + 6)
        window = lines[window_start:window_end]

        # School: capture text from lines i-2 to i+2 using 3-space-gap boundary.
        school_parts: list[str] = []
        for wi, wl in enumerate(window):
            actual_line = window_start + wi
            if abs(actual_line - line_idx) <= 2:
                frag = _school_from_line(wl)
                if frag and frag not in school_parts:
                    school_parts.append(frag)
        school = re.sub(r"\s+", " ", " ".join(school_parts)).strip()

        # Cadre: tighter ±2 window so the previous post's cadre column doesn't bleed in.
        cadre_ws = max(prev_post_line + 2, line_idx - 2)
        cadre_we = min(next_post_line, line_idx + 3)
        cadre_text = " ".join(_col(lines[j], _CADRE_START, _CADRE_END) for j in range(cadre_ws, cadre_we))
        cadre_text = re.sub(r"\s+", " ", cadre_text)
        cm = _CADRE_RE.search(cadre_text)
        cadre = re.sub(r"\s+", " ", cm.group(1)).strip() if cm else "Professor"

        # Category: first match in the window, then scan forward for (Backlog).
        cat_str = "UR"
        is_backlog = False
        for j, wl in enumerate(window):
            seg = _col(wl, _CAT_START, _CAT_END)
            cm = _CAT_RE.search(seg)
            if cm:
                cat_str = cm.group(1).upper()
                for k in range(j, min(j + 5, len(window))):
                    if _BACKLOG_RE.search(_col(window[k], _CAT_START, _CAT_END + 15)):
                        is_backlog = True
                        break
                break

        if cat_str in ("GEN",):
            cat_str = "UR"
        elif "PWD" in cat_str.upper() or "PWBD" in cat_str.upper():
            cat_str = "PwBD"

        if cat_str in ("UR", "SC", "ST", "OBC", "EWS"):
            breakdown = {cat_str: 1}
        else:
            breakdown = {"PwBD": 1}

        quals_ws = max(prev_post_line + 1, line_idx - 1)
        quals_we = next_post_line
        qualifications = _qualifications_from_window(lines[quals_ws:quals_we])

        title = f"{cadre} — {school}" if school else cadre
        if ad_number:
            title = f"{ad_number} Post {num}: {title}"

        posts.append(make_ad(
            id=stable_id(pdf_url, str(num)),
            ad_number=ad_number,
            title=title[:250],
            department=school[:120] if school else None,
            post_type="Faculty",
            contract_status="Regular",
            category_breakdown=breakdown,
            number_of_posts=1,
            original_url=pdf_url,
            snapshot_fetched_at=fetched_at,
            parse_confidence=0.78,
            raw_text_excerpt=(
                f"Post {num} | {cadre} | {cat_str}"
                f"{'  (Backlog)' if is_backlog else ''} | {school}"
            )[:400],
            info_url=listing_url,
            unit_eligibility=qualifications,
            pdf_parsed=True,
        ))

    return posts
