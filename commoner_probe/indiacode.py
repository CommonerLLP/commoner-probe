# SPDX-License-Identifier: MIT
"""India Code (indiacode.nic.in) adapter — legacy DSpace, no REST API.

India Code is a legacy DSpace (XMLUI/JSPUI) install. The DSpace-7 REST API
(``/server/api``) is disabled (404/500); this adapter parses the legacy HTML
directly. Contract verified live 2026-07 against the West Bengal Public
Libraries Act, 1979 (handle 14547):

    Per-state parent collection:
        GET /handle/123456789/{state_handle}/
    Per-state Act enumeration (paginated, chronological browse index):
        GET /handle/123456789/{state_handle}/browse?type=dateissued&rpp=100&offset=N
        -> item handles via ``href="/handle/123456789/{id}?view_type=browse"``,
           plus "Showing items X to Y of Z" for the stop condition.
    Per-Act detail page:
        GET /handle/123456789/{item_handle}
        -> ``itemDisplayTable`` metadata rows: Act ID, Act Number, Enactment
           Date, Act Year, Short Title, Department, Type, Location.
        -> main Act PDF: ``/bitstream/123456789/{item_handle}/1/{file}.pdf``.
        -> subordinate legislation (Rules, Regulations, Notifications,
           Orders, Circulars, Ordinances, Statutes) are embedded directly on
           the same page as Bootstrap modal tables (``id="myTable{Category}"``),
           one row per instrument: date, English description, Hindi
           description, and file links of the form
           ``/ViewFileUploaded?path={actid}/{category}individualfile/&file={NN}.pdf``.
           Amendments are not a distinct category — they show up as
           Notification (occasionally Rule) rows whose description contains
           "(Amendment)"; ``is_amendment`` is derived from that text, not a
           separate site field. Filenames are sparse (not a dense 1..N
           sequence) — never assume a range, always parse from the page.

Central Acts live in a separate collection tree and are out of scope here
(state library-law research only, per the filing issue).

Known gap: no archive.org/Wayback snapshot-on-fetch yet — no other adapter
in this repo does that either, and building a bespoke, unverified
integration for one adapter would be scope creep. Tracked in TODO.md.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .base import safe_filename_segment
from .http_client import make_session

BASE_URL = "https://indiacode.nic.in"

# indiacode.nic.in sits behind Akamai, which 403s the shared http_client
# USER_AGENT (it contains a "+https://..." URL fragment — a common bot
# fingerprint) on *every* path, including robots.txt itself. Verified live
# 2026-07: a bare "commoner-probe/<ver> (research)" UA (no URL fragment,
# same style as NEVA_UA in neva.py) is not blocked. robots.txt itself,
# fetched with a passing UA, only disallows ``/discover`` and
# ``/simple-search`` (the Discovery search UI) — neither of which this
# adapter touches (``/handle``, ``/bitstream``, ``/ViewFileUploaded`` are
# all open) — so ``respect_robots=False`` below is honest policy-following,
# not a bypass; it exists only because the robots.txt *fetch* itself 403s
# for a UA that plainly identifies this tool.
HEADERS = {
    "User-Agent": "commoner-probe/0.5.0 (research)",
}

# Parent-collection handle for each state/UT, scraped from the
# /simple-search state facet (verified live 2026-07). Central Acts are a
# separate collection tree and are not covered by this registry.
STATE_HANDLES: dict[str, str] = {
    "Andaman and Nicobar Islands": "2454",
    "Andhra Pradesh": "2486",
    "Arunachal Pradesh": "2487",
    "Assam": "2513",
    "Bihar": "2488",
    "Chandigarh": "2489",
    "Chhattisgarh": "2490",
    "Dadra and Nagar Haveli and Daman and Diu": "2492",
    "Delhi": "2493",
    "Goa": "2514",
    "Gujarat": "2455",
    "Haryana": "2193",
    "Himachal Pradesh": "2494",
    "Jammu and Kashmir": "2495",
    "Jharkhand": "2515",
    "Karnataka": "2485",
    "Kerala": "2516",
    "Ladakh": "14011",
    "Lakshadweep": "2496",
    "Madhya Pradesh": "2497",
    "Maharashtra": "2517",
    "Manipur": "2498",
    "Meghalaya": "2499",
    "Mizoram": "2500",
    "Nagaland": "2501",
    "Odisha": "2502",
    "Puducherry": "2503",
    "Punjab": "2504",
    "Rajasthan": "2505",
    "Sikkim": "2506",
    "Tamil Nadu": "2507",
    "Telangana": "2508",
    "Tripura": "2509",
    "Uttarakhand": "2511",
    "Uttar Pradesh": "2510",
    "West Bengal": "2512",
}

# (modal table id, instrument_type). Order matches the site's own button row.
SUBORDINATE_TABLES: tuple[tuple[str, str], ...] = (
    ("myTableRules", "rule"),
    ("myTableRegulation", "regulation"),
    ("myTableNotification", "notification"),
    ("myTableOrders", "order"),
    ("myTableCircular", "circular"),
    ("myTableOrdinances", "ordinance"),
    ("myTableStatutes", "statute"),
)

PUBLIC_LIBRARIES_HANDLES = {
    "Andhra Pradesh": "",
    "Arunachal Pradesh": "",
    "Bihar": "",
    "Chhattisgarh": "",
    "Goa": "",
    "Gujarat": "",
    "Haryana": "",
    "Karnataka": "",
    "Kerala": "",
    "Maharashtra": "",
    "Manipur": "",
    "Mizoram": "",
    "Odisha": "",
    "Rajasthan": "",
    "Tamil Nadu": "",
    "Telangana": "",
    "Uttar Pradesh": "",
    "Uttarakhand": "",
    "West Bengal": "14547",
}

_AMENDMENT_RE = re.compile(r"\bamendment\b", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(value: str) -> str:
    return html.unescape(_TAG_RE.sub(" ", value)).strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------

def parse_browse_page(html_text: str) -> tuple[dict[str, str], int, int]:
    """Item handles on one browse page mapped to their titles, plus (shown_to, total) for pagination.

    Falls back to (len(handles), len(handles)) when the "Showing items"
    banner is absent (e.g. a state with 0 Acts), which naturally halts the
    caller's pagination loop.
    """
    handles: dict[str, str] = {}
    for tr_match in re.finditer(r'<tr.*?>(.*?)</tr>', html_text, flags=re.DOTALL | re.IGNORECASE):
        tr_text = tr_match.group(1)
        hm = re.search(r'href="/handle/123456789/(\d+)\?view_type=browse"', tr_text)
        if not hm:
            continue
        h = hm.group(1)

        tm = re.search(r'<td headers="t3"[^>]*>(.*?)</td>', tr_text, re.DOTALL | re.IGNORECASE)
        title = _strip_tags(tm.group(1)) if tm else ""

        if h not in handles:
            handles[h] = title

    m = re.search(r"Showing items (\d+) to (\d+) of (\d+)", html_text)
    if m:
        return handles, int(m.group(2)), int(m.group(3))
    return handles, len(handles), len(handles)


def parse_act_metadata(html_text: str) -> dict[str, str | None]:
    """Parse the ``itemDisplayTable`` metadata rows on an Act handle page."""
    fields: dict[str, str] = {}
    for m in re.finditer(
        r'<td class="metadataFieldLabel">([^<:]+):?&nbsp;</td>'
        r'<td[^>]*class="metadataFieldValue"[^>]*>(.*?)</td>',
        html_text,
    ):
        fields[m.group(1).strip()] = _strip_tags(m.group(2))
    return {
        "act_id": fields.get("Act ID"),
        "act_no": fields.get("Act Number"),
        "enactment_date": fields.get("Enactment Date"),
        "act_year": fields.get("Act Year"),
        "short_title": fields.get("Short Title"),
        "department": fields.get("Department"),
        "act_type": fields.get("Type"),
        "location": fields.get("Location"),
    }


def parse_act_pdf_url(html_text: str) -> str | None:
    m = re.search(r'href="(/bitstream/123456789/\d+/\d+/[^"]+\.pdf)"', html_text)
    return BASE_URL + m.group(1) if m else None


def parse_subordinate_rows(html_text: str, table_id: str) -> list[dict]:
    """Parse one subordinate-legislation modal table (Rules/Notifications/...).

    Rows are ``<tr>``-delimited in markup but the site's own HTML leaves
    ``<tr>`` unclosed, so rows are sliced between successive
    ``<td class="modaltd1">`` (date-cell) markers rather than trusting tag
    balance — the same defensive slicing pattern used for NeVA member cards.
    """
    block_m = re.search(rf'<table id="{table_id}"[^>]*>(.*?)</table>', html_text, re.DOTALL)
    if not block_m:
        return []
    block = block_m.group(1)
    starts = [m.start() for m in re.finditer(r'<td class="modaltd1">', block)]
    rows: list[dict] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(block)
        chunk = block[start:end]

        date_m = re.search(r'<td class="modaltd1">(.*?)</td>', chunk, re.DOTALL)
        instrument_date = _strip_tags(date_m.group(1)) if date_m else ""

        descs = re.findall(r'<td\s*class="modaltd2"[^>]*>(.*?)</td>', chunk, re.DOTALL)
        desc_en = _strip_tags(descs[0]) if len(descs) > 0 else ""
        desc_hi = _strip_tags(descs[1]) if len(descs) > 1 else ""

        file_cells = re.findall(r'<td\s*class="modaltd3"[^>]*>(.*?)</td>', chunk, re.DOTALL)
        for lang, cell in zip(("en", "hi"), file_cells):
            # NB: the site's own href values sometimes have a trailing space
            # before the closing quote (e.g. ``file=32.pdf "``) — capture up
            # to the quote and strip, rather than stopping at whitespace.
            link_m = re.search(
                r'href="/ViewFileUploaded\?path=([^/"]+)/([a-z]+individualfile)/&file=([^"]+)"',
                cell,
            )
            if not link_m:
                continue
            rows.append(
                {
                    "instrument_date": instrument_date or None,
                    "description": desc_en or None,
                    "description_hi": desc_hi or None,
                    "lang": lang,
                    "actid": link_m.group(1),
                    "folder": link_m.group(2),
                    "filename": link_m.group(3).strip(),
                }
            )
    return rows


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------

class IndiaCodeProbe:
    """Acquire India Code state Acts + amendments/rules/notifications with provenance."""

    def __init__(self, out_dir: Path, *, sleep: float = 1.0, rpp: int = 100) -> None:
        self.out_dir = out_dir
        self.sleep = sleep
        self.rpp = rpp
        self.manifest = out_dir / "manifest.jsonl"
        self.session = make_session()

    #: Statuses that mean "nothing left to do for this key" — a record with
    #: one of these can be skipped outright on a rerun. Anything else
    #: (``pending``, ``fetch_error``) is retried, so a metadata-only
    #: ``--no-download`` pass followed by a downloads-enabled rerun on the
    #: same corpus actually downloads the file and updates the manifest,
    #: instead of silently downloading to disk while leaving a stale
    #: ``pending`` row behind (no ``dest``/``sha256``) that a downstream
    #: reader would never discover.
    _TERMINAL_STATUSES = frozenset({"downloaded", "skipped_exists", "no_pdf_found"})

    def load_seen(self) -> dict[str, str]:
        """Return ``{key: last_known_status}`` from the existing manifest."""
        seen: dict[str, str] = {}
        if not self.manifest.exists():
            return seen
        with self.manifest.open(encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("key"):
                    seen[rec["key"]] = rec.get("status", "")
        return seen

    def append_manifest(self, record: dict) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _get(self, path: str) -> str:
        r = self.session.get(BASE_URL + path, headers=HEADERS, timeout=45, respect_robots=False)
        r.raise_for_status()
        if self.sleep:
            time.sleep(self.sleep)
        return r.text

    def iter_act_handles(
        self,
        state_handle: str,
        query_re: re.Pattern | None = None,
        exclude_re: re.Pattern | None = None
    ) -> Iterator[str]:
        offset = 0
        while True:
            text = self._get(
                f"/handle/123456789/{state_handle}/browse"
                f"?type=dateissued&rpp={self.rpp}&offset={offset}"
            )
            handles, shown_to, total = parse_browse_page(text)
            if not handles:
                return
            for h, title in handles.items():
                if query_re and not query_re.search(title):
                    continue
                if exclude_re and exclude_re.search(title):
                    continue
                yield h
            if shown_to >= total:
                return
            offset += self.rpp

    def _base_fields(self, state_name: str, state_handle: str, item_handle: str, meta: dict) -> dict:
        return {
            "source": "indiacode.nic.in",
            "state": state_name,
            "state_handle": state_handle,
            "act_handle": item_handle,
            "act_id": meta.get("act_id"),
            "act_no": meta.get("act_no"),
            "act_year": meta.get("act_year"),
            "short_title": meta.get("short_title"),
            "department": meta.get("department"),
            "act_type": meta.get("act_type"),
            "location": meta.get("location"),
        }

    def probe_act(self, state_name: str, state_handle: str, item_handle: str) -> list[dict]:
        """Fetch one Act's detail page and emit one record per instrument
        (the Act itself + every amendment/rule/notification/etc. found)."""
        text = self._get(f"/handle/123456789/{item_handle}")
        meta = parse_act_metadata(text)
        base = self._base_fields(state_name, state_handle, item_handle, meta)
        now = _now_iso()

        pdf_url = parse_act_pdf_url(text)
        act_rec = {
            "key": f"INDIACODE|{state_handle}|{item_handle}|act|en",
            "kind": "indiacode_instrument",
            "record_type": "indiacode_instrument",
            **base,
            "instrument_type": "act",
            "is_amendment": False,
            "instrument_date": meta.get("enactment_date"),
            "description": meta.get("short_title"),
            "description_hi": None,
            "lang": "en",
            "actid": None,
            "filename": pdf_url.rsplit("/", 1)[-1] if pdf_url else None,
            "source_url": pdf_url,
            "dest": None,
            "status": "pending" if pdf_url else "no_pdf_found",
            "probed_at": now,
        }
        records = [act_rec]

        for table_id, category in SUBORDINATE_TABLES:
            for row in parse_subordinate_rows(text, table_id):
                source_url = (
                    f"{BASE_URL}/ViewFileUploaded?path={row['actid']}/{row['folder']}/"
                    f"&file={row['filename']}"
                )
                records.append(
                    {
                        "key": (
                            f"INDIACODE|{state_handle}|{item_handle}|{category}|"
                            f"{row['lang']}|{row['filename']}"
                        ),
                        "kind": "indiacode_instrument",
                        "record_type": "indiacode_instrument",
                        **base,
                        "instrument_type": category,
                        "is_amendment": bool(_AMENDMENT_RE.search(row["description"] or "")),
                        "instrument_date": row["instrument_date"],
                        "description": row["description"],
                        "description_hi": row["description_hi"],
                        "lang": row["lang"],
                        "actid": row["actid"],
                        "filename": row["filename"],
                        "source_url": source_url,
                        "dest": None,
                        "status": "pending",
                        "probed_at": now,
                    }
                )
        return records

    def download_instrument(self, record: dict) -> dict:
        """Download one instrument's PDF and patch ``dest``/``status``/``sha256`` in place."""
        if not record.get("source_url"):
            return record
        dest = (
            self.out_dir
            / "pdfs"
            / safe_filename_segment(record["state"])
            / str(record["act_handle"])
            / safe_filename_segment(
                f"{record['instrument_type']}_{record['lang']}_{record['filename']}"
            )
        )
        if dest.exists() and dest.stat().st_size > 0:
            record["dest"] = str(dest.relative_to(self.out_dir))
            record["status"] = "skipped_exists"
            record["sha256"] = hashlib.sha256(dest.read_bytes()).hexdigest()
            return record
        try:
            r = self.session.get(record["source_url"], headers=HEADERS, timeout=60, respect_robots=False)
            r.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            body = r.content if hasattr(r, "content") else r.text.encode("utf-8")
            dest.write_bytes(body)
            record["dest"] = str(dest.relative_to(self.out_dir))
            record["status"] = "downloaded"
            record["sha256"] = hashlib.sha256(body).hexdigest()
            if self.sleep:
                time.sleep(self.sleep)
        except Exception as exc:  # noqa: BLE001
            record["status"] = "fetch_error"
            record["error"] = str(exc)[:500]
        return record

    def _status_record(self, state_name: str, state_handle: str | None, *, fetch_status: str, error: str | None = None) -> dict:
        now = _now_iso()
        rec = {
            "key": f"INDIACODE|{state_handle or 'UNKNOWN'}|_{fetch_status}",
            "kind": "indiacode_instrument",
            "record_type": "indiacode_instrument",
            "source": "indiacode.nic.in",
            "state": state_name,
            "state_handle": state_handle,
            "status": fetch_status,
            "probed_at": now,
        }
        if error:
            rec["error"] = error[:500]
        return rec

    def probe_states(
        self,
        states: list[str],
        *,
        download: bool = True,
        dry_run: bool = False,
        max_acts: int | None = None,
        query_re: re.Pattern | None = None,
        exclude_re: re.Pattern | None = None,
        known_handles: dict[str, str] | None = None,
        classify_availability: bool = False,
    ) -> list[dict]:
        known_handles = known_handles or {}
        out: list[dict] = []
        for state_name in states:
            state_handle = STATE_HANDLES.get(state_name)
            if not state_handle:
                rec = self._status_record(state_name, None, fetch_status="unknown_state")
                self.append_manifest(rec)
                out.append(rec)
                continue
            if dry_run:
                rec = self._status_record(state_name, state_handle, fetch_status="dry_run")
                out.append(rec)
                continue

            seen = self.load_seen()
            acts_done = 0
            try:
                handle_for_state = known_handles.get(state_name)
                if handle_for_state:
                    item_handles = [handle_for_state]
                else:
                    item_handles = list(self.iter_act_handles(state_handle, query_re, exclude_re))

                if classify_availability and not item_handles:
                    rec = self._status_record(state_name, state_handle, fetch_status="absent")
                    self.append_manifest(rec)
                    out.append(rec)

                state_has_principal = False

                for item_handle in item_handles:
                    records = self.probe_act(state_name, state_handle, item_handle)

                    if classify_availability:
                        act_record = next((r for r in records if r["instrument_type"] == "act"), None)
                        if act_record:
                            desc = act_record.get("description") or ""
                            if not _AMENDMENT_RE.search(desc):
                                state_has_principal = True

                    for rec in records:
                        old_status = seen.get(rec["key"])
                        will_download = download and bool(rec.get("source_url"))
                        if old_status in self._TERMINAL_STATUSES:
                            continue  # already fully done, nothing this run can add
                        if old_status is not None and not will_download:
                            continue  # already recorded and this run adds nothing new
                        if will_download:
                            self.download_instrument(rec)
                        seen[rec["key"]] = rec["status"]
                        self.append_manifest(rec)
                        out.append(rec)

                    acts_done += 1
                    if max_acts is not None and acts_done >= max_acts:
                        break

                if classify_availability and item_handles:
                    status = "principal_present" if state_has_principal else "amendment_only"
                    rec = self._status_record(state_name, state_handle, fetch_status=status)
                    self.append_manifest(rec)
                    out.append(rec)

            except Exception as exc:  # noqa: BLE001
                rec = self._status_record(state_name, state_handle, fetch_status="fetch_error", error=str(exc))
                self.append_manifest(rec)
                out.append(rec)
        return out
