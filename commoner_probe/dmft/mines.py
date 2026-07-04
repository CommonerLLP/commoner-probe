# SPDX-License-Identifier: MIT
from __future__ import annotations

import hashlib
import json
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from commoner_probe.http_client import USER_AGENT

MINES_BASE_URL = "https://mines.gov.in/webportal"
ODISHA_BASE_URL = "https://dmf.odisha.gov.in"


@dataclass(frozen=True)
class DmftEndpoint:
    source_name: str
    publisher: str
    endpoint_kind: str
    filename: str
    url: str
    media_type: str
    period_kind: str = "source_snapshot"
    data_period: str | None = None


MINISTRY_ENDPOINTS: tuple[DmftEndpoint, ...] = (
    DmftEndpoint(
        source_name="mines-gov-in",
        publisher="Ministry of Mines",
        endpoint_kind="static_csv",
        filename="DMF_Collection.csv",
        url=f"{MINES_BASE_URL}/assets/img/DMF_Collection.csv",
        media_type="text/csv",
        period_kind="cumulative_snapshot",
    ),
    DmftEndpoint(
        source_name="mines-gov-in",
        publisher="Ministry of Mines",
        endpoint_kind="static_csv",
        filename="Project_Fund_Status_Detail.csv",
        url=f"{MINES_BASE_URL}/assets/img/Project_Fund_Status_Detail.csv",
        media_type="text/csv",
        period_kind="cumulative_snapshot",
    ),
    DmftEndpoint(
        source_name="mines-gov-in",
        publisher="Ministry of Mines",
        endpoint_kind="static_csv",
        filename="Sector_Wise_Project_Fund_Allocation.csv",
        url=f"{MINES_BASE_URL}/assets/img/Sector_Wise_Project_Fund_Allocation.csv",
        media_type="text/csv",
        period_kind="cumulative_snapshot",
    ),
    DmftEndpoint(
        source_name="mines-gov-in",
        publisher="Ministry of Mines",
        endpoint_kind="static_csv",
        filename="State_wise_Project_Details.csv",
        url=f"{MINES_BASE_URL}/assets/img/State_wise_Project_Details.csv",
        media_type="text/csv",
        period_kind="cumulative_snapshot",
    ),
)

ODISHA_ENDPOINTS: tuple[DmftEndpoint, ...] = (
    DmftEndpoint(
        source_name="odisha-dmf",
        publisher="District Mineral Foundation, Government of Odisha",
        endpoint_kind="static_json",
        filename="state_summary_data.json",
        url=f"{ODISHA_BASE_URL}/assets/cron_files/state_summary_data.json",
        media_type="application/json",
        period_kind="source_snapshot",
    ),
    DmftEndpoint(
        source_name="odisha-dmf",
        publisher="District Mineral Foundation, Government of Odisha",
        endpoint_kind="static_json",
        filename="district_summary_data.json",
        url=f"{ODISHA_BASE_URL}/assets/cron_files/district_summary_data.json",
        media_type="application/json",
        period_kind="source_snapshot",
    ),
    DmftEndpoint(
        source_name="odisha-dmf",
        publisher="District Mineral Foundation, Government of Odisha",
        endpoint_kind="report_page",
        filename="report/fund_collection_report.html",
        url=f"{ODISHA_BASE_URL}/report/fund_collection_report",
        media_type="text/html",
        period_kind="source_snapshot",
    ),
    DmftEndpoint(
        source_name="odisha-dmf",
        publisher="District Mineral Foundation, Government of Odisha",
        endpoint_kind="report_page",
        filename="report/allocation_report.html",
        url=f"{ODISHA_BASE_URL}/report/allocation_report",
        media_type="text/html",
        period_kind="source_snapshot",
    ),
    DmftEndpoint(
        source_name="odisha-dmf",
        publisher="District Mineral Foundation, Government of Odisha",
        endpoint_kind="report_page",
        filename="report/sector_wise_summary_report.html",
        url=f"{ODISHA_BASE_URL}/report/sector_wise_summary_report",
        media_type="text/html",
        period_kind="source_snapshot",
    ),
)


def _http_date_to_iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return value
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class MinesDmftProbe:
    """Acquire raw Ministry of Mines / DMFT disclosure files with provenance."""

    def __init__(
        self,
        out_dir: Path,
        *,
        sleep: float = 1.0,
        ministry_endpoints: list[str] | None = None,
        odisha_endpoints: list[str] | None = None,
    ) -> None:
        self.out_dir = out_dir
        self.sleep = sleep
        self.manifest = out_dir / "manifest.jsonl"
        self._ministry_filter = set(ministry_endpoints or [])
        self._odisha_filter = set(odisha_endpoints or [])

    def _build_opener(self) -> urllib.request.OpenerDirector:
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            # Lower security level for legacy gov servers
            ctx.set_ciphers("DEFAULT@SECLEVEL=1")
            ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
        except Exception:
            pass
        return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))

    def endpoints_for(self, sources: list[str]) -> list[DmftEndpoint]:
        endpoints: list[DmftEndpoint] = []
        if "mines-gov-in" in sources:
            endpoints.extend(
                e for e in MINISTRY_ENDPOINTS
                if not self._ministry_filter or e.filename in self._ministry_filter
            )
        if "odisha" in sources or "odisha-dmf" in sources:
            endpoints.extend(
                e for e in ODISHA_ENDPOINTS
                if not self._odisha_filter or e.filename in self._odisha_filter
            )
        return endpoints

    def _record(self, endpoint: DmftEndpoint, *, status: str) -> dict:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        dest = self.out_dir / endpoint.source_name / endpoint.filename
        return {
            "key": f"MINES_DMFT|{endpoint.source_name}|{endpoint.filename}",
            "kind": "mines_dmft_source_file",
            "record_type": "mines_dmft_source_file",
            "source_family": "mines-dmft",
            "source_name": endpoint.source_name,
            "publisher": endpoint.publisher,
            "endpoint_kind": endpoint.endpoint_kind,
            "filename": endpoint.filename,
            "dest": str(dest),
            "url": endpoint.url,
            "status": status,
            "media_type": endpoint.media_type,
            "period_kind": endpoint.period_kind,
            "data_period": endpoint.data_period,
            "fetched_at": now,
            "probed_at": now,
        }

    def download_endpoint(
        self,
        opener: urllib.request.OpenerDirector,
        endpoint: DmftEndpoint,
        *,
        dry_run: bool,
    ) -> dict:
        record = self._record(endpoint, status="dry_run" if dry_run else "pending")
        dest = Path(record["dest"])

        if dry_run:
            return record

        if dest.exists():
            body = dest.read_bytes()
            record["status"] = "skipped_exists"
            record["sha256"] = hashlib.sha256(body).hexdigest()
            return record

        req = urllib.request.Request(endpoint.url, headers={"User-Agent": USER_AGENT})
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                with opener.open(req, timeout=120) as resp:
                    body = resp.read()
                    source_last_modified_raw = resp.headers.get("Last-Modified")
                    content_type = resp.headers.get("Content-Type")
                break
            except Exception as e:
                import logging
                logging.warning(f"Attempt {attempt+1} failed for {endpoint.url}: {e}")
                if attempt == max_retries - 1:
                    raise
                time.sleep(2 ** attempt)

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        record["status"] = "downloaded"
        record["sha256"] = hashlib.sha256(body).hexdigest()
        if content_type:
            record["media_type"] = content_type.split(";", 1)[0].strip()
        if source_last_modified_raw:
            record["source_last_modified_raw"] = source_last_modified_raw
            record["source_last_modified"] = _http_date_to_iso(source_last_modified_raw)
        if self.sleep:
            time.sleep(self.sleep)
        return record

    def append_manifest(self, record: dict) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def probe_sources(self, sources: list[str], *, dry_run: bool = False) -> list[dict]:
        endpoints = self.endpoints_for(sources)
        opener = self._build_opener()
        records = [
            self.download_endpoint(opener, endpoint, dry_run=dry_run)
            for endpoint in endpoints
        ]
        if not dry_run:
            for record in records:
                self.append_manifest(record)
        return records
