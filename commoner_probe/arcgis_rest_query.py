# SPDX-License-Identifier: MIT
"""Query an Esri ArcGIS REST layer and return every feature as GeoJSON.

CONTEXT
=======
A FeatureServer or MapServer layer answers ``<layer>/query``. Two behaviours
matter:

- A server caps one response. When more features match, it sets
  ``exceededTransferLimit`` to true. In GeoJSON output the flag sits at the top
  level or inside ``properties``, depending on the server. A caller that ignores
  it holds a silent partial set. This adapter pages with ``resultOffset`` and
  ``resultRecordCount`` until the flag is absent, and never returns a partial
  set.
- ArcGIS reports an error as HTTP 200 with a body ``{"error": {...}}``. A layer
  that does not support pagination answers this way to ``resultOffset``. Both
  cases raise :class:`ArcGISError`.

A query is a live snapshot. Every call fetches fresh into a new run directory;
nothing is skipped because an earlier run exists.

Acquisition only. Each page is stored as received, with a sha256 in the
manifest. The adapter does not read a figure from any feature.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from commoner_probe.base import _private_tmp_path
from commoner_probe.http_client import get_capped

_ENVELOPE_KEYS = ("xmin", "ymin", "xmax", "ymax")


class ArcGISError(RuntimeError):
    """The server returned an error object, a non-GeoJSON body, or an endless empty page."""


def _truncated(page: dict) -> bool:
    props = page.get("properties")
    inner = props.get("exceededTransferLimit") if isinstance(props, dict) else None
    return bool(page.get("exceededTransferLimit") or inner)


def _params(
    where: str,
    out_fields: str,
    geometry: dict | None,
    return_geometry: bool,
    out_sr: int,
    offset: int,
    page_size: int,
) -> dict[str, str]:
    params = {
        "f": "geojson",
        "where": where,
        "outFields": out_fields,
        "returnGeometry": "true" if return_geometry else "false",
        "outSR": str(out_sr),
        "resultOffset": str(offset),
        "resultRecordCount": str(page_size),
    }
    if geometry is not None:
        missing = [k for k in _ENVELOPE_KEYS if k not in geometry]
        if missing:
            raise ValueError(f"geometry must be an envelope with xmin, ymin, xmax, ymax; missing {missing}")
        params["geometry"] = ",".join(str(geometry[k]) for k in _ENVELOPE_KEYS)
        params["geometryType"] = "esriGeometryEnvelope"
        params["inSR"] = str(geometry.get("inSR", 4326))
        params["spatialRel"] = "esriSpatialRelIntersects"
    return params


def query_layer(
    session: Any,
    layer_url: str,
    *,
    where: str = "1=1",
    out_fields: str = "*",
    geometry: dict | None = None,
    return_geometry: bool = True,
    out_sr: int = 4326,
    page_size: int = 2000,
    out_dir: Path,
    max_bytes: int = 200_000_000,
    max_pages: int = 1000,
) -> dict:
    """Return the merged GeoJSON FeatureCollection for one layer query.

    ``geometry`` is an envelope ``{"xmin", "ymin", "xmax", "ymax"}`` in WGS84
    (or in ``geometry["inSR"]``). Each run writes its pages to its own
    subdirectory ``out_dir/run-<UTC time>/page-NNNN.geojson``, created when the
    first page is stored, so a rerun never overwrites an earlier page and an
    invalid query leaves no empty directory. The per-page records are appended to
    ``out_dir/manifest.jsonl`` only after the last page succeeds; a failed run
    leaves no record. Raises :class:`ArcGISError` on an error object (including
    a layer without pagination support), on a page identical to the one before
    it (a server that ignores ``resultOffset``), and after ``max_pages`` pages.
    Never returns a partial set. Besides ``type`` and ``features`` the result
    carries ``records``, the list of page manifest records as appended to
    ``manifest.jsonl``, and ``pages``, which is ``len(records)``.
    """
    out_dir = Path(out_dir)
    run_dir: Path | None = None
    records: list[dict] = []
    previous_sha = None
    base = layer_url.rstrip("/") + "/query"
    features: list[dict] = []
    offset = 0
    page_no = 0
    while True:
        if page_no >= max_pages:
            raise ArcGISError(f"more than {max_pages} pages for {layer_url}; raise max_pages if intended")
        query = urllib.parse.urlencode(
            _params(where, out_fields, geometry, return_geometry, out_sr, offset, page_size)
        )
        url = f"{base}?{query}"
        body = get_capped(session, url, max_bytes=max_bytes, timeout=120)
        arrived = datetime.now(timezone.utc)
        try:
            page = json.loads(body)
        except ValueError as exc:
            raise ArcGISError(f"response is not JSON: {url}") from exc
        if not isinstance(page, dict):
            raise ArcGISError(f"unexpected response shape: {url}")
        if "error" in page:
            err = page["error"]
            msg = err.get("message") if isinstance(err, dict) else err
            raise ArcGISError(f"ArcGIS error from {url}: {msg}")
        page_features = page.get("features")
        if not isinstance(page_features, list):
            raise ArcGISError(f"response has no features list: {url}")
        sha = hashlib.sha256(body).hexdigest()
        if sha == previous_sha:
            raise ArcGISError(f"the server repeated a page; it is ignoring resultOffset: {url}")
        previous_sha = sha
        more = _truncated(page)
        if more and not page_features:
            raise ArcGISError(f"transfer limit exceeded but the page has no features: {url}")

        page_no += 1
        if run_dir is None:
            # First stored page: parameters are valid and the server answered.
            run_dir = out_dir / arrived.strftime("run-%Y%m%dT%H%M%S%fZ")
            run_dir.mkdir(parents=True, exist_ok=True)
        dest = run_dir / f"page-{page_no:04d}.geojson"
        tmp = _private_tmp_path(dest)
        try:
            tmp.write_bytes(body)
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)
        record = {
            "kind": "arcgis_query_page",
            "source_family": "arcgis",
            "source_url": url,
            "layer_url": layer_url,
            "page": page_no,
            "result_offset": offset,
            "feature_count": len(page_features),
            "dest": str(dest),
            "fetched_at": arrived.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "sha256": sha,
            "bytes": len(body),
            "status": "downloaded",
        }
        records.append(record)

        features.extend(page_features)
        if not more:
            with (out_dir / "manifest.jsonl").open("a", encoding="utf-8") as f:
                for rec in records:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            return {"type": "FeatureCollection", "features": features, "records": records, "pages": len(records)}
        offset += len(page_features)
