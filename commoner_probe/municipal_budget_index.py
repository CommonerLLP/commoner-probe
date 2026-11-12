# SPDX-License-Identifier: MIT
"""Find, and download, the budget documents three city publishers list on index pages.

CONTEXT
=======
Three publishers, three page shapes. The parsers at the top of this module are
pure: they fetch nothing and write nothing. :class:`MunicipalBudgetProbe`, at
the bottom, fetches the pages, runs the parsers, and downloads each document it
finds. :data:`MUNICIPAL_BUDGET_PORTALS` names the eight pages it knows.

- Ahmedabad Municipal Corporation (host ``ahmedabadcity.gov.in``) lists budget
  books and balance sheets as plain anchors on two pages, ``/SP/Budget`` and
  ``/SP/BalanceSheet``. Some anchors point at ``.pdf`` files and some at
  ``ViewFile?TYPE=FileRepository,<id>`` links. The same label can appear twice
  with different URLs, and both are kept. See :func:`parse_anchor_index`.
- The Government of NCT of Delhi Finance Department (host
  ``finance.delhi.gov.in``) is a Drupal site. Four pages list one PDF per
  fiscal year in a ``generic_multiple_files`` view. The Detailed Demands for
  Grants index lists one detail page per year, and each detail page lists that
  year's volumes. See :func:`parse_drupal_file_links` and
  :func:`parse_detail_page_links`.
- The Municipal Corporation of Delhi (host ``mcdonline.nic.in``) serves its
  menus as JSON from ``/portal/showSubMenu``, ``/portal/showContent``, and
  ``/portal/showPDFContent``. A response can nest child menus, and the document
  table sits in an HTML string inside a JSON field. See
  :func:`parse_mcd_menu_guides` and :func:`parse_mcd_documents`.

Decision B (owner decision, confirmed November 12, 2026):
``finance.delhi.gov.in/robots.txt`` answers 403 to every User-Agent while the
pages answer 200. The probe that fetches these pages relaxes the robots check
for that one host. The parsers don't depend on it.

The Detailed Demands gap. On a GNCTD Detailed Demands detail page the demand
volumes carry bare numbers as filenames (``d-1_8.pdf``, ``d7.pdf``,
``111.pdf``), and the title carries no year. :func:`parse_drupal_file_links`
is a faithful port of sevent4's ``extract_finance_pdf_links``, so it drops all
of them. The probe keeps that behaviour for parity with sevent4, and a
follow-up bead (commoner-probe-6e7) decides how to close the gap. Pagination
follows the same rule: the Drupal ``?page=1`` pagers are not followed.

The probe finds documents and records where they are. Parsing figures out of
the PDFs is a sevent4 concern.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from commoner_probe.base import _private_tmp_path, note_text_layer
from commoner_probe.budget.probe import BUDGET_USER_AGENT
from commoner_probe.http_client import make_session, read_capped_response

#: A PDF whose extracted text is shorter than this has no usable text layer.
TEXT_LAYER_MIN_CHARS = 200

# Ported from sevent4 scripts/recipes/delhi/acquire_finance.py, DETAIL_PAGE_RE
# (used by extract_detail_page_links).
DETAIL_PAGE_RE = re.compile(r"/finance/detailed-demands-grants-[0-9]{4}-[0-9]{2}(?:-voa)?")

# Ported from sevent4 scripts/recipes/delhi/acquire_finance.py, PDF_RE
# (used by _is_pdf_url).
PDF_RE = re.compile(r"\.pdf(?:$|[?#])", re.I)

# Ported from sevent4 scripts/recipes/delhi/acquire_finance.py, BUDGETISH_RE
# (used by _looks_like_budget_doc and extract_mcd_menu_guides).
BUDGETISH_RE = re.compile(
    r"\b(budget|estimate|estimates|grant|grants|receipt|receipts|accounts?|finance|annual\s+account)\b",
    re.I,
)

# Ported from sevent4 scripts/recipes/delhi/acquire_finance.py, URL_BUDGETISH_RE
# (used by _url_looks_like_budget_doc).
URL_BUDGETISH_RE = re.compile(r"(budget|receipt|demand|grant|ddg|bag|gender|speech)", re.I)

# Ported from sevent4 scripts/recipes/delhi/acquire_finance.py, MCD_BUDGET_RE
# (used by _append_mcd_doc).
MCD_BUDGET_RE = re.compile(
    r"\b(budget|estimate|estimates|grant|grants|receipt|receipts|rbe|be|income|expenditure)\b",
    re.I,
)

# Ported from sevent4 sevent4/domain/budget.py, finance_link_year (the Ahmedabad
# year heuristic), tightened into one rule set for both portals. The first rule
# also covers sevent4 scripts/recipes/delhi/acquire_finance.py FISCAL_YEAR_RE.
# `20YY-YY` or `20YY-20YY`; the end year must be the start year plus one.
_FY_LONG_RE = re.compile(r"(?<!\d)(20\d{2})[-_/](20\d{2}|\d{2})(?!\d)")
# `31-03-YYYY` or `31-03-YY`; the fiscal year ends on that date. The hyphen is
# the only separator: the 2005-06 schedule files named `31_03_06` don't match.
_FY_DATE_RE = re.compile(r"(?<!\d)31-03-(20\d{2}|\d{2})(?!\d)")
# `YY-YY`, `YY_YY`, or `YY/YY` with the start from 05 to 29.
_FY_SHORT_RE = re.compile(r"(?<!\d)(0[5-9]|1\d|2\d)[-_/](\d{2})(?!\d)")


def clean_text(value: str | None) -> str:
    """Unescape entities, drop tags, and collapse whitespace.

    Ported from sevent4 scripts/recipes/delhi/acquire_finance.py, clean_text.
    """
    value = unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def fiscal_year_from_text(text: str | None) -> str | None:
    """Return the first fiscal year named in `text`, as ``YYYY-YY``, or None.

    The earliest valid match by position wins. A range whose end year isn't the
    start year plus one, such as ``2026-26``, is not a match.
    """
    if not text:
        return None
    candidates: list[tuple[int, str]] = []
    for match in _FY_LONG_RE.finditer(text):
        start, end = match.groups()
        end_year = int(end) if len(end) == 4 else int(f"20{end}")
        if end_year == int(start) + 1:
            candidates.append((match.start(), f"{start}-{end[-2:]}"))
    for match in _FY_DATE_RE.finditer(text):
        token = match.group(1)
        end_year = int(token) if len(token) == 4 else 2000 + int(token)
        candidates.append((match.start(), f"{end_year - 1}-{end_year % 100:02d}"))
    for match in _FY_SHORT_RE.finditer(text):
        start, end = (int(part) for part in match.groups())
        if end == start + 1:
            candidates.append((match.start(), f"20{start:02d}-{end:02d}"))
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1]


class _AnchorParser(HTMLParser):
    """Collect every anchor's href, title attribute, and text, plus the page title.

    Ported from sevent4 scripts/recipes/delhi/acquire_finance.py, _AnchorParser.
    Each anchor keeps its text two ways: `text` joins text chunks with a space
    (the Delhi recipe) and `text_tight` joins them with nothing (sevent4's
    sevent4/application/budget.py, _FinanceBookHTMLParser, the Ahmedabad
    recipe).
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._text: list[str] = []
        self._in_title = False
        self.title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "title":
            self._in_title = True
            return
        if tag.lower() != "a":
            return
        attr = {k.lower(): v or "" for k, v in attrs}
        self._current = {"href": attr.get("href", ""), "title": attr.get("title", "")}
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if self._current is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False
            return
        if tag.lower() == "a" and self._current is not None:
            self._current["text"] = clean_text(" ".join(self._text))
            self._current["text_tight"] = " ".join("".join(self._text).split())
            self.anchors.append(self._current)
            self._current = None
            self._text = []

    @property
    def page_title(self) -> str:
        return clean_text(" ".join(self.title_parts))


def _html_anchors(page_html: str) -> tuple[list[dict[str, str]], str]:
    parser = _AnchorParser()
    parser.feed(page_html)
    return parser.anchors, parser.page_title


def _is_pdf_url(url: str) -> bool:
    """Ported from sevent4 scripts/recipes/delhi/acquire_finance.py, _is_pdf_url."""
    return bool(PDF_RE.search(urlparse(url).path))


def _looks_like_budget_doc(title: str, url: str) -> bool:
    """Ported from sevent4 scripts/recipes/delhi/acquire_finance.py, _looks_like_budget_doc."""
    return bool(BUDGETISH_RE.search(f"{title} {url}".replace("_", " ")))


def _url_looks_like_budget_doc(url: str) -> bool:
    """Ported from sevent4 scripts/recipes/delhi/acquire_finance.py, _url_looks_like_budget_doc."""
    return bool(URL_BUDGETISH_RE.search(urlparse(url).path))


def parse_anchor_index(
    html: str, page_url: str, *, keywords: tuple[str, ...]
) -> list[dict[str, str]]:
    """List the finance-book anchors on an Ahmedabad index page.

    Ported from sevent4 sevent4/application/budget.py, discover_finance_links and
    _FinanceBookHTMLParser. An anchor is kept when its label and href, lower-cased,
    contain a keyword and contain ``.pdf`` or ``viewfile``, and when a fiscal year
    appears in the label or href. Anchors are de-duplicated on
    ``(fiscal_year, url)`` and sorted by fiscal year, newest first. Two anchors
    with the same label and different URLs are both kept.
    """
    anchors, _ = _html_anchors(html)
    docs: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for anchor in anchors:
        href = anchor.get("href", "")
        if not href:
            continue
        label = anchor["text_tight"]
        haystack = f"{label} {href}".lower()
        if not any(keyword in haystack for keyword in keywords):
            continue
        if ".pdf" not in haystack and "viewfile" not in haystack:
            continue
        fiscal_year = fiscal_year_from_text(f"{label} {href}")
        if not fiscal_year:
            continue
        url = urljoin(page_url, href)
        if (fiscal_year, url) in seen:
            continue
        seen.add((fiscal_year, url))
        docs.append(
            {"title": label, "url": url, "fiscal_year": fiscal_year, "source_page": page_url}
        )
    return sorted(docs, key=lambda doc: doc["fiscal_year"], reverse=True)


def parse_drupal_file_links(
    html: str, page_url: str, *, document_type: str
) -> list[dict[str, str]]:
    """List the PDF links on one GNCTD Finance listing page.

    A port of sevent4's ``extract_finance_pdf_links`` in
    scripts/recipes/delhi/acquire_finance.py. It drops the Detailed Demands volumes
    whose filenames are bare demand numbers (see the module docstring).
    """
    anchors, page_title = _html_anchors(html)
    docs: list[dict[str, str]] = []
    seen: set[str] = set()
    for anchor in anchors:
        href = anchor.get("href", "")
        if not href:
            continue
        url = urljoin(page_url, href)
        if not _is_pdf_url(url):
            continue
        title = clean_text(anchor.get("title")) or clean_text(anchor.get("text"))
        if title.lower() in {"download", "view"}:
            title = ""
        fallback_title = page_title if fiscal_year_from_text(page_title) else ""
        used_fallback_title = not title and bool(fallback_title)
        doc_title = title or fallback_title
        if used_fallback_title and not _url_looks_like_budget_doc(url):
            continue
        fiscal_year = fiscal_year_from_text(doc_title)
        # Take the year from the file URL only when the URL looks like a budget
        # document. That keeps unrelated dated PDFs in the site chrome out.
        if fiscal_year is None and _url_looks_like_budget_doc(url):
            fiscal_year = fiscal_year_from_text(urlparse(url).path)
        if fiscal_year is None:
            continue
        if not doc_title:
            doc_title = f"{document_type.replace('_', ' ').title()} {fiscal_year}"
        if not _looks_like_budget_doc(doc_title, url):
            continue
        if url in seen:
            continue
        seen.add(url)
        docs.append(
            {
                "title": doc_title,
                "url": url,
                "fiscal_year": fiscal_year,
                "source_page": page_url,
                "document_type": document_type,
            }
        )
    return docs


def parse_detail_page_links(html: str, page_url: str) -> list[dict[str, str | None]]:
    """List the per-year Detailed Demands detail pages on the GNCTD index.

    Ported from sevent4's ``extract_detail_page_links`` in
    scripts/recipes/delhi/acquire_finance.py.
    """
    anchors, _ = _html_anchors(html)
    pages: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for anchor in anchors:
        href = anchor.get("href", "")
        if not href or not DETAIL_PAGE_RE.search(href):
            continue
        url = urljoin(page_url, href)
        if url in seen:
            continue
        seen.add(url)
        fiscal_year = fiscal_year_from_text(url)
        suffix = " VOA" if url.lower().endswith("-voa") else ""
        pages.append(
            {
                "fiscal_year": fiscal_year,
                "title": f"Detailed Demands for Grants {fiscal_year}{suffix}".strip(),
                "url": url,
                "source_page": page_url,
            }
        )
    return pages


def _first_text(obj: dict[str, Any], *keys: str) -> str:
    """Ported from sevent4 scripts/recipes/delhi/acquire_finance.py, _first_text."""
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return clean_text(value)
    return ""


def _string_contains_html(value: Any) -> bool:
    """Ported from sevent4 scripts/recipes/delhi/acquire_finance.py, _string_contains_html."""
    return isinstance(value, str) and "<" in value and ">" in value


def parse_mcd_menu_guides(payload: str) -> list[dict[str, str]]:
    """List the child MCD menu guides whose title looks like a budget menu.

    A port of sevent4's ``extract_mcd_menu_guides`` in
    scripts/recipes/delhi/acquire_finance.py.
    """
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return []

    guides: list[dict[str, str]] = []
    seen: set[str] = set()

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            title = _first_text(obj, "menuName", "title", "name", "label")
            menu_guide = _first_text(obj, "menuGuide", "menuGuid", "guide")
            if title and menu_guide and BUDGETISH_RE.search(title):
                if menu_guide not in seen:
                    seen.add(menu_guide)
                    guides.append({"title": title, "menu_guide": menu_guide})
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(parsed)
    return guides


def _append_mcd_doc(
    docs: list[dict[str, str | None]],
    seen: set[str],
    title: str,
    href: str,
    source_url: str,
) -> None:
    """Ported from sevent4 scripts/recipes/delhi/acquire_finance.py, _append_mcd_doc."""
    url = urljoin(source_url, href)
    leaf = f"{title} {urlparse(url).path}".replace("_", "-")
    if not MCD_BUDGET_RE.search(leaf):
        return
    if not (_is_pdf_url(url) or "download" in url.lower() or "showpdf" in url.lower()):
        return
    if url in seen:
        return
    seen.add(url)
    docs.append(
        {
            "title": clean_text(title),
            "url": url,
            "fiscal_year": fiscal_year_from_text(leaf),
            "source_page": source_url,
        }
    )


def _extract_mcd_from_html(page_html: str, source_url: str) -> list[dict[str, str | None]]:
    """Ported from sevent4 scripts/recipes/delhi/acquire_finance.py, _extract_mcd_from_html."""
    anchors, _ = _html_anchors(page_html)
    docs: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for anchor in anchors:
        title = clean_text(anchor.get("title")) or clean_text(anchor.get("text"))
        href = anchor.get("href", "")
        if title and href:
            _append_mcd_doc(docs, seen, title, href, source_url)
    return docs


def parse_mcd_documents(payload: str, source_url: str) -> list[dict[str, str | None]]:
    """List the budget document links in one MCD menu API response.

    A port of sevent4's ``extract_mcd_budget_entries`` in
    scripts/recipes/delhi/acquire_finance.py. A body that isn't JSON is parsed as
    HTML.
    """
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return _extract_mcd_from_html(payload, source_url)

    docs: list[dict[str, str | None]] = []
    seen: set[str] = set()

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            title = _first_text(
                obj, "menuName", "documentName", "docName", "title", "name", "label", "subject"
            )
            href = _first_text(
                obj, "pdfPath", "filePath", "documentPath", "downloadUrl", "url", "href", "link"
            )
            if href and title:
                _append_mcd_doc(docs, seen, title, href, source_url)
            for value in obj.values():
                if _string_contains_html(value):
                    for row in _extract_mcd_from_html(value, source_url):
                        key = str(row["url"])
                        if key not in seen:
                            seen.add(key)
                            docs.append(row)
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(parsed)
    return docs


# --- portal registry --------------------------------------------------------

MCD_BASE = "https://mcdonline.nic.in"
#: The most menu guides one MCD crawl fetches. A menu tree that loops, or fans out
#: past this, stops here.
MAX_MENU_GUIDES = 40
_GNCTD_HOST = "finance.delhi.gov.in"
_TEMPLATES = ("anchor_index", "drupal_files", "drupal_detail_pages", "mcd_menu_json")


@dataclass(frozen=True)
class MunicipalBudgetPortal:
    """One index page, or one menu tree, that lists a publisher's budget documents."""

    code: str
    city: str
    body: str
    host: str
    template: str  # one of _TEMPLATES
    document_type: str
    pages: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    menu_guides: tuple[str, ...] = ()


_AMC = "Ahmedabad Municipal Corporation"
_GNCTD = "Government of NCT of Delhi"
_GNCTD_PAGES = (
    ("gnctd-budget-at-a-glance", "budget_at_a_glance", "/finance/budget-glance"),
    ("gnctd-receipts-budget", "receipts_budget", "/finance/receipts-budget"),
    ("gnctd-budget-speech", "budget_speech", "/finance/budget-speech"),
    ("gnctd-gender-budget", "gender_budget", "/finance/gender-budget"),
)

#: Live-verified on November 4, 2026. Each entry parsed real rows on that date.
MUNICIPAL_BUDGET_PORTALS: tuple[MunicipalBudgetPortal, ...] = (
    MunicipalBudgetPortal(
        code="amc-budget",
        city="ahmedabad",
        body=_AMC,
        host="ahmedabadcity.gov.in",
        template="anchor_index",
        document_type="budget_book",
        pages=("https://ahmedabadcity.gov.in/SP/Budget",),
        keywords=("budget",),
    ),
    MunicipalBudgetPortal(
        code="amc-balance-sheet",
        city="ahmedabad",
        body=_AMC,
        host="ahmedabadcity.gov.in",
        template="anchor_index",
        document_type="balance_sheet",
        pages=("https://ahmedabadcity.gov.in/SP/BalanceSheet",),
        keywords=("balance sheet", "audit report", "account"),
    ),
    *(
        MunicipalBudgetPortal(
            code=code,
            city="delhi",
            body=_GNCTD,
            host=_GNCTD_HOST,
            template="drupal_files",
            document_type=document_type,
            pages=(f"https://{_GNCTD_HOST}{path}",),
        )
        for code, document_type, path in _GNCTD_PAGES
    ),
    MunicipalBudgetPortal(
        code="gnctd-detailed-demands",
        city="delhi",
        body=_GNCTD,
        host=_GNCTD_HOST,
        template="drupal_detail_pages",
        document_type="detailed_demands_for_grants",
        pages=(f"https://{_GNCTD_HOST}/finance/detailed-demands-grants-0",),
    ),
    MunicipalBudgetPortal(
        code="mcd-budget-documents",
        city="delhi",
        body="Municipal Corporation of Delhi",
        host="mcdonline.nic.in",
        template="mcd_menu_json",
        document_type="mcd_budget",
        menu_guides=(
            "914bf40f-81d3-4ae4-91c0-202a64bfa925",
            "561ea9dd-8ddb-46cf-b19f-a3eabd02903f",
        ),
    ),
)


def get_portal(code: str) -> MunicipalBudgetPortal:
    """The portal with this code. Raises ``ValueError`` naming the known codes."""
    for portal in MUNICIPAL_BUDGET_PORTALS:
        if portal.code == code:
            return portal
    known = ", ".join(p.code for p in MUNICIPAL_BUDGET_PORTALS)
    raise ValueError(f"unknown municipal budget portal {code!r}; known portals: {known}")


def portals_for_city(city: str) -> list[MunicipalBudgetPortal]:
    """Every portal for `city`, matched case-insensitively."""
    wanted = city.strip().lower()
    return [p for p in MUNICIPAL_BUDGET_PORTALS if p.city == wanted]


def respect_robots_for(url: str) -> bool:
    """False only for ``finance.delhi.gov.in``; True for every other host.

    Decision B (owner decision, November 12, 2026). That host's robots.txt
    answers 403 to every User-Agent while its pages answer 200, so a strict
    robots check would refuse every page. Re-verified November 12, 2026. The
    relaxation covers that one host and nothing else.
    """
    return (urlparse(url).hostname or "").lower() != _GNCTD_HOST


def mcd_menu_urls(guide: str) -> list[str]:
    """The three MCD API URLs for one menu guide: submenu, content, PDF content."""
    return [
        f"{MCD_BASE}/portal/{endpoint}?menuguide={guide}"
        for endpoint in ("showSubMenu", "showContent", "showPDFContent")
    ]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def document_slug(title: str, url: str) -> str:
    """A stable, filename-safe name for one document.

    The tail is the URL path's basename without ``.pdf`` when the path ends in
    ``.pdf``, else the digits of the query string joined by hyphens (the AMC
    ``ViewFile?TYPE=FileRepository,<id>`` links). The title and the tail are
    joined, lower-cased, collapsed to single hyphens, and cut to 80 characters.
    The tail tells apart two anchors that share a label.
    """
    parsed = urlparse(url)
    if parsed.path.lower().endswith(".pdf"):
        tail = parsed.path.rsplit("/", 1)[-1][: -len(".pdf")]
    else:
        tail = "-".join(re.findall(r"\d+", parsed.query))
    return _slug(f"{title} {tail}")[:80].strip("-")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class MunicipalBudgetProbe:
    """Discover, and later download, one portal's budget documents."""

    def __init__(
        self,
        out_dir: Path,
        *,
        portal: MunicipalBudgetPortal,
        sleep: float = 2.0,
        max_menu_guides: int = MAX_MENU_GUIDES,
        user_agent: str = BUDGET_USER_AGENT,
        session=None,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.portal = portal
        self.sleep = sleep
        self.max_menu_guides = max_menu_guides
        self.manifest = self.out_dir / "manifest.jsonl"
        self.session = session or make_session(rate_limit_sec=sleep, user_agent=user_agent)

    def _fetch_text(self, url: str) -> str:
        resp = self.session.get(url, timeout=60, respect_robots=respect_robots_for(url))
        resp.raise_for_status()
        return resp.text

    def discover(self) -> list[dict]:
        """One `pending` record per document, plus one `error` record per page that fails."""
        template = self.portal.template
        if template in ("anchor_index", "drupal_files"):
            docs, errors = self._discover_pages()
        elif template == "drupal_detail_pages":
            docs, errors = self._discover_detail_pages()
        elif template == "mcd_menu_json":
            docs, errors = self._discover_mcd()
        else:
            raise ValueError(f"unknown template {template!r} for portal {self.portal.code}")
        records: list[dict] = []
        urls_by_key: dict[str, str] = {}
        for doc in docs:
            record = self._record(doc, status="pending")
            owner = urls_by_key.get(record["key"])
            if owner == record["url"]:
                continue
            if owner is not None:
                # Two URLs, one key: the 80-character cut of the slug can do
                # this. A hash of the URL keeps both documents.
                record = self._with_slug_suffix(record, hashlib.sha1(record["url"].encode()).hexdigest()[:8])
            urls_by_key[record["key"]] = record["url"]
            records.append(record)
        return records + errors

    def _with_slug_suffix(self, record: dict, suffix: str) -> dict:
        filename = record["filename"].removesuffix(".pdf") + f"-{suffix}.pdf"
        return {
            **record,
            "key": f"{record['key']}-{suffix}",
            "filename": filename,
            "dest": str(self.out_dir / self.portal.code / filename),
        }

    def _error_record(self, page_url: str, exc: Exception) -> dict:
        title = f"Page not fetched: {page_url}"
        record = self._record(
            {"title": title, "url": page_url, "fiscal_year": None, "source_page": page_url},
            status="error",
        )
        record["error"] = str(exc) or type(exc).__name__
        return record

    def _discover_pages(self) -> tuple[list[dict], list[dict]]:
        docs: list[dict] = []
        errors: list[dict] = []
        for page in self.portal.pages:
            try:
                html = self._fetch_text(page)
            except Exception as exc:  # noqa: BLE001 - one bad page must not end the run
                errors.append(self._error_record(page, exc))
                continue
            if self.portal.template == "anchor_index":
                docs.extend(parse_anchor_index(html, page, keywords=self.portal.keywords))
            else:
                docs.extend(
                    parse_drupal_file_links(html, page, document_type=self.portal.document_type)
                )
        return docs, errors

    def _discover_detail_pages(self) -> tuple[list[dict], list[dict]]:
        docs: list[dict] = []
        errors: list[dict] = []
        for page in self.portal.pages:
            try:
                index_html = self._fetch_text(page)
            except Exception as exc:  # noqa: BLE001
                errors.append(self._error_record(page, exc))
                continue
            for detail in parse_detail_page_links(index_html, page):
                detail_url = str(detail["url"])
                try:
                    html = self._fetch_text(detail_url)
                except Exception as exc:  # noqa: BLE001
                    errors.append(self._error_record(detail_url, exc))
                    continue
                docs.extend(
                    parse_drupal_file_links(
                        html, detail_url, document_type=self.portal.document_type
                    )
                )
        return docs, errors

    def _discover_mcd(self) -> tuple[list[dict], list[dict]]:
        """Breadth-first walk of the MCD menu tree, at most `max_menu_guides` guides."""
        docs: list[dict] = []
        errors: list[dict] = []
        queue = deque(self.portal.menu_guides)
        queued = set(queue)
        fetched = 0
        while queue and fetched < self.max_menu_guides:
            guide = queue.popleft()
            fetched += 1
            for url in mcd_menu_urls(guide):
                try:
                    payload = self._fetch_text(url)
                except Exception as exc:  # noqa: BLE001
                    errors.append(self._error_record(url, exc))
                    continue
                docs.extend(parse_mcd_documents(payload, url))
                if "/showSubMenu" in url:
                    for child in parse_mcd_menu_guides(payload):
                        if child["menu_guide"] not in queued:
                            queued.add(child["menu_guide"])
                            queue.append(child["menu_guide"])
        return docs, errors

    def _record(self, doc: dict, *, status: str) -> dict:
        portal = self.portal
        fiscal_year = doc.get("fiscal_year")
        slug = document_slug(doc["title"], doc["url"]) or "document"
        filename = f"{portal.code}_{fiscal_year or 'unknown'}_{slug}.pdf"
        now = _now_iso()
        return {
            "key": f"MUNICIPAL_BUDGET|{portal.code}|{fiscal_year or 'unknown'}|{slug}",
            "kind": "municipal_budget_document",
            "record_type": "municipal_budget_document",
            "source_family": "municipal-budget",
            "portal_code": portal.code,
            "city": portal.city,
            "body": portal.body,
            "publisher": portal.host,
            "document_type": portal.document_type,
            "title": doc["title"],
            "fiscal_year": fiscal_year,
            "url": doc["url"],
            "source_page": doc.get("source_page"),
            "filename": filename,
            "dest": str(self.out_dir / portal.code / filename),
            "status": status,
            "media_type": "application/pdf",
            "fetched_at": now,
            "probed_at": now,
        }

    @staticmethod
    def _finalize(record: dict, dest: Path, body: bytes) -> None:
        record["bytes"] = len(body)
        note_text_layer(record, dest, body, min_chars=TEXT_LAYER_MIN_CHARS)

    def download(self, record: dict, *, dry_run: bool) -> dict:
        """Fetch one document to `record["dest"]` and return the updated record.

        A dry run makes no request. A record that is already an `error` passes
        through. A non-empty file already at `dest` is kept and recorded as
        `skipped_exists`. A response whose `Content-Type` is HTML is not
        written: an index page or a login page isn't the PDF. A failed fetch
        becomes an `error` record; it never raises.
        """
        if record["status"] == "error":
            return dict(record)
        if dry_run:
            return {**record, "status": "dry_run"}
        record = dict(record)
        dest = Path(record["dest"])
        if dest.exists() and dest.stat().st_size > 0:
            record["status"] = "skipped_exists"
            self._finalize(record, dest, dest.read_bytes())
            return record
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = _private_tmp_path(dest)
        try:
            url = record["url"]
            resp = self.session.get(
                url, timeout=180, stream=True, respect_robots=respect_robots_for(url)
            )
            record["http_status"] = getattr(resp, "status_code", 200)
            resp.raise_for_status()
            headers = getattr(resp, "headers", None) or {}
            content_type = (headers.get("Content-Type") or "").split(";", 1)[0].strip()
            if content_type.lower().startswith("text/html"):
                record["status"] = "error"
                record["error"] = "body isn't a PDF"
                return record
            body = read_capped_response(resp)
            tmp_path.write_bytes(body)
            os.replace(tmp_path, dest)
        except Exception as exc:  # noqa: BLE001 - one bad document must not end the run
            record["status"] = "error"
            record["error"] = str(exc)
            return record
        finally:
            tmp_path.unlink(missing_ok=True)
        if content_type:
            record["media_type"] = content_type
        record["status"] = "downloaded"
        self._finalize(record, dest, body)
        return record

    def append_manifest(self, record: dict) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def probe(self, *, dry_run: bool = False) -> list[dict]:
        """Discover and download every document of the portal.

        Records are appended to `manifest.jsonl` unless `dry_run`.
        """
        records = [self.download(r, dry_run=dry_run) for r in self.discover()]
        if not dry_run:
            for record in records:
                self.append_manifest(record)
        return records
