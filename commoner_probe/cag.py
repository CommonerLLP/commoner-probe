# SPDX-License-Identifier: MIT
"""CAG State Finance Accounts (Volume II) acquisition adapter.

The Comptroller and Auditor General publishes each State's statutory
*Finance Accounts* on cag.gov.in's State-Accounts portal. Volume II carries the
detailed statements this org needs — Statement 15 (Detailed Statement of Revenue
Expenditure by Minor Heads) and Statement 16 (Detailed Statement of Capital
Expenditure by Minor Heads and Sub-Heads) — from which minor-head figures like
``2205-00-105`` (Public Libraries, revenue) and ``4202-04-105`` (Public
Libraries, capital) are read downstream by public-finance's
``account_code_extract`` (REQ-0003).

Unlike the audit-report side of cag.gov.in, State Accounts have no central index
and no per-document detail page. One server-rendered page per State
(``state-accounts-report?defuat_state_id=<id>``) carries a tabbed accordion; the
"Finance Accounts" tab (``id="tab-359"``) lists the Vol-I / Vol-II PDFs directly,
grouped under ``<div class="accTrigger"> YYYY - YY</div>`` fiscal-year headers,
each header owning the ``<li>`` rows that follow it until the next header.

Portal facts, all live-verified 2026-07-23:

* Plain HTTP-over-TLS, no JS/WAF gate; the server honours ``defuat_state_id``.
* **The fiscal year is NOT reliable in the filename** — Telangana ships
  ``…Volume-ll…`` (lowercase L-L typo) with no year; Kerala's 2024-25 misspells
  "Finace"; Odisha's is a bare ``VOLUME-II``. The year MUST come from the
  ``accTrigger`` header, never the URL.
* Each document renders three anchors to the same URL (title ``<h5>`` link,
  icon, "Download") — dedupe by URL.
* At survey time 24 States had a live FY2023-24 Vol-II. Seven had none and are
  documented in ``_UNAVAILABLE`` below, not in the registry — same posture as
  ddg.py's excluded portals: an agent does not silently guess a missing source.

Acquisition only — parsing Statement 15/16 minor-head lines out of the
downloaded Vol-II PDFs is a public-finance concern. When the finance/
document-disclosure package (``docs/TODO-finance-document-disclosure-adapters.md``)
is built, this module can fold into it; until then it stands alone on the
self-contained ddg.py precedent, with its own per-adapter manifest schema.
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

from .http_client import make_session
from .textparse import extract_pdf_text

# cag.gov.in is a live government CMS served from behind a CDN; keep a polite
# gap between requests, same posture as ddg.py's DEFAULT_SLEEP.
DEFAULT_SLEEP = 2.0

# Below this many extracted characters the manifest records text_layer: false.
# CAG State-Accounts uploads are digital-native, but confirm per file at
# download time rather than assuming.
TEXT_LAYER_MIN_CHARS = 200

BASE_URL = "https://cag.gov.in"
ACCOUNTS_URL = "https://cag.gov.in/en/state-accounts-report"


@dataclass(frozen=True)
class CAGState:
    """One State/UT whose Finance Accounts live on the CAG State-Accounts portal.

    ``state_id`` is the ``defuat_state_id`` query value; ``name`` is the label
    from the cag.gov.in dropdown (both live-verified 2026-07-23).
    """

    state_id: int
    name: str


# Seed registry: every State/UT with a live, HEAD-verified Finance Accounts
# Vol-II on the portal (survey 2026-07-23). Grown the way ddg.py's registry is —
# only entries confirmed to return a real Vol-II document, never a guessed batch.
CAG_ACCOUNTS_STATES: tuple[CAGState, ...] = (
    CAGState(64, "Andhra Pradesh"),
    CAGState(65, "Arunachal Pradesh"),
    CAGState(66, "Assam"),
    CAGState(67, "Bihar"),
    CAGState(68, "Chhattisgarh"),
    CAGState(71, "Gujarat"),
    CAGState(72, "Haryana"),
    CAGState(73, "Himachal Pradesh"),
    CAGState(75, "Jharkhand"),
    CAGState(76, "Karnataka"),
    CAGState(77, "Kerala"),
    CAGState(78, "Madhya Pradesh"),
    CAGState(79, "Maharashtra"),
    CAGState(80, "Manipur"),
    CAGState(81, "Meghalaya"),
    CAGState(82, "Mizoram"),
    CAGState(83, "Nagaland"),
    CAGState(84, "Odisha"),
    CAGState(85, "Punjab"),
    CAGState(87, "Sikkim"),
    CAGState(88, "Tamil Nadu"),
    CAGState(89, "Tripura"),
    CAGState(90, "Uttar Pradesh"),
    CAGState(91, "Uttarakhand"),
    CAGState(93, "Telangana"),
)

# States/UTs deliberately NOT in the registry — verified 2026-07-23 to have no
# obtainable Finance Accounts Vol-II on this portal. Each needs a human decision
# or an alternative source, not a silent crawl that yields nothing:
#
# * Rajasthan (86) — Finance Accounts tab holds only Vol-I + combined-English
#   volumes; no file classifiable as Vol-II for recent years. Its Statement
#   15/16 is inside the combined volumes (older years) or off-portal. Manual.
# * West Bengal (92) — no Finance Accounts on the portal at all (only "Monthly
#   Key Indicators"). Must be sourced from the WB AG site / the SFAR track.
# * Delhi (69), Goa (70), Puducherry (366), undivided J&K (74, ≤2019), and the
#   "UT Composite" pseudo-entry (380) — accounts pages return an empty shell
#   (all tabs 0). Delhi / Puducherry / UT accounts are compiled via the CGA / UT
#   route, not this State-Accounts portal; Goa is simply absent here.
_UNAVAILABLE: dict[int, str] = {
    86: "Vol-II not published (Vol-I + combined only)",
    92: "no Finance Accounts on portal (Monthly Key Indicators only)",
    69: "accounts page empty (compiled via CGA route)",
    70: "accounts page empty",
    366: "accounts page empty (compiled via UT route)",
    74: "undivided J&K (≤2019); no accounts on portal",
    380: "UT Composite pseudo-entry; accounts page empty",
}

_STATES_BY_ID = {s.state_id: s for s in CAG_ACCOUNTS_STATES}
_STATES_BY_NAME = {s.name.lower(): s for s in CAG_ACCOUNTS_STATES}


def get_state(ref: str | int) -> CAGState:
    """Resolve a State by numeric ``defuat_state_id`` or (case-insensitive) name."""
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
        sid = int(ref)
        if sid in _STATES_BY_ID:
            return _STATES_BY_ID[sid]
        reason = _UNAVAILABLE.get(sid)
        hint = f" — no Vol-II on the portal ({reason})" if reason else ""
        raise KeyError(f"unknown/unavailable state id {sid}{hint}")
    key = str(ref).strip().lower()
    if key in _STATES_BY_NAME:
        return _STATES_BY_NAME[key]
    raise KeyError(
        f"unknown state {ref!r}; known: {sorted(s.name for s in CAG_ACCOUNTS_STATES)}"
    )


# --- listing parser (pure, unit-testable with canned HTML) -------------------

# The "Finance Accounts" tab is delimited by its own id and the next tab's id.
_TAB_SLICE_RE = re.compile(r'id="tab-359"(.*?)id="tab-360"', re.DOTALL | re.IGNORECASE)
# Fiscal-year accordion header: "<div class="accTrigger"> 2023 - 24</div>".
_YEAR_HEADER_RE = re.compile(
    r'<div\s+class="accTrigger">\s*(\d{4})\s*-\s*(\d{2})\s*</div>', re.IGNORECASE
)
_LI_RE = re.compile(r"<li\b[^>]*>(.*?)</li>", re.DOTALL | re.IGNORECASE)
_H5_RE = re.compile(r"<h5>\s*(.*?)\s*</h5>", re.DOTALL | re.IGNORECASE)
_PDF_HREF_RE = re.compile(r'href="([^"]+\.pdf)"', re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
# Volume classifiers on (title + filename). Vol-II must be tested BEFORE Vol-I.
# Catches: Volume-II, Vol-II, VOL-II, Volume-2, Vol-2, VOL-02, and the real
# Telangana typo "Volume-ll" (lowercase L-L).
_VOL2_RE = re.compile(r"vol(?:ume)?[-\s]*(?:ii|2|02|ll)\b", re.IGNORECASE)
_VOL1_RE = re.compile(r"vol(?:ume)?[-\s]*(?:i|1|01|l)\b", re.IGNORECASE)


def _clean(text: str) -> str:
    return unescape(_TAG_RE.sub(" ", text)).strip()


def _classify_volume(signal: str) -> str | None:
    """Return "II", "I", or None from a title+filename signal string."""
    if _VOL2_RE.search(signal):
        return "II"
    if _VOL1_RE.search(signal):
        return "I"
    return None


def parse_finance_accounts_tab(html: str, state: CAGState) -> list[dict[str, Any]]:
    """Parse the "Finance Accounts" tab (``#tab-359``) of a State-Accounts page.

    Pure function, unit-testable with canned HTML. Returns one dict per document
    ``{state_id, state, year, volume, title, url}``. The fiscal year is read
    from the ``accTrigger`` header that owns each ``<li>`` (never the filename —
    several States ship year-less or misspelled filenames). Each document's
    three anchors (title/icon/Download) collapse to one URL via de-duplication.
    """
    slice_m = _TAB_SLICE_RE.search(html)
    if not slice_m:
        return []
    tab = slice_m.group(1)

    # Partition the tab into (year, section_html) by accTrigger headers: each
    # header owns everything up to the next header.
    headers = list(_YEAR_HEADER_RE.finditer(tab))
    documents: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for i, h in enumerate(headers):
        year = f"{h.group(1)}-{h.group(2)}"
        start = h.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(tab)
        section = tab[start:end]
        for li_m in _LI_RE.finditer(section):
            block = li_m.group(1)
            href_m = _PDF_HREF_RE.search(block)
            if not href_m:
                continue
            url = urljoin(BASE_URL + "/", unescape(href_m.group(1)))
            if url in seen_urls:
                continue
            title_m = _H5_RE.search(block)
            title = _clean(title_m.group(1)) if title_m else ""
            basename = Path(unquote(urlparse(url).path)).name
            volume = _classify_volume(f"{title} {basename}")
            if volume is None:
                continue
            seen_urls.add(url)
            documents.append({
                "state_id": state.state_id,
                "state": state.name,
                "year": year,
                "volume": volume,
                "title": title or basename,
                "url": url,
            })
    return documents


# --- probe -------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class CAGAccountsProbe:
    """Acquire one State's CAG Finance Accounts (Vol-II by default) with provenance."""

    def __init__(self, out_dir: Path, *, sleep: float = DEFAULT_SLEEP) -> None:
        self.out_dir = out_dir
        self.sleep = sleep
        self.manifest = out_dir / "manifest.jsonl"
        self.session = make_session(rate_limit_sec=sleep)

    def discover(self, state: CAGState) -> list[dict[str, Any]]:
        url = f"{ACCOUNTS_URL}?defuat_state_id={state.state_id}"
        r = self.session.get(url, timeout=60)
        r.raise_for_status()
        return parse_finance_accounts_tab(r.text, state)

    def _record(self, doc: dict[str, Any], *, status: str) -> dict[str, Any]:
        now = _now_iso()
        basename = Path(unquote(urlparse(doc["url"]).path)).name
        state_slug = _slug(doc["state"])
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", basename)
        filename = f"cag_{doc['state_id']}_{doc['year']}_vol{doc['volume']}_{safe}"
        dest = self.out_dir / state_slug / filename
        return {
            "key": f"CAG_STATE_ACCOUNT|{doc['state_id']}|{doc['year']}|vol-{doc['volume'].lower()}",
            "kind": "cag_state_account",
            "record_type": "cag_state_account",
            "source_family": "cag-state-accounts",
            "state_id": doc["state_id"],
            "state": doc["state"],
            "government": doc["state"],
            "jurisdiction": doc["state"],
            "publisher": "Comptroller and Auditor General of India",
            "document_type": "finance_accounts",
            "volume": doc["volume"],
            "title": doc["title"],
            "year": doc["year"],
            "filename": filename,
            "dest": str(dest),
            "url": doc["url"],
            "source_page": f"{ACCOUNTS_URL}?defuat_state_id={doc['state_id']}",
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
        r = self.session.get(doc["url"], timeout=300)
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

    def probe(
        self,
        state: CAGState,
        *,
        years: list[str] | None = None,
        volumes: list[str] | None = None,
        dry_run: bool = False,
    ) -> list[dict[str, Any]]:
        documents = self.discover(state)
        if volumes:
            wanted_v = {v.upper() for v in volumes}
            documents = [d for d in documents if d["volume"] in wanted_v]
        if years:
            wanted_y = set(years)
            documents = [d for d in documents if d["year"] in wanted_y]
        records = [self.download_document(d, dry_run=dry_run) for d in documents]
        if not dry_run:
            for record in records:
                self.append_manifest(record)
        return records
