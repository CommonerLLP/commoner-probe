# SPDX-License-Identifier: MIT
"""Site-specific parser for IIT Indore faculty recruitment.

Target: the recruitments/faculty-positions page — loose
``<p><strong>Title</strong></p>`` followed by ``<p><a href=".pdf">Download</a></p>``.
Finds PDF links under /public/storage/recruitments/ and associates the nearest
preceding bold text as the title.

Probe-native port of academiaindia/scraper/parsers/iit_indore.py (Pydantic JobAd
output rewritten to plain dicts via make_ad).
"""

from __future__ import annotations

import re
from typing import Any, Callable
from urllib.parse import urljoin

from ..ad_factory import make_ad, stable_id

AD_NUMBER_RE = re.compile(r"IITI[/_][A-Z0-9/_\-\.]+", re.IGNORECASE)

SKIP_TEXTS = {
    "download", "advertisement in hindi", "click here", "view", "pdf",
    "notice", "notice: extension of last date", "extension of last date",
}


def parse(html: str, url: str, fetched_at: Any, pdf: Callable | None = None) -> list[dict]:
    from bs4 import BeautifulSoup  # lazy: bs4 is the `academia` extra

    soup = BeautifulSoup(html, "html.parser")
    ads: list[dict] = []
    seen: set[str] = set()
    seen_ad_numbers: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if "public/storage/recruitments" not in href and "public/storage/career/faculty" not in href:
            continue
        abs_url = urljoin(url, href)
        if abs_url in seen:
            continue
        seen.add(abs_url)

        # Walk backwards up the DOM for the nearest bold/heading title.
        title = ""
        ad_number = None
        node = a.parent
        for _ in range(6):
            if node is None:
                break
            for sib in reversed(list(node.previous_siblings)):
                text = sib.get_text(" ", strip=True) if hasattr(sib, "get_text") else str(sib).strip()
                if not text or text.lower() in SKIP_TEXTS:
                    continue
                if len(text) > 10:
                    title = text[:250]
                    m = AD_NUMBER_RE.search(title)
                    if m:
                        ad_number = m.group(0)
                    break
            if title:
                break
            node = node.parent

        if not title:
            title = "Faculty Recruitment Advertisement"
        if "closed" in title.lower():
            continue
        href_lower = href.lower()
        if "hindi" in href_lower or "_hi." in href_lower:
            continue
        if ad_number and ad_number in seen_ad_numbers:
            continue
        if ad_number:
            seen_ad_numbers.add(ad_number)

        ads.append(make_ad(
            id=stable_id("iit-indore", abs_url, ad_number or title),
            title=title,
            original_url=abs_url,
            snapshot_fetched_at=fetched_at,
            ad_number=ad_number,
            post_type="Faculty",
            contract_status="Regular",
            parse_confidence=0.75,
            info_url=url,
        ))

    return ads
