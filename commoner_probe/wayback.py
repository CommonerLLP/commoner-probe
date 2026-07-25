# SPDX-License-Identifier: MIT
"""Internet Archive snapshot capture for acquisition-time provenance (REQ-0036).

A citation to a government page is only as good as proof of what that page said
on the day it was cited. This module pushes an acquired URL to the Wayback
Machine and hands back manifest fields recording the snapshot, so a later reader
can see the source as acquired even after the page changes or disappears.

Two public Internet Archive endpoints, no key and no account:

* ``https://web.archive.org/save/<url>`` — Save Page Now. Anonymous callers are
  rate-limited and the capture is not always immediate, so this is fired as a
  trigger and never trusted for its response body.
* ``https://web.archive.org/cdx/search/cdx`` — the capture index. ``limit=-1``
  returns the most recent capture. Every row carries a content ``digest``,
  which is what makes change detection cheap: two captures of the same URL with
  the same digest are byte-identical, so a re-check never has to download either.

**Best-effort by contract.** The Internet Archive is a third party that rate-
limits and has outages. Nothing here raises: a failed snapshot records
``wayback_status`` and lets acquisition proceed. Provenance capture must never
be the reason a public record goes unacquired.

This is a deliberate reimplementation over IA's own public endpoints rather than
a dependency on the community ``mcp-wayback-machine`` package, which is
CC-BY-NC-SA-4.0 and cannot be vendored into this MIT-licensed, PyPI-published
package. See REQ-0036 for the licence analysis.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from .http_client import make_session

CDX_API = "https://web.archive.org/cdx/search/cdx"
SAVE_BASE = "https://web.archive.org/save/"
REPLAY_BASE = "https://web.archive.org/web/"

# The CDX fields this module reads, in the order the API returns them.
_CDX_FIELDS = "timestamp,original,statuscode,digest"

DEFAULT_TIMEOUT = 30.0
SAVE_TIMEOUT = 60.0

# Manifest keys written by snapshot_fields(), so callers and schemas agree.
WAYBACK_FIELDS = ("wayback_url", "wayback_timestamp", "wayback_digest", "wayback_status")


def replay_url(timestamp: str, url: str) -> str:
    """The stable citation URL for one capture."""
    return f"{REPLAY_BASE}{timestamp}/{url}"


def latest_capture(url: str, *, session: Any = None, timeout: float = DEFAULT_TIMEOUT) -> dict | None:
    """Most recent Wayback capture of `url`, or None if it has never been archived.

    Returns `timestamp`, `statuscode`, `digest` and a ready-to-cite `snapshot_url`.
    Returns None on any failure — an unreachable index is indistinguishable from
    an unarchived URL for our purposes, and neither is worth an exception.
    """
    session = session or make_session()
    params = {"url": url, "output": "json", "limit": -1, "fl": _CDX_FIELDS}
    try:
        r = session.get(CDX_API, params=params, timeout=timeout)
        r.raise_for_status()
        rows = r.json()
    except Exception:
        return None
    if not isinstance(rows, list) or len(rows) < 2:
        # Row 0 is the header; a lone header means no captures.
        return None
    header, last = rows[0], rows[-1]
    record = dict(zip(header, last))
    timestamp = str(record.get("timestamp") or "")
    if not timestamp:
        return None
    return {
        "timestamp": timestamp,
        "statuscode": str(record.get("statuscode") or ""),
        "digest": str(record.get("digest") or ""),
        "snapshot_url": replay_url(timestamp, str(record.get("original") or url)),
    }


def request_save(url: str, *, session: Any = None, timeout: float = SAVE_TIMEOUT) -> bool:
    """Ask Save Page Now to capture `url`. True if the request was accepted.

    A True here means the request went through, NOT that a capture now exists —
    SPN2 queues work and anonymous callers are throttled. Confirm with
    latest_capture() rather than trusting this return value.
    """
    session = session or make_session()
    try:
        r = session.get(f"{SAVE_BASE}{quote(url, safe=':/?&=#%')}", timeout=timeout)
        r.raise_for_status()
    except Exception:
        return False
    return True


def snapshot_fields(
    url: str,
    *,
    save: bool = True,
    session: Any = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """Provenance fields recording the Wayback state of `url`, for a manifest row.

    `wayback_status` is one of:

    * `captured`   — a save was requested and a capture is now indexed
    * `existing`   — no save requested (save=False), but a prior capture exists
    * `unarchived` — the index has no capture, and any save has not landed yet
    * `unavailable` — the save request itself failed (throttled, or IA is down)

    Merge the result into the record; never gate acquisition on it.
    """
    session = session or make_session()
    saved = request_save(url, session=session) if save else True
    capture = latest_capture(url, session=session, timeout=timeout)
    if capture is None:
        status = "unarchived" if saved else "unavailable"
        return {"wayback_url": None, "wayback_timestamp": None, "wayback_digest": None, "wayback_status": status}
    return {
        "wayback_url": capture["snapshot_url"],
        "wayback_timestamp": capture["timestamp"],
        "wayback_digest": capture["digest"],
        "wayback_status": "captured" if save else "existing",
    }


def changed_since(
    url: str,
    digest: str,
    *,
    session: Any = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> bool | None:
    """Has `url` changed since the capture with content `digest`?

    Compares against the newest indexed capture's digest, so neither version is
    downloaded. Returns None when there is nothing to compare — no captures, or
    no recorded digest — which is not the same answer as False and should not be
    collapsed into one.
    """
    if not digest:
        return None
    capture = latest_capture(url, session=session, timeout=timeout)
    if capture is None or not capture["digest"]:
        return None
    return capture["digest"] != digest


def _main(argv: list[str]) -> int:  # pragma: no cover - thin CLI shim
    if not argv:
        print("usage: python -m commoner_probe.wayback <url> [--no-save]")
        return 2
    fields = snapshot_fields(argv[0], save="--no-save" not in argv)
    print(json.dumps(fields, ensure_ascii=False))
    return 0
