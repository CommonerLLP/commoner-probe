# SPDX-License-Identifier: MIT
"""Parser for IIM-style recruitment pages (one PDF per discrete call).

Most IIMs post one PDF per discrete faculty call (or keep a permanent "apply
year-round" page). We treat each faculty-recruitment-tagged PDF on the careers
page as one ad, pulling deadline / publications / reservation counts from the
PDF text. If no relevant PDF is found, emit one rolling-stub so the IIM is still
visible.

Probe-native port of academiaindia/scraper/parsers/iim_recruit.py. PDF fetching
+ text extraction is injected via the ``pdf`` callable (provided by the probe in
download mode; ``None`` skips PDF work, e.g. ``--no-download`` / dry-run).
"""

from __future__ import annotations

import re
from typing import Any, Callable
from urllib.parse import urljoin

from .._common import PLACEHOLDER_INSTITUTION_ID, iso, stable_id
from ..pdf_text import (
    find_category_breakdown,
    find_deadline,
    find_publications,
    parse_deadline_iso,
)

RECRUIT_RE = re.compile(
    r"\bfaculty[\s/_-]+(?:recruit|position|opening|hiring|advert|search|job|vacanc|appointment)\w*"
    r"|\btenure[- ]track\s+faculty"
    r"|\bprofessor\b"
    r"|\brecruitment\s+in\s+\w+\s+area",
    re.I,
)
SKIP_RE = re.compile(
    r"(recruiters?\s+guide|placement|brochure|prospectus|hr\s+policy|admission|"
    r"non[- _]teaching|non[- _]faculty|technical\s+staff|administrative\s+staff|"
    r"research\s+assistant|field\s+investigator)",
    re.I,
)

_MAX_PDF_CANDIDATES = 6  # origin IIM_MAX_PDF_CANDIDATES
_EXCERPT_MAX_CHARS = 700  # origin IIM_PDF_EXCERPT_MAX_CHARS


def parse(html: str, url: str, fetched_at: Any, pdf: Callable | None = None) -> list[dict]:
    from bs4 import BeautifulSoup  # lazy: bs4 is the `academia` extra

    soup = BeautifulSoup(html, "html.parser")

    candidates: list[tuple[str, str]] = []  # (absolute_url, anchor_text)
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if ".pdf" not in href.lower():
            continue
        text = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        haystack = href + " " + text
        if SKIP_RE.search(haystack) or not RECRUIT_RE.search(haystack):
            continue
        candidates.append((urljoin(url, href), text or href.rsplit("/", 1)[-1]))

    seen, deduped = set(), []
    for u, t in candidates:
        if u in seen:
            continue
        seen.add(u)
        deduped.append((u, t))

    out: list[dict] = []
    for pdf_url, anchor_text in deduped[:_MAX_PDF_CANDIDATES]:
        excerpt: str | None = None
        deadline_iso: str | None = None
        publications: str | None = None
        category_breakdown: dict | None = None
        pdf_path: str | None = None
        pdf_parsed = False

        if pdf is not None:
            pdf_path, text = pdf(pdf_url)
            if text and text.strip():
                pdf_parsed = True
                joined = re.sub(r"\s+", " ", text).strip()
                excerpt = (joined[:_EXCERPT_MAX_CHARS] + "…") if len(joined) > _EXCERPT_MAX_CHARS else joined
                deadline_iso = parse_deadline_iso(find_deadline(text))
                publications = find_publications(text)
                category_breakdown = find_category_breakdown(text)

        title = re.sub(r"\s+", " ", anchor_text.strip() or "Faculty position")[:160]
        out.append({
            "id": stable_id("iim", url, pdf_url),
            "institution_id": PLACEHOLDER_INSTITUTION_ID,
            "ad_number": None,
            "title": title,
            "department": None,
            "discipline": None,
            "post_type": "Faculty",
            "contract_status": "Unknown",
            "category_breakdown": category_breakdown,
            "number_of_posts": (sum(category_breakdown.values()) if category_breakdown else None),
            "pay_scale": None,
            "publication_date": None,
            "closing_date": deadline_iso,
            "original_url": pdf_url,
            "snapshot_fetched_at": iso(fetched_at),
            "parse_confidence": 0.6,
            "raw_text_excerpt": excerpt,
            "apply_url": None,
            "info_url": url,
            "publications_required": publications,
            "unit_eligibility": None,
            "pdf_path": pdf_path,
            "pdf_parsed": pdf_parsed,
        })

    if not out:
        out.append({
            "id": stable_id("iim-stub", url),
            "institution_id": PLACEHOLDER_INSTITUTION_ID,
            "ad_number": None,
            "title": "Rolling faculty recruitment (no discrete area postings)",
            "department": None,
            "discipline": None,
            "post_type": "Faculty",
            "contract_status": "Unknown",
            "category_breakdown": None,
            "number_of_posts": None,
            "pay_scale": None,
            "publication_date": None,
            "closing_date": None,
            "original_url": url,
            "snapshot_fetched_at": iso(fetched_at),
            "parse_confidence": 0.5,
            "raw_text_excerpt": (
                "No discrete faculty postings found on the careers page. Most IIMs route "
                "applications through internal channels; check the listing page directly."
            ),
            "apply_url": None,
            "info_url": url,
            "publications_required": None,
            "unit_eligibility": None,
            "pdf_path": None,
            "pdf_parsed": False,
            "rolling_stub": True,
        })
    return out
