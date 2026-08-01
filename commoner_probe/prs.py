# SPDX-License-Identifier: MIT
"""PRS Legislative Research acquisition.

The licensing posture is internal-research-only: PRS pages carry an
"All Rights Reserved" footer and no usable ToS. Records are stamped
``source: prsindia.org`` so downstream consumers can segregate them and avoid
republication of PRS text.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from html import unescape
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urljoin

from .http_client import make_session

BASE_URL = "https://prsindia.org"
PRS_CRAWL_DELAY_SEC = 10.0

_CSV_DOWNLOAD_RE = re.compile(r"window\.open\('(/mptrack/download\?file_path=([^']+\.csv))'")

BILL_TRACK_PATH = "/billtrack"

_TAG_RE = re.compile(r"<[^>]+>")

# Bill Track is a Drupal Views listing: every bill is one `views-row` carrying a
# title anchor and a status span, with no pagination — the whole corpus (964
# bills, live 2026-07-25) renders in a single ~400KB page.
_BILL_ROW_RE = re.compile(r'<div class="views-row">(.*?)</div>\s*</div>', re.DOTALL)
_BILL_LINK_RE = re.compile(r'<a\s+href="([^"]+)"\s*>(.*?)</a>', re.DOTALL)
# The status CSS class is ALWAYS "status-pending" regardless of the real status
# (verified live: 964/964 rows carry it while the text spans Passed/Lapsed/
# Withdrawn/…). The class is decoration; only the text is data.
_BILL_STATUS_RE = re.compile(r'<span\s+class="status-[a-z-]*"\s*>(.*?)</span>', re.DOTALL)

# Report Summaries and Vital Stats are the SAME Drupal Views template — an
# `other_fields` block per publication holding an `<h3>` title anchor and a
# "Download" anchor to the PDF. One parser serves both surfaces; only the
# listing path and the record kind differ.
#
# Their rows carry inline style attributes BEFORE the class, so the literal
# `<div class="views-row">` that Bill Track matches finds nothing here. Anchor
# on the class attribute, not on a whole opening tag.
PUBLICATION_PATHS = {
    "report-summaries": "/policy/report-summaries",
    "vital-stats": "/policy/vital-stats",
}
_PUB_ROW_RE = re.compile(r'class="other_fields"(.*?)(?=class="other_fields"|\Z)', re.DOTALL)
_PUB_TITLE_RE = re.compile(r"<h3[^>]*>\s*<a\s+href=\"([^\"]+)\"[^>]*>(.*?)</a>", re.DOTALL)
# Anchored on the download_pdf container, not on "any .pdf href in the block".
# The last block runs to end-of-document, so a bare .pdf search would hand the
# final item whatever PDF the page footer happens to carry — a wrong URL is
# worse than a recorded absence.
_PUB_PDF_RE = re.compile(r'class="[^"]*download_pdf[^"]*"[^>]*>\s*<a\s+href="([^"]+)"', re.IGNORECASE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", unescape(value)).strip()
    return text or None


def _number(value: str | None) -> int | float | str | None:
    text = _clean(value)
    if text is None or text.upper() == "N/A":
        return None
    try:
        f = float(text)
    except ValueError:
        return text
    return int(f) if f.is_integer() else f


def _int(value: str | None) -> int | None:
    parsed = _number(value)
    return parsed if isinstance(parsed, int) else None


#: The Rajya Sabha MP Track CSV names 13 of its 27 columns differently from the
#: Lok Sabha one. Measured live 2026-07-29: RS 828 rows, LS-18 544 rows, only 14
#: column names shared. The identity column is ``mp_index`` rather than
#: ``mp_election_index``, the activity counts carry an ``ag_`` prefix, and
#: attendance is ``avg_attendance``. Mapped RS -> LS so one record builder serves
#: both houses; the names are disjoint, so the map is safe to apply either way.
_RS_COLUMN_ALIASES = {
    "mp_index": "mp_election_index",
    "ag_debates": "debates",
    "ag_private_member_bills": "private_member_bills",
    "ag_questions": "questions",
    "avg_attendance": "attendance",
    "ag_national_average_debate": "national_average_debate",
    "ag_national_average_pmb": "national_average_pmb",
    "ag_national_average_questions": "national_average_questions",
    "avg_attendance_national_average": "attendance_national_average",
    "ag_state_average_debate": "state_average_debate",
    "ag_state_average_pmb": "state_average_pmb",
    "ag_state_average_questions": "state_average_questions",
    "avg_attendance_state_average": "attendance_state_average",
}


def parse_mptrack_csv(text: str) -> list[dict[str, str]]:
    """Parse an MP Track CSV, normalizing the Rajya Sabha column names.

    Without the normalization the RS surface is a **silent no-op**: every row
    lacks ``mp_election_index``, so all 828 are dropped for want of a key and the
    command writes nothing and exits 0. The adapter was
    built and tested against the Lok Sabha CSV only, and the fixture inherited
    its column names, so no test could see it.
    """
    rows = list(csv.DictReader(StringIO(text)))
    return [
        {_RS_COLUMN_ALIASES.get(key, key): value for key, value in row.items()}
        for row in rows
    ]


def parse_bill_track(page_html: str, *, base_url: str = BASE_URL) -> list[dict[str, str]]:
    """Parse the Bill Track listing into one dict per bill.

    Returns ``{title, url, slug, status}``. The status comes from the span's
    TEXT, never its class: PRS renders ``class="status-pending"`` on every row
    regardless of the real status, so a class-driven parser would label all 964
    bills "Pending". Rows with no title anchor are skipped; a row with an empty
    status keeps ``status: ""`` rather than being dropped, since the bill is
    still real.
    """
    bills: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for block in _BILL_ROW_RE.findall(page_html):
        link = _BILL_LINK_RE.search(block)
        if not link:
            continue
        href = unescape(link.group(1).strip())
        title = _clean(_TAG_RE.sub(" ", link.group(2)))
        if not title:
            continue
        url = urljoin(base_url + "/", href)
        if url in seen_urls:
            continue
        seen_urls.add(url)
        status_m = _BILL_STATUS_RE.search(block)
        status = _clean(_TAG_RE.sub(" ", status_m.group(1))) if status_m else ""
        bills.append({
            "title": title,
            "url": url,
            "slug": href.rsplit("/", 1)[-1],
            "status": status or "",
        })
    return bills


def parse_publications(page_html: str, *, base_url: str = BASE_URL) -> list[dict[str, str]]:
    """Parse a Report Summaries or Vital Stats listing into one dict per item.

    Returns ``{title, url, slug, pdf_url}``. ``pdf_url`` is ``""`` when a row
    offers no download — that is a real state on this surface, and dropping
    those rows would silently shrink the corpus, so the item is kept and the
    absence recorded.

    Both listings render whole: 442 report summaries and 24 vital stats in one
    response each, with no pager markup, and ``?page=1`` returns the identical
    first row (verified live 2026-07-28). Nothing here paginates.
    """
    items: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for block in _PUB_ROW_RE.findall(page_html):
        title_m = _PUB_TITLE_RE.search(block)
        if not title_m:
            continue
        href = unescape(title_m.group(1).strip())
        title = _clean(_TAG_RE.sub(" ", title_m.group(2)))
        if not title:
            continue
        url = urljoin(base_url + "/", href)
        if url in seen_urls:
            continue
        seen_urls.add(url)
        pdf_m = _PUB_PDF_RE.search(block)
        items.append({
            "title": title,
            "url": url,
            "slug": href.rstrip("/").rsplit("/", 1)[-1],
            "pdf_url": urljoin(base_url + "/", unescape(pdf_m.group(1))) if pdf_m else "",
        })
    return items


def parse_mptrack_download(page_html: str) -> tuple[str, str]:
    match = _CSV_DOWNLOAD_RE.search(page_html)
    if not match:
        raise ValueError("PRS MP Track page did not expose a CSV download link")
    raw_path = unescape(match.group(1))
    file_path = unescape(match.group(2))
    # The page sometimes exposes file_path already percent-encoded (%2F
    # separators); decode first so quoting cannot double-encode to %252F.
    encoded_path = "/mptrack/download?file_path=" + quote(unquote(file_path), safe="/")
    return raw_path, encoded_path


class PrsProbe:
    """Acquire PRS MP Track CSV rows."""

    _TERMINAL = frozenset({"downloaded"})

    def __init__(
        self,
        out_dir: Path,
        *,
        sleep: float = PRS_CRAWL_DELAY_SEC,
        base_url: str = BASE_URL,
    ) -> None:
        self.out_dir = out_dir
        self.sleep = sleep
        self.base_url = base_url.rstrip("/")
        self.manifest = out_dir / "manifest.jsonl"
        self.csv_dir = out_dir / "csv" / "prs-mp-track"
        self.session = make_session(rate_limit_sec=sleep)

    def load_seen(self) -> dict[str, str]:
        seen: dict[str, str] = {}
        if not self.manifest.exists():
            return seen
        with self.manifest.open(encoding="utf-8") as f:
            for line in f:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("key"):
                    seen[record["key"]] = record.get("status", "")
        return seen

    def append_manifest(self, record: dict[str, Any]) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _get_text(self, url: str) -> str:
        r = self.session.get(url, timeout=60)
        r.raise_for_status()
        return r.text

    def _mptrack_page_slug(self, house: str, loksabha: int | None = None) -> str:
        if house == "rs":
            return "rajya-sabha"
        if loksabha is None:
            raise ValueError("loksabha is required for Lok Sabha MP Track")
        return f"{loksabha}-lok-sabha"

    def discover_mptrack_csv(self, house: str, loksabha: int | None = None) -> dict[str, str]:
        slug = self._mptrack_page_slug(house, loksabha)
        page_url = f"{self.base_url}/mptrack/{slug}"
        html = self._get_text(page_url)
        raw_path, encoded_path = parse_mptrack_download(html)
        return {
            "page_url": page_url,
            "csv_url": urljoin(self.base_url, encoded_path),
            "csv_path_raw": raw_path,
            "slug": slug,
        }

    def fetch_mptrack_csv(self, csv_url: str) -> bytes:
        r = self.session.get(csv_url, timeout=120)
        r.raise_for_status()
        return r.content

    def _record(
        self,
        row: dict[str, str],
        *,
        house: str,
        loksabha: int | None,
        source: dict[str, str],
        status: str,
        csv_path: str | None,
        csv_sha256: str | None,
    ) -> dict[str, Any]:
        mp_index = _clean(row.get("mp_election_index"))
        house_label = _clean(row.get("mp_house")) or ("Rajya Sabha" if house == "rs" else "Lok Sabha")
        return {
            "key": f"PRS_MP_TRACK|{house}|{loksabha or 'rs'}|{mp_index}",
            "kind": "prs_mp_track",
            "record_type": "prs_mp_track",
            "source": "prsindia.org",
            "source_page_url": source["page_url"],
            "csv_url": source["csv_url"],
            "csv_path": csv_path,
            "csv_sha256": csv_sha256,
            "house": house_label,
            "house_code": house,
            "loksabha": loksabha,
            "mp_election_index": _int(mp_index) if mp_index else None,
            "mp_name": _clean(row.get("mp_name")),
            "nature_membership": _clean(row.get("nature_membership")),
            "term_start_date_raw": _clean(row.get("term_start_date")),
            "term_end_date_raw": _clean(row.get("term_end_date")),
            "term": _clean(row.get("term")),
            "constituency": _clean(row.get("pc_name")),
            "state": _clean(row.get("state")),
            "party": _clean(row.get("mp_political_party")),
            "gender": _clean(row.get("mp_gender")),
            "educational_qualification": _clean(row.get("educational_qualification")),
            "educational_qualification_details": _clean(row.get("educational_qualification_details")),
            "age": _int(row.get("mp_age")),
            "debates": _number(row.get("debates")),
            "private_member_bills": _number(row.get("private_member_bills")),
            "questions": _number(row.get("questions")),
            "attendance": _number(row.get("attendance")),
            "mp_note": _clean(row.get("mp_note")),
            "national_average_debate": _number(row.get("national_average_debate")),
            "national_average_pmb": _number(row.get("national_average_pmb")),
            "national_average_questions": _number(row.get("national_average_questions")),
            "attendance_national_average": _number(row.get("attendance_national_average")),
            "state_average_debate": _number(row.get("state_average_debate")),
            "state_average_pmb": _number(row.get("state_average_pmb")),
            "state_average_questions": _number(row.get("state_average_questions")),
            "attendance_state_average": _number(row.get("attendance_state_average")),
            "status": status,
            "probed_at": _now(),
        }

    def seen_bill_status(self) -> dict[str, str]:
        """Prior ``bill_status`` per bill key, last row wins.

        Bill Track is a tracker, not an archive: a bill moves Pending → Passed.
        Resume therefore keys on the recorded STATUS, not mere presence, so a
        re-run appends exactly when a bill has actually moved.
        """
        seen: dict[str, str] = {}
        if not self.manifest.exists():
            return seen
        for line in self.manifest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("kind") == "prs_bill_track" and row.get("key"):
                seen[row["key"]] = row.get("bill_status") or ""
        return seen

    def _bill_record(self, bill: dict[str, str], *, page_url: str) -> dict[str, Any]:
        return {
            "key": f"PRS_BILL_TRACK|{bill['slug']}",
            "kind": "prs_bill_track",
            "record_type": "prs_bill_track",
            "source": "prsindia.org",
            "source_page_url": page_url,
            "title": bill["title"],
            "url": bill["url"],
            "slug": bill["slug"],
            "bill_status": bill["status"],
            "status": "metadata_only",
            "probed_at": _now(),
        }

    def probe_billtrack(
        self,
        *,
        max_records: int | None = None,
        dry_run: bool = False,
    ) -> list[dict[str, Any]]:
        """Acquire the Bill Track listing (one row per bill).

        The whole corpus renders in a single page with no pagination, so this
        is one request. Metadata only — the per-bill detail pages and their
        attached PDFs are a separate surface, not this slice.
        """
        page_url = f"{self.base_url}{BILL_TRACK_PATH}"
        bills = parse_bill_track(self._get_text(page_url), base_url=self.base_url)
        seen = self.seen_bill_status()
        records: list[dict[str, Any]] = []
        for bill in bills:
            record = self._bill_record(bill, page_url=page_url)
            if dry_run:
                records.append({**record, "status": "dry_run"})
            else:
                if seen.get(record["key"]) == record["bill_status"]:
                    continue
                self.append_manifest(record)
                records.append(record)
                seen[record["key"]] = record["bill_status"]
            if max_records is not None and len(records) >= max_records:
                break
        return records

    def _publication_record(
        self,
        item: dict[str, str],
        *,
        surface: str,
        page_url: str,
        status: str,
        pdf_path: str | None = None,
        pdf_sha256: str | None = None,
    ) -> dict[str, Any]:
        kind = "prs_report_summary" if surface == "report-summaries" else "prs_vital_stats"
        return {
            "key": f"PRS_{surface.upper().replace('-', '_')}|{item['slug']}",
            "kind": kind,
            "record_type": kind,
            "source": "prsindia.org",
            "surface": surface,
            "source_page_url": page_url,
            "title": item["title"],
            "url": item["url"],
            "slug": item["slug"],
            "pdf_url": item["pdf_url"] or None,
            "pdf_path": pdf_path,
            "pdf_sha256": pdf_sha256,
            "status": status,
            "probed_at": _now(),
        }

    def probe_publications(
        self,
        *,
        surface: str,
        max_records: int | None = None,
        download: bool = False,
        dry_run: bool = False,
    ) -> list[dict[str, Any]]:
        """Acquire the Report Summaries or Vital Stats listing.

        One request per surface — neither paginates. ``download`` also fetches
        each item's PDF; without it this is metadata only.

        A row whose PDF fetch fails is recorded with ``status: "error"`` and
        the reason, not skipped. A missing row and a row that could not be
        downloaded are different facts, and only one of them is about PRS.
        """
        if surface not in PUBLICATION_PATHS:
            raise ValueError(f"unknown PRS surface {surface!r}; expected one of {sorted(PUBLICATION_PATHS)}")
        page_url = f"{self.base_url}{PUBLICATION_PATHS[surface]}"
        items = parse_publications(self._get_text(page_url), base_url=self.base_url)
        seen = self.load_seen()
        pdf_dir = self.out_dir / "pdf" / f"prs-{surface}"
        records: list[dict[str, Any]] = []

        for item in items:
            record = self._publication_record(
                item, surface=surface, page_url=page_url, status="dry_run" if dry_run else "metadata_only"
            )
            if dry_run:
                records.append(record)
                if max_records is not None and len(records) >= max_records:
                    break
                continue

            prior = seen.get(record["key"])
            if prior in self._TERMINAL or (prior == "metadata_only" and not download):
                continue

            if download and item["pdf_url"]:
                if self.sleep:
                    time.sleep(self.sleep)
                try:
                    r = self.session.get(item["pdf_url"], timeout=120)
                    r.raise_for_status()
                    body = r.content
                    if not body.startswith(b"%PDF"):
                        raise ValueError("response is not a PDF (WAF interstitial?)")
                except Exception as exc:
                    record["status"] = "error"
                    record["error"] = f"{type(exc).__name__}: {exc}"
                else:
                    pdf_dir.mkdir(parents=True, exist_ok=True)
                    dest = pdf_dir / f"{item['slug']}.pdf"
                    dest.write_bytes(body)
                    record["pdf_path"] = str(dest.relative_to(self.out_dir))
                    record["pdf_sha256"] = hashlib.sha256(body).hexdigest()
                    record["status"] = "downloaded"

            self.append_manifest(record)
            records.append(record)
            seen[record["key"]] = record["status"]
            if max_records is not None and len(records) >= max_records:
                break
        return records

    def probe_mptrack(
        self,
        *,
        houses: list[str],
        loksabhas: list[int],
        max_records: int | None = None,
        download: bool = False,
        dry_run: bool = False,
    ) -> list[dict[str, Any]]:
        seen = self.load_seen()
        records: list[dict[str, Any]] = []
        targets: list[tuple[str, int | None]] = []
        if "ls" in houses:
            targets.extend(("ls", n) for n in loksabhas)
        if "rs" in houses:
            targets.append(("rs", None))

        for house, loksabha in targets:
            source = self.discover_mptrack_csv(house, loksabha)
            if dry_run:
                records.append({
                    "key": f"PRS_MP_TRACK|{house}|{loksabha or 'rs'}|_csv",
                    "house_code": house,
                    "loksabha": loksabha,
                    "source_page_url": source["page_url"],
                    "csv_url": source["csv_url"],
                    "status": "dry_run",
                })
                if self.sleep:
                    time.sleep(self.sleep)
                continue

            # Pace the page->CSV request pair here: the stdlib-fallback session
            # has no per-domain limiter, so the crawl delay must not depend on
            # the optional requests stack being installed.
            if self.sleep:
                time.sleep(self.sleep)
            body = self.fetch_mptrack_csv(source["csv_url"])
            digest = hashlib.sha256(body).hexdigest()
            csv_rel: str | None = None
            if download:
                self.csv_dir.mkdir(parents=True, exist_ok=True)
                dest = self.csv_dir / f"{source['slug']}.csv"
                dest.write_bytes(body)
                csv_rel = str(dest.relative_to(self.out_dir))
            text = body.decode("utf-8-sig")
            status = "downloaded" if download else "metadata_only"
            parsed = parse_mptrack_csv(text)
            dropped_no_index = 0
            for row in parsed:
                mp_index = _clean(row.get("mp_election_index"))
                if not mp_index:
                    dropped_no_index += 1
                    continue
                key = f"PRS_MP_TRACK|{house}|{loksabha or 'rs'}|{mp_index}"
                prior_status = seen.get(key)
                if prior_status in self._TERMINAL:
                    continue
                if prior_status == "metadata_only" and not download:
                    continue
                record = self._record(
                    row,
                    house=house,
                    loksabha=loksabha,
                    source=source,
                    status=status,
                    csv_path=csv_rel,
                    csv_sha256=digest if download else None,
                )
                self.append_manifest(record)
                records.append(record)
                seen[key] = status
                if max_records is not None and len(records) >= max_records:
                    return records
            # Two ways this CSV can be useless, and both must be loud. Exiting 0
            # with no output is what made it invisible for a day.
            #
            # Neither is a resume. A resume run HAS parsed rows — it writes
            # nothing because their keys are already terminal — so zero usable
            # rows never describes one, and the quiet path stays quiet.
            if not parsed:
                raise ValueError(
                    f"PRS MP Track ({house}{loksabha or ''}): the CSV returned no rows "
                    f"at all ({len(body)} bytes from {source['csv_url']}). Neither House "
                    "has zero members, so an empty or header-only CSV is a broken or "
                    "changed source, not an empty result."
                )
            if dropped_no_index == len(parsed):
                raise ValueError(
                    f"PRS MP Track ({house}{loksabha or ''}): parsed {len(parsed)} rows "
                    f"and none carried an identity column. Expected "
                    f"'mp_election_index' (Lok Sabha) or 'mp_index' (Rajya Sabha); "
                    f"got {sorted(parsed[0])[:6]}... The source contract may have "
                    "changed — see _RS_COLUMN_ALIASES."
                )
            if self.sleep:
                time.sleep(self.sleep)
        return records
