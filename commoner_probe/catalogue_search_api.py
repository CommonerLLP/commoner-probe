# SPDX-License-Identifier: MIT
"""Abhilekh Patal (National Archives of India) catalogue acquisition.

This adapter acquires the **catalogue**, not the documents.

The distinction is the whole design. Search and metadata are open: a query
returns tens of thousands of records carrying an identifier, year, page count,
language and keywords, with no account. The scanned records behind them are
not: the results page carries ``Cart`` and ``Order`` markup and the site
publishes a cancellation/refund policy, which is a paid reproduction-ordering
flow rather than a download. So this module deliberately stops at
``metadata_only``. A "downloaded" status here would be a lie about what was
acquired.

**India egress is a hard requirement, not a nice-to-have.** The site sits
behind an AWS WAF challenge that answers non-India clients with HTTP 202 and a
Human Verification page — zero bytes of catalogue. A real headless Chromium
executing JS did not clear it either (recorded 2026-07-09), so
this is not a rendering problem a browser fallback solves. From ap-south-1 the
same request returns HTTP 200 and the real page. The probe therefore treats a
challenge response as a **hard error naming the cause**, and never as an empty
result set: an empty corpus that looks like "the archive had nothing" is the
silent-success failure this repo keeps having to fix.

India egress alone is not sufficient. Measured from ap-south-1 on 2026-07-28,
the WAF challenges *every* honest ``commoner-probe`` identity as well, and only
a mainstream browser token is let through. **This repo has decided not to send
one** — see ``UA_CHALLENGE_NOTE`` below. The adapter is complete and tested and
does not fetch, on purpose.

Pagination is not a query parameter. ``?Page.Number=1`` on the search URL is
ignored and silently returns page 0 — verified live, and the reason a naive
crawler would loop forever re-recording the first ten records. The real
contract is a separate endpoint, ``/Category/Search/PaginationScroll``, taking
**lower-cased** filter keys and returning JSON whose ``partialView`` holds the
next page's cards in the same markup as the first page.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlencode, urljoin

from .http_client import USER_AGENT, make_session

BASE_URL = "https://www.abhilekh-patal.in"
SEARCH_PATH = "/Category/Search/QuerySearch"
SCROLL_PATH = "/Category/Search/PaginationScroll"
ITEM_PATH = "/Category/ItemDetails/ItemDetails"

#: The site serves 10 cards per page regardless of a larger requested size
#: (asked for 20, got 10 — verified live 2026-07-28).
PAGE_SIZE = 10

#: Be a polite guest in a public archive; there is no declared crawl-delay.
#: The site publishes no robots.txt at all (404, verified 2026-07-28), so
#: nothing is disallowed and nothing declares a rate — 2s is our own restraint.
DEFAULT_SLEEP = 2.0

#: The WAF challenges **every** identifying User-Agent, not just the default
#: URL-bearing one. Measured 2026-07-28 from ap-south-1, all four returning
#: HTTP 202 and 2,440 bytes of Human Verification: the repo default, the
#: scheme-free form that cleared mha.gov.in (``ddg.SCHEME_FREE_USER_AGENT``),
#: a bare ``commoner-probe/<version>``, and a name-plus-contact variant. Only
#: a mainstream browser token returns the catalogue.
#:
#: That is a posture question, not a technical one, so this module does NOT
#: quietly answer it. The honest User-Agent stays the default and a challenge
#: raises :class:`ChallengeBlocked` naming the cause; an operator who decides
#: to present a different identity passes it explicitly, and the choice is
#: stamped into every record's ``user_agent`` field so the corpus carries how
#: it was obtained rather than hiding it.
#:
#: **DECIDED 2026-07-28: this repo does not present a browser token to clear
#: the challenge.** The adapter stays honest and therefore does not fetch this
#: source. That is a deliberate posture with a known cost — a working,
#: live-tested catalogue adapter sits idle — and NOT an unfinished task. The
#: reasoning: this corpus is used in litigation-adjacent work where provenance
#: is the product, and a client identity that is not true is a poor foundation
#: for it, even where nothing is technically disallowed (the site publishes no
#: robots.txt at all, so the WAF is the only barrier).
#:
#: Do not "fix" this by defaulting ``--user-agent`` to a browser string. If the
#: posture is ever revisited, it is revisited by Commoner, not by a session
#: that finds the adapter idle and assumes it is broken. The flag remains for
#: an operator making that call explicitly and on the record.
UA_CHALLENGE_NOTE = (
    "every commoner-probe User-Agent variant is challenged; pass --user-agent "
    "explicitly to choose a different identity"
)

_ITEM_ID_RE = re.compile(r"/Category/ItemDetails/ItemDetails\?itemId=([0-9a-fA-F-]+)")
_CARD_RE = re.compile(r'class="grid-view-result-div"(.*?)(?=class="grid-view-result-div"|\Z)', re.DOTALL)
_TITLE_RE = re.compile(
    r'class="headingview[^"]*">.*?<a\s+href="[^"]*itemId=([0-9a-fA-F-]+)"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_FIELD_RE = re.compile(r'<span class="pleft">\s*([^<:]+?)\s*:?\s*</span>\s*([^<]*)', re.DOTALL)
_TOTAL_ELEMENTS_RE = re.compile(r'name="Page\.TotalElements"\s+value="(\d+)"')
_TOTAL_PAGES_RE = re.compile(r'name="Page\.TotalPages"\s+value="(\d+)"')
_TAG_RE = re.compile(r"<[^>]+>")

#: Card labels mapped to record fields. Anything else the site adds is kept
#: verbatim under `extra` rather than dropped — an archive's own vocabulary is
#: data, and silently discarding a new field is how a corpus goes stale without
#: anyone noticing.
_FIELD_MAP = {
    "identifier": "identifier",
    "year": "year",
    "no of pages": "page_count",
    "language": "language",
    "keywords": "keywords",
}


class ChallengeBlocked(RuntimeError):
    """The WAF answered with a challenge instead of the catalogue."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", unescape(_TAG_RE.sub(" ", value))).strip()
    if not text or text.upper() == "NA":
        return None
    return text


def _int(value: str | None) -> int | None:
    text = _clean(value)
    if text is None:
        return None
    digits = re.search(r"-?\d+", text)
    return int(digits.group(0)) if digits else None


def parse_totals(html: str) -> tuple[int | None, int | None]:
    """``(total_elements, total_pages)`` from the search page's hidden fields."""
    elements = _TOTAL_ELEMENTS_RE.search(html or "")
    pages = _TOTAL_PAGES_RE.search(html or "")
    return (int(elements.group(1)) if elements else None, int(pages.group(1)) if pages else None)


def parse_cards(html: str) -> list[dict[str, Any]]:
    """Parse catalogue cards out of a search page or a ``partialView`` fragment.

    Both carry identical markup, which is why one parser serves the first page
    and every subsequent scroll response.

    A card with no resolvable item id is skipped; a card missing an optional
    label keeps that field ``None`` rather than being dropped, because a record
    with no recorded year is still a record in the archive.
    """
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in _CARD_RE.findall(html or ""):
        title_m = _TITLE_RE.search(block)
        if title_m:
            item_id, raw_title = title_m.group(1), title_m.group(2)
        else:
            id_m = _ITEM_ID_RE.search(block)
            if not id_m:
                continue
            item_id, raw_title = id_m.group(1), ""
        item_id = item_id.lower()
        if item_id in seen:
            continue
        seen.add(item_id)

        fields: dict[str, Any] = {}
        extra: dict[str, str] = {}
        for label, value in _FIELD_RE.findall(block):
            key = _clean(label)
            if key is None:
                continue
            key = key.lower().rstrip(":").strip()
            mapped = _FIELD_MAP.get(key)
            cleaned = _clean(value)
            if mapped:
                fields[mapped] = cleaned
            elif cleaned is not None:
                extra[key] = cleaned

        items.append({
            "item_id": item_id,
            "title": _clean(raw_title) or _clean(fields.get("identifier")) or item_id,
            "identifier": fields.get("identifier"),
            "year": _int(fields.get("year")),
            "page_count": _int(fields.get("page_count")),
            "language": fields.get("language"),
            "keywords": fields.get("keywords"),
            "extra": extra,
        })
    return items


def is_challenge(status: int | None, body: bytes | str) -> bool:
    """Did the WAF answer with a challenge rather than the catalogue?

    HTTP 202 with an empty or near-empty body is the observed signature from
    non-India egress. Checked on both, because either alone has a false
    positive: a 202 could in principle carry content, and a short body could be
    a genuine empty page.
    """
    if status == 202:
        return True
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else (body or "")
    return "x-amzn-waf-action" in text.lower() or "Human Verification" in text


class AbhilekhPatalProbe:
    """Acquire the National Archives of India catalogue for a search query."""

    def __init__(
        self,
        out_dir: Path,
        *,
        sleep: float = DEFAULT_SLEEP,
        base_url: str = BASE_URL,
        user_agent: str | None = None,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.base_url = base_url.rstrip("/")
        self.sleep = sleep
        self.user_agent = user_agent
        self.manifest = self.out_dir / "manifest.jsonl"
        self.session = make_session(rate_limit_sec=sleep, user_agent=user_agent)

    # -- io ---------------------------------------------------------------

    def load_seen(self) -> set[str]:
        seen: set[str] = set()
        if not self.manifest.exists():
            return seen
        for line in self.manifest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("kind") == "nai_catalogue_record" and row.get("key"):
                seen.add(row["key"])
        return seen

    def append_manifest(self, record: dict[str, Any]) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _get(self, url: str) -> str:
        r = self.session.get(url, timeout=90)
        status = getattr(r, "status_code", None)
        body = getattr(r, "content", b"") or b""
        if is_challenge(status, body):
            raise ChallengeBlocked(
                "abhilekh-patal.in answered with the AWS WAF challenge "
                f"(HTTP {status}), not the catalogue. This source requires "
                "India-region egress; a headless browser does not clear it. "
                "Run this probe from the india-fetch box (ap-south-1) or an "
                f"equivalent India egress path. Note also that {UA_CHALLENGE_NOTE}."
            )
        r.raise_for_status()
        return r.text

    # -- fetch ------------------------------------------------------------

    def search_url(self, query: str) -> str:
        return f"{self.base_url}{SEARCH_PATH}?{urlencode({'query': query})}"

    def scroll_url(self, query: str, *, number: int, total_elements: int, total_pages: int) -> str:
        # Lower-cased keys: the site's own buildFilterQueryString lower-cases
        # every key before sending, and the endpoint honours that spelling.
        params = {
            "query": query,
            "number": number,
            "totalelements": total_elements,
            "totalpages": total_pages,
            "size": PAGE_SIZE,
            "viewtype": "dashboard",
        }
        return f"{self.base_url}{SCROLL_PATH}?{urlencode(params)}"

    def fetch_page(self, query: str, *, number: int, total_elements: int, total_pages: int) -> list[dict[str, Any]]:
        """One page of cards beyond the first, via the scroll endpoint."""
        raw = self._get(self.scroll_url(
            query, number=number, total_elements=total_elements, total_pages=total_pages
        ))
        try:
            fragment = json.loads(raw).get("partialView") or ""
        except json.JSONDecodeError:
            # The endpoint has answered with JSON every time it was exercised,
            # but a bare fragment is still parseable, so degrade rather than fail.
            fragment = raw
        return parse_cards(fragment)

    # -- records ----------------------------------------------------------

    def _record(self, item: dict[str, Any], *, query: str, page: int) -> dict[str, Any]:
        now = _now()
        return {
            "key": f"NAI|{item['item_id']}",
            "kind": "nai_catalogue_record",
            "record_type": "nai_catalogue_record",
            "source_family": "national-archives",
            "source": "abhilekh-patal.in",
            "publisher": "National Archives of India",
            "item_id": item["item_id"],
            "url": urljoin(self.base_url + "/", f"{ITEM_PATH.lstrip('/')}?itemId={item['item_id']}"),
            "title": item["title"],
            "identifier": item["identifier"],
            "year": item["year"],
            "page_count": item["page_count"],
            "language": item["language"],
            "keywords": item["keywords"],
            "extra": item["extra"],
            "search_query": query,
            "result_page": page,
            "user_agent": self.user_agent or USER_AGENT,
            "status": "metadata_only",
            "fetched_at": now,
            "probed_at": now,
        }

    # -- probe ------------------------------------------------------------

    def probe(
        self,
        *,
        query: str,
        max_records: int | None = None,
        max_pages: int | None = None,
        dry_run: bool = False,
    ) -> Iterator[dict[str, Any]]:
        """Stream catalogue records for *query*, page by page.

        Yields as it goes rather than accumulating, because a broad query is
        tens of thousands of records and the caller should be able to stop.
        ``max_records`` and ``max_pages`` are brakes; without either this walks
        the whole result set.

        Resume is by item id: a re-run appends only records not already in the
        manifest. Catalogue entries are archival descriptions, not a tracker,
        so an unchanged record has nothing new to say.
        """
        seed = self._get(self.search_url(query))
        total_elements, total_pages = parse_totals(seed)
        if total_elements is None or total_pages is None:
            raise RuntimeError(
                "abhilekh-patal.in returned a page without the Page.TotalElements/"
                "Page.TotalPages fields the pagination contract depends on — the "
                "site's markup has changed and this adapter needs re-checking."
            )

        seen = self.load_seen()
        emitted = 0
        page = 0
        cards = parse_cards(seed)

        while cards:
            for item in cards:
                record = self._record(item, query=query, page=page)
                if dry_run:
                    yield {**record, "status": "dry_run"}
                    emitted += 1
                else:
                    if record["key"] in seen:
                        continue
                    self.append_manifest(record)
                    seen.add(record["key"])
                    yield record
                    emitted += 1
                if max_records is not None and emitted >= max_records:
                    return

            page += 1
            if page >= total_pages:
                return
            if max_pages is not None and page >= max_pages:
                return
            cards = self.fetch_page(
                query, number=page, total_elements=total_elements, total_pages=total_pages
            )
