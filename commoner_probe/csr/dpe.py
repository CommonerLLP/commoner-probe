# SPDX-License-Identifier: MIT
from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from commoner_probe.http_client import USER_AGENT

BASE_URL = "https://dpe.gov.in/cms/wp-json"


class DpeCsrProbe:
    """Download DPE CPSE CSR documents via WordPress REST API.
    
    Target: https://dpe.gov.in/cms/wp-json
    """

    def __init__(
        self,
        out_dir: Path,
        *,
        sleep: float = 1.0,
        base_url: str = BASE_URL,
    ) -> None:
        self.out_dir = out_dir
        self.sleep = sleep
        self.base_url = base_url
        self.manifest = out_dir / "manifest.jsonl"

    def fetch_page(self, page: int, per_page: int = 100, search: str | None = "csr") -> list[dict[str, Any]]:
        params = {"page": page, "per_page": per_page}
        if search:
            params["search"] = search
            
        url = f"{self.base_url}/wp/v2/media?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status != 200:
                    return []
                body = resp.read().decode("utf-8")
                return json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code == 400:
                return []
            raise

    def _record(self, item: dict[str, Any], dest: Path) -> dict[str, Any]:
        source_url = item.get("source_url") or item.get("guid", {}).get("rendered")
        title = item.get("title", {}).get("rendered", "")
        item_id = item.get("id")
        date_str = item.get("date", "")
        
        return {
            "key": f"DPE_CSR|{item_id}",
            "kind": "dpe_csr_document",
            "record_type": "dpe_csr_document",
            "id": item_id,
            "date": date_str,
            "title": title,
            "filename": dest.name,
            "dest": str(dest),
            "url": source_url,
            "status": "pending",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "probed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    def download_item(self, item: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        source_url = item.get("source_url") or item.get("guid", {}).get("rendered")
        if not source_url:
            return {}

        parsed = urllib.parse.urlparse(source_url)
        filename = Path(parsed.path).name
        if not filename:
            filename = f"dpe_document_{item.get('id')}.pdf"
            
        filename = f"{item.get('id')}_{filename}"
        dest = self.out_dir / filename
        
        record = self._record(item, dest)

        if dest.exists():
            record["status"] = "skipped_exists"
            record["sha256"] = hashlib.sha256(dest.read_bytes()).hexdigest()
            return record

        if dry_run:
            record["status"] = "dry_run"
            return record

        req = urllib.request.Request(source_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read()

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        record["status"] = "downloaded"
        record["sha256"] = hashlib.sha256(body).hexdigest()
        
        if self.sleep:
            time.sleep(self.sleep)
            
        return record

    def append_manifest(self, record: dict[str, Any]) -> None:
        if not record:
            return
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def probe(self, search: str | None = "csr", dry_run: bool = False, max_pages: int = 10) -> list[dict[str, Any]]:
        records = []
        for page in range(1, max_pages + 1):
            items = self.fetch_page(page, search=search)
            if not items:
                break
            
            for item in items:
                record = self.download_item(item, dry_run=dry_run)
                if record:
                    if not dry_run:
                        self.append_manifest(record)
                    records.append(record)
                    
            if self.sleep:
                time.sleep(self.sleep)
                
        return records
