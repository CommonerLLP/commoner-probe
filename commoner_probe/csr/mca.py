# SPDX-License-Identifier: MIT
from __future__ import annotations

import hashlib
import http.cookiejar
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from commoner_probe.http_client import USER_AGENT

BASE_URL = "https://www.mcacdm.nic.in"
CSR_PAGE_PATH = "/csr-data"
EXPORT_PATH = "/cdm/export.php"
CSR_PAGE_URL = f"{BASE_URL}{CSR_PAGE_PATH}"
EXPORT_URL = f"{BASE_URL}{EXPORT_PATH}"
CAPTCHA_VALUE = "COMMON"
EXPECTED_CSV_HEADER = (
    '"Company Name","Financial Year",PSU/Non-PSU,"CSR State",'
    '"CSR Development Sector","CSR Sub Development Sector",'
    '"Project Amount Spent (In INR Cr.)"'
)


class _CSRFParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.csrf_token: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "input":
            return
        attrs_dict = dict(attrs)
        if attrs_dict.get("name") in {"csrf_test_name", "csrf_token"}:
            self.csrf_token = attrs_dict.get("value")


def parse_csrf_token(html: str) -> str | None:
    parser = _CSRFParser()
    parser.feed(html)
    return parser.csrf_token


class McaCsrProbe:
    """Download MCA CSR company-wise raw data with manifest logging.

    The live MCA CDM CSR portal exposes a browser form at ``/csr-data`` and
    posts CSV downloads to ``/cdm/export.php``.
    """

    def __init__(
        self,
        out_dir: Path,
        *,
        sleep: float = 2.0,
        base_url: str = BASE_URL,
        source_page: str | None = None,
        export_url: str = EXPORT_URL,
    ) -> None:
        self.out_dir = out_dir
        self.sleep = sleep
        self.base_url = base_url
        self.source_page = source_page or urllib.parse.urljoin(base_url, CSR_PAGE_PATH)
        self.export_url = export_url
        self.manifest = out_dir / "manifest.jsonl"

    def init_session(self) -> tuple[urllib.request.OpenerDirector, str | None]:
        cookie_jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
        req = urllib.request.Request(self.source_page, headers={"User-Agent": USER_AGENT})
        with opener.open(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        return opener, parse_csrf_token(html)

    def _record(self, year: str, filename: str) -> dict:
        financial_year = _format_financial_year(year)
        return {
            "key": f"MCA_CSR|{financial_year}",
            "kind": "mca_csr_company_spend",
            "record_type": "mca_csr_company_spend",
            "year": year,
            "financial_year": financial_year,
            "filename": filename,
            "dest": str(self.out_dir / filename),
            "source_page": self.source_page,
            "url": self.export_url,
            "status": "pending",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "probed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    def download_year(
        self,
        opener: urllib.request.OpenerDirector | None,
        csrf_token: str | None,
        year: str,
        *,
        dry_run: bool,
    ) -> dict:
        filename = f"mca_csr_company_spend_{year}.csv"
        dest = self.out_dir / filename
        record = self._record(year, filename)

        if dest.exists():
            record["status"] = "skipped_exists"
            record["sha256"] = hashlib.sha256(dest.read_bytes()).hexdigest()
            return record

        if dry_run:
            record["status"] = "dry_run"
            return record

        if opener is None:
            raise ValueError("opener is required when dry_run is false")

        data = urllib.parse.urlencode({
            "csrf_token": csrf_token or "",
            "financialyear[]": _format_financial_year(year),
            "psunonpsu[]": "all",
            "csrstate[]": "all",
            "csrdevelopmentsector[]": "all",
            "captcha_input": CAPTCHA_VALUE,
            "captcha_hidden": CAPTCHA_VALUE,
            "export": "true",
        }).encode("utf-8")
        req = urllib.request.Request(
            self.export_url,
            data=data,
            headers={"User-Agent": USER_AGENT},
        )
        with opener.open(req, timeout=60) as resp:
            body = resp.read()

        stripped = body.strip().lower()
        if stripped.startswith(b"<!doctype html") or stripped.startswith(b"<html"):
            raise ValueError("received HTML page instead of CSV; MCA export endpoint may have changed")
        first_line = body.decode("utf-8-sig", errors="replace").splitlines()[0]
        if first_line != EXPECTED_CSV_HEADER:
            raise ValueError("received unexpected MCA CSR CSV header; source contract may have changed")

        dest.write_bytes(body)
        record["status"] = "downloaded"
        record["sha256"] = hashlib.sha256(body).hexdigest()
        if self.sleep:
            time.sleep(self.sleep)
        return record

    def append_manifest(self, record: dict) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def probe_years(self, years: list[str], *, dry_run: bool = False) -> list[dict]:
        if dry_run:
            opener, csrf_token = None, None
        else:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            opener, csrf_token = self.init_session()

        records = [
            self.download_year(opener, csrf_token, year, dry_run=dry_run)
            for year in years
        ]
        if not dry_run:
            for record in records:
                self.append_manifest(record)
        return records


def _format_financial_year(year: str) -> str:
    year = year.strip()
    if year.upper().startswith("FY "):
        return year
    return f"FY {year}"
