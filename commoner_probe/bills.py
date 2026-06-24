# SPDX-License-Identifier: MIT
"""Bills / legislation probe (sansad.in).

Roadmap source (README "Upcoming"): every bill since independence with
introduction date, stage dates, and status — enables tracking legislative
velocity, committee-scrutiny rates, and private-member-bill outcomes.

Topic-less: the bills list is an exhaustive catalog, so this follows the
mca/dmft "fetch the known catalog, dedup by stable key" shape.

Contract (captured live via the bills page's network calls):

    GET https://sansad.in/api_rs/legislation/getBills
        ?house=Lok Sabha            # or "Rajya Sabha"; blank = both houses
        &billType=Government        # or "Private Member"; blank = all
        &page=1&size=200&locale=en
        &sortOn=billIntroducedDate&sortBy=desc
        (+ optional: ministryName, billCategory, billStatus, billName,
         loksabha, sessionNo, introductionDateFrom/To, passedInLs/RsDateFrom/To)

Note the endpoint lives under ``api_rs`` even for Lok Sabha bills. Response is
the committee-style envelope ``{"_metadata": {"totalPages": N, ...},
"records": [...]}``. ~10k bills total (6.7k LS + 3.4k RS).
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import urlencode

from .http_client import make_session

BILLS_API = "https://sansad.in/api_rs/legislation/getBills"

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "commoner-probe/0.4.1 (github.com/CommonerLLP/commoner-probe; public-interest research; rate-limited)",
    "Referer": "https://sansad.in/ls/legislation/bills",
}

# Internal house code -> sansad ``house`` query value.
_HOUSE_PARAM = {"ls": "Lok Sabha", "rs": "Rajya Sabha"}
VALID_HOUSES = ("ls", "rs")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _date(value: object) -> str | None:
    """sansad dates arrive as ``"2026-04-16 00:00:00.0"`` — keep the ISO date."""
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:10]


def bill_key(house: str, raw: dict) -> str:
    """Stable dedup key. (house, billYear, billNumber) is unique; fall back to
    a raw-record hash if the number is missing."""
    bill_no = raw.get("billNumber")
    year = raw.get("billYear")
    if bill_no not in (None, ""):
        return f"BILL|{house}|{year or 'NA'}|{bill_no}"
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
        bill_type: str | None = None,
        api_url: str = BILLS_API,
    ) -> None:
        self.out_dir = out_dir
        self.sleep = sleep
        self.houses = [h for h in (houses or list(VALID_HOUSES)) if h in VALID_HOUSES]
        self.bill_type = bill_type or ""
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
        params = {
            "house": _HOUSE_PARAM.get(house, ""),
            "billType": self.bill_type,
            "ministryName": "",
            "billCategory": "",
            "billStatus": "",
            "billName": "",
            "loksabha": "",
            "sessionNo": "",
            "page": page,
            "size": size,
            "locale": "en",
            "sortOn": "billIntroducedDate",
            "sortBy": "desc",
        }
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

    def _record(self, raw: dict, house: str) -> dict:
        now = _now_iso()
        return {
            "key": bill_key(house, raw),
            "kind": "bill_record",
            "record_type": "bill_record",
            "source": "sansad.in/api_rs/legislation/getBills",
            "house": house,
            "bill_no": raw.get("billNumber"),
            "bill_name": raw.get("billName"),
            "bill_type": raw.get("billType"),
            "bill_category": raw.get("billCategory"),
            "ministry": raw.get("ministryName"),
            "bill_year": raw.get("billYear"),
            "introduced_house": raw.get("billIntroducedInHouse"),
            "introduced_by": raw.get("billIntroducedBy"),
            "introduced_date": _date(raw.get("billIntroducedDate")),
            "introduced_file": raw.get("billIntroducedFile"),
            "passed_ls_date": _date(raw.get("billPassedInLSDate")),
            "passed_ls_file": raw.get("billPassedInLSFile"),
            "passed_rs_date": _date(raw.get("billPassedInRSDate")),
            "passed_rs_file": raw.get("billPassedInRSFile"),
            "passed_both_houses_file": raw.get("billPassedInBothHousesFile"),
            "referred_to_committee_date": _date(raw.get("referredToCommitteeDate")),
            "report_presented_date": _date(raw.get("reportPresentedDate")),
            "report_file": raw.get("reportFile"),
            "act_no": raw.get("actNo"),
            "act_year": raw.get("actYear"),
            "assent_date": _date(raw.get("billAssentedDate")),
            "gazetted_file": raw.get("billGazettedFile"),
            "synopsis_file": raw.get("billSynopsisFile"),
            "errata_file": raw.get("errataFile"),
            "status": raw.get("status"),
            "fetch_status": "ok",
            "fetched_at": now,
            "probed_at": now,
        }

    def _status_record(self, house: str, *, fetch_status: str, error: str | None = None) -> dict:
        now = _now_iso()
        rec = {
            "key": f"BILL|{house}|_{fetch_status}",
            "kind": "bill_record",
            "record_type": "bill_record",
            "source": "sansad.in/api_rs/legislation/getBills",
            "house": house,
            "fetch_status": fetch_status,
            "api_url": self.api_url,
            "fetched_at": now,
            "probed_at": now,
        }
        if error:
            rec["error"] = error[:500]
        return rec

    def probe(self, *, max_records: int | None = None, dry_run: bool = False) -> list[dict]:
        seen = self.load_seen()
        out: list[dict] = []
        for house in self.houses:
            if dry_run:
                out.append(self._status_record(house, fetch_status="dry_run"))
                continue
            added = 0
            try:
                for raw in self.bills_all(house):
                    rec = self._record(raw, house)
                    if rec["key"] in seen:
                        continue
                    seen.add(rec["key"])
                    self.append_manifest(rec)
                    out.append(rec)
                    added += 1
                    if max_records is not None and added >= max_records:
                        break
            except Exception as exc:  # noqa: BLE001
                rec = self._status_record(house, fetch_status="fetch_error", error=str(exc))
                self.append_manifest(rec)
                out.append(rec)
            if self.sleep:
                time.sleep(self.sleep)
        return out
