# SPDX-License-Identifier: MIT
"""Dedicated parser for IIT Hyderabad recruitment pages.

IITH posts two streams on the same careers page: permanent faculty positions
and rolling project/research positions (JRF, SRF, RA, postdoc, project staff).
The generic parser sees both but misclassifies and misses department info.

This parser adds department extraction and accurate post_type by delegating
post-type and department logic to parser_utils — no duplication.

Ported from academiaindia's parked `feat/parser-dry-layer` branch — that
branch was never merged into academiaindia's history, so this parser didn't
exist anywhere in a released form until now.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from .._common import stable_id
from ..ad_factory import make_ad
from .parser_utils import classify_post_type, extract_department, iter_recruitment_links

# Skip result notifications and cancellations — not job listings.
_SKIP_RE = re.compile(
    r"\b(notification\s+of\s+results?|list\s+of\s+provisional|cancellation\s+of\s+advertisement)\b",
    re.I,
)


def parse(html: str, url: str, fetched_at: Any, pdf: Callable | None = None) -> list[dict]:
    from bs4 import BeautifulSoup  # lazy: bs4 is the `academia` extra

    soup = BeautifulSoup(html, "html.parser")
    ads: list[dict] = []

    for abs_url, title, parent_text in iter_recruitment_links(soup, url):
        if _SKIP_RE.search(title):
            continue

        ads.append(make_ad(
            id=stable_id("iith", abs_url, title),
            title=title[:250],
            original_url=abs_url,
            snapshot_fetched_at=fetched_at,
            department=extract_department(title),
            post_type=classify_post_type(title),
            apply_url=abs_url if abs_url.lower().endswith(".pdf") else None,
            info_url=url,
            parse_confidence=0.55,
            raw_text_excerpt=parent_text[:500],
        ))

    return ads
