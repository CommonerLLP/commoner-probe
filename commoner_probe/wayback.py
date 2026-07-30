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
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .http_client import make_session

CDX_API = "https://web.archive.org/cdx/search/cdx"
SAVE_BASE = "https://web.archive.org/save/"
REPLAY_BASE = "https://web.archive.org/web/"

# The CDX fields this module reads, in the order the API returns them.
_CDX_FIELDS = "timestamp,original,statuscode,digest"

# Fields requested when listing captures. Always sent explicitly: the API's
# default column order is not the same as this one (it leads with `urlkey` and
# puts `mimetype` before `statuscode`), so a positional read of a defaulted
# response silently mis-assigns every column.
CAPTURE_FIELDS = ("timestamp", "original", "statuscode", "digest", "mimetype", "length")

# Rows per CDX request when walking a full capture list. The index is paged with
# an opaque resumeKey rather than an offset.
DEFAULT_BATCH = 1000

# Listing a batch is a far heavier query than fetching one capture, and the
# index is slow under load, so the listing timeout is its own (larger) number.
LIST_TIMEOUT = 120.0

# The index flaps: the same query returned `200 []` and then `503` three seconds
# apart (measured 2026-07-28), and read timeouts are common. Retrying is not
# optional politeness — without it a transient failure mid-walk truncates a
# capture history and the corpus silently ends early.
LIST_RETRIES = 4
LIST_BACKOFF_SEC = 5.0

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


# ---------------------------------------------------------------------------
# Capture listing — the full history of a URL or prefix, not just the newest
# ---------------------------------------------------------------------------

class IndexUnavailable(RuntimeError):
    """The CDX index could not be read. NOT the same as "no captures exist"."""


def _cdx_rows(payload: Any) -> list[list[str]]:
    """Data rows from a CDX json payload, header and resume markers removed."""
    if not isinstance(payload, list) or not payload:
        return []
    header = payload[0]
    rows = payload[1:] if isinstance(header, list) and header and header[0] in CAPTURE_FIELDS + ("urlkey",) else payload
    # A resumeKey response ends with a blank row then a one-element row holding
    # the key. Both are markers, not captures.
    return [r for r in rows if isinstance(r, list) and len(r) > 1]


def _resume_key(payload: Any) -> str | None:
    """The opaque continuation token, if the response carried one.

    Shape verified live 2026-07-28: the last two rows are ``[]`` followed by
    ``["<key>"]``. A single-element final row is the only place the key appears.
    """
    if not isinstance(payload, list) or len(payload) < 2:
        return None
    last = payload[-1]
    if isinstance(last, list) and len(last) == 1 and isinstance(last[0], str) and last[0]:
        return last[0]
    return None


def capture_query(
    url: str,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    match_prefix: bool = False,
    collapse_digest: bool = False,
    only_ok: bool = False,
    limit: int = DEFAULT_BATCH,
    resume_key: str | None = None,
) -> dict[str, Any]:
    """CDX query parameters for one batch of a capture listing.

    ``from_date``/``to_date`` accept any timestamp prefix the API does — a bare
    year, a year-month, or a full 14-digit stamp (verified live).

    ``match_prefix`` appends ``/*``, which asks for every captured URL under the
    given host or path rather than that exact URL.

    ``collapse_digest`` drops consecutive captures whose content digest is
    unchanged, which is how you get "when did this page actually change" rather
    than "how often was it crawled". ``only_ok`` keeps HTTP 200 captures alone,
    dropping the redirects and error pages the crawler also recorded.
    """
    target = url.rstrip("/") + "/*" if match_prefix else url
    params: dict[str, Any] = {
        "url": target,
        "output": "json",
        "fl": ",".join(CAPTURE_FIELDS),
        "limit": limit,
        "showResumeKey": "true",
    }
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date
    if collapse_digest:
        params["collapse"] = "digest"
    if only_ok:
        params["filter"] = "statuscode:200"
    if resume_key:
        params["resumeKey"] = resume_key
    return params


def iter_captures(
    url: str,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    match_prefix: bool = False,
    collapse_digest: bool = False,
    only_ok: bool = False,
    max_records: int | None = None,
    batch: int = DEFAULT_BATCH,
    session: Any = None,
    timeout: float = LIST_TIMEOUT,
    retries: int = LIST_RETRIES,
    backoff: float = LIST_BACKOFF_SEC,
) -> Any:
    """Yield every capture of *url*, following resumeKey pagination.

    Each capture is ``{timestamp, original, statuscode, digest, mimetype,
    length, snapshot_url}``.

    **A URL with no captures yields nothing; an unreachable index raises.**
    Those are opposite facts and the API does not make them easy to tell apart:
    "no captures" is HTTP 200 with a body of ``[]``, while the index answers
    5xx — or resets the connection — often enough that the *same* query returned
    ``200 []`` and then ``503`` three seconds later (measured 2026-07-28). A
    caller that read a 503 as "never archived" would record an outage as a fact
    about the source, which is the failure ``recheck()`` exists to prevent.
    """
    session = session or make_session()
    resume: str | None = None
    emitted = 0
    seen_batches = 0

    while True:
        params = capture_query(
            url,
            from_date=from_date,
            to_date=to_date,
            match_prefix=match_prefix,
            collapse_digest=collapse_digest,
            only_ok=only_ok,
            limit=batch,
            resume_key=resume,
        )
        # Parsing and shape validation live INSIDE the retry, not after it. An
        # interstitial or an error page can arrive as HTTP 200 with an HTML
        # body; parsed outside the loop it raised on the first attempt and the
        # configured retries were never spent, even though the next response
        # would have been fine. A transient 200 is as transient as a 503.
        payload: Any = None
        last_exc: Exception | None = None
        empty_body = False
        for attempt in range(retries):
            try:
                r = session.get(CDX_API, params=params, timeout=timeout)
                r.raise_for_status()
                body = r.text
                if not body.strip():
                    empty_body = True
                    break
                parsed = json.loads(body)
                # A parseable body of the wrong SHAPE (null, an error object) is
                # an outage dressed as data. Treating it as an empty row set is
                # how an outage gets recorded as "this URL has no captures".
                if not isinstance(parsed, list):
                    raise ValueError(
                        f"expected a JSON array of CDX rows, got {type(parsed).__name__}"
                    )
                payload = parsed
                break
            except Exception as exc:  # noqa: PERF203 - retry is the point
                last_exc = exc
                if attempt < retries - 1:
                    time.sleep(backoff * (attempt + 1))
        if empty_body:
            return
        if payload is None:
            raise IndexUnavailable(
                f"the Wayback CDX index could not be read for {url!r} after "
                f"{retries} attempts: {type(last_exc).__name__}: {last_exc}. "
                "This is NOT evidence that the URL has no captures — a 5xx, a "
                "reset connection, a read timeout and a 200 carrying an error "
                "page are all facts about the index, not about the source."
            ) from last_exc

        rows = _cdx_rows(payload)
        seen_batches += 1
        for row in rows:
            record = dict(zip(CAPTURE_FIELDS, row))
            timestamp = str(record.get("timestamp") or "")
            original = str(record.get("original") or url)
            if not timestamp:
                continue
            record["snapshot_url"] = replay_url(timestamp, original)
            yield record
            emitted += 1
            if max_records is not None and emitted >= max_records:
                return

        resume = _resume_key(payload)
        if not resume or not rows:
            return


# ---------------------------------------------------------------------------
# Probe — capture lists into the provenance manifest
# ---------------------------------------------------------------------------

class WaybackCaptureProbe:
    """Write a URL's (or a prefix's) capture history into a corpus manifest.

    This is the archival counterpart to :func:`attach_snapshot`. That records
    *one* snapshot alongside a file as it is acquired; this records *what the
    archive holds*, which is what answers "when did this page change, and what
    did it say before" for a source nobody captured at the time.
    """

    def __init__(
        self,
        out_dir: Any,
        *,
        sleep: float = 1.0,
        session: Any = None,
    ) -> None:
        from pathlib import Path

        self.out_dir = Path(out_dir)
        self.sleep = sleep
        self.manifest = self.out_dir / "manifest.jsonl"
        self.session = session or make_session(rate_limit_sec=sleep)

    def load_seen(self) -> set:
        seen: set = set()
        if not self.manifest.exists():
            return seen
        for line in self.manifest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("kind") == "wayback_capture" and row.get("key"):
                seen.add(row["key"])
        return seen

    def append_manifest(self, record: dict) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _record(self, capture: dict, *, query_url: str) -> dict:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        timestamp = str(capture.get("timestamp") or "")
        original = str(capture.get("original") or "")
        digest = str(capture.get("digest") or "")
        length = capture.get("length")
        try:
            length_int = int(length) if length not in (None, "", "-") else None
        except (TypeError, ValueError):
            length_int = None
        return {
            # Timestamp plus digest, because one URL is captured many times and
            # a re-crawl of unchanged content is a different row from a change.
            "key": f"WAYBACK|{original}|{timestamp}",
            "kind": "wayback_capture",
            "record_type": "wayback_capture",
            "source_family": "wayback",
            "source": "web.archive.org",
            "publisher": "Internet Archive",
            "query_url": query_url,
            "url": original,
            "timestamp": timestamp,
            "captured_at": _iso_from_cdx(timestamp),
            "snapshot_url": capture.get("snapshot_url"),
            "http_status": str(capture.get("statuscode") or "") or None,
            "digest": digest or None,
            "media_type": str(capture.get("mimetype") or "") or None,
            "length": length_int,
            "status": "metadata_only",
            "fetched_at": now,
            "probed_at": now,
        }

    def probe(
        self,
        *,
        url: str,
        from_date: str | None = None,
        to_date: str | None = None,
        match_prefix: bool = False,
        collapse_digest: bool = False,
        only_ok: bool = False,
        max_records: int | None = None,
        dry_run: bool = False,
    ) -> Any:
        """Stream capture records, writing each to the manifest as it arrives.

        Resume is by ``key`` (url + timestamp): a capture already recorded is
        never appended twice, so re-running extends the history rather than
        duplicating it.

        **A walk that fails part-way writes nothing.** Appending pages as they
        arrive would leave an index outage on page six looking like a complete
        five-page history — this module's own failure (an outage recorded as a
        fact about the source) reappearing one level up, at the file instead of
        the row. So rows are spooled to a scratch file beside the manifest and
        streamed onto it in one pass once the walk finishes: either the whole
        history lands, or none of this invocation's rows do and the error says
        why. Records already yielded are the caller's to discard.

        Staging, rather than writing and then restoring the file's prior size,
        is what makes that safe on a **shared** manifest. Truncating back to a
        byte offset taken before the walk would delete whatever another probe
        appended in the meantime, turning this probe's outage into silent data
        loss for a different corpus.

        Staging to **disk** rather than to a list is what keeps it bounded.
        ``--prefix`` without ``--max-records`` walks every URL under a host, and
        a large government domain's capture history does not fit in memory;
        peak memory here is one row regardless of how long the history is.

        Abandoning the generator early (a ``break`` in the caller, or
        ``max_records``) is a deliberate stop, not a failure, and keeps its rows.
        """
        seen = self.load_seen()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._spool_path = self.manifest.with_suffix(f".{os.getpid()}.spool")
        self._spool_preserved = False
        spool = self._spool_path.open("w+", encoding="utf-8")
        try:
            for capture in iter_captures(
                url,
                from_date=from_date,
                to_date=to_date,
                match_prefix=match_prefix,
                collapse_digest=collapse_digest,
                only_ok=only_ok,
                max_records=max_records,
                session=self.session,
            ):
                record = self._record(capture, query_url=url)
                if dry_run:
                    yield {**record, "status": "dry_run"}
                    continue
                if record["key"] in seen:
                    continue
                self._stage(spool, record)
                seen.add(record["key"])
                yield record
        except GeneratorExit:
            # A caller that stops reading meant to stop, and keeps its rows.
            self._flush_manifest(spool)
            raise
        except Exception:
            # Whole history or nothing: the spool is discarded unflushed.
            raise
        else:
            self._flush_manifest(spool)
        finally:
            spool.close()
            # Only an UNPRESERVED spool is scratch. Once _preserve_spool() has
            # kept the rows — renamed, or left in place because the rename
            # failed — deleting anything here would destroy the file the raised
            # error just told the operator to recover from.
            if not self._spool_preserved:
                self._spool_path.unlink(missing_ok=True)

    @staticmethod
    def _stage(handle, record: dict) -> None:
        """Park one row in the spool. Kept separate so it can be observed."""
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _flush_manifest(self, spool) -> None:
        """Copy the spool onto the manifest in one streaming pass.

        Two properties, and the second is why the spool exists at all.

        **The failure path must not touch other writers' rows.** Restoring the
        manifest to a byte offset taken before the walk deletes whatever another
        probe appended in the meantime — one probe's index outage becoming
        silent data loss for a different corpus. Nothing is written until the
        walk has finished, so there is nothing to roll back.

        **Staging goes to disk, not to a list.** ``--prefix`` without
        ``--max-records`` is an advertised way to walk every URL under a host,
        and a large government domain's capture history will not fit in memory.
        The spool grows on disk as rows arrive and is streamed out at the end,
        so peak memory is one row regardless of history size.

        A disk-full shows up while spooling — before the manifest is opened —
        which is what keeps the whole-history-or-nothing guarantee honest. An
        I/O error during the copy itself is still possible; it raises with the
        spool path named so the rows can be recovered by hand rather than
        silently lost. Truncating the manifest back is deliberately NOT done:
        on a shared append-only file that is the very bug this replaced.
        """
        spool.flush()
        if spool.tell() == 0:
            return
        spool.seek(0)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        try:
            with self.manifest.open("a", encoding="utf-8") as f:
                # One write() per record, NOT a block copy. The manifest is
                # opened O_APPEND and shared, so each write lands whole at the
                # end of the file; a buffer-sized copy can end mid-record and
                # let another process's chunk land inside this row, producing
                # malformed JSONL. Record-aligned writes keep every row
                # indivisible without needing a lock the other manifest writers
                # in this package do not take.
                for line in spool:
                    f.write(line)
        except OSError as exc:
            raise OSError(
                f"manifest append failed part-way ({exc}). This invocation's rows "
                f"are preserved at {self._preserve_spool()}; the manifest was NOT "
                "truncated, because other writers may have appended to it."
            ) from exc

    def _preserve_spool(self) -> Path | str:
        """Keep the spool for hand-recovery. Renames — never reads.

        Reading the spool into memory to re-write it would reintroduce the
        exact OOM this spool exists to avoid, on the one path where the walk
        was large enough to fail. A rename is O(1) and cannot run out of
        memory; if even that fails, the spool is left in place and the caller
        is told where it is rather than losing it to the cleanup.

        The flag is what protects the file. Repointing ``_spool_path`` at the
        kept file does not: the caller's cleanup unlinks whatever that attribute
        names, so the repoint deleted the recovery file it was meant to spare
        (Codex, PR #82).
        """
        self._spool_preserved = True
        keep = self.manifest.with_suffix(".recover.jsonl")
        try:
            self._spool_path.rename(keep)
        except OSError:
            return f"{self._spool_path} (left in place; rename failed)"
        self._spool_path = keep
        return keep


def _iso_from_cdx(timestamp: str) -> str | None:
    """``20260720035455`` -> ``2026-07-20T03:54:55Z``, or None if malformed."""
    if len(timestamp) != 14 or not timestamp.isdigit():
        return None
    y, mo, d = timestamp[0:4], timestamp[4:6], timestamp[6:8]
    h, mi, s = timestamp[8:10], timestamp[10:12], timestamp[12:14]
    return f"{y}-{mo}-{d}T{h}:{mi}:{s}Z"
