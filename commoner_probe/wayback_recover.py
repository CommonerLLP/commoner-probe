# SPDX-License-Identifier: MIT
"""Recover documents from the Internet Archive's Wayback Machine.

The Wayback Machine is two public services with no key and no account: the CDX
capture index (``web.archive.org/cdx/search/cdx``) and the replay endpoint
(``web.archive.org/web/<timestamp>id_/<url>``). ``wayback.py`` uses them to
prove what a page said on a given day. This module uses them for the other
question: the government file is gone, so **give me the document**.

Written against the Department of School Education & Literacy
(``dsel.education.gov.in``, vintage 2026-08), whose Samagra Shiksha Project
Approval Board minutes returned HTTP 404 for every PDF while the listing page
returned HTTP 200 with an empty body. The archive held 391 of those PDFs. The
traps below are the archive's, not the department's.

THE NEWEST CAPTURE IS OFTEN A TRUNCATED RE-CRAWL
================================================
``limit=-1`` returns the most recent capture, which is what provenance wants and
the opposite of what recovery wants. Measured on one file::

    AN_PAB_2018_2019.pdf   20220121062121  200  14,561,108
    AN_PAB_2018_2019.pdf   20231015155748  200  14,561,045
    AN_PAB_2018_2019.pdf   20250517032756  200   5,242,957   <- newest, cut at 5 MiB

The 5 MiB fragment arrives as HTTP 200 with no error and no warning. Four of the
first eleven files recovered by newest were silently truncated. So ``prefer``
defaults to ``largest``; ``newest`` remains available for callers who want the
current state rather than the most complete artefact.

BYTE LENGTH IS A HEURISTIC, THE VERIFIER IS THE PROOF
=====================================================
The CDX ``length`` column is the archive's record of what it stored, not
evidence that the bytes form a whole document. Every candidate is therefore
verified after download and the walk falls back to the next-largest capture on
failure. For a PDF the check is the magic bytes AND ``%%EOF`` near the end,
because a fragment keeps the header and loses the trailer. Pass ``verify="none"``
for content this module has no predicate for.

A THROTTLED INDEX ANSWERS EMPTY, NOT WITH AN ERROR
==================================================
One CDX query per URL, run concurrently, gets throttled, and the throttled
responses come back as HTTP 200 with an empty body. That reported 375 of 391
documents as "no capture" when every one was present. So the index is asked
**once per host**: a single prefix query with ``filter=statuscode:200`` returned
4,181 capture rows, and every selection after that is local. An index read that
fails raises :class:`IndexUnavailable` rather than yielding nothing.

THE REPLAY ENDPOINT REFUSES UNDER LOAD, AND THE REFUSAL LOOKS LIKE ABSENCE
=========================================================================
After about 19 successful downloads the archive began answering 429. The
refusals raised instantly, and a naive ``except: continue`` recorded 365 present
files as incomplete at nine items per second. So a refusal is retried with an
escalating sleep, and a run that still cannot get the bytes records
``status="throttled"`` with the HTTP status in ``reason``. ``throttled`` and
``no-capture`` are opposite claims: one is a fact about the archive, the other
a fact about the record.

RESUME RE-VERIFIES; A RECORDED "OK" IS NOT PROOF
================================================
A run interrupted mid-write leaves a short file beside a manifest row that calls
it complete. Resume therefore re-hashes the local file and re-runs the verifier,
and refetches anything that fails. Trusting the row is how a truncated document
becomes permanent.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence
from urllib.parse import urlparse

from .base import safe_filename_segment
from .http_client import make_session, read_capped_response
from .wayback import CDX_API, REPLAY_BASE

__all__ = [
    "Capture",
    "IndexUnavailable",
    "RECOVERY_FIELDS",
    "host_captures",
    "index_query",
    "parse_captures",
    "rank_captures",
    "raw_replay_url",
    "recover",
    "sha256_hex",
    "verifier_for",
    "verify_pdf",
]

# The CDX fields this module reads, in the order it reads them. Always sent
# explicitly: the API's default column order is not this one, so a positional
# read of a defaulted response silently mis-assigns every column.
INDEX_FIELDS = "original,timestamp,statuscode,length"

# Rows per index request. The index is paged with an opaque resumeKey.
DEFAULT_BATCH = 10000

# The index and the replay endpoint have different costs. A prefix query over a
# whole government host is slow; one file download is not.
INDEX_TIMEOUT = 180.0
FETCH_TIMEOUT = 180.0

# Refusals are the normal state of an anonymous bulk read, so the retry budget
# is larger than the package default and the sleep escalates.
DEFAULT_RETRIES = 5
DEFAULT_BACKOFF_SEC = 10.0

# The statuses the archive answers when it wants a slower caller.
THROTTLE_STATUSES = frozenset({429, 503})

# Manifest fields this module commits to, so callers and schemas agree.
RECOVERY_FIELDS = (
    "source_url",
    "wayback_timestamp",
    "local_file",
    "bytes",
    "sha256",
    "status",
)

# Every outcome a recovery row can carry. `unverified` and `fetch-failed` are
# separate on purpose: the first says the bytes arrived and did not form a whole
# document, the second says no bytes ever arrived. Collapsing them tells a reader
# the archive's copy is broken when the truth is that the transport failed.
RECOVERY_STATUSES = ("ok", "no-capture", "unverified", "fetch-failed", "throttled")

MANIFEST_KIND = "wayback_recovery"

# Bytes of the tail searched for a PDF trailer. Some producers append a linearised
# hint table or junk after %%EOF, so the marker is near the end and rarely last.
PDF_TAIL_BYTES = 4096

# Longest destination filename before the URL path is hashed into it.
MAX_NAME_LEN = 180

#: A CDX row's `original` is the URL **as crawled**, not as requested. A gov host
#: crawled in 2018 is recorded `http://`, while a target list scraped off today's
#: site carries `https://`, and the archive normalises neither the trailing slash
#: nor the case of the host. Keying the index on the raw string therefore reported
#: `no-capture` for documents the index visibly held — which is this module's own
#: headline failure, one layer further down.
_DEFAULT_PORTS = {"http": "80", "https": "443"}


def canonical_key(url: str) -> str:
    """A comparison key that survives how the archive recorded a URL.

    Scheme, a leading ``www.``, the default port, host case and one trailing
    slash are all dropped. The path's case is NOT: gov hosts serve
    case-sensitive paths, and folding them would merge two real documents.
    """
    parts = urlparse(url if "//" in url else f"//{url}", scheme="http")
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    port = parts.port
    if port is not None and str(port) != _DEFAULT_PORTS.get(parts.scheme or "http"):
        host = f"{host}:{port}"
    path = (parts.path or "").rstrip("/")
    query = f"?{parts.query}" if parts.query else ""
    return f"{host}{path}{query}"


class IndexUnavailable(RuntimeError):
    """The CDX index could not be read. NOT the same as "no captures exist"."""


@dataclass(frozen=True)
class Capture:
    """One archived copy of one URL."""

    original: str
    timestamp: str
    statuscode: str
    length: int | None


def raw_replay_url(timestamp: str, url: str) -> str:
    """The replay URL for the stored bytes, without the archive's rewriting.

    The ``id_`` modifier is what makes this a document fetch. Without it the
    archive returns its own framed HTML for HTML captures and can rewrite links
    inside the body, so the sha256 is of the archive's page, not the source's.
    """
    return f"{REPLAY_BASE}{timestamp}id_/{url}"


def sha256_hex(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def verify_pdf(body: bytes) -> bool:
    """True when *body* looks like a whole PDF.

    Two checks, because a truncated download keeps the header and loses the
    trailer. A 5,242,957-byte fragment of a 14,561,108-byte file passed a magic-
    bytes test and failed this one.
    """
    if not body.startswith(b"%PDF-"):
        return False
    return b"%%EOF" in body[-PDF_TAIL_BYTES:]


def verify_any(body: bytes) -> bool:
    """True for any non-empty body. Zero bytes is never a recovered document."""
    return bool(body)


VERIFIERS: dict[str, Callable[[bytes], bool]] = {"pdf": verify_pdf, "none": verify_any}


def verifier_for(name: str) -> Callable[[bytes], bool]:
    """The predicate named *name*, or ValueError.

    Refusing an unknown name is the point. Falling back to "accept anything"
    would report a fragment as recovered for every content type this module has
    not been taught yet.
    """
    try:
        return VERIFIERS[name]
    except KeyError:
        raise ValueError(
            f"no verifier named {name!r}; known: {', '.join(sorted(VERIFIERS))}"
        ) from None


def index_query(host: str, *, resume_key: str | None = None, limit: int = DEFAULT_BATCH) -> dict[str, Any]:
    """CDX parameters for one batch of a whole host's capture index.

    ``<host>*`` asks for every captured URL under the host. ``statuscode:200``
    drops the redirects and error pages the crawler also recorded, which is what
    keeps a 404 capture from being ranked as a candidate document.
    """
    params: dict[str, Any] = {
        "url": f"{host.rstrip('/')}*",
        "output": "json",
        "fl": INDEX_FIELDS,
        "filter": "statuscode:200",
        "limit": limit,
        "showResumeKey": "true",
    }
    if resume_key:
        params["resumeKey"] = resume_key
    return params


def _length(value: Any) -> int | None:
    """The CDX length column as an int. ``-`` and junk become None."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_captures(payload: Any) -> dict[str, list[Capture]]:
    """Captures from one CDX json payload, grouped by original URL.

    Row 0 is a header. A resumeKey response ends with a blank row then a
    one-element row holding the key; both are markers, not captures.
    """
    if not isinstance(payload, list):
        raise IndexUnavailable(
            f"expected a JSON array of CDX rows, got {type(payload).__name__}. "
            "A parseable body of the wrong shape is an outage dressed as data."
        )
    out: dict[str, list[Capture]] = {}
    for row in payload[1:] if payload else []:
        if not isinstance(row, list) or len(row) < 4:
            continue
        original, timestamp = str(row[0]), str(row[1])
        if not original or not timestamp:
            continue
        statuscode = str(row[2])
        # The 200-ness is REQUESTED of the server with `filter=statuscode:200`,
        # and a request is not a guarantee. With `verify="none"` an archived
        # "Page not found" page would otherwise be written out and reported `ok`
        # with a sha256, which is a recovered document that is not the document.
        if statuscode and statuscode != "200":
            continue
        out.setdefault(canonical_key(original), []).append(
            Capture(original, timestamp, statuscode, _length(row[3]))
        )
    return out


def _resume_key(payload: Any) -> str | None:
    """The opaque continuation token, if the response carried one."""
    if not isinstance(payload, list) or len(payload) < 2:
        return None
    last = payload[-1]
    if isinstance(last, list) and len(last) == 1 and isinstance(last[0], str) and last[0]:
        return last[0]
    return None


def _read_index(
    params: dict[str, Any],
    *,
    session: Any,
    timeout: float,
    retries: int,
    backoff: float,
    sleep_fn: Callable[[float], None],
) -> Any:
    """One index batch, retried. Raises rather than returning an empty page.

    An empty body, a 5xx, a reset connection and a 200 carrying an error object
    are all facts about the index. Reading any of them as "this host has no
    captures" is the failure that reported 375 present documents as absent.
    """
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = session.get(CDX_API, params=params, timeout=timeout)
            r.raise_for_status()
            body = r.text
            if not body.strip():
                raise ValueError("empty index body")
            payload = json.loads(body)
            if not isinstance(payload, list):
                raise ValueError(f"index returned {type(payload).__name__}, not rows")
            return payload
        except Exception as exc:  # noqa: BLE001,PERF203 - retry is the point
            last = exc
            if attempt < retries - 1:
                sleep_fn(backoff * (attempt + 1))
    raise IndexUnavailable(
        f"the CDX index could not be read for {params.get('url')!r} after "
        f"{retries} attempts: {type(last).__name__}: {last}. This is NOT "
        "evidence that the host has no captures."
    ) from last


def host_captures(
    host: str,
    *,
    session: Any = None,
    timeout: float = INDEX_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    backoff: float = DEFAULT_BACKOFF_SEC,
    sleep_fn: Callable[[float], None] = time.sleep,
    batch: int = DEFAULT_BATCH,
) -> dict[str, list[Capture]]:
    """Every HTTP 200 capture under *host*, grouped by URL, in one walk.

    This is the whole reason the module is cheap: one prefix query per host
    answers for every URL under it. Querying per URL, concurrently, is what the
    archive throttles.

    A walk that finds NO capture under a whole government host raises
    :class:`IndexUnavailable`. A parseable empty array is a fact about the query,
    not about the archive: a host with a decade of published PDFs and zero rows
    is a block, a wrong prefix, or a throttle answering 200. Turning that into
    one absence claim per URL is the failure this module exists to prevent.
    """
    session = session if session is not None else make_session()
    out: dict[str, list[Capture]] = {}
    # Data rows the index served, before the local 200 filter. Zero rows and
    # "rows, none of them 200" are different answers: the first is a query that
    # did not work, the second is evidence that no usable capture exists.
    rows_served = 0
    resume: str | None = None
    while True:
        payload = _read_index(
            index_query(host, resume_key=resume, limit=batch),
            session=session,
            timeout=timeout,
            retries=retries,
            backoff=backoff,
            sleep_fn=sleep_fn,
        )
        rows_served += sum(
            1 for row in (payload[1:] if isinstance(payload, list) else [])
            if isinstance(row, list) and len(row) >= 4
        )
        for url, captures in parse_captures(payload).items():
            out.setdefault(url, []).extend(captures)
        resume = _resume_key(payload)
        if not resume:
            if not rows_served:
                raise IndexUnavailable(
                    f"the index returned no capture at all under {host!r}. That is a "
                    "statement about the query, not about the archive — a blocked or "
                    "throttled read answers this way too. Verify the host and the "
                    "prefix against a URL you already hold before concluding absence."
                )
            return out


def rank_captures(captures: Iterable[Capture], *, prefer: str = "largest") -> list[Capture]:
    """Candidates in the order they should be tried.

    ``largest`` puts the biggest capture first and breaks ties by recency, so
    the truncated newest re-crawl is tried last rather than first. A capture of
    unknown length ranks below every known one, and is still a candidate — the
    verifier decides, not the index.
    """
    if prefer not in ("largest", "newest"):
        raise ValueError(f"prefer must be 'largest' or 'newest', not {prefer!r}")
    rows = list(captures)
    if prefer == "newest":
        return sorted(rows, key=lambda c: c.timestamp, reverse=True)
    return sorted(rows, key=lambda c: (c.length if c.length is not None else -1, c.timestamp), reverse=True)


def _is_throttle(text: str) -> bool:
    """Does a failure text name a throttling status?

    The session may hand back the response or raise; ``RetrySession`` retries
    429 and 503 itself and then raises ``HTTP 429 <url>``. Reading the status
    out of the text is what keeps a refusal from being filed as absence.

    The code must appear as a STATUS TOKEN. A bare substring search matched the
    digits inside the replay URL's own 14-digit timestamp, so every capture from
    29 April or 3 May turned a name-resolution failure into a reported throttle —
    an assertion that the archive refused bytes it holds, which nothing observed.
    """
    return any(re.search(rf"\b(?:HTTP|status)\W{{0,2}}{code}\b", text, re.I)
               for code in THROTTLE_STATUSES)


def fetch_capture(
    capture: Capture,
    *,
    session: Any,
    timeout: float = FETCH_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    backoff: float = DEFAULT_BACKOFF_SEC,
    sleep_fn: Callable[[float], None],
) -> tuple[bytes | None, str, bool]:
    """``(body, reason, throttled)`` for one capture.

    ``body`` is None whenever the bytes did not arrive, and ``reason`` says why
    in words a manifest row can carry. ``throttled`` is separate because the
    caller must never record a refusal as "the archive does not have it".
    """
    url = raw_replay_url(capture.timestamp, capture.original)
    reason = ""
    throttled = False
    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=timeout, stream=True)
            status = int(getattr(resp, "status_code", 0) or 0)
            if status in THROTTLE_STATUSES:
                reason = f"{capture.timestamp}: throttled HTTP {status}"
                throttled = True
            elif status >= 400:
                # A replay 404 is a fact about this capture, not a refusal. It
                # ends this candidate immediately; retrying only wastes the
                # budget the next candidate needs.
                return None, f"{capture.timestamp}: HTTP {status}", False
            else:
                return read_capped_response(resp), "", False
        except Exception as exc:  # noqa: BLE001 - the reason is the product
            text = f"{type(exc).__name__}: {exc}"
            throttled = _is_throttle(text)
            reason = f"{capture.timestamp}: {'throttled ' if throttled else ''}{text}"
        if attempt < retries - 1:
            sleep_fn(backoff * (attempt + 1))
    return None, f"{reason} after {retries} attempts", throttled


def _dest_name(url: str) -> str:
    """A collision-free filename for *url*.

    The basename alone is not enough: one host published the same filename under
    several paths, so a basename destination silently overwrote one document
    with another. The HOST and the QUERY are part of the name for the same
    reason. Two hosts publishing ``/files/pab.pdf`` wrote one file, so the first
    row said ``ok`` and carried the sha256 of bytes that were no longer on disk;
    ``?id=101`` and ``?id=102`` collided the same way. A path too long for a
    filesystem keeps its head and gains a hash of the URL.
    """
    parts = urlparse(url)
    path = (parts.netloc + "/" + parts.path.strip("/")).strip("/") or "index"
    if parts.query:
        path = f"{path}_{parts.query}"
    name = safe_filename_segment(path, collapse=True)
    if len(name) > MAX_NAME_LEN:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
        name = f"{name[: MAX_NAME_LEN - 13]}-{digest}"
    return name


def _write_atomic(dest: Path, body: bytes) -> None:
    """Write to a temp file beside *dest*, then rename.

    A dropped connection or a Ctrl-C writing straight to *dest* leaves a short
    file that the next run's resume check would hash and accept.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.part")
    try:
        tmp.write_bytes(body)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


def _recorded_rows(manifest: Path) -> dict[str, dict]:
    """The last recovery row per source URL, for resume."""
    rows: dict[str, dict] = {}
    if not manifest.exists():
        return rows
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("kind") == MANIFEST_KIND and row.get("source_url"):
            rows[row["source_url"]] = row
    return rows


def _still_verifies(out_dir: Path, row: dict, verify: Callable[[bytes], bool]) -> bool:
    """Does the file this row claims still exist, hash true, and verify?

    All three, in this order. A row saying ``ok`` is a record of what a previous
    process believed, and that process may have died between the write and the
    append.
    """
    if row.get("status") != "ok" or not row.get("local_file"):
        return False
    path = out_dir / str(row["local_file"])
    if not path.exists():
        return False
    body = path.read_bytes()
    if row.get("sha256") and sha256_hex(body) != row["sha256"]:
        return False
    return verify(body)


def _targets(
    *,
    urls: str | Sequence[str] | None,
    host: str | None,
    match: str | None,
    session: Any,
    timeout: float,
    retries: int,
    backoff: float,
    sleep_fn: Callable[[float], None],
) -> tuple[list[str], dict[str, list[Capture]]]:
    """The URLs to recover, and the capture index that covers them.

    One index walk per host either way. A caller with a list of URLs gets the
    same single query per host that ``host`` mode does.

    The index is keyed by :func:`canonical_key`, so a requested ``https://`` URL
    finds the ``http://`` capture the crawler recorded. In host mode the URLs
    come back as the archive spells them, because that spelling is what a
    citation must carry.
    """
    if isinstance(urls, str):
        urls = [urls]
    if not urls and not host:
        raise ValueError("recover needs urls= or host=")
    if host:
        hosts = {host}
    else:
        # A URL with no host produces no query, so every URL in the list would
        # be reported absent without the archive ever being asked. A scraped
        # anchor list is full of scheme-relative and relative hrefs, which is
        # exactly how N absence claims got made with zero index calls.
        hostless = [u for u in urls or [] if not urlparse(u).netloc]
        if hostless:
            raise ValueError(
                "these URLs carry no host, so no index query can be built: "
                f"{', '.join(hostless[:5])}. An absence claim made without "
                "asking the archive is not an absence."
            )
        hosts = {urlparse(u).netloc for u in urls or []}
    index: dict[str, list[Capture]] = {}
    for one in sorted(h for h in hosts if h):
        index.update(
            host_captures(
                one,
                session=session,
                timeout=timeout,
                retries=retries,
                backoff=backoff,
                sleep_fn=sleep_fn,
            )
        )
    if urls:
        wanted = list(dict.fromkeys(urls))
    else:
        wanted = sorted(
            max(caps, key=lambda c: c.timestamp).original
            for caps in index.values()
        )
    if match:
        pattern = re.compile(match)
        wanted = [u for u in wanted if pattern.search(u)]
    return wanted, index


def recover(
    out_dir: str | Path,
    *,
    urls: str | Sequence[str] | None = None,
    host: str | None = None,
    match: str | None = None,
    prefer: str = "largest",
    verify: str = "pdf",
    session: Any = None,
    timeout: float = FETCH_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    backoff: float = DEFAULT_BACKOFF_SEC,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Iterator[dict]:
    """Recover documents from the archive, yielding one manifest row each.

    Give ``urls`` (one URL or a list) or ``host`` with an optional ``match``
    regex over the URL. Rows carry :data:`RECOVERY_FIELDS` plus ``reason``, and
    ``status`` is one of :data:`RECOVERY_STATUSES`.

    **The status distinctions are the product.** ``no-capture`` says the index
    holds nothing for this URL. ``throttled`` says the archive refused to serve
    bytes it has. ``unverified`` says every capture downloaded and none formed a
    whole document. An unreadable index raises :class:`IndexUnavailable` instead
    of reporting any of them.

    Rows are appended to ``<out_dir>/manifest.jsonl`` and files land under
    ``<out_dir>/recovered/``. Re-running re-verifies what is already on disk and
    refetches whatever fails.
    """
    out_dir = Path(out_dir)
    session = session if session is not None else make_session()
    predicate = verifier_for(verify)
    wanted, index = _targets(
        urls=urls,
        host=host,
        match=match,
        session=session,
        timeout=timeout,
        retries=retries,
        backoff=backoff,
        sleep_fn=sleep_fn,
    )
    manifest = out_dir / "manifest.jsonl"
    recorded = _recorded_rows(manifest)

    for url in wanted:
        row = recorded.get(url)
        if row is not None and _still_verifies(out_dir, row, predicate):
            yield row
            continue
        yield _append(manifest, _recover_one(
            url,
            out_dir,
            captures=index.get(canonical_key(url), []),
            prefer=prefer,
            verify_name=verify,
            predicate=predicate,
            session=session,
            timeout=timeout,
            retries=retries,
            backoff=backoff,
            sleep_fn=sleep_fn,
        ))


def _recover_one(
    url: str,
    out_dir: Path,
    *,
    captures: Sequence[Capture],
    prefer: str,
    verify_name: str,
    predicate: Callable[[bytes], bool],
    session: Any,
    timeout: float,
    retries: int,
    backoff: float,
    sleep_fn: Callable[[float], None],
) -> dict:
    """Walk one URL's captures until one verifies, or say what stopped it."""
    if not captures:
        return _row(url, status="no-capture", reason="the index holds no HTTP 200 capture")
    reasons: list[str] = []
    throttled = False
    # Whether any candidate delivered a body at all. Without it, a transport
    # failure on every capture reported `unverified`, which tells a reader the
    # archive's copies are broken when nothing was ever downloaded.
    got_bytes = False
    for capture in rank_captures(captures, prefer=prefer):
        body, reason, was_throttled = fetch_capture(
            capture,
            session=session,
            timeout=timeout,
            retries=retries,
            backoff=backoff,
            sleep_fn=sleep_fn,
        )
        throttled = throttled or was_throttled
        if body is None:
            reasons.append(reason)
            continue
        got_bytes = True
        if not predicate(body):
            reasons.append(f"{capture.timestamp}: failed verify ({verify_name})")
            continue
        dest = out_dir / "recovered" / _dest_name(url)
        _write_atomic(dest, body)
        return _row(
            url,
            status="ok",
            timestamp=capture.timestamp,
            local_file=str(dest.relative_to(out_dir)),
            size=len(body),
            digest=sha256_hex(body),
            reason="; ".join(reasons) or None,
        )
    if throttled:
        status = "throttled"
    elif got_bytes:
        status = "unverified"
    else:
        status = "fetch-failed"
    return _row(url, status=status, reason="; ".join(reasons))


def _row(
    url: str,
    *,
    status: str,
    timestamp: str | None = None,
    local_file: str | None = None,
    size: int | None = None,
    digest: str | None = None,
    reason: str | None = None,
) -> dict:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {
        "key": f"WAYBACK-RECOVER|{url}",
        "kind": MANIFEST_KIND,
        "record_type": MANIFEST_KIND,
        "source_family": "wayback",
        "source": "web.archive.org",
        "publisher": "Internet Archive",
        "source_url": url,
        "wayback_timestamp": timestamp,
        "snapshot_url": raw_replay_url(timestamp, url) if timestamp else None,
        "local_file": local_file,
        "bytes": size,
        "sha256": digest,
        "status": status,
        "reason": reason or None,
        "fetched_at": now,
    }


def _append(manifest: Path, row: dict) -> dict:
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row
