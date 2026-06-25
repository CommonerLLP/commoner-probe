# SPDX-License-Identifier: MIT
"""Parser for private-university careers pages.

Private universities publish HSS jobs in one of three shapes: table-based
portals (Shiv Nadar, FLAME), card/list jobs pages (Azim Premji, Ashoka), and
standing faculty-call pages (Ahmedabad, JGU). Intentionally permissive — a
coarse official listing beats silence.

Probe-native port of academiaindia/scraper/parsers/private_university.py. The
Azim Premji per-position sub-page fetch is rerouted through the injected probe
``Fetcher`` (``pdf.get_html``); without it, APU degrades to index parsing.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Callable, Optional
from urllib.parse import urljoin

JOB_HINT_RE = re.compile(
    r"\b(faculty|professor|lecturer|academic\s+associate|research\s+fellow|"
    r"research\s+positions?|post[- ]?doc|teaching\s+fellow|visiting\s+scholar)\b",
    re.I,
)
TITLE_RE = re.compile(
    r"\b((?:chair\s+)?(?:assistant|associate|visiting)?\s*professor(?:\s*(?:-|/|in)\s+[^.;|\\n]{2,160})?|"
    r"faculty\s+positions?\s+in\s+[^.;|\\n]{2,160}|"
    r"teaching\s+fellow\s+positions?|research\s+positions?|academic\s+associate)\b",
    re.I,
)
SKIP_RE = re.compile(
    r"\b(admission|student|placement|alumni|newsletter|programme|program\b|"
    r"job\s+opportunities|apply\s+now\s*$|explore\s+opportunities\s*$)\b",
    re.I,
)
NAV_RE = re.compile(r"^\s*(home|jobs|about us|contact us|www\.|https?://|[\w.%-]+@[\w.-]+)\s*$", re.I)

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
DATE_RES = [
    re.compile(r"(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(?P<day>\d{1,2}),?\s+(?P<year>20\d{2})", re.I),
    re.compile(r"(?P<day>\d{1,2})\s+(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*,?\s+(?P<year>20\d{2})", re.I),
    re.compile(r"(?P<day>\d{1,2})[./-](?P<mon>\d{1,2})[./-](?P<year>20\d{2})"),
]


def _stable_id(*parts: str) -> str:
    m = hashlib.sha256()
    for p in parts:
        m.update((p or "").encode("utf-8"))
        m.update(b"\x00")
    return m.hexdigest()[:16]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _parse_date(text: str) -> Optional[str]:
    if not text or re.search(r"\bopen\b", text, re.I):
        return None
    for r in DATE_RES:
        m = r.search(text)
        if not m:
            continue
        gd = m.groupdict()
        mon_raw = gd["mon"]
        mon = int(mon_raw) if mon_raw.isdigit() else MONTHS.get(mon_raw[:3].lower())
        if not mon:
            continue
        day = int(gd["day"])
        year = int(gd["year"])
        if 1 <= day <= 31 and 1 <= mon <= 12:
            return f"{year:04d}-{mon:02d}-{day:02d}"
    return None


def _title_from_text(text: str) -> str:
    m = TITLE_RE.search(text or "")
    if m:
        title = _clean(m.group(1))
        title = re.split(r"\s+(?:Know More|Apply Now|Click here|The selected candidate|position requires)\b", title, flags=re.I)[0]
        return title.rstrip(" ,:-")
    first = re.split(r"\s{2,}| Deadline | Campus | Location ", text or "")[0]
    return _clean(first)


def _post_type(title: str) -> str:
    t = title.lower()
    if "academic associate" in t:
        return "Research"
    if "research" in t or "postdoc" in t or "fellow" in t:
        return "Research"
    if "faculty" in t or "professor" in t or "lecturer" in t or "teaching" in t:
        return "Faculty"
    return "Unknown"


def _contract(title: str) -> str:
    t = title.lower()
    if "visiting" in t:
        return "Visiting"
    if "contract" in t:
        return "Contractual"
    if "teaching fellow" in t or "academic associate" in t:
        return "Contractual"
    return "TenureTrack" if re.search(r"\b(professor|faculty)\b", t) else "Unknown"


def _make_ad(title: str, url: str, fetched_at: Any, excerpt: str,
             closing: Optional[str] = None, apply_url: Optional[str] = None,
             confidence: float = 0.55, excerpt_cap: int = 700) -> dict:
    title = _clean(title)[:220]
    excerpt = _clean(excerpt)[:excerpt_cap]
    return {
        "id": _stable_id("private", url, title, closing or ""),
        "institution_id": "__placeholder__",
        "ad_number": None,
        "title": title,
        "department": None,
        "discipline": None,
        "post_type": _post_type(title),
        "contract_status": _contract(title),
        "category_breakdown": None,
        "number_of_posts": None,
        "pay_scale": None,
        "publication_date": None,
        "closing_date": closing,
        "original_url": url,
        "snapshot_fetched_at": fetched_at.isoformat() if hasattr(fetched_at, "isoformat") else str(fetched_at),
        "parse_confidence": confidence,
        "raw_text_excerpt": excerpt,
        "apply_url": apply_url,
        "info_url": url,
        "unit_eligibility": None,
        "pdf_path": None,
        "pdf_parsed": False,
    }


def _row_ads(soup, base_url: str, fetched_at: Any) -> list[dict]:
    ads: list[dict] = []
    for tr in soup.find_all("tr"):
        cells = [_clean(td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])]
        row_text = _clean(" | ".join(cells))
        if not JOB_HINT_RE.search(row_text) or SKIP_RE.search(row_text):
            continue
        title = next(
            (c for c in cells
             if 8 <= len(c) <= 190 and JOB_HINT_RE.search(c) and not NAV_RE.search(c) and not SKIP_RE.search(c)),
            "",
        ) or _title_from_text(row_text)
        if NAV_RE.search(title):
            continue
        closing = _parse_date(row_text)
        link = tr.find("a", href=True)
        apply_url = urljoin(base_url, link["href"]) if link else None
        ads.append(_make_ad(title, base_url, fetched_at, row_text, closing, apply_url, 0.65))
    return ads


def _block_ads(soup, base_url: str, fetched_at: Any) -> list[dict]:
    ads: list[dict] = []
    seen: set[str] = set()
    selectors = ["article", "li", ".job", ".card", ".views-row", ".opportunity", "section"]
    for node in soup.select(",".join(selectors)):
        text = _clean(node.get_text(" ", strip=True))
        if len(text) < 20 or not JOB_HINT_RE.search(text) or SKIP_RE.search(text):
            continue
        heading = node.find(["h1", "h2", "h3", "h4", "strong"])
        title = _clean(heading.get_text(" ", strip=True)) if heading else ""
        if not title or not JOB_HINT_RE.search(title):
            title = _title_from_text(text)
        if SKIP_RE.search(title):
            continue
        if len(title) < 8 or NAV_RE.search(title):
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        link = node.find("a", href=True)
        apply_url = urljoin(base_url, link["href"]) if link else None
        closing = _parse_date(text)
        ads.append(_make_ad(title, base_url, fetched_at, text, closing, apply_url, 0.55))
    return ads


def _link_ads(soup, base_url: str, fetched_at: Any) -> list[dict]:
    ads: list[dict] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        if a["href"].strip() == "#":
            continue
        text = _clean(a.get_text(" ", strip=True))
        parent = _clean(a.parent.get_text(" ", strip=True) if a.parent else text)
        hay = f"{text} {parent}"
        if not JOB_HINT_RE.search(hay) or SKIP_RE.search(hay):
            continue
        title = parent if len(parent) < 220 and JOB_HINT_RE.search(parent) else text
        title = _title_from_text(title)
        if SKIP_RE.search(title):
            continue
        if len(title) < 8 or NAV_RE.search(title):
            continue
        href = urljoin(base_url, a["href"])
        key = f"{title.lower()} {href}"
        if key in seen:
            continue
        seen.add(key)
        ads.append(_make_ad(title, href, fetched_at, parent, _parse_date(parent), href, 0.5))
    return ads


# --- FLAME (one <table> per position, no per-position URL) -------------------

_FLAME_INVITE_PATTERNS = [
    re.compile(
        r"\b(?:we\s+(?:are\s+)?invit(?:e|ing)|is\s+inviting|are\s+invited|are\s+looking)"
        r"(?:\s+applications?)?(?:\s+from\s+[^.]{0,150}?)?\s+for\s+"
        r"(?:the\s+)?(?:positions?\s+of\s+)?(?:a\s+)?(?:full[- ]time\s+)?([^.]{3,180})\.",
        re.I,
    ),
    re.compile(
        r"\bwe\s+welcome\s+applications?\s+from\s+(?:all\s+subfields\s+of\s+)?([^.]{3,160})\.",
        re.I,
    ),
]
_FLAME_END_RE = re.compile(
    r"\b(?:To know more about our|For informal enquiries|FLAME University is an affirmative)\b", re.I)


def _flame_clean_title(raw: str) -> str:
    t = raw.strip()
    t = re.sub(r"^(?:the\s+|full[- ]time\s+|positions?\s+of\s+|a\s+)+", "", t, flags=re.I)
    t = re.sub(r"\s+positions?$", "", t, flags=re.I)
    t = re.split(r"\s+(?:While|Faculty|We are|The selected|Endowed|This|Candidates)\b", t, flags=re.I)[0]
    return _clean(t).rstrip(",.;: ")


def _flame_table_ad(table, base_url: str, fetched_at: Any) -> Optional[dict]:
    text = _clean(table.get_text(" ", strip=True))
    if len(text) < 200:
        return None
    invite_m = None
    for p in _FLAME_INVITE_PATTERNS:
        invite_m = p.search(text)
        if invite_m:
            break
    if not invite_m:
        return None
    title = _flame_clean_title(invite_m.group(1) if invite_m.groups() else "")
    if len(title) < 5:
        return None
    body = text[invite_m.start():]
    end_m = _FLAME_END_RE.search(body)
    if end_m:
        body = body[: end_m.start()]
    body = _clean(body)
    dept_m = re.search(
        r"\b((?:Faculty|School)\s+of\s+[A-Z][\w\s&]{2,40}?)"
        r"(?=\s+(?:at\s+FLAME|has\s+|stands\s+|is\s+|offers?\s+|in\s+the\s+areas|—|invites?\s+))",
        text,
    )
    department = _clean(dept_m.group(1)).rstrip(",.;& ") if dept_m else None
    discipline = None
    disc_m = re.match(
        r"(?:Chair\s+)?(?:Assistant|Associate|Visiting|Adjunct)?\s*"
        r"(?:Distinguished\s+)?Professor(?:\s+of\s+Practice)?"
        r"\s+in\s+(?:all\s+areas\s+of\s+)?(.+?)$",
        title, re.I,
    )
    if disc_m:
        discipline = _clean(disc_m.group(1)).rstrip(" ,.;:")
        discipline = re.sub(r"\s+positions?$", "", discipline, flags=re.I).strip()
    closing = _parse_date(body)
    ad = _make_ad(title, base_url, fetched_at, body, closing, base_url, 0.72, excerpt_cap=2400)
    if department:
        ad["department"] = department[:120]
    if discipline:
        ad["discipline"] = discipline[:120]
    return ad


def _flame_ads(soup, base_url: str, fetched_at: Any) -> list[dict]:
    ads: list[dict] = []
    seen_titles: set[str] = set()
    for table in soup.find_all("table"):
        ad = _flame_table_ad(table, base_url, fetched_at)
        if not ad:
            continue
        key = ad["title"].casefold()
        if key in seen_titles:
            continue
        seen_titles.add(key)
        ads.append(ad)
    return ads


# --- Azim Premji (per-position detail pages) ---------------------------------

_APU_NON_POSITION_RE = re.compile(
    r"/jobs/(?:index|at:|role:|location:|department:|category:)|\.ics$", re.I)


def _apu_position_ad(html: str, url: str, fetched_at: Any, closing: Optional[str] = None) -> Optional[dict]:
    from bs4 import BeautifulSoup  # lazy

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    title = ""
    for h in soup.find_all("h1"):
        candidate = _clean(h.get_text(" ", strip=True))
        if candidate:
            title = candidate
            break
    if not title or "page not found" in title.lower():
        return None
    meta_desc = soup.find("meta", attrs={"name": "description"})
    summary = _clean(meta_desc.get("content", "")) if meta_desc else ""
    narrative_parts: list[str] = []
    intro_re = re.compile(
        r"\bWe (?:are particularly interested|invite applications|welcome applicants?)\b", re.I)
    for p in soup.find_all("p"):
        ptxt = _clean(p.get_text(" ", strip=True))
        if ptxt and intro_re.search(ptxt):
            narrative_parts.append(ptxt)
    narrative = " ".join(narrative_parts)

    def section_for(heading: str) -> str:
        for h in soup.find_all(["h2", "h3"]):
            if heading.casefold() in h.get_text(" ", strip=True).casefold():
                section = h.find_parent("section")
                if not section:
                    continue
                section_text = _clean(section.get_text(" ", strip=True))
                return _clean(re.sub(rf"^{re.escape(heading)}\b\s*", "", section_text, flags=re.I))
        return ""

    requirements = section_for("Requirements")
    if summary and narrative.startswith(summary):
        excerpt_parts = [narrative]
    else:
        excerpt_parts = [s for s in [summary, narrative] if s]
    excerpt = " — ".join(excerpt_parts)
    discipline = None
    disc_m = re.match(r"Faculty\s+Positions?\s+(?:for|in)\s+(.+?)$", title, re.I)
    if disc_m:
        discipline = _clean(disc_m.group(1)).rstrip(" ,.;:")
    posts_count: Optional[int] = None
    if requirements:
        posts_m = re.search(r"\bOpen\s+Positions?\s*:?\s*(\d+)\b", requirements, re.IGNORECASE)
        if posts_m:
            try:
                posts_count = int(posts_m.group(1))
            except ValueError:
                posts_count = None
            requirements = re.sub(
                r"\s*\bOpen\s+Positions?\s*:?\s*\d+\b\s*\.?\s*", " ", requirements, flags=re.IGNORECASE,
            ).strip()
    ad = _make_ad(title, url, fetched_at, excerpt, closing, url, 0.85, excerpt_cap=2400)
    if requirements:
        ad["unit_eligibility"] = requirements[:600]
    if discipline:
        ad["discipline"] = discipline[:120]
    if posts_count is not None:
        ad["number_of_posts"] = posts_count
    return ad


def _apu_ads(soup, base_url: str, fetched_at: Any, fetch_position: Optional[Callable] = None) -> list[dict]:
    if fetch_position is None:
        return []  # no fetcher injected -> caller falls back to index parsing
    ads: list[dict] = []
    seen_urls: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"]).split("#")[0].rstrip("/")
        if "azimpremjiuniversity.edu.in" not in href:
            continue
        if not re.search(r"/jobs/[a-z0-9-]+$", href, re.I):
            continue
        if _APU_NON_POSITION_RE.search(href):
            continue
        if href in seen_urls:
            continue
        seen_urls.add(href)
        closing = None
        article = a.find_parent("article")
        if article:
            closing = _parse_date(_clean(article.get_text(" ", strip=True)))
        try:
            html = fetch_position(href)
        except Exception:
            continue
        if not html or len(html) < 1000:
            continue
        ad = _apu_position_ad(html, href, fetched_at, closing=closing)
        if ad:
            ads.append(ad)
    return ads


def parse(html: str, url: str, fetched_at: Any, pdf: Any = None) -> list[dict]:
    from bs4 import BeautifulSoup  # lazy: bs4 is the `academia` extra

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    page_text = _clean(soup.get_text(" ", strip=True))
    if "ahduni.edu.in" in url:
        return [_make_ad("Standing faculty recruitment - Ahmedabad University", url, fetched_at, page_text, None, url, 0.45)]
    if "krea.edu.in" in url:
        return [_make_ad("Faculty - SIAS, 2025-26", url, fetched_at, page_text, None, url, 0.4)]

    fetch_position = pdf.get_html if pdf is not None else None
    if "ashoka.edu.in" in url:
        parsed_ads = [*_block_ads(soup, url, fetched_at), *_link_ads(soup, url, fetched_at)]
    elif "flame.edu.in" in url:
        parsed_ads = _flame_ads(soup, url, fetched_at) or _row_ads(soup, url, fetched_at)
    elif "azimpremjiuniversity.edu.in" in url:
        parsed_ads = _apu_ads(soup, url, fetched_at, fetch_position) or _block_ads(soup, url, fetched_at)
    else:
        parsed_ads = _row_ads(soup, url, fetched_at)
    if not parsed_ads:
        parsed_ads = _block_ads(soup, url, fetched_at)
    if not parsed_ads:
        parsed_ads = _link_ads(soup, url, fetched_at)

    ads: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for ad in parsed_ads:
        if "flame.edu.in" in url and (ad.get("title") or "").casefold() in {
            "professor", "associate professor", "assistant professor",
        }:
            continue
        key = ((ad.get("title") or "").casefold(), "")
        if key in seen:
            continue
        seen.add(key)
        ads.append(ad)

    if not ads and JOB_HINT_RE.search(page_text):
        ads.append(_make_ad("Standing faculty recruitment / careers page", url, fetched_at, page_text, None, url, 0.4))

    return ads[:80]
