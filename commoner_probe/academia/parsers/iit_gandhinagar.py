# SPDX-License-Identifier: MIT
"""Dedicated parser for IIT Gandhinagar recruitment pages.

IITGN publishes a single rolling "Professor of Practice" (PoP) page
(iitgn.ac.in/careers/pop) that lists all eligible departments as a
pipe-separated block with no per-department PDF or closing date. This
parser explodes that composite page into one ad per department.

Regular faculty and non-teaching pages fall back to the generic parser.

Ported from academiaindia's parked `feat/parser-dry-layer` branch — that
branch was never merged into academiaindia's history, so this parser didn't
exist anywhere in a released form until now.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from .._common import stable_id
from ..ad_factory import make_ad

# Fallback department list when live scraping fails.
_POP_DEPTS_FALLBACK = [
    "Biological Engineering",
    "Chemical Engineering",
    "Chemistry",
    "Civil Engineering",
    "Computer Science and Engineering",
    "Earth Sciences",
    "Electrical Engineering",
    "Humanities and Social Sciences",
    "Materials Science and Engineering",
    "Mathematics",
    "Mechanical Engineering",
    "Physics",
    "Archaeological Sciences",
    "Biomedical Engineering",
    "Cognitive Science",
    "Design and Innovation",
    "Safety",
    "Sustainable Development",
]

_PIPE_DEPT_RE = re.compile(
    r"(?:Disciplines?|Areas?|Departments?|Centers?)\s+((?:[A-Za-z &]+\s*\|\s*)+[A-Za-z &]+)",
    re.I,
)

# The live page's pipe-delimited list has no closing terminator, so the
# regex's final (non-repeated) segment can bleed into trailing nav-menu
# text; and a section sub-heading ("Interdisciplinary Centers") is
# sometimes glued onto a department name with no separating pipe at all.
# Neither is discoverable from the regex alone — both were only found by
# running against the live page, not any synthetic fixture — so split on
# these known noise phrases as a post-processing pass.
_DEPT_NOISE_RE = re.compile(
    r"\b(?:find out more|apply now|interdisciplinary centers?)\b", re.I,
)
_MAX_DEPT_WORDS = 6  # longest real name observed is 4 words; leaves headroom


def _split_department_segment(raw: str, *, is_last: bool) -> list[str]:
    """Split one pipe-delimited segment on embedded noise, dropping garbage.

    Non-last segments are still pipe-bounded on both sides, so content on
    *either* side of an embedded noise phrase (e.g. a sub-heading) may be a
    real department name — keep both. The last segment has no closing pipe,
    so once a noise phrase appears, everything from there on is presumed
    nav-menu chrome and discarded — keep only what precedes it.
    """
    pieces = _DEPT_NOISE_RE.split(raw)
    if is_last:
        pieces = pieces[:1]
    names = []
    for piece in pieces:
        name = piece.strip(" |")
        if name and len(name.split()) <= _MAX_DEPT_WORDS:
            names.append(name)
    return names


def _extract_pop_departments(text: str) -> list[str]:
    m = _PIPE_DEPT_RE.search(text)
    if not m:
        return []
    raw_segments = m.group(1).split("|")
    depts: list[str] = []
    for i, raw in enumerate(raw_segments):
        depts.extend(_split_department_segment(raw, is_last=(i == len(raw_segments) - 1)))
    return depts if len(depts) >= 3 else []


def parse(html: str, url: str, fetched_at: Any, pdf: Callable | None = None) -> list[dict]:
    from bs4 import BeautifulSoup  # lazy: bs4 is the `academia` extra

    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text(" ", strip=True)

    # ── Professor of Practice rolling page ────────────────────────────────
    if "/careers/pop" in url.lower() or "professor of practice" in page_text.lower():
        depts = _extract_pop_departments(page_text) or _POP_DEPTS_FALLBACK
        return [
            make_ad(
                id=stable_id("iitgn-pop", dept),
                title=f"Professor of Practice — {dept}",
                original_url=url,
                snapshot_fetched_at=fetched_at,
                department=dept,
                post_type="Faculty",
                contract_status="Unknown",
                parse_confidence=0.6,
                raw_text_excerpt=(
                    f"Rolling recruitment for Professor of Practice positions in {dept}. "
                    "Applications are invited on a rolling basis; see listing page for details."
                ),
                apply_url=url,
                info_url=url,
            )
            for dept in depts
        ]

    # ── All other pages: fall back to generic link extraction ─────────────
    from .generic import parse as generic_parse
    return generic_parse(html, url, fetched_at, pdf)
