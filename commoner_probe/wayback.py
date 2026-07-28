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


def _latest_capture(url: str, *, session: Any, timeout: float) -> tuple[dict | None, str]:
    """``(capture, reason)`` — separates "no capture" from "could not ask".

    The two look identical to a caller that only gets ``None`` back, but they
    are opposite facts: one says the URL was never archived, the other says the
    Internet Archive was unreachable (it rate-limits, and returns 503 under
    load — observed live 2026-07-26 on back-to-back CDX calls). A re-check that
    treats a 503 as "nothing to compare" silently stops detecting change.
    """
    session = session or make_session()
    params = {"url": url, "output": "json", "limit": -1, "fl": _CDX_FIELDS}
    try:
        r = session.get(CDX_API, params=params, timeout=timeout)
        r.raise_for_status()
        rows = r.json()
    except Exception:
        return None, "index-unavailable"
    if not isinstance(rows, list) or len(rows) < 2:
        # Row 0 is the header; a lone header means no captures.
        return None, "never-archived"
    header, last = rows[0], rows[-1]
    record = dict(zip(header, last))
    timestamp = str(record.get("timestamp") or "")
    if not timestamp:
        return None, "never-archived"
    return {
        "timestamp": timestamp,
        "statuscode": str(record.get("statuscode") or ""),
        "digest": str(record.get("digest") or ""),
        "snapshot_url": replay_url(timestamp, str(record.get("original") or url)),
    }, ""


def latest_capture(url: str, *, session: Any = None, timeout: float = DEFAULT_TIMEOUT) -> dict | None:
    """Most recent Wayback capture of `url`, or None if there isn't one to hand.

    Returns `timestamp`, `statuscode`, `digest` and a ready-to-cite `snapshot_url`.
    None covers both "never archived" and "index unreachable"; call
    :func:`recheck` when that difference matters.
    """
    capture, _ = _latest_capture(url, session=session, timeout=timeout)
    return capture


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

    * `captured`   — a save was requested and a NEW capture is now indexed
    * `save-pending` — a save was requested, but the newest indexed capture is
      the one that was already there. SPN2 queues work, so this is the ordinary
      outcome of saving a URL that has been archived before; the recorded
      capture is somebody else's, not this acquisition's.
    * `existing`   — no save requested (save=False), but a prior capture exists
    * `unarchived` — the index has no capture, and any save has not landed yet
    * `unavailable` — the index could not be reached, or the save request itself
      failed (throttled, or IA is down)

    `unarchived` and `unavailable` are opposite claims and the CDX call is the
    only thing that can tell them apart: "no capture exists" is a fact about the
    URL, "the check failed" is a fact about the Internet Archive.

    Merge the result into the record; never gate acquisition on it.
    """
    session = session or make_session()
    before, _ = _latest_capture(url, session=session, timeout=timeout) if save else (None, "")
    saved = request_save(url, session=session) if save else True
    capture, failure = _latest_capture(url, session=session, timeout=timeout)
    if capture is None:
        status = "unavailable" if (failure == "index-unavailable" or not saved) else "unarchived"
        return {"wayback_url": None, "wayback_timestamp": None, "wayback_digest": None, "wayback_status": status}
    if not save:
        status = "existing"
    elif before is not None and before["timestamp"] == capture["timestamp"]:
        status = "save-pending"
    else:
        status = "captured"
    return {
        "wayback_url": capture["snapshot_url"],
        "wayback_timestamp": capture["timestamp"],
        "wayback_digest": capture["digest"],
        "wayback_status": status,
    }


def attach_snapshot(
    record: dict,
    *,
    url_key: str = "url",
    save: bool = False,
    session: Any = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """Merge Wayback provenance fields into a manifest record, in place.

    The wiring point between this module and a probe's write path: a probe
    calls this on each record it is about to append, and the record gains the
    four ``WAYBACK_FIELDS``.

    ``save`` defaults to **False** here even though ``snapshot_fields``
    defaults to True. Firing Save Page Now is an outward-facing write to a
    public, effectively permanent archive, and a probe should not make that
    request as a side effect of acquiring a file — the caller has to ask for
    it. With ``save=False`` this is CDX reads only: it records whether a
    capture already exists and never creates one.

    Never raises, and never removes fields: a record with no usable URL comes
    back untouched rather than carrying null provenance that reads as "checked
    and absent".
    """
    url = record.get(url_key)
    if not url:
        return record
    record.update(snapshot_fields(url, save=save, session=session, timeout=timeout))
    return record


#: Every outcome ``recheck`` can report. ``index-unavailable`` is an error and
#: the others are not — the distinction is the point of the function.
RECHECK_REASONS = (
    "changed",
    "unchanged",
    "no-recorded-digest",
    "never-archived",
    "index-unavailable",
)


def recheck(
    url: str,
    digest: str,
    *,
    session: Any = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """Has `url` changed since the capture with content `digest`? With the reason.

    Returns ``{"changed": bool | None, "reason": str}``, where ``reason`` is one
    of :data:`RECHECK_REASONS`. Compares CDX content digests, so neither version
    is downloaded.

    ``changed`` is None for three different situations, and a caller that acts
    on the answer needs to tell them apart: the caller recorded no digest, the
    URL was never archived, or **the index could not be reached**. Only the last
    is an error, and only the last should be retried — reading a 503 as "nothing
    to compare" is how change detection silently stops working.
    """
    if not digest:
        return {"changed": None, "reason": "no-recorded-digest"}
    capture, failure = _latest_capture(url, session=session, timeout=timeout)
    if capture is None:
        return {"changed": None, "reason": failure}
    if not capture["digest"]:
        return {"changed": None, "reason": "never-archived"}
    changed = capture["digest"] != digest
    return {"changed": changed, "reason": "changed" if changed else "unchanged"}


def changed_since(
    url: str,
    digest: str,
    *,
    session: Any = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> bool | None:
    """Boolean form of :func:`recheck`; None when there is no usable comparison.

    Convenient, but it cannot distinguish an unreachable index from an
    unarchived URL. Prefer :func:`recheck` anywhere the difference decides what
    happens next.
    """
    return recheck(url, digest, session=session, timeout=timeout)["changed"]


def _main(argv: list[str]) -> int:  # pragma: no cover - thin CLI shim
    if not argv:
        print("usage: python -m commoner_probe.wayback <url> [--no-save]")
        return 2
    fields = snapshot_fields(argv[0], save="--no-save" not in argv)
    print(json.dumps(fields, ensure_ascii=False))
    return 0
