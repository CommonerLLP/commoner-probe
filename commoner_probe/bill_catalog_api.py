# SPDX-License-Identifier: MIT
"""Acquire the whole bill catalogue and dedupe it on a stable key.

CONTEXT
=======
The Parliament of India operates the source. The host is sansad.in.
The catalogue is exhaustive. A topic filter is therefore unnecessary.

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
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import urlencode

from .base import download_file, safe_filename_segment
from .http_client import make_session

BILLS_API = "https://sansad.in/api_rs/legislation/getBills"

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "commoner-probe/0.5.0 (github.com/CommonerLLP/commoner-probe; public-interest research; rate-limited)",
    "Referer": "https://sansad.in/ls/legislation/bills",
}

#: The eight document fields a bill record can carry. A full pull on 0.15.1
#: returned 10,506 URLs across 9,929 bills, and only 7,480 of them were the
#: as-introduced text — so a downloader that reads one field misses 29% of the
#: documents. Order is the bill's own lifecycle, which is also the order a
#: reader wants them in.
DOCUMENT_FIELDS = (
    "introduced_file", "passed_ls_file", "passed_rs_file",
    "passed_both_houses_file", "report_file", "gazetted_file",
    "synopsis_file", "errata_file",
)

# Internal house code -> sansad ``house`` query value.
_HOUSE_PARAM = {"ls": "Lok Sabha", "rs": "Rajya Sabha"}
VALID_HOUSES = ("ls", "rs")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


#: The one manifest kind this probe writes. Resume and compaction are scoped to
#: it, because a corpus can be shared and another adapter's rows are not this
#: probe's to index or to drop.
MANIFEST_KIND = "bill_record"


class UnreadableDate(ValueError):
    """A date field carried a shape this reader does not know.

    Raised rather than passed through. A value returned under a field name that
    implies ISO, in a format that is not ISO, compares as a string against ISO
    bounds and silently matches nothing.
    """


#: THIS ENDPOINT SERVES TWO DATE FORMATS IN ONE RECORD, and the second one is
#: the trap. Five fields arrive as `YYYY-MM-DD HH:MM:SS.0`. `billAssentedDate`
#: alone arrives as `DD/MM/YYYY`. Measured over the live catalogue 2026-08-17:
#: 3,576 records carry an assent date, all ten characters, and all 3,576 parse
#: as `%d/%m/%Y`. Zero fail — so this is the source's convention, not corruption.
#:
#: The old reader kept the first ten characters, so `20/12/2025` travelled
#: through beside `passed_ls_date: "2025-12-16"`. A two-year count of assents
#: compared those strings against ISO bounds and reported 0 where the answer is
#: 53. No exception, no empty field, a clean-looking run.
_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y")

#: An ISO 8601 date carrying a time: `2025-12-20T00:00:00Z`, `2025-12-20T05:30`,
#: with or without an offset. The date half is taken and the time half is
#: discarded, because these fields carry midnight — but it is RANGE-checked
#: first, not merely counted. A shape test alone accepted `2025-12-20T99:99:99Z`
#: and shipped the record as `ok`, which is the silent pass this reader exists
#: to remove. 60 seconds is legal: a leap second is a real timestamp.
_ISO_STAMP = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?"
    r"(?:Z|[+-](\d{2}):?(\d{2}))?$")


def _in_range(stamped: re.Match) -> bool:
    """Whether every time component of a matched stamp is a possible one."""
    hour, minute, second, off_hour, off_minute = stamped.groups()[1:]
    return (int(hour) < 24 and int(minute) < 60
            and (second is None or int(second) <= 60)
            and (off_hour is None or int(off_hour) < 24)
            and (off_minute is None or int(off_minute) < 60))


def _date(value: object, field: str = "date") -> str | None:
    """One date field as ISO ``YYYY-MM-DD``, or None when the field is empty.

    Raises :class:`UnreadableDate` for a value that matches no known format. A
    truncated string is never returned: a shape nobody has seen must stop the
    field, not enter the record looking like every other date. `field` names
    the source key, because six fields go through here and the value alone
    does not say which one failed.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise UnreadableDate(
            f"{field}: {value!r} is a {type(value).__name__}, and every date this "
            "endpoint has ever served is a string. A number here is a shape change "
            "in the source; reading it as an absent date hides that.")
    if not value.strip():
        return None
    text = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # The shape a modernising endpoint moves TO: `2025-12-20T00:00:00Z`. Matched
    # by pattern rather than by `fromisoformat`, which accepts a different set of
    # strings on every Python this package supports — 3.10 takes only
    # `isoformat()` output, 3.11 took basic form as well. A date field must not
    # read differently on two machines walking the same source.
    stamped = _ISO_STAMP.match(text)
    if stamped and _in_range(stamped):
        try:
            return datetime.strptime(stamped.group(1), "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError as exc:
            # `2025-02-30` has the shape of a date and is not one. A bare
            # ValueError here escaped `_record()`, which catches UnreadableDate
            # alone, reached the house handler, and abandoned every later bill
            # in that house over one field.
            raise UnreadableDate(
                f"{field}: {text!r} has the shape of an ISO timestamp and names a day "
                f"that does not exist ({exc}).") from exc
    raise UnreadableDate(
        f"{field}: {text!r} matches no date format this endpoint is known to serve "
        f"({', '.join(_DATE_FORMATS)}, or ISO 8601). It is not being truncated into "
        "the record: a field named for ISO that is not ISO compares against ISO "
        "bounds and matches nothing, with no error.")


#: Fields that differ between two runs of an unchanged record, and so cannot
#: take part in "have I already got this?".
#:
#: `documents` is here for a second reason. The digest is taken BEFORE the
#: documents are fetched, and the manifest row is written AFTER, so a digest
#: over `documents` never matches the row it produced: every run then re-emitted
#: every bill it had already downloaded. What the source asserts about a bill
#: is the catalogue record, and where its files landed locally is not part of
#: that assertion.
_VOLATILE = ("fetched_at", "probed_at", "documents")


def _fingerprint(record: dict) -> str:
    """A digest of what a record asserts, ignoring when it was fetched."""
    body = {k: v for k, v in record.items() if k not in _VOLATILE}
    return hashlib.sha1(
        json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:12]


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

    def load_documents(self) -> dict[str, dict]:
        """The document outcomes each stored key already carries.

        Document acquisition cannot ride on the catalogue fingerprint. A corpus
        pulled before `--download` existed carries rows whose fingerprint
        matches perfectly, so a dedup `continue` skipped every fetch and
        enabling the flag made no requests at all. A failed document is the
        same shape of problem: the catalogue has not changed, and the file
        still is not there.
        """
        stored: dict[str, dict] = {}
        if not self.manifest.exists():
            return stored
        for rec in self._rows():
            if rec.get("key") and rec.get("kind") == MANIFEST_KIND:
                stored[rec["key"]] = rec.get("documents") or {}
        return stored

    def load_seen(self) -> dict[str, str]:
        """Every key already on disk, mapped to the content it was written with.

        Key alone is not enough. A reader fix changes what a record SAYS while
        its key stays the same, so a key-only resume declares the corrected
        record already held and leaves the wrong value on disk. That is how the
        `DD/MM/YYYY` assent dates would have survived their own fix: the run
        that repairs them re-fetches the same bills under the same keys.
        """
        seen: dict[str, str] = {}
        if not self.manifest.exists():
            return seen
        for rec in self._rows():
            if rec.get("key") and rec.get("kind") == MANIFEST_KIND:
                seen[rec["key"]] = _fingerprint(rec)
        return seen

    def _rows(self) -> Iterator[dict]:
        """Each manifest line that is a JSON object.

        A line that is valid JSON but not an object — `null`, `123`, `[]` — is
        skipped rather than crashed on. `json.loads(line).get(...)` raises
        AttributeError there, which JSONDecodeError does not cover.
        """
        with self.manifest.open(encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    yield rec

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
        """One bill, with every date read as ISO.

        A date this reader cannot parse degrades THAT FIELD, not the record. The
        field goes to null, `fetch_status` becomes `parse_error`, and `error`
        names the field and the value. Discarding the record instead would throw
        away the bill's name, ministry, status and every file URL over one bad
        string — and if the source ever changes the shape of `billAssentedDate`,
        that is 3,576 bills stripped to a key. A bad unit degrades a result and
        never empties it, and one field is a unit.
        """
        now = _now_iso()
        unreadable: dict[str, object] = {}

        def date(key: str, field: str) -> str | None:
            try:
                return _date(raw.get(key), field)
            except UnreadableDate:
                unreadable[field] = raw.get(key)
                return None

        record = {
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
            "introduced_date": date("billIntroducedDate", "introduced_date"),
            "introduced_file": raw.get("billIntroducedFile"),
            "passed_ls_date": date("billPassedInLSDate", "passed_ls_date"),
            "passed_ls_file": raw.get("billPassedInLSFile"),
            "passed_rs_date": date("billPassedInRSDate", "passed_rs_date"),
            "passed_rs_file": raw.get("billPassedInRSFile"),
            "passed_both_houses_file": raw.get("billPassedInBothHousesFile"),
            "referred_to_committee_date": date(
                "referredToCommitteeDate", "referred_to_committee_date"),
            "report_presented_date": date("reportPresentedDate", "report_presented_date"),
            "report_file": raw.get("reportFile"),
            "act_no": raw.get("actNo"),
            "act_year": raw.get("actYear"),
            "assent_date": date("billAssentedDate", "assent_date"),
            "gazetted_file": raw.get("billGazettedFile"),
            "synopsis_file": raw.get("billSynopsisFile"),
            "errata_file": raw.get("errataFile"),
            "status": raw.get("status"),
            "fetch_status": "ok",
            "fetched_at": now,
            "probed_at": now,
        }
        if unreadable:
            # Every field, not the first two. One 250-character explanation per
            # field filled the 500-character budget after two, and the case this
            # exists for is a source-wide shape change where all six fail at
            # once — the run where knowing WHICH fields failed matters most.
            record["fetch_status"] = "parse_error"
            record["unreadable_fields"] = sorted(unreadable)
            pairs = ", ".join(f"{f}={unreadable[f]!r}" for f in sorted(unreadable))
            record["error"] = (
                f"unreadable date: {pairs}"[:400]
                + ". The field is null and this row is not `ok`; a null here means "
                  "unreadable, not absent.")
        return record

    def _status_record(self, house: str, *, fetch_status: str, error: str | None = None) -> dict:
        """A row that stands in for a whole house this run could not fetch."""
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

    def _document_path(self, record: dict, field: str) -> Path:
        """Where one document lands, derived from the bill rather than the URL.

        The source's filenames are not stable and not unique: they carry the
        upload timestamp and a human's spacing, as in
        ``As intro Tribunal8102026124202PM.pdf``. A path built from the bill's
        own house, year and number joins back to the record without parsing a
        filename, and stays put when the source renames its file.
        """
        stem = field[:-5] if field.endswith("_file") else field
        # `bill_key` falls back to a raw-record hash when the number is absent,
        # and the path has to fall back with it. Writing `unknown_intro.pdf`
        # for every numberless bill in one house and year meant the second such
        # bill found the first one's bytes already present, and its manifest
        # then claimed a different URL had produced them.
        number = record.get("bill_no")
        number = (safe_filename_segment(number) if number
                  else safe_filename_segment(record["key"].rsplit("|", 1)[-1]))
        year = safe_filename_segment(record.get("bill_year") or "unknown")
        return (self.out_dir / "documents" / record["house"] / str(year)
                / f"{number}_{stem}.pdf")

    def download_documents(self, record: dict) -> dict:
        """Fetch every document URL this bill carries. Returns the counts.

        Each field gets its own outcome under ``record["documents"]``, because
        a URL that 404s and a URL nobody attempted are different facts and a
        path alone cannot tell them apart.

        **A failed document never fails the bill.** 0.15.0 abandoned a whole
        house over one unreadable date, and 0.15.1 fixed that; the same rule
        holds here. The record keeps its name, its ministry, its dates and its
        other seven documents, and ``fetch_status`` stays ``ok``.
        """
        stats = {"fetched": 0, "failed": 0, "present": 0}
        documents: dict[str, dict] = {}
        for field in DOCUMENT_FIELDS:
            url = (record.get(field) or "").strip()
            if not url:
                continue
            dest = self._document_path(record, field)
            already = dest.exists() and dest.stat().st_size > 1000
            ok = download_file(self.session, url, dest, HEADERS)
            if ok:
                stats["present" if already else "fetched"] += 1
            else:
                stats["failed"] += 1
            documents[field] = {
                "url": url,
                "path": str(dest.relative_to(self.out_dir)) if ok else None,
                "status": "ok" if ok else "failed",
            }
            if self.sleep and not already:
                time.sleep(self.sleep)
        if documents:
            record["documents"] = documents
        return stats

    def probe(self, *, max_records: int | None = None, dry_run: bool = False,
              download: bool = False) -> list[dict]:
        seen = self.load_seen()
        held = self.load_documents() if download else {}
        out: list[dict] = []
        for house in self.houses:
            if dry_run:
                out.append(self._status_record(house, fetch_status="dry_run"))
                continue
            added = 0
            try:
                for raw in self.bills_all(house):
                    rec = self._record(raw, house)
                    digest = _fingerprint(rec)
                    unchanged = seen.get(rec["key"]) == digest
                    if unchanged and not download:
                        continue
                    if download:
                        self.download_documents(rec)
                        # The row is rewritten only when the documents moved.
                        # An unchanged catalogue row with the same outcomes has
                        # nothing new to record, and re-appending 9,929 of them
                        # every run would double the manifest before `compact`.
                        if unchanged and rec.get("documents", {}) == held.get(rec["key"], {}):
                            continue
                    seen[rec["key"]] = digest
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
        if not dry_run:
            # A planning run reports what a real one would do. Compaction rewrites
            # the file, and the state it rewrites — duplicate keys — is exactly what
            # an interrupted repair leaves, so an unguarded call let `--dry-run`
            # drop rows from a corpus it was only supposed to describe.
            self.compact()
        return out

    def compact(self) -> int:
        """Leave one row per key, the last, and return how many rows went.

        ARCHITECTURE.md states the contract this keeps: one schema-validated
        record per item. Every reader of the file — `Corpus.manifest_bills()`
        included — streams every line, so a corrected record appended beside the
        wrong one serves BOTH: the run that repairs the `DD/MM/YYYY` assent
        dates would double the catalogue and still hand out the old value.

        It scans for duplicates rather than trusting what THIS run rewrote,
        because a run killed between the append and the compaction leaves pairs
        behind, and resume then reads the newest row, finds the record
        unchanged, and rewrites nothing. Under the narrower rule that repair was
        unreachable: the duplicates were permanent and the stale value was
        served forever.

        The write is a temp file and a rename, which is atomic for readers.
        Rows this class did not write are copied through untouched, and they are
        not indexed either: duplicate keys are this probe's problem alone. A
        second writer appending to the same `manifest.jsonl` DURING this rewrite
        would still lose those rows, so run one probe per output directory.
        """
        if not self.manifest.exists():
            return 0
        with self.manifest.open(encoding="utf-8") as f:
            lines = f.readlines()
        rows: dict[str, list[int]] = {}
        for i, line in enumerate(lines):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Only this probe's own rows. Another adapter sharing the corpus may
            # keep several rows under one key deliberately — `prs_bill_track`
            # records Pending then Passed, and that pair IS the history it
            # exists to hold. Indexing every keyed object deleted it and
            # reported success.
            if (isinstance(rec, dict) and rec.get("key")
                    and rec.get("kind") == MANIFEST_KIND):
                rows.setdefault(rec["key"], []).append(i)
        drop = {i for indexes in rows.values() if len(indexes) > 1
                for i in indexes[:-1]}
        if not drop:
            return 0
        tmp = self.manifest.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            f.writelines(line for i, line in enumerate(lines) if i not in drop)
        tmp.replace(self.manifest)
        return len(drop)
