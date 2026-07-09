# SPDX-License-Identifier: MIT
"""Legacy DSpace (XMLUI/JSPUI) acquisition adapter, parameterised by base
URL + handle prefix — for state-legislature digital libraries and similar
archives that run an old-style DSpace instance with no working REST API.

First target: Assam Legislative Assembly Digital Library
(``aladigitallibrary.in``, DSpace 6.3, verified live 2026-07-08). OAI-PMH
is enabled but unusable there (``ListRecords`` -> ``noRecordsMatch``,
``Identify`` baseURL points at ``localhost:8080``) — this adapter harvests
through the browse index and item/bitstream pages instead, the same
approach ``commoner_probe.indiacode`` uses for indiacode.nic.in. That
module's markup (``?view_type=browse`` suffix, "Showing items N to M of
TOTAL") is JSPUI-flavoured; ALA runs the XMLUI/Mirage theme, whose browse
links and pagination banner read differently ("results N to M of TOTAL",
no ``?view_type=browse`` suffix) — verified live, not assumed identical.
The two adapters are kept separate rather than forcing one regex set to
match both themes.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any, Iterator

from .http_client import make_session

DEFAULT_HANDLE_PREFIX = "123456789"

_TAG_RE = re.compile(r"<[^>]+>")
_HANDLE_LINK_RE_TMPL = r'href="/handle/{prefix}/(\d+)"'
_RESULTS_RANGE_RE = re.compile(r"(?:results|Showing items)\s+(\d+)\s+to\s+(\d+)\s+of\s+(\d+)")
_FIELD_RE = re.compile(
    r'<td class="metadataFieldLabel[^"]*">([^<:]+):?&nbsp;</td>\s*'
    r'<td[^>]*class="metadataFieldValue[^"]*"[^>]*>(.*?)</td>',
    re.DOTALL,
)
_BITSTREAM_HREF_RE_TMPL = r'href="(/bitstream/{prefix}/\d+/\d+/[^"]+)"'
_BREADCRUMB_RE = re.compile(r'<li><a href="/handle/[^"]+">([^<]+)</a></li>')


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _clean(text: str) -> str:
    return unescape(_TAG_RE.sub(" ", text)).strip()


def parse_browse_page(html_text: str, handle_prefix: str) -> tuple[dict[str, str], int, int]:
    """Item handles on one browse page, plus (shown_to, total) for pagination.

    Falls back to (len(handles), len(handles)) when no results-range banner
    is found, which naturally halts the caller's pagination loop.
    """
    link_re = re.compile(_HANDLE_LINK_RE_TMPL.format(prefix=re.escape(handle_prefix)))
    handles: dict[str, str] = {}
    for tr_match in re.finditer(r"<tr.*?>(.*?)</tr>", html_text, flags=re.DOTALL | re.IGNORECASE):
        tr_text = tr_match.group(1)
        hm = link_re.search(tr_text)
        if not hm:
            continue
        handle_id = hm.group(1)
        title_m = re.search(rf'href="/handle/{re.escape(handle_prefix)}/{handle_id}"[^>]*>(.*?)</a>', tr_text)
        title = _clean(title_m.group(1)) if title_m else ""
        if handle_id not in handles:
            handles[handle_id] = title

    m = _RESULTS_RANGE_RE.search(html_text)
    if m:
        return handles, int(m.group(2)), int(m.group(3))
    return handles, len(handles), len(handles)


def parse_item_metadata(html_text: str) -> dict[str, str]:
    """Parse the ``itemDisplayTable`` metadata rows on a DSpace item page.

    Field labels are whatever the site's Dublin Core mapping renders
    (Title, Issue Date, Publisher, Type, ... — verified against ALA:
    Title/Keywords/Issue Date/Publisher/URI). Returned as a plain
    label->value dict; callers pick the fields they need.
    """
    return {m.group(1).strip(): _clean(m.group(2)) for m in _FIELD_RE.finditer(html_text)}


def parse_breadcrumb_collection(html_text: str) -> str | None:
    """Second-to-last breadcrumb entry is the parent collection name."""
    crumbs = _BREADCRUMB_RE.findall(html_text)
    return _clean(crumbs[-2]) if len(crumbs) >= 2 else None


def parse_bitstream_urls(html_text: str, handle_prefix: str) -> list[str]:
    href_re = re.compile(_BITSTREAM_HREF_RE_TMPL.format(prefix=re.escape(handle_prefix)))
    seen: list[str] = []
    for m in href_re.finditer(html_text):
        path = unescape(m.group(1))
        if path not in seen:
            seen.append(path)
    return seen


class LegacyDSpaceProbe:
    """Acquire items from a legacy DSpace (XMLUI/JSPUI) instance via its
    browse index, item pages, and bitstream downloads — no REST API."""

    #: Statuses that mean "nothing left to do" regardless of --download.
    #: "metadata_only" is handled separately in probe() since a later
    #: --download rerun must still fetch bitstreams for it.
    _ALWAYS_TERMINAL_STATUSES = frozenset({"downloaded", "no_bitstream_found"})

    def __init__(
        self,
        out_dir: Path,
        *,
        base_url: str,
        portal_name: str,
        handle_prefix: str = DEFAULT_HANDLE_PREFIX,
        sleep: float = 1.0,
        rpp: int = 100,
    ) -> None:
        self.out_dir = out_dir
        self.base_url = base_url.rstrip("/")
        self.portal_name = portal_name
        self.handle_prefix = handle_prefix
        self.sleep = sleep
        self.rpp = rpp
        self.manifest = out_dir / "manifest.jsonl"
        self.pdf_dir = out_dir / "pdfs" / portal_name
        self.session = make_session(rate_limit_sec=sleep)

    def _get(self, path: str) -> str:
        r = self.session.get(self.base_url + path, timeout=45)
        r.raise_for_status()
        return r.text

    def iter_handles(self, browse_type: str = "dateissued") -> Iterator[tuple[str, str]]:
        offset = 0
        while True:
            text = self._get(f"/browse?type={browse_type}&order=ASC&rpp={self.rpp}&offset={offset}")
            handles, shown_to, total = parse_browse_page(text, self.handle_prefix)
            if not handles:
                return
            yield from handles.items()
            if self.sleep:
                time.sleep(self.sleep)
            if shown_to >= total:
                return
            offset += self.rpp

    def fetch_item(self, handle_id: str) -> dict[str, Any]:
        html_text = self._get(f"/handle/{self.handle_prefix}/{handle_id}")
        fields = parse_item_metadata(html_text)
        bitstreams = parse_bitstream_urls(html_text, self.handle_prefix)
        return {
            "handle_id": handle_id,
            "title": fields.get("Title"),
            "issue_date_raw": fields.get("Issue Date"),
            "publisher": fields.get("Publisher"),
            "type": fields.get("Type"),
            "collection": parse_breadcrumb_collection(html_text),
            "bitstream_paths": bitstreams,
        }

    def load_seen(self) -> dict[str, str]:
        seen: dict[str, str] = {}
        if not self.manifest.exists():
            return seen
        with self.manifest.open(encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("key"):
                    seen[rec["key"]] = rec.get("status", "")
        return seen

    def append_manifest(self, record: dict[str, Any]) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _record(self, handle_id: str, item: dict[str, Any], *, status: str) -> dict[str, Any]:
        now = _now_iso()
        return {
            "key": f"DSPACE|{self.portal_name}|{handle_id}",
            "kind": "legacy_dspace_item",
            "record_type": "legacy_dspace_item",
            "source": self.base_url,
            "portal_name": self.portal_name,
            "handle_id": handle_id,
            "handle_prefix": self.handle_prefix,
            "title": item.get("title"),
            "issue_date_raw": item.get("issue_date_raw"),
            "publisher": item.get("publisher"),
            "type": item.get("type"),
            "collection": item.get("collection"),
            "bitstream_paths": item.get("bitstream_paths", []),
            "downloads": [],
            "status": status,
            "probed_at": now,
        }

    def download_bitstreams(self, record: dict[str, Any]) -> None:
        for path in record.get("bitstream_paths", []):
            filename = path.rsplit("/", 1)[-1]
            dest = self.pdf_dir / record["handle_id"] / filename
            if dest.exists() and dest.stat().st_size > 0:
                body = dest.read_bytes()
            else:
                r = self.session.get(self.base_url + path, timeout=120)
                r.raise_for_status()
                body = r.content
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(body)
                if self.sleep:
                    time.sleep(self.sleep)
            record["downloads"].append({
                "path": path,
                "dest": str(dest.relative_to(self.out_dir)),
                "sha256": hashlib.sha256(body).hexdigest(),
            })
        record["status"] = "downloaded" if record["downloads"] else "no_bitstream_found"

    def probe(
        self,
        *,
        browse_type: str = "dateissued",
        max_records: int | None = None,
        download: bool = False,
        dry_run: bool = False,
    ) -> list[dict[str, Any]]:
        seen = self.load_seen()
        records: list[dict[str, Any]] = []
        for handle_id, browse_title in self.iter_handles(browse_type):
            key = f"DSPACE|{self.portal_name}|{handle_id}"
            prior_status = seen.get(key)
            if prior_status in self._ALWAYS_TERMINAL_STATUSES:
                continue
            # "metadata_only" is deliberately NOT always-terminal: a
            # --no-download pass followed by a --download rerun must still
            # fetch bitstreams for that item (the 2026-07-03 indiacode.py
            # resume-staleness lesson — see _org/mistakes.md).
            if prior_status == "metadata_only" and not download:
                continue
            if dry_run:
                records.append({"key": key, "handle_id": handle_id, "title": browse_title, "status": "dry_run"})
                continue
            item = self.fetch_item(handle_id)
            record = self._record(handle_id, item, status="metadata_only")
            if download:
                self.download_bitstreams(record)
            self.append_manifest(record)
            records.append(record)
            if self.sleep:
                time.sleep(self.sleep)
            if max_records is not None and len(records) >= max_records:
                return records
        return records
