# SPDX-License-Identifier: MIT
from __future__ import annotations

import hashlib
import html
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from commoner_probe.ministry_pdf_index import SCHEME_FREE_USER_AGENT

# The www host is canonical. The bare dpe.gov.in/cms/wp-json answers 302 to it.
BASE_URL = "https://www.dpe.gov.in/cms/wp-json"

# www.dpe.gov.in answers 403 to a User-Agent that contains a URL scheme such as
# the project's default "(+https://github.com/...)". This is the same WAF
# behavior as mha.gov.in and indiabudget.gov.in. Verified live on
# 18 October 2026: the scheme-free form gets 200 for the API, the downloads,
# and robots.txt.
USER_AGENT = SCHEME_FREE_USER_AGENT

# DPE no longer uploads CSR files to the media library. As of 18 October 2026 it
# publishes them as "Document Repository" posts, and each post points to its PDF
# through the ACF `pdf` field, which holds a media ID.
POSTS_PATH = "central_documents"
# A post can carry a separate Hindi or combined file. Use the first one present.
_FILE_FIELDS = ("pdf", "pdf_both", "pdf_hindi")
# WordPress caps `per_page` at 100, which is also the most IDs one `include` returns.
_MAX_PER_PAGE = 100

# DPE has no CSR category, tag, or ACF field. Checked on 3 November 2026: the post
# type's only taxonomy is `cd_category` ("Repository Category"), whose terms name a
# document type, not a subject, and the ACF fields hold only title, file, language,
# and date. CSR documents therefore sit under four of DPE's own document types:
#   418 Guidelines, 400 reports, 428 Archive-Guideline, 434 office-order
# A free-text search also matches unrelated posts in other types, such as the NMDC
# CSR Foundation survey annexures (446, 448) and a vacancy note (399), so every
# request keeps only these four types.
CSR_CATEGORIES = (418, 400, 428, 434)
# Two searches, merged: DPE's long-form guidelines (category 428) say "Corporate
# Social Responsibility" and never "CSR". Checked on 3 November 2026: the two
# searches return 34 and 10 posts in these categories, 39 after de-duplication.
SEARCHES = ("csr", "corporate social responsibility")


def _is_media_id(value: Any) -> bool:
    """Return True for a positive integer ID; an empty ACF file field is false, 0, or null."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


class DpeCsrProbe:
    """Download DPE CPSE CSR documents via WordPress REST API.

    Target: https://www.dpe.gov.in/cms/wp-json (Document Repository posts)
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

    def _get_json(self, url: str) -> Any:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                return []
            return json.loads(resp.read().decode("utf-8"))

    def _file_urls(self, ids: list[int]) -> dict[int, str]:
        """Map media IDs to file URLs with one `include` request."""
        if not ids:
            return {}
        params = {"include": ",".join(str(i) for i in ids), "per_page": _MAX_PER_PAGE}
        media = self._get_json(f"{self.base_url}/wp/v2/media?{urllib.parse.urlencode(params)}")
        return {m["id"]: m["source_url"] for m in media if m.get("source_url")}

    def fetch_page(self, page: int, per_page: int = 100, search: str | None = "csr") -> list[dict[str, Any]]:
        """Return one page of one search's Document Repository posts, newest first.

        Only posts in `CSR_CATEGORIES` come back. Each post gets a `source_url`, the
        file that its ACF `pdf` field points to. A post without a file has no
        `source_url`.
        """
        params = {
            "page": page,
            "per_page": per_page,
            "orderby": "date",
            "order": "desc",
            "cd_category": ",".join(str(c) for c in CSR_CATEGORIES),
        }
        if search:
            params["search"] = search

        url = f"{self.base_url}/wp/v2/{POSTS_PATH}?{urllib.parse.urlencode(params)}"
        try:
            items = self._get_json(url)
        except urllib.error.HTTPError as e:
            # WordPress answers 400 for a page past the last one.
            if e.code == 400:
                return []
            raise

        # WordPress ignores a parameter it does not know, so check the types again here.
        items = [i for i in items if set(i.get("cd_category") or ()) & set(CSR_CATEGORIES)]

        file_ids = {}
        for item in items:
            acf = item.get("acf") or {}
            file_ids[item["id"]] = next((acf[f] for f in _FILE_FIELDS if _is_media_id(acf.get(f))), None)
        wanted = sorted({i for i in file_ids.values() if i})
        urls: dict[int, str] = {}
        for start in range(0, len(wanted), _MAX_PER_PAGE):
            urls.update(self._file_urls(wanted[start:start + _MAX_PER_PAGE]))
        for item in items:
            if file_ids[item["id"]] in urls:
                item["source_url"] = urls[file_ids[item["id"]]]
        return items

    def fetch_documents(
        self,
        per_page: int = 100,
        max_pages: int = 10,
        searches: Sequence[str] = SEARCHES,
    ) -> list[dict[str, Any]]:
        """Return the posts that any of `searches` finds, de-duplicated, newest first.

        Each search pages until it runs out or reaches `max_pages`. With `max_pages=1`
        the result is the newest `per_page` posts of the union, since each search
        returns its own newest first.
        """
        found: dict[Any, dict[str, Any]] = {}
        for search in searches:
            for page in range(1, max_pages + 1):
                items = self.fetch_page(page, per_page=per_page, search=search)
                if not items:
                    break
                for item in items:
                    found.setdefault(item["id"], item)
                if self.sleep:
                    time.sleep(self.sleep)
        merged = sorted(found.values(), key=lambda i: (i.get("date") or "", i["id"]), reverse=True)
        return merged[:per_page] if max_pages == 1 else merged

    @staticmethod
    def _title(item: dict[str, Any]) -> str:
        """Return the ACF `title` when it has text, else WordPress `title.rendered`.

        For some Document Repository posts `title.rendered` is a slug such as
        "7th-csr", while `acf.title` holds the readable title.
        """
        acf_title = (item.get("acf") or {}).get("title")
        if isinstance(acf_title, str):
            acf_title = html.unescape(acf_title).strip()
            if acf_title:
                return acf_title
        return item.get("title", {}).get("rendered", "")

    def _record(self, item: dict[str, Any], dest: Path) -> dict[str, Any]:
        source_url = item.get("source_url")
        title = self._title(item)
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
        source_url = item.get("source_url")
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

    def probe(
        self,
        searches: Sequence[str] = SEARCHES,
        dry_run: bool = False,
        max_pages: int = 10,
    ) -> list[dict[str, Any]]:
        records = []
        for item in self.fetch_documents(max_pages=max_pages, searches=searches):
            record = self.download_item(item, dry_run=dry_run)
            if record:
                if not dry_run:
                    self.append_manifest(record)
                records.append(record)

        return records
