# SPDX-License-Identifier: MIT
"""Bills / legislation probe (sansad.in).

Roadmap source (README "Upcoming"): every bill since independence with
introduction date, debate dates, and stage status — enables tracking
legislative velocity, committee-scrutiny rates, and private-member-bill outcomes.

Topic-less: the bills list is an exhaustive catalog (no thematic search
buckets), so this follows the dmft/mca "fetch the known catalog, dedup by stable
key" shape rather than the topic/searches machinery.

============================  PROVISIONAL CONTRACT  ===========================
The live sansad.in bills API is NOT yet confirmed. The README names the HTML
route ``sansad.in/ls/legislation/bills``, but that is a Next.js page whose bill
list is fetched client-side from a Strapi-style backend; the listing endpoint is
not exposed in the page's SSR data and could not be recovered by black-box
probing (every guessed ``api_ls/...`` path 404s).

This module is wired end-to-end EXCEPT the live contract:

* ``BILLS_API`` is a best-guess default, overridable via constructor / ``--api-url``.
* Pagination assumes the committee API's ``{"records": [...],
  "_metadata": {"totalPages": N}}`` envelope.
* Field extraction tries several plausible key names and tolerates misses
  (non-core fields are nullable in the schema).
* The dedup ``key`` uses the bill number if present, else a hash of the raw
  record — stable across re-runs regardless of canonical field names.

TO FINALISE (see bead sansad-crawler-4xd): capture one real response from the
bills page Network tab, then (1) set ``BILLS_API`` + page params in
``bills_page``, (2) map real field names in ``_record``, (3) tighten the schema
``required`` list, (4) replace the FakeSession fixture in tests/test_bills.py.
==============================================================================
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .http_client import make_session

# PROVISIONAL — unverified. See module docstring / bead sansad-crawler-4xd.
BILLS_API = "https://sansad.in/api_ls/legislation/bills"

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "commoner-probe/0.4.1 (github.com/CommonerLLP/commoner-probe; public-interest research; rate-limited)",
    "Referer": "https://sansad.in/ls/legislation/bills",
}

VALID_HOUSES = ("ls", "rs")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _first(raw: dict, *keys: str) -> object | None:
    for k in keys:
        v = raw.get(k)
        if v not in (None, ""):
            return v
    return None


def bill_key(house: str, raw: dict) -> str:
    """Stable dedup key: bill number if available, else a raw-record hash."""
    bill_no = _first(raw, "billNumber", "billNo", "number", "billNum")
    if bill_no not in (None, ""):
        return f"BILL|{house}|{bill_no}"
    digest = hashlib.sha1(
        json.dumps(raw, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:12]
    return f"BILL|{house}|{digest}"


class BillsProbe:
    """Acquire sansad.in bill records with provenance (topic-less)."""

    def __init__(
        self,
        out_dir: Path,
        *,
        sleep: float = 0.5,
        houses: list[str] | None = None,
        api_url: str = BILLS_API,
    ) -> None:
        self.out_dir = out_dir
        self.sleep = sleep
        self.houses = [h for h in (houses or list(VALID_HOUSES)) if h in VALID_HOUSES]
        self.api_url = api_url
        self.manifest = out_dir / "manifest.jsonl"
        self.session = make_session()

    def load_seen(self) -> set[str]:
        seen: set[str] = set()
        if not self.manifest.exists():
            return seen
        with self.manifest.open(encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("key"):
                    seen.add(rec["key"])
        return seen

    def append_manifest(self, record: dict) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def bills_page(self, house: str, page: int, size: int = 200) -> dict:
        # PROVISIONAL param names — adjust once the live contract is captured.
        from urllib.parse import urlencode

        params = {"house": house.upper(), "page": page, "size": size}
        url = f"{self.api_url}?{urlencode(params)}"
        r = self.session.get(url, headers=HEADERS, timeout=45)
        r.raise_for_status()
        return r.json()

    def bills_all(self, house: str) -> Iterator[dict]:
        page = 1
        while True:
            data = self.bills_page(house, page)
            records = data.get("records") or []
            if not records:
                return
            yield from records
            meta = data.get("_metadata") or {}
            total_pages = int(meta.get("totalPages") or 0)
            if page >= total_pages:
                return
            page += 1
            time.sleep(self.sleep)

    def _record(self, raw: dict, house: str, *, fetch_status: str = "ok") -> dict:
        now = _now_iso()
        return {
            "key": bill_key(house, raw),
            "kind": "bill_record",
            "record_type": "bill_record",
            "source": "sansad.in/legislation",
            "house": house,
            "bill_no": _first(raw, "billNumber", "billNo", "number", "billNum"),
            "bill_name": _first(raw, "billName", "name", "title"),
            "bill_type": _first(raw, "billType", "type", "category"),
            "ministry": _first(raw, "ministry", "ministryName"),
            "introduced_date": _first(raw, "introducedDate", "dateOfIntroduction", "introductionDate"),
            "introduced_house": _first(raw, "introducedHouse", "houseOfIntroduction"),
            "passed_ls_date": _first(raw, "passedLsDate", "lsPassedDate"),
            "passed_rs_date": _first(raw, "passedRsDate", "rsPassedDate"),
            "assent_date": _first(raw, "assentDate", "dateOfAssent"),
            "current_stage": _first(raw, "currentStage", "stage"),
            "status": _first(raw, "status", "billStatus"),
            "pdf_url": _first(raw, "url", "pdfUrl", "fileUrl"),
            "pdf_path": None,
            "fetch_status": fetch_status,
            "fetched_at": now,
            "probed_at": now,
        }

    def _status_record(self, house: str, *, fetch_status: str, error: str | None = None) -> dict:
        now = _now_iso()
        rec = {
            "key": f"BILL|{house}|_{fetch_status}",
            "kind": "bill_record",
            "record_type": "bill_record",
            "source": "sansad.in/legislation",
            "house": house,
            "bill_no": None,
            "bill_name": None,
            "bill_type": None,
            "ministry": None,
            "introduced_date": None,
            "introduced_house": None,
            "passed_ls_date": None,
            "passed_rs_date": None,
            "assent_date": None,
            "current_stage": None,
            "status": None,
            "pdf_url": None,
            "pdf_path": None,
            "fetch_status": fetch_status,
            "api_url": self.api_url,
            "fetched_at": now,
            "probed_at": now,
        }
        if error:
            rec["error"] = error[:500]
        return rec

    def probe(self, *, dry_run: bool = False) -> list[dict]:
        seen = self.load_seen()
        out: list[dict] = []
        for house in self.houses:
            if dry_run:
                out.append(self._status_record(house, fetch_status="dry_run"))
                continue
            try:
                for raw in self.bills_all(house):
                    rec = self._record(raw, house)
                    if rec["key"] in seen:
                        continue
                    seen.add(rec["key"])
                    self.append_manifest(rec)
                    out.append(rec)
            except Exception as exc:  # noqa: BLE001 — provisional endpoint may 4xx
                rec = self._status_record(house, fetch_status="fetch_error", error=str(exc))
                self.append_manifest(rec)
                out.append(rec)
            if self.sleep:
                time.sleep(self.sleep)
        return out
