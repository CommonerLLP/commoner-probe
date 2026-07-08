# SPDX-License-Identifier: MIT
"""DoE "Annual Report on Pay and Allowances of Central Government
Civilian Employees" acquisition (doe.gov.in).

The listing page carries one table row per report year; the archive page
(``/archive/annual-report-pay-and-allowances``) is empty (views-empty,
verified live 2026-07-08), so the listing page is the whole universe.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

from .http_client import make_session
from .textparse import extract_pdf_text

DOE_LISTING_URL = "https://doe.gov.in/annual-report-pay-and-allowances"

# doe.gov.in's WAF resets back-to-back connections ("Connection reset by
# peer" on an immediate second request, verified live 2026-07-08); keep one
# session and a multi-second gap between requests.
DEFAULT_SLEEP = 3.0

# Some editions are flattened scans with no text layer (2022-23 verified
# live: pdftotext yields nothing). Below this many extracted characters the
# manifest records text_layer: false so downstream consumers know OCR is
# needed before parsing.
TEXT_LAYER_MIN_CHARS = 200

_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL)
_PDF_HREF_RE = re.compile(r'href="([^"]+\.pdf)"', re.IGNORECASE)
_YEAR_RE = re.compile(r"(20\d{2})\s*[-_– ]?\s*(?:20)?(\d{2})")
_TAG_RE = re.compile(r"<[^>]+>")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class DoePayAllowancesProbe:
    """Acquire the DoE Pay & Allowances annual-report series with provenance."""

    def __init__(
        self,
        out_dir: Path,
        *,
        sleep: float = DEFAULT_SLEEP,
        listing_url: str = DOE_LISTING_URL,
    ) -> None:
        self.out_dir = out_dir
        self.sleep = sleep
        self.listing_url = listing_url
        self.manifest = out_dir / "manifest.jsonl"
        self.session = make_session(rate_limit_sec=sleep)

    def parse_listing(self, html: str) -> list[dict[str, Any]]:
        reports: list[dict[str, Any]] = []
        seen_years: set[str] = set()
        for row_html in _TR_RE.findall(html):
            href_m = _PDF_HREF_RE.search(row_html)
            if not href_m:
                continue
            url = urljoin(self.listing_url, unescape(href_m.group(1)))
            if "annual_reports_documents" not in url:
                continue
            title = ""
            for cell in _TD_RE.findall(row_html):
                cell_text = re.sub(r"\s+", " ", unescape(_TAG_RE.sub(" ", cell))).strip()
                if re.search(r"pay", cell_text, re.IGNORECASE):
                    title = cell_text
                    break
            year_m = _YEAR_RE.search(title) or _YEAR_RE.search(unquote(url))
            if not year_m:
                continue
            year = f"{year_m.group(1)}-{year_m.group(2)}"
            if year in seen_years:
                continue
            seen_years.add(year)
            reports.append({"title": title, "year": year, "url": url})
        return reports

    def discover(self) -> list[dict[str, Any]]:
        r = self.session.get(self.listing_url, timeout=60)
        r.raise_for_status()
        return self.parse_listing(r.text)

    def _record(self, report: dict[str, Any], *, status: str) -> dict[str, Any]:
        now = _now()
        basename = Path(unquote(urlparse(report["url"]).path)).name
        filename = f"{report['year']}_" + re.sub(r"[^A-Za-z0-9._-]", "_", basename)
        dest = self.out_dir / filename
        return {
            "key": f"DOE_PAY_ALLOWANCES|{report['year']}",
            "kind": "doe_pay_allowances_report",
            "record_type": "doe_pay_allowances_report",
            "source_family": "doe-pay-allowances",
            "source_name": "doe-gov-in",
            "publisher": "Department of Expenditure, Ministry of Finance",
            "title": report["title"],
            "year": report["year"],
            "filename": filename,
            "dest": str(dest),
            "url": report["url"],
            "listing_url": self.listing_url,
            "status": status,
            "media_type": "application/pdf",
            "fetched_at": now,
            "probed_at": now,
        }

    def _finalize(self, record: dict[str, Any], dest: Path, body: bytes) -> None:
        record["sha256"] = hashlib.sha256(body).hexdigest()
        text = extract_pdf_text(dest)
        record["text_layer"] = len(text.strip()) >= TEXT_LAYER_MIN_CHARS

    def download_report(self, report: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
        record = self._record(report, status="dry_run" if dry_run else "pending")
        if dry_run:
            return record
        dest = Path(record["dest"])
        if dest.exists() and dest.stat().st_size > 1000:
            record["status"] = "skipped_exists"
            self._finalize(record, dest, dest.read_bytes())
            return record
        r = self.session.get(report["url"], timeout=180)
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
        reports = self.discover()
        if years:
            wanted = set(years)
            reports = [r for r in reports if r["year"] in wanted]
        records = [self.download_report(r, dry_run=dry_run) for r in reports]
        if not dry_run:
            for record in records:
                self.append_manifest(record)
        return records
