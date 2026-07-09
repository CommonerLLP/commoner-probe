# SPDX-License-Identifier: MIT
"""Ministry-hosted Detailed Demands for Grants (DDG) acquisition adapter.

DDGs are laid annually by each ministry/department and, unlike
indiabudget.gov.in's "Demand for Grants" (SBE — a major-head summary
only), carry the object-head-wise breakdown, including the "Professional
Services" head (REQ-0019: object head 28, confirmed against the live
Ministry of Finance DDG 2026-07-08). There is no central index — each
ministry hosts its own listing page, on its own domain, in its own site
template.

Three listing-page templates are confirmed live and supported here, all
classic server-rendered markup (so a plain HTTP GET is enough — no browser
needed):

* ``"card"`` — a Bootstrap-grid "documentRecordTitle" card per document.
  Verified against the Department of Economic Affairs (dea.gov.in) — the
  department hosting the Ministry of Finance's own DDG series (10 editions,
  2017-18 through 2026-27, one flattened-scan edition at 2022-23).
* ``"table"`` — one ``<tr>`` per document, title in one ``<td>`` and a
  ``.pdf`` href in another (Drupal Views or WordPress "document category"
  tables both fit). Verified against MHA (32 docs back to 2012-13, two
  volumes/year), Department of Expenditure (7 docs back to 2006-07),
  Department of Land Resources (12 docs), MoEFCC (16 docs back to 2008-09,
  Hindi-only titles — see ``_DEMAND_GRANT_RE``), and MoPNG (18 docs back to
  2008-09).
* ``"list"`` — a flat run of ``<a href="...pdf">title</a>`` anchors with no
  wrapping card/table structure; the anchor's own text is the title. Verified
  against DST (``<li class="views-row">`` rows, 10 docs back to 2017-18) —
  DST's older editions drop the "Demand"/"Grant" wording entirely (anchor
  text is just "2017-18 (3.37 MB)"), so this template also accepts a PDF
  filename containing "ddg" as a fallback signal (see ``_DDG_FILENAME_RE``).

For the full survey of every ministry checked this session — including the
~13 blocked by JS-rendered SPA platforms (a shared "digifootprint.gov.in"-
family Next.js/Angular build turned up across many ministries), WAF/bot
blocks, and network-unreachable sites — see
``docs/gov-site-platforms.md``. Three more are verified-working but
deliberately excluded from the registry pending a human decision (broken
TLS on the ministry's own server, or a full-site robots.txt disallow) — see
the comment block immediately after ``MINISTRY_DDG_PORTALS`` below.

Grow this registry the way ``neva_portals.py`` grew: one live-verified
entry at a time, never a guessed batch.

Acquisition only — parsing the ``NN.01.28 Professional Services`` object-head
lines out of the downloaded PDFs is a public-finance concern (REQ-0019).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

from .http_client import TOOL_VERSION, make_session
from .textparse import extract_pdf_text

# dea.gov.in returned a plain HTTP listing page (no WAF interstitial
# observed) but is a live government CMS — keep a polite gap between
# requests, same posture as doe.py's DEFAULT_SLEEP.
DEFAULT_SLEEP = 2.0

# Below this many extracted characters the manifest records text_layer:
# false — dea.gov.in's own listing includes at least one flattened-scan
# edition ("DDG_2022_2023_Scanned_Copy.pdf") alongside born-digital ones.
TEXT_LAYER_MIN_CHARS = 200

# mha.gov.in's Akamai WAF returns 403 (not 404) for commoner-probe's default
# User-Agent — because it contains a URL scheme, the same false-positive
# already documented for indiabudget.gov.in in budget/probe.py's
# BUDGET_USER_AGENT. A 403 on the robots.txt fetch itself would otherwise be
# read as a real "disallow all" (see http_client._get_robot_parser) even
# though mha.gov.in has no robots.txt. Verified live 2026-07-09: this
# scheme-free form clears the WAF for both the robots.txt fetch and the
# actual listing page.
SCHEME_FREE_USER_AGENT = (
    f"commoner-probe/{TOOL_VERSION} "
    "(github.com/CommonerLLP/commoner-probe; public-interest research; rate-limited)"
)


@dataclass(frozen=True)
class MinistryDDGPortal:
    """One ministry/department's DDG listing page.

    ``template`` selects the parser: ``"card"`` (dea.gov.in-style Bootstrap
    card grid) or ``"table"`` (classic Drupal Views table, mha/doe.gov.in-style).
    ``user_agent`` overrides the default commoner-probe UA for portals whose
    WAF blocks it (see ``SCHEME_FREE_USER_AGENT``).
    """

    ministry_code: str
    ministry_name: str
    listing_url: str
    template: str = "card"
    user_agent: str | None = None


# Seed registry. Each entry verified live (HTTP 200, real document rows
# parsed, not just a 200 status) on the date noted.
MINISTRY_DDG_PORTALS: tuple[MinistryDDGPortal, ...] = (
    MinistryDDGPortal(
        ministry_code="dea",
        ministry_name="Department of Economic Affairs (Ministry of Finance)",
        listing_url="http://dea.gov.in/reports-detail-demands-grants",
        template="card",
    ),
    MinistryDDGPortal(
        ministry_code="mha",
        ministry_name="Ministry of Home Affairs",
        listing_url="https://www.mha.gov.in/en/divisionofmha/finance-division",
        template="table",
        user_agent=SCHEME_FREE_USER_AGENT,
    ),
    MinistryDDGPortal(
        ministry_code="doe",
        ministry_name="Department of Expenditure (Ministry of Finance)",
        listing_url="https://doe.gov.in/detailed-demands-for-grants",
        template="table",
    ),
    MinistryDDGPortal(
        ministry_code="dolr",
        ministry_name="Department of Land Resources (Ministry of Rural Development)",
        listing_url="https://dolr.gov.in/document-category/detailed-demand-for-grants/",
        template="table",
    ),
    MinistryDDGPortal(
        ministry_code="moefcc",
        ministry_name="Ministry of Environment, Forest and Climate Change",
        listing_url="https://moef.gov.in/detailed-demand-for-grants",
        template="table",
    ),
    MinistryDDGPortal(
        ministry_code="mopng",
        ministry_name="Ministry of Petroleum and Natural Gas",
        listing_url="https://mopng.gov.in/en/accounts/demands-grants",
        template="table",
    ),
    MinistryDDGPortal(
        ministry_code="dst",
        ministry_name="Department of Science and Technology",
        listing_url="https://dst.gov.in/documents/budget",
        template="list",
    ),
)

# Verified live 2026-07-09 but deliberately NOT in the registry above —
# each needs an explicit human decision, not an agent's unilateral call:
#
# * Ministry of Steel (steel.gov.in/detailed-demands-for-grants, 4 docs,
#   "table" template) and Ministry of Tribal Affairs (tribal.nic.in/Finance.aspx,
#   8 docs, "list" template) both serve broken TLS — steel.gov.in a
#   self-signed cert, tribal.nic.in an incomplete chain. `curl` tolerates
#   both (different trust store); Python's `requests`/certifi correctly
#   rejects them. Disabling certificate verification is a security-relevant
#   change, not a default an adapter should make silently.
# * Ministry of Women and Child Development (wcd.gov.in/documents/budget +
#   /documents/budget-archives, 13 docs, "card"-shaped but not the same
#   markup as dea.gov.in's card template) is technically scrapeable but its
#   robots.txt is "Disallow: /" — a full-site block. `http_client.py` has an
#   explicit `respect_robots=False` opt-out for exactly this situation, but
#   it must be a deliberate registry decision, not silently wired in here.

_PORTALS_BY_CODE = {p.ministry_code: p for p in MINISTRY_DDG_PORTALS}


def get_portal(ministry_code: str) -> MinistryDDGPortal:
    try:
        return _PORTALS_BY_CODE[ministry_code]
    except KeyError as exc:
        raise KeyError(
            f"unknown ministry_code {ministry_code!r}; known: {sorted(_PORTALS_BY_CODE)} "
            "— pass --listing-url/--ministry-name directly for a ministry not yet in the registry"
        ) from exc


_CARD_TITLE_RE = re.compile(r'<div class="documentRecordTitle">\s*(.*?)\s*</div>', re.IGNORECASE | re.DOTALL)
_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL)
# DDGs are published bilingually; a table's title cells may be Hindi-only
# (e.g. moef.gov.in's "विस्तृत मांगें" — "detailed demands", verified
# 2026-07-09). मांग / मांगें / माँगें (demand) and अनुदान (grant) cover the
# terms actually observed; English stays first since it's the common case.
_DEMAND_GRANT_RE = re.compile(r"demand|grant|मां?ग|अनुदान", re.IGNORECASE)
_HREF_RE = re.compile(r'href="([^"]+\.pdf)"', re.IGNORECASE)
_ANCHOR_RE = re.compile(r'<a\b[^>]*href="([^"]+\.pdf)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_YEAR_RE = re.compile(r"(20\d{2})\s*[-_– ]?\s*(?:20)?(\d{2})")
_TAG_RE = re.compile(r"<[^>]+>")
# Some sites drop the "Demand"/"Grant" wording for older editions and give
# the anchor text just "<year> (<size>)" (dst.gov.in, verified 2026-07-09).
# The filename itself still says DDG in every case observed, so it's a safe
# second signal for the anchor-list template specifically (checked against
# tribal.nic.in's other 42 non-DDG PDFs on the same page — zero false
# positives).
_DDG_FILENAME_RE = re.compile(r"ddg", re.IGNORECASE)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _clean(text: str) -> str:
    return unescape(_TAG_RE.sub(" ", text)).strip()


def _document_from_block(block: str, title: str, listing_url: str) -> dict[str, Any] | None:
    href_m = _HREF_RE.search(block)
    if not href_m:
        return None
    url = urljoin(listing_url, unescape(href_m.group(1)))
    year_m = _YEAR_RE.search(title) or _YEAR_RE.search(unquote(url))
    if not year_m:
        return None
    year = f"{year_m.group(1)}-{year_m.group(2)}"
    return {"title": title, "year": year, "url": url}


def parse_ddg_listing_card(html: str, listing_url: str) -> list[dict[str, Any]]:
    """Parse the dea.gov.in-style Bootstrap card listing template.

    Pure function, unit-testable with canned HTML. Matches the S3WaaS/Drupal
    "customTable" listing template: a ``documentRecordTitle`` div (the
    document title, carrying the fiscal year) followed — before the next
    such div — by a ``.pdf`` href for the row's "View" button. Verified
    against dea.gov.in 2026-07-08.
    """
    documents: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    blocks = re.split(r'(?=<div class="documentRecordTitle">)', html, flags=re.IGNORECASE)
    for block in blocks:
        title_m = _CARD_TITLE_RE.search(block)
        if not title_m:
            continue
        doc = _document_from_block(block, _clean(title_m.group(1)), listing_url)
        if doc is None or doc["url"] in seen_urls:
            continue
        seen_urls.add(doc["url"])
        documents.append(doc)
    return documents


def parse_ddg_listing_table(html: str, listing_url: str) -> list[dict[str, Any]]:
    """Parse a classic Drupal Views table listing template.

    Pure function, unit-testable with canned HTML. One ``<tr>`` per
    document; the title is whichever ``<td>`` cell's text mentions
    "demand"/"grant" (field machine names vary by site — mha.gov.in uses
    ``views-field-field-title``, doe.gov.in a differently-named field —
    matching on cell content instead of a CSS class name generalises across
    both). Verified against mha.gov.in and doe.gov.in 2026-07-09.
    """
    documents: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for row in _TR_RE.findall(html):
        title = ""
        for cell in _TD_RE.findall(row):
            cell_text = _clean(cell)
            if _DEMAND_GRANT_RE.search(cell_text):
                title = cell_text
                break
        if not title:
            continue
        doc = _document_from_block(row, title, listing_url)
        if doc is None or doc["url"] in seen_urls:
            continue
        seen_urls.add(doc["url"])
        documents.append(doc)
    return documents


def parse_ddg_listing_list(html: str, listing_url: str) -> list[dict[str, Any]]:
    """Parse a flat anchor-list template — no wrapping card/table structure.

    Pure function, unit-testable with canned HTML. Every ``<a href="...pdf">``
    is a candidate; its own inner text is the title (there is no separate
    title cell). Accepted when the anchor text mentions "demand"/"grant" OR
    the PDF filename itself contains "ddg" (see ``_DDG_FILENAME_RE``).
    Verified against tribal.nic.in (``<span class="far fa-file-pdf"></span>
    <a>...</a><br>`` rows) and dst.gov.in (``<li class="views-row">``) 2026-07-09
    — different wrapper markup, same anchor-is-the-title shape.
    """
    documents: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for m in _ANCHOR_RE.finditer(html):
        href, inner = m.group(1), _clean(m.group(2))
        url = urljoin(listing_url, unescape(href))
        if url in seen_urls:
            continue
        basename = url.rsplit("/", 1)[-1]
        if not (_DEMAND_GRANT_RE.search(inner) or _DDG_FILENAME_RE.search(unquote(basename))):
            continue
        year_m = _YEAR_RE.search(inner) or _YEAR_RE.search(unquote(url))
        if not year_m:
            continue
        year = f"{year_m.group(1)}-{year_m.group(2)}"
        seen_urls.add(url)
        documents.append({"title": inner, "year": year, "url": url})
    return documents


_PARSERS = {
    "card": parse_ddg_listing_card,
    "list": parse_ddg_listing_list,
    "table": parse_ddg_listing_table,
}


class MinistryDDGProbe:
    """Acquire one ministry's Detailed Demands for Grants series with provenance."""

    def __init__(
        self,
        out_dir: Path,
        *,
        portal: MinistryDDGPortal,
        sleep: float = DEFAULT_SLEEP,
    ) -> None:
        self.out_dir = out_dir
        self.portal = portal
        self.sleep = sleep
        self.manifest = out_dir / "manifest.jsonl"
        self.session = make_session(rate_limit_sec=sleep, user_agent=portal.user_agent)

    def discover(self) -> list[dict[str, Any]]:
        try:
            parser = _PARSERS[self.portal.template]
        except KeyError as exc:
            raise ValueError(
                f"unknown template {self.portal.template!r} for portal {self.portal.ministry_code!r}; "
                f"known: {sorted(_PARSERS)}"
            ) from exc
        r = self.session.get(self.portal.listing_url, timeout=60)
        r.raise_for_status()
        return parser(r.text, self.portal.listing_url)

    def _record(self, doc: dict[str, Any], *, status: str) -> dict[str, Any]:
        now = _now_iso()
        basename = Path(unquote(urlparse(doc["url"]).path)).name
        # Some sources (e.g. mha.gov.in) publish more than one document per
        # fiscal year (Vol-I, Vol-II A/B) — fold a slug of the source
        # filename into the key so those don't collide.
        doc_slug = re.sub(r"[^a-z0-9]+", "-", Path(basename).stem.lower()).strip("-")
        filename = f"{self.portal.ministry_code}_{doc['year']}_" + re.sub(r"[^A-Za-z0-9._-]", "_", basename)
        dest = self.out_dir / self.portal.ministry_code / filename
        return {
            "key": f"MINISTRY_DDG|{self.portal.ministry_code}|{doc['year']}|{doc_slug}",
            "kind": "ministry_ddg_document",
            "record_type": "ministry_ddg_document",
            "source_family": "ministry-ddg",
            "ministry_code": self.portal.ministry_code,
            "ministry_name": self.portal.ministry_name,
            "publisher": self.portal.ministry_name,
            "title": doc["title"],
            "year": doc["year"],
            "filename": filename,
            "dest": str(dest),
            "url": doc["url"],
            "listing_url": self.portal.listing_url,
            "status": status,
            "media_type": "application/pdf",
            "fetched_at": now,
            "probed_at": now,
        }

    def _finalize(self, record: dict[str, Any], dest: Path, body: bytes) -> None:
        record["sha256"] = hashlib.sha256(body).hexdigest()
        text = extract_pdf_text(dest)
        record["text_layer"] = len(text.strip()) >= TEXT_LAYER_MIN_CHARS

    def download_document(self, doc: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
        record = self._record(doc, status="dry_run" if dry_run else "pending")
        if dry_run:
            return record
        dest = Path(record["dest"])
        if dest.exists() and dest.stat().st_size > 1000:
            record["status"] = "skipped_exists"
            self._finalize(record, dest, dest.read_bytes())
            return record
        r = self.session.get(doc["url"], timeout=180)
        r.raise_for_status()
        body = r.content
        if not body.startswith(b"%PDF"):
            record["status"] = "error"
            record["error"] = "response is not a PDF (WAF interstitial?)"
            return record
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        record["status"] = "downloaded"
        content_type = r.headers.get("Content-Type") if hasattr(r, "headers") else None
        if content_type:
            record["media_type"] = content_type.split(";", 1)[0].strip()
        self._finalize(record, dest, body)
        if self.sleep:
            time.sleep(self.sleep)
        return record

    def append_manifest(self, record: dict[str, Any]) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def probe(self, years: list[str] | None = None, *, dry_run: bool = False) -> list[dict[str, Any]]:
        documents = self.discover()
        if years:
            wanted = set(years)
            documents = [d for d in documents if d["year"] in wanted]
        records = [self.download_document(d, dry_run=dry_run) for d in documents]
        if not dry_run:
            for record in records:
                self.append_manifest(record)
        return records
