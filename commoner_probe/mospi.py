# SPDX-License-Identifier: MIT
"""MoSPI eSankhyiki API client — reusable statistical-data acquisition.

The eSankhyiki portal (https://esankhyiki.mospi.gov.in) fronts a REST
backend at ``https://api.mospi.gov.in/api`` with one route family per
dataset. Route names are heterogeneous (``getUdiseRecords`` vs
``getData`` vs ``getAsiData``) — the registry below carries the
live-verified paths per dataset.

Contract (live-verified 2026-07-13 via India-region egress):

- Indicator lists:  ``{"data": [{"indicator_code": N, "description"|"label": ...}]}``
- Filter lists:     ``{"data": {"<filter>": [{...id/label...}], ...}}``
- Data:             ``{"data": [<tidy row dicts>], "meta_data": {"page",
  "totalRecords", "totalPages", "recordPerPage"}}`` — paginated via
  ``page`` and ``limit`` query params (default 10 rows/page). Some data
  routes (e.g. PLFS ``getData``) return everything in one response with
  no ``meta_data``.
- Filters are passed as ``<name>_code`` query params (``state_code=8``
  is Gujarat, ``99`` All-India in PLFS, ``37`` All-India in AISHE);
  omitting ``state_code`` returns all states. Codes are dataset-specific
  and must be read from the filter endpoint, never guessed.
- Unknown routes return the docs SPA's HTML (HTTP 200), NOT a JSON
  error — the client treats a non-JSON body as "no such endpoint".

**Egress**: ``api.mospi.gov.in`` is TCP-blocked from at least some
non-India network paths (DNS resolves; the connection times out). Run
from an India-egress host, or set ``HTTPS_PROXY=socks5h://...`` to an
India-region SOCKS relay — the client uses ``requests``, which honours
proxy environment variables.
"""

from __future__ import annotations

import csv
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .http_client import make_session

API_BASE = "https://api.mospi.gov.in/api"


@dataclass(frozen=True)
class DatasetRoutes:
    """Live-verified route family for one eSankhyiki dataset."""

    indicators: str | None
    filters: str
    data: str
    #: Query params the API requires on every call for this dataset.
    notes: str = ""


#: Route registry. Every path was verified against the live API
#: (2026-07-13); datasets whose routes have not been verified are not
#: registered — extend by probing, not guessing (unknown routes 200
#: with HTML).
DATASETS: dict[str, DatasetRoutes] = {
    "PLFS": DatasetRoutes(
        indicators="/plfs/getIndicatorListByFrequency",
        filters="/plfs/getFilterByIndicatorId",
        data="/plfs/getData",
        notes="requires frequencyCode (1=Annual incl. wages, 2=Quarterly, 3=Monthly)",
    ),
    "UDISE": DatasetRoutes(
        indicators="/udise/getIndicatorList",
        filters="/udise/getUdiseFilterByIndicatorId",
        data="/udise/getUdiseRecords",
    ),
    "AISHE": DatasetRoutes(
        indicators="/aishe/getAisheIndicatorList",
        filters="/aishe/getAisheFilterByIndicatorId",
        data="/aishe/getAisheRecords",
    ),
    "HCES": DatasetRoutes(
        indicators="/hces/getHcesIndicatorList",
        filters="/hces/getHcesFilterByIndicatorId",
        data="/hces/getHcesRecords",
    ),
    "NAS": DatasetRoutes(
        indicators=None,  # list route exists but rejects all probed param sets
        filters="/nas/getNasFilterByIndicatorId",
        data="/nas/getNasData",
        notes="requires base_year (2022-23|2011-12); filters also need series + frequency_code",
    ),
    "ASI": DatasetRoutes(
        indicators=None,  # ASI discovery goes through its filter route
        filters="/asi/getAsiFilter",
        data="/asi/getAsiData",
        notes="requires classification_year (2008|2004|1998|1987)",
    ),
}


class MospiApiError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class MospiClient:
    """Thin typed wrapper over the eSankhyiki REST routes."""

    def __init__(self, *, sleep: float = 0.5, session=None) -> None:
        self.session = session or make_session()
        self.sleep = sleep

    def _get_json(self, path: str, params: dict[str, Any]) -> dict:
        url = f"{API_BASE}{path}"
        r = self.session.get(url, params=params, timeout=60)
        r.raise_for_status()
        try:
            payload = r.json()
        except ValueError as exc:
            # Unknown routes return the docs SPA's HTML with HTTP 200.
            raise MospiApiError(
                f"{path} did not return JSON — the route does not exist "
                "on the API (unknown paths 200 with an HTML docs page)"
            ) from exc
        if isinstance(payload, dict) and payload.get("error"):
            raise MospiApiError(f"{path}: {payload['error']}")
        return payload

    @staticmethod
    def _routes(dataset: str) -> DatasetRoutes:
        try:
            return DATASETS[dataset.upper()]
        except KeyError:
            raise MospiApiError(
                f"unknown dataset {dataset!r} — registered: {', '.join(sorted(DATASETS))}"
            ) from None

    def list_datasets(self) -> list[str]:
        return sorted(DATASETS)

    def indicators(self, dataset: str, **params: Any) -> list[dict]:
        routes = self._routes(dataset)
        if not routes.indicators:
            raise MospiApiError(
                f"{dataset} has no verified indicator-list route ({routes.notes})"
            )
        payload = self._get_json(routes.indicators, params)
        return payload.get("data") or []

    def filters(self, dataset: str, **params: Any) -> dict:
        routes = self._routes(dataset)
        payload = self._get_json(routes.filters, params)
        return payload.get("data") or {}

    def pull(
        self,
        dataset: str,
        params: dict[str, Any] | None = None,
        *,
        page_size: int = 500,
        max_rows: int | None = None,
    ) -> Iterator[dict]:
        """Stream tidy rows for one data query, following pagination.

        ``params`` are passed to the dataset's data route as-is (filter
        codes from :meth:`filters`). Omitting ``state_code`` returns all
        states in one query.
        """
        routes = self._routes(dataset)
        base_params = dict(params or {})
        page = 1
        yielded = 0
        while True:
            payload = self._get_json(
                routes.data, {**base_params, "page": page, "limit": page_size}
            )
            rows = payload.get("data") or []
            for row in rows:
                yield row
                yielded += 1
                if max_rows is not None and yielded >= max_rows:
                    return
            meta = payload.get("meta_data") or {}
            total_pages = meta.get("totalPages")
            if not total_pages or page >= total_pages or not rows:
                return
            page += 1
            time.sleep(self.sleep)


class MospiProbe:
    """Acquire eSankhyiki pulls into a provenance-manifested corpus.

    Each pull lands as one tidy CSV under ``csv/`` plus one
    ``manifest.jsonl`` row carrying the endpoint, exact query params,
    row count, sha256 of the CSV, and fetch timestamp.
    """

    def __init__(self, out_dir: Path, *, sleep: float = 0.5, session=None) -> None:
        self.out_dir = Path(out_dir)
        self.client = MospiClient(sleep=sleep, session=session)
        self.manifest = self.out_dir / "manifest.jsonl"

    def _append_manifest(self, rec: dict) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    @staticmethod
    def _params_slug(params: dict[str, Any]) -> str:
        blob = json.dumps(params, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]

    def pull_to_csv(
        self,
        dataset: str,
        params: dict[str, Any] | None = None,
        *,
        max_rows: int | None = None,
    ) -> dict:
        """Run one pull, write the CSV, append and return the manifest row."""
        dataset = dataset.upper()
        params = dict(params or {})
        rows = list(self.client.pull(dataset, params, max_rows=max_rows))
        slug = self._params_slug(params)
        csv_rel = Path("csv") / f"{dataset.lower()}_{slug}.csv"
        csv_path = self.out_dir / csv_rel
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames: list[str] = []
        for row in rows:
            for k in row:
                if k not in fieldnames:
                    fieldnames.append(k)
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        sha = hashlib.sha256(csv_path.read_bytes()).hexdigest()
        record = {
            "key": f"MOSPI|{dataset}|{slug}",
            "kind": "mospi_pull",
            "record_type": "mospi_pull",
            "source": "api.mospi.gov.in",
            "dataset": dataset,
            "endpoint": DATASETS[dataset].data,
            "params": params,
            "rows": len(rows),
            "csv_path": str(csv_rel),
            "sha256": sha,
            "truncated": bool(max_rows is not None and len(rows) >= max_rows),
            "fetched_at": _now(),
            "probed_at": _now(),
        }
        self._append_manifest(record)
        return record

    def dump_all(
        self,
        dataset: str,
        indicator_param: dict[str, Any],
        *,
        years: list[str] | None = None,
        max_rows_per_pull: int | None = None,
    ) -> list[dict]:
        """Exhaustive dump: one pull per year (all states per pull).

        ``indicator_param`` carries the indicator selection and any
        dataset-required params (e.g. ``{"indicator_code": 41}`` or
        ``{"indicator_code": 6, "frequencyCode": 1}``). Years default to
        every year the filter endpoint offers for that selection.
        """
        dataset = dataset.upper()
        if years is None:
            filters = self.client.filters(dataset, **indicator_param)
            year_entries = filters.get("year") or []
            years = []
            for e in year_entries:
                value = e.get("id") or e.get("year") or e.get("label")
                if value is not None:
                    years.append(str(value))
            if not years:
                raise MospiApiError(
                    f"{dataset}: filter endpoint returned no years for {indicator_param}"
                )
        records = []
        for year in years:
            records.append(
                self.pull_to_csv(
                    dataset,
                    {**indicator_param, "year": year},
                    max_rows=max_rows_per_pull,
                )
            )
            time.sleep(self.client.sleep)
        return records
