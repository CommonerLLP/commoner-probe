# SPDX-License-Identifier: MIT
"""Site-specific parser for IIT Indore faculty recruitment.

Target: the careers/teaching_positions page. Each advertisement is a ``.tp-card``
with a heading, an advertisement number, and a "View" PDF link under
/uploads/career/. The parser emits one ad per card, using the card heading as the
title. Links outside a card fall back to the nearest preceding text as the title.
Hindi copies are dropped, and the mixed iiti.ac.in hosts and http:// links are
normalized to https://www.iiti.ac.in so one PDF always gets one ID.

Probe-native port of the origin project's scraper/parsers/iit_indore.py (Pydantic JobAd
output rewritten to plain dicts via make_ad).
"""

from __future__ import annotations

import re
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit, urlunsplit

from ..ad_factory import make_ad, stable_id

AD_NUMBER_RE = re.compile(r"IITI[/_][A-Z0-9/_\-\.]+", re.IGNORECASE)

SKIP_TEXTS = {
    "download", "advertisement in hindi", "click here", "view", "pdf",
    "notice", "notice: extension of last date", "extension of last date",
}

CAREER_PATH = "/uploads/career/"
CANONICAL_HOST = "www.iiti.ac.in"


def _normalize(abs_url: str) -> str:
    """Return abs_url with the iiti.ac.in host and scheme canonicalized."""
    parts = urlsplit(abs_url)
    host = (parts.hostname or "").lower()
    if host == "iiti.ac.in" or host == CANONICAL_HOST:
        return urlunsplit(("https", CANONICAL_HOST, parts.path, parts.query, parts.fragment))
    return abs_url


def _is_ad_link(href: str) -> bool:
    href_lower = href.lower()
    return CAREER_PATH in href_lower and "hindi" not in href_lower and "_hi." not in href_lower


def _card_ad_link(card):
    """Return the anchor that carries a card's advertisement PDF."""
    preferred = card.select_one(".tp-adv-link a[href]")
    if preferred is not None and _is_ad_link(preferred["href"].strip()):
        return preferred
    for a in card.find_all("a", href=True):
        if _is_ad_link(a["href"].strip()):
            return a
    return None


def parse(html: str, url: str, fetched_at: Any, pdf: Callable | None = None) -> list[dict]:
    from bs4 import BeautifulSoup  # lazy: bs4 is the `academia` extra

    soup = BeautifulSoup(html, "html.parser")
    ads: list[dict] = []
    seen: set[str] = set()
    seen_ad_numbers: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if CAREER_PATH not in href.lower():
            continue
        card = a.find_parent(class_="tp-card")
        if card is not None and a is not _card_ad_link(card):
            continue
        abs_url = _normalize(urljoin(url, href))
        if abs_url in seen:
            continue
        seen.add(abs_url)

        title = ""
        ad_number = None
        if card is not None:
            heading = card.select_one(".tp-job-title")
            number = card.select_one(".tp-adv-number")
            title = heading.get_text(" ", strip=True)[:250] if heading else ""
            m = AD_NUMBER_RE.search(number.get_text(" ", strip=True)) if number else None
            if m:
                ad_number = m.group(0)

        # Outside a card, walk backwards up the DOM for the nearest bold/heading title.
        node = a.parent if card is None else None
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
