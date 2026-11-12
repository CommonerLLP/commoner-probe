# SPDX-License-Identifier: MIT
"""Discover dataset resources on a CKAN open-data portal through its Action API.

CONTEXT
=======
CKAN is open-source data-portal software. A CKAN portal serves each dataset
(a "package") with a list of resources, which are files, and answers
``/api/3/action/package_show?id=<slug>`` with that list as JSON. The first
live-verified use here is data.opencity.in, which Oorvani Foundation operates.
OpenCity is a mirror, not the publisher. The publisher of a dataset is its
``organization``, for example the Greater Bengaluru Authority or the Greater
Chennai Corporation, and every record carries that organization so a citation
names the right body.

**robots.txt and the Action API (owner decision A, confirmed November 12,
2026).** OpenCity's robots.txt disallows ``/api/`` and sets
``Crawl-Delay: 10``. The disallow line is CKAN's stock robots.txt, which keeps
search engines off the Action API. The Action API is the publisher's documented
machine interface, so :class:`CkanClient` calls ``/api/3/action/`` paths with
``respect_robots=False`` and nothing else. The robots check stays on for every
resource download. This follows the narrow exemptions in ``cdn_dashboard.py``
and ``statute_dspace.py``. The crawl delay is honoured: the session is built
with ``rate_limit_sec=CKAN_CRAWL_DELAY_SEC``. The alternative, parsing each
dataset's HTML page, is robots-clean and brittle.

Acquisition only. The probe lists resources and records where each file would
land. It does not read a figure from any of them.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.parse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from commoner_probe.base import _private_tmp_path, note_text_layer, safe_filename_segment
from commoner_probe.budget.probe import _MEDIA_TYPES, BUDGET_USER_AGENT
from commoner_probe.http_client import make_session, read_capped_response

#: OpenCity's robots.txt `Crawl-Delay`, in seconds. Applied to every request.
CKAN_CRAWL_DELAY_SEC = 10.0

_ACTION_PATH = "/api/3/action/"
_EXTENSION = re.compile(r"^\.[A-Za-z0-9]{1,8}$")
_FALLBACK_MEDIA_TYPE = "application/octet-stream"
#: A PDF whose extracted text is shorter than this has no usable text layer.
TEXT_LAYER_MIN_CHARS = 200


class CkanError(RuntimeError):
    """A CKAN call failed, or the portal answered `success: false`."""


@dataclass(frozen=True)
class CkanResource:
    """One file listed on a CKAN dataset."""

    portal: str  # scheme and host, no path
    dataset: str
    dataset_title: str
    organization: str | None
    license: str | None
    dataset_url: str
    resource_id: str
    resource_name: str | None
    format: str  # upper-cased; "" when the portal gives none
    url: str
    resource_last_modified: str | None  # CKAN `last_modified`, else `created`


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_package(payload: dict, *, portal: str, dataset: str) -> list[CkanResource]:
    """Read the resources out of a ``package_show`` response. Pure; no network.

    Raises :class:`CkanError` when the portal answered ``success: false`` or the
    payload has no ``result``. Resources without a ``url`` are skipped, since
    there is nothing to acquire.
    """
    if not isinstance(payload, dict) or not payload.get("success"):
        error = payload.get("error") if isinstance(payload, dict) else None
        raise CkanError(f"package {dataset!r}: portal answered success=false ({error!r})")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise CkanError(f"package {dataset!r}: response has no result")

    org = result.get("organization")
    organization = (org.get("title") or org.get("name")) if isinstance(org, dict) else None
    license_title = result.get("license_title") or None
    title = result.get("title") or dataset
    dataset_url = f"{portal}/dataset/{dataset}"

    resources: list[CkanResource] = []
    for raw in result.get("resources") or []:
        url = (raw.get("url") or "").strip()
        if not url:
            continue
        resources.append(
            CkanResource(
                portal=portal,
                dataset=dataset,
                dataset_title=title,
                organization=organization,
                license=license_title,
                dataset_url=dataset_url,
                resource_id=str(raw.get("id") or ""),
                resource_name=(raw.get("name") or None),
                format=(raw.get("format") or "").strip().upper(),
                url=url,
                resource_last_modified=raw.get("last_modified") or raw.get("created") or None,
            )
        )
    return resources


def filter_formats(resources: list[CkanResource], formats: set[str] | None) -> list[CkanResource]:
    """Keep resources whose upper-cased format is in `formats`; None keeps all."""
    if formats is None:
        return list(resources)
    wanted = {f.upper() for f in formats}
    return [r for r in resources if r.format.upper() in wanted]


def resource_filename(res: CkanResource) -> str:
    """A safe local filename: the resource name, plus the file's extension.

    The extension comes from the URL path when it has one, else from the
    format, else there is none. A name that already ends in the extension keeps
    it once.
    """
    stem = safe_filename_segment(res.resource_name or res.resource_id, collapse=True)
    suffix = Path(urllib.parse.urlparse(res.url).path).suffix
    if _EXTENSION.match(suffix):
        ext = suffix.lower()
    elif res.format:
        ext = "." + res.format.lower()
    else:
        return stem
    return stem if stem.lower().endswith(ext) else stem + ext


def media_type_for(res: CkanResource) -> str:
    """The media type implied by the filename's extension."""
    ext = Path(resource_filename(res)).suffix.lstrip(".").lower()
    if ext == "csv":
        return "text/csv"
    return _MEDIA_TYPES.get(ext, _FALLBACK_MEDIA_TYPE)


class CkanClient:
    """Calls a CKAN portal's Action API. Reads only; never downloads a resource."""

    def __init__(
        self,
        base_url: str,
        *,
        session=None,
        user_agent: str = BUDGET_USER_AGENT,
        sleep: float = CKAN_CRAWL_DELAY_SEC,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session or make_session(rate_limit_sec=sleep, user_agent=user_agent)

    def _get(self, action: str, params: dict[str, Any]) -> Any:
        url = f"{self.base_url}{_ACTION_PATH}{action}?{urllib.parse.urlencode(params)}"
        # respect_robots=False is deliberate and narrow: /api/3/action/ only.
        # See the module docstring, owner decision A.
        resp = self.session.get(url, timeout=90, respect_robots=False)
        status = getattr(resp, "status_code", 200)
        if status == 404:
            raise CkanError(f"not found (HTTP 404): {url}")
        if not 200 <= status < 300:
            raise CkanError(f"HTTP {status}: {url}")
        try:
            payload = json.loads(resp.text)
        except (TypeError, ValueError) as exc:
            raise CkanError(f"response is not JSON: {url}") from exc
        if not isinstance(payload, dict) or not payload.get("success"):
            error = payload.get("error") if isinstance(payload, dict) else None
            raise CkanError(f"portal answered success=false ({error!r}): {url}")
        return payload

    def package_show(self, dataset: str) -> dict:
        """The whole ``package_show`` payload for one dataset slug."""
        return self._get("package_show", {"id": dataset})

    def package_search(self, q: str, *, rows: int = 20) -> list[dict]:
        """The ``result.results`` list of a ``package_search`` query."""
        payload = self._get("package_search", {"q": q, "rows": rows})
        return (payload.get("result") or {}).get("results") or []


class CkanResourceProbe:
    """Discover, and later download, the resources of CKAN datasets."""

    def __init__(
        self,
        out_dir: Path,
        base_url: str,
        *,
        sleep: float = CKAN_CRAWL_DELAY_SEC,
        formats: set[str] | None = None,
        user_agent: str = BUDGET_USER_AGENT,
        session=None,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.base_url = base_url.rstrip("/")
        parsed = urllib.parse.urlparse(self.base_url)
        self.portal = f"{parsed.scheme}://{parsed.netloc}"
        self.host = (parsed.hostname or parsed.netloc).lower()
        self.sleep = sleep
        self.formats = formats
        self.manifest = self.out_dir / "manifest.jsonl"
        self.session = session or make_session(rate_limit_sec=sleep, user_agent=user_agent)
        self.client = CkanClient(
            self.base_url, session=self.session, user_agent=user_agent, sleep=sleep
        )

    def discover(self, dataset: str) -> list[dict]:
        """One `pending` record per kept resource of `dataset`.

        Two resources in a dataset can share a name, and so a filename. Each
        colliding filename gets its resource id before the extension, so no
        record's `dest` points at another record's file.
        """
        payload = self.client.package_show(dataset)
        resources = parse_package(payload, portal=self.portal, dataset=dataset)
        kept = filter_formats(resources, self.formats)
        names = [resource_filename(r) for r in kept]
        counts = Counter(n.casefold() for n in names)
        records = []
        for res, name in zip(kept, names, strict=True):
            if counts[name.casefold()] > 1:
                stem, ext = os.path.splitext(name)
                name = f"{stem}_{safe_filename_segment(res.resource_id, collapse=True)}{ext}"
            records.append(self._record(res, status="pending", filename=name))
        return records

    def _record(self, res: CkanResource, *, status: str, filename: str | None = None) -> dict:
        now = _now_iso()
        filename = filename or resource_filename(res)
        dest = self.out_dir / self.host / res.dataset / filename
        return {
            "key": f"CKAN|{self.host}|{res.dataset}|{res.resource_id}",
            "kind": "ckan_resource",
            "record_type": "ckan_resource",
            "source_family": "ckan",
            "portal": res.portal,
            "dataset": res.dataset,
            "dataset_title": res.dataset_title,
            "organization": res.organization,
            "license": res.license,
            "dataset_url": res.dataset_url,
            "resource_id": res.resource_id,
            "resource_name": res.resource_name,
            "format": res.format,
            "resource_last_modified": res.resource_last_modified,
            "filename": filename,
            "dest": str(dest),
            "url": res.url,
            "status": status,
            "media_type": media_type_for(res),
            "fetched_at": now,
            "probed_at": now,
        }

    def _error_record(self, dataset: str, exc: Exception) -> dict:
        """One `error` record for a dataset that could not be listed.

        No resource exists, so the required fields carry the dataset slug.
        """
        now = _now_iso()
        dataset_url = f"{self.portal}/dataset/{dataset}"
        return {
            "key": f"CKAN|{self.host}|{dataset}|{dataset}",
            "kind": "ckan_resource",
            "record_type": "ckan_resource",
            "source_family": "ckan",
            "portal": self.portal,
            "dataset": dataset,
            "dataset_title": dataset,
            "organization": None,
            "license": None,
            "dataset_url": dataset_url,
            "resource_id": dataset,
            "resource_name": None,
            "format": "",
            "resource_last_modified": None,
            "filename": dataset,
            "dest": str(self.out_dir / self.host / dataset),
            "url": dataset_url,
            "status": "error",
            "media_type": _FALLBACK_MEDIA_TYPE,
            "fetched_at": now,
            "probed_at": now,
            "error": str(exc),
        }

    @staticmethod
    def _finalize(record: dict, dest: Path, body: bytes) -> None:
        record["bytes"] = len(body)
        if record["media_type"] == "application/pdf" or dest.suffix.lower() == ".pdf":
            note_text_layer(record, dest, body, min_chars=TEXT_LAYER_MIN_CHARS)
        else:
            record["sha256"] = hashlib.sha256(body).hexdigest()

    def download(self, record: dict, *, dry_run: bool) -> dict:
        """Fetch one resource to `record["dest"]` and return the updated record.

        A dry run makes no request. A non-empty file already at `dest` is kept
        and recorded as `skipped_exists`. A failed fetch becomes an `error`
        record; it never raises. The download keeps the robots.txt check.
        """
        if dry_run:
            return {**record, "status": "dry_run"}
        record = dict(record)
        dest = Path(record["dest"])
        if dest.exists() and dest.stat().st_size > 0:
            record["status"] = "skipped_exists"
            self._finalize(record, dest, dest.read_bytes())
            return record
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = _private_tmp_path(dest)
        try:
            resp = self.session.get(record["url"], timeout=180, stream=True)
            status = getattr(resp, "status_code", 200)
            record["http_status"] = status
            resp.raise_for_status()
            body = read_capped_response(resp)
            tmp_path.write_bytes(body)
            os.replace(tmp_path, dest)
        except Exception as exc:  # noqa: BLE001 - one bad resource must not end the run
            record["status"] = "error"
            record["error"] = str(exc)
            return record
        finally:
            tmp_path.unlink(missing_ok=True)
        headers = getattr(resp, "headers", None) or {}
        content_type = headers.get("Content-Type")
        if content_type:
            record["media_type"] = content_type.split(";", 1)[0].strip()
        record["status"] = "downloaded"
        self._finalize(record, dest, body)
        return record

    def append_manifest(self, record: dict) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def probe(self, datasets: list[str], *, dry_run: bool = False) -> list[dict]:
        """Discover and download every kept resource of each dataset.

        A dataset that cannot be listed yields one `error` record; the other
        datasets still run. Records are appended to `manifest.jsonl` unless
        `dry_run`.
        """
        records: list[dict] = []
        for dataset in datasets:
            try:
                found = self.discover(dataset)
            except CkanError as exc:
                records.append(self._error_record(dataset, exc))
                continue
            records.extend(self.download(r, dry_run=dry_run) for r in found)
        if not dry_run:
            for record in records:
                self.append_manifest(record)
        return records
