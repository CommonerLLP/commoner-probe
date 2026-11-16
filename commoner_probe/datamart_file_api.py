# SPDX-License-Identifier: MIT
"""List and fetch files from Environment and Climate Change Canada's Datamart.

CONTEXT
=======
The Datamart file API at ``data-donnees.az.ec.gc.ca`` serves a directory tree
of published files. Two calls matter:

- ``GET /api/path_contents?path=/<dir>`` lists one directory as JSON.
- ``GET /api/file?path=/<file>`` returns the bytes of one file. It may answer
  302 first; the session follows redirects.

An unknown path does not return HTTP 404. It returns the JSON string
``"Path not found."`` with a success status, so a caller that only checks the
status would save that string as if it were the file. Both calls here treat it
as an error.

Acquisition only. The adapter writes bytes and a manifest record. It does not
read a figure from any file.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from commoner_probe.base import _private_tmp_path, safe_filename_segment
from commoner_probe.http_client import get_capped

DATAMART_BASE_URL = "https://data-donnees.az.ec.gc.ca"
_NOT_FOUND = "Path not found."


class DatamartError(RuntimeError):
    """The Datamart answered "Path not found." or a body that is not what the call returns."""


@dataclass(frozen=True)
class DatamartEntry:
    """One file or directory in a Datamart listing."""

    name: str
    path: str  # no leading slash, as the API prints it
    is_directory: bool
    content_length: str  # as printed, for example "104 KiB"
    last_modified: str  # as printed, for example "2023-11-10"


def _url(endpoint: str, path: str) -> str:
    query = urllib.parse.urlencode({"path": "/" + path.lstrip("/")})
    return f"{DATAMART_BASE_URL}/api/{endpoint}?{query}"


def _is_not_found(body: bytes) -> bool:
    # A real file can be any bytes; only a short body can be the error string.
    if len(body) > 64:
        return False
    try:
        return json.loads(body) == _NOT_FOUND
    except ValueError:
        return False


def list_path(session: Any, path: str, *, max_bytes: int = 5_000_000) -> list[DatamartEntry]:
    """The entries of one Datamart directory."""
    url = _url("path_contents", path)
    body = get_capped(session, url, max_bytes=max_bytes, timeout=90)
    if _is_not_found(body):
        raise DatamartError(f"Path not found: {path!r} ({url})")
    try:
        payload = json.loads(body)
        rows = payload["path_contents"]
        return [
            DatamartEntry(
                name=r["name"],
                path=r["path"],
                is_directory=bool(r["is_directory"]),
                content_length=r["content_length"],
                last_modified=r["last_modified"],
            )
            for r in rows
        ]
    except (ValueError, KeyError, TypeError) as exc:
        raise DatamartError(f"unexpected path_contents response for {path!r}: {url}") from exc


def _stored_record(out_dir: Path, path: str) -> dict | None:
    """The newest ``downloaded`` record for *path* whose file is still intact, else None."""
    manifest = out_dir / "manifest.jsonl"
    if not manifest.is_file():
        return None
    found = None
    for line in manifest.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict) or rec.get("kind") != "datamart_file":
            continue
        if rec.get("path") != path or rec.get("status") != "downloaded":
            continue
        dest = Path(str(rec.get("dest", "")))
        try:
            intact = dest.is_file() and hashlib.sha256(dest.read_bytes()).hexdigest() == rec.get("sha256")
        except OSError:
            intact = False
        if intact:
            found = rec
    return found


def fetch_file(
    session: Any, path: str, out_dir: Path, *, max_bytes: int, refresh: bool = False
) -> dict:
    """Download one file to ``out_dir/run-<UTC time>/<path>`` and append a record to ``manifest.jsonl``.

    A file that is already stored is not fetched again. When the manifest holds a
    ``downloaded`` record for the same Datamart ``path`` and that record's file
    still exists and matches its sha256, that earlier record is returned and no
    request is made. A missing or changed file is fetched fresh. Pass
    ``refresh=True`` to fetch again anyway into a new run directory.

    Each fetch writes under its own run directory, so a refresh never overwrites
    an earlier file and an earlier record's sha256 stays true.

    Returns the record: ``source_url``, ``path``, ``fetched_at``, ``sha256``,
    ``bytes``, ``status`` (``downloaded``), plus ``dest``. Raises
    :class:`DatamartError` for an unknown path and
    :class:`~commoner_probe.http_client.ResponseTooLarge`, naming the cap, for a
    file above ``max_bytes``. Nothing is written in either case. A connection
    reset mid-body is retried inside ``get_capped``.
    """
    out_dir = Path(out_dir)
    path = path.lstrip("/")
    if not refresh:
        existing = _stored_record(out_dir, path)
        if existing is not None:
            return existing
    url = _url("file", path)
    body = get_capped(session, url, max_bytes=max_bytes, timeout=180)
    if _is_not_found(body):
        raise DatamartError(f"Path not found: {path!r} ({url})")

    run_dir = out_dir / datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%S%fZ")
    dest = run_dir.joinpath(*(safe_filename_segment(part) for part in path.split("/") if part))
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = _private_tmp_path(dest)
    try:
        tmp.write_bytes(body)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)

    record = {
        "kind": "datamart_file",
        "source_family": "datamart",
        "source_url": url,
        "path": path,
        "dest": str(dest),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "sha256": hashlib.sha256(body).hexdigest(),
        "bytes": len(body),
        "status": "downloaded",
    }
    with (out_dir / "manifest.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record
