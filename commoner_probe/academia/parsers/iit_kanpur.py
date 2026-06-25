# SPDX-License-Identifier: MIT
"""IIT Kanpur department-wise rolling-recruitment parser.

Targets the DOFA "department-wise vacancies and area of specialization" page,
which lists each academic unit as ``<Department Name>: <areas sought>``. HTML-
only (IIT-K publishes the area descriptions inline, no PDF).

Probe-native port of academiaindia/scraper/parsers/iit_kanpur.py.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

from .._common import PLACEHOLDER_INSTITUTION_ID, iso, stable_id

logger = logging.getLogger(__name__)

_DEPT_EXCERPT_MAX_CHARS = 4000  # origin IIT_KANPUR_DEPT_EXCERPT_MAX_CHARS

KNOWN_DEPTS = {
    "Aerospace Engineering",
    "Biological Sciences and Bioengineering",
    "Chemical Engineering",
    "Chemistry",
    "Civil Engineering",
    "Cognitive Science",
    "Computer Science and Engineering",
    "Earth Sciences",
    "Economic Sciences",
    "Electrical Engineering",
    "Humanities and Social Sciences",
    "Industrial and Management Engineering",
    "Materials Science and Engineering",
    "Mathematics and Statistics",
    "Mechanical Engineering",
    "Mechanics, Aerodynamics & Astrodynamics",
    "Nuclear Engineering and Technology",
    "Physics",
    "Statistics and Mathematics",
    "Sustainable Energy Engineering",
    "Photonics Science and Engineering",
    "Materials Science Programme",
    "Design Programme",
    "Environmental Science and Engineering",
    "Centre for Lasers and Photonics",
    "Centre for Mechatronics",
    "School of Medical Research and Technology",
    "Kotak School of Sustainability",
}

_KNOWN_DEPT_PATTERNS: list[tuple[str, re.Pattern]] = [
    (dept, re.compile(rf"\b{re.escape(dept)}\s*:\s+", re.I)) for dept in KNOWN_DEPTS
]

_GENERIC_DEPT_RE = re.compile(
    r"\b("
    r"[A-Z][A-Za-z]{2,30}"
    r"(?:\s+(?:[A-Z][\w&\-]{1,30}|of|and|for|&|the))"
    r"(?:\s+(?:[A-Z][\w&\-]{1,30}|of|and|for|&|the)){0,5}"
    r")\s*:\s+"
)

_SKIP_PHRASE_RE = re.compile(
    r"^(page|note|notice|details?|annexure|table|figure|section|"
    r"chapter|appendix|abstract|summary)\b",
    re.I,
)


def _extract_dept_blocks(plain: str) -> list[tuple[str, str]]:
    found_positions: set[int] = set()
    out: list[tuple[str, int, int]] = []

    for dept, pat in _KNOWN_DEPT_PATTERNS:
        m = pat.search(plain)
        if m:
            out.append((dept, m.start(), m.end()))
            found_positions.add(m.start())

    for m in _GENERIC_DEPT_RE.finditer(plain):
        if m.start() in found_positions:
            continue
        name = re.sub(r"\s+", " ", m.group(1)).strip()
        if _SKIP_PHRASE_RE.match(name):
            continue
        if m.start() > 0 and plain[m.start() - 1].isalpha():
            continue
        logger.info("iit_kanpur generic-pass found unknown dept: %r", name)
        out.append((name, m.start(), m.end()))
        found_positions.add(m.start())

    out.sort(key=lambda x: x[1])

    blocks: list[tuple[str, str]] = []
    for i, (dept, _start, end) in enumerate(out):
        body_end = out[i + 1][1] if i + 1 < len(out) else len(plain)
        body = plain[end:body_end].strip()
        if len(body) > _DEPT_EXCERPT_MAX_CHARS:
            body = body[:_DEPT_EXCERPT_MAX_CHARS].rsplit(" ", 1)[0] + "…"
        blocks.append((dept, body))
    return blocks


def parse(html: str, url: str, fetched_at: Any, pdf: Callable | None = None) -> list[dict]:
    from bs4 import BeautifulSoup  # lazy: bs4 is the `academia` extra

    soup = BeautifulSoup(html, "html.parser")
    plain = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    blocks = _extract_dept_blocks(plain)
    out: list[dict] = []
    for dept, body in blocks:
        out.append({
            "id": stable_id("iit-kanpur", dept),
            "institution_id": PLACEHOLDER_INSTITUTION_ID,
            "ad_number": None,
            "title": f"Faculty — {dept}",
            "department": dept,
            "discipline": dept,
            "post_type": "Faculty",
            "contract_status": "TenureTrack",
            "category_breakdown": None,
            "number_of_posts": None,
            "pay_scale": None,
            "publication_date": None,
            "closing_date": None,  # rolling
            "original_url": url,
            "snapshot_fetched_at": iso(fetched_at),
            "parse_confidence": 0.7,
            "raw_text_excerpt": body,
            "apply_url": "https://iitk.ac.in/dofa/online-application-form",
            "info_url": url,
            "publications_required": None,
            "unit_eligibility": None,
            "pdf_path": None,
            "pdf_parsed": False,
        })
    return out
