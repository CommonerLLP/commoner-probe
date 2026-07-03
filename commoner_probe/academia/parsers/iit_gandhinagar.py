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


def _extract_pop_departments(text: str) -> list[str]:
    m = _PIPE_DEPT_RE.search(text)
    if m:
        depts = [d.strip() for d in m.group(1).split("|") if d.strip()]
        if len(depts) >= 3:
            return depts
    return []


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
