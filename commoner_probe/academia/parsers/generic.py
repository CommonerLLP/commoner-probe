# SPDX-License-Identifier: MIT
"""Generic heuristic parser for HEI recruitment pages.

Reads the listing HTML and yields ads for links that look like recruitment
advertisements (keyword match in link text / href / surrounding text, or a PDF
near recruitment context). Does NOT fetch PDFs — keeps latency predictable; a
site-specific parser earns its keep by going into the PDF.

Probe-native port of academiaindia/scraper/parsers/generic.py: emits plain
dicts with string ``post_type`` / ``contract_status`` instead of Pydantic
``JobAd`` objects.
"""

from __future__ import annotations

import re
from typing import Any, Callable
from urllib.parse import urljoin

from .._common import PLACEHOLDER_INSTITUTION_ID, iso, stable_id

RECRUITMENT_KEYWORDS = [
    r"recruit", r"vacanc", r"advert", r"non[- ]?teaching", r"ministerial",
    r"scientist", r"engagement", r"walk[- ]?in",
    r"अधिसूचना", r"भर्ती", r"विज्ञापन", r"रिक्ति",
]

AD_NUMBER_RE = re.compile(
    r"(?:Advertisement|Advt\.?|Notification|Ref\.?|F\.?\s?No\.?)[\s:/No\.]*([A-Z0-9/\-\.\s]{3,40})",
    re.IGNORECASE,
)
DATE_RE = re.compile(
    r"(\d{1,2})[\s\.\-/](Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|[0-1]?\d)[\s\.\-/](20\d{2})",
    re.IGNORECASE,
)
CLOSING_HINTS = re.compile(r"(last|closing|deadline|apply\s*by)\s*(date)?", re.IGNORECASE)
PUBLISHED_HINTS = re.compile(r"(advertise|publish|issued|dated)", re.IGNORECASE)
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
    r"/(career|careers|jobs?)/(staff/)?[^/]*\.(pdf|docx?)$|/(career|careers|jobs?)/jobs?/\d+", re.I
)

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def _is_recruitment_link(link_text: str, href: str, surrounding_text: str) -> bool:
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


def _extract_ad_number(text: str) -> str | None:
    m = AD_NUMBER_RE.search(text)
    return m.group(1).strip().rstrip(".,;:") if m else None


def _to_iso(day: str, month_s: str, year: str) -> str | None:
    try:
        d, y = int(day), int(year)
        ms = month_s.lower()[:3]
        if ms in _MONTHS:
            mo = _MONTHS[ms]
        elif month_s.isdigit():
            mo = int(month_s)
        else:
            return None
        if not (1 <= mo <= 12 and 1 <= d <= 31 and 2015 <= y <= 2035):
            return None
        return f"{y:04d}-{mo:02d}-{d:02d}"
    except Exception:
        return None


def _extract_dates(text: str) -> tuple[str | None, str | None]:
    pub: str | None = None
    close: str | None = None
    matches = list(DATE_RE.finditer(text))
    for m in matches:
        ctx = text[max(0, m.start() - 60): m.start()].lower()
        iso_date = _to_iso(*m.groups())
        if iso_date is None:
            continue
        if CLOSING_HINTS.search(ctx):
            close = close or iso_date
        elif PUBLISHED_HINTS.search(ctx):
            pub = pub or iso_date
    if close is None and len(matches) == 1:
        close = _to_iso(*matches[0].groups())
    return pub, close


def _classify(context: str) -> tuple[str, str]:
    lc = context.lower()
    if "faculty" in lc or "professor" in lc or "reader" in lc or "lecturer" in lc:
        post_type = "Faculty"
    elif "scientist" in lc or "research" in lc:
        post_type = "Scientific"
    elif "ministerial" in lc or "non-teaching" in lc or "section officer" in lc or "assistant" in lc:
        post_type = "NonFaculty"
    else:
        post_type = "Unknown"

    if "guest" in lc:
        contract = "Guest"
    elif "ad-hoc" in lc or "adhoc" in lc:
        contract = "AdHoc"
    elif "contractual" in lc or "contract basis" in lc:
        contract = "Contractual"
    elif "visiting" in lc:
        contract = "Visiting"
    elif "tenure track" in lc or "tenure-track" in lc:
        contract = "TenureTrack"
    elif "regular" in lc or "permanent" in lc:
        contract = "Regular"
    else:
        contract = "Unknown"
    return post_type, contract


def parse(html: str, url: str, fetched_at: Any, pdf: Callable | None = None) -> list[dict]:
    from bs4 import BeautifulSoup  # lazy: bs4 is the `academia` extra

    soup = BeautifulSoup(html, "html.parser")
    ads: list[dict] = []
    seen_urls: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue
        link_text = (a.get_text(" ", strip=True) or "").strip()
        if not link_text:
            continue
        parent_text = a.parent.get_text(" ", strip=True) if a.parent else link_text
        context = f"{link_text}  {parent_text}"
        if not _is_recruitment_link(link_text, href, context):
            continue
        abs_url = urljoin(url, href)
        if abs_url in seen_urls:
            continue
        seen_urls.add(abs_url)

        ad_number = _extract_ad_number(context)
        pub, close = _extract_dates(context)
        post_type, contract = _classify(context)

        generic_link = link_text.lower() in {
            "view", "click here", "click", "download", "pdf", "read more",
            "view advertisement", "here", "download advertisement pdf", "download pdf",
        }
        if generic_link and len(parent_text) > len(link_text) + 5:
            title_text = parent_text.replace(link_text, "").strip(" -—·|")
            title_text = re.sub(
                r"\.?\s*(download|view|click|pdf)\s*(advertisement|pdf)?\s*$", "",
                title_text, flags=re.IGNORECASE,
            ).strip(" -—·|.")
            title = title_text or link_text
        else:
            title = link_text

        ad_number_or_title = ad_number or title
        ads.append({
            "id": stable_id("pending-inst-id", ad_number_or_title, pub or close or ""),
            "institution_id": PLACEHOLDER_INSTITUTION_ID,
            "ad_number": ad_number,
            "title": title[:250],
            "department": None,
            "discipline": None,
            "post_type": post_type,
            "contract_status": contract,
            "category_breakdown": None,
            "number_of_posts": None,
            "pay_scale": None,
            "publication_date": pub,
            "closing_date": close,
            "original_url": abs_url,
            "snapshot_fetched_at": iso(fetched_at),
            "parse_confidence": 0.5 if RECRUITMENT_LINK_RE.search(f"{link_text} {href}") else 0.4,
            "raw_text_excerpt": context[:500],
            "apply_url": None,
            "info_url": url,
            "publications_required": None,
            "unit_eligibility": None,
            "pdf_path": None,
            "pdf_parsed": False,
        })

    return ads
