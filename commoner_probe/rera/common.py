# SPDX-License-Identifier: MIT
"""Helpers that every state RERA adapter shares.

Each state runs a different portal application, so the parsers live in one
module per state. What they share is the output: the same two files, written
the same way. This module holds that.

* ``manifest.jsonl``: one ``rera_listing`` record per request.
* ``rera_project_rows.jsonl``: one ``rera_project_row`` per project.

The probe records the dates a portal publishes. It never computes delay.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

ROWS_FILE = "rera_project_rows.jsonl"
MANIFEST_FILE = "manifest.jsonl"

#: Shortest pause between two requests to one portal. These pages are large
#: (Karnataka sends about 6.5 MB of page chrome with every response), so the
#: adapters wait much longer than ``make_session``'s one-second floor.
MIN_SLEEP_SEC = 5.0


class ReraError(RuntimeError):
    """A portal answered, but not with the listing the adapter was built for."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def slugify(text: str) -> str:
    """Lower-case ASCII slug for a file name: ``Bengaluru  Rural`` -> ``bengaluru-rural``."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def iso_date(text: str | None, sep: str) -> str | None:
    """Convert ``dd<sep>mm<sep>yyyy`` to ``yyyy-mm-dd``, or None.

    None for an empty cell and for text that isn't a real date in this shape.
    The caller keeps the original text in ``fields``, so nothing is lost when
    this returns None.
    """
    if not text:
        return None
    m = re.fullmatch(rf"(\d{{2}}){re.escape(sep)}(\d{{2}}){re.escape(sep)}(\d{{4}})", text.strip())
    if not m:
        return None
    day, month, year = (int(g) for g in m.groups())
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def upsert_jsonl(path: Path, new: list[dict], *, key: str = "key") -> None:
    """Write *new* into a JSONL file, replacing any record with the same key.

    A rerun therefore replaces a project's row instead of duplicating it.
    The file is rewritten through a temporary file, so a killed run leaves the
    previous file intact.
    """
    existing: dict[str, dict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            existing[record.get(key)] = record
    for record in new:
        existing[record[key]] = record
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in existing.values()),
        encoding="utf-8",
    )
    tmp.replace(path)


def write_bytes_atomic(path: Path, body: bytes) -> None:
    """Write *body* to *path* through a temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(body)
    tmp.replace(path)
