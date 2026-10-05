#!/usr/bin/env python3
"""Check whether each data source still returns current, parseable data.

This script is the engine of the scheduled source freshness check. For every
entry in the source registry it runs the adapter's own code path, checks that
the records have the fields the parsers need, fetches the newest record's
document, and evaluates the entry's freshness rules.

This module holds the data model, exception classification, date handling,
registry ordering, the per-source check sequence, and the run loop. The
statuses (`fresh`, `stale`, `broken`, `down`, `unreachable`, `geo-fenced`)
are defined in the "Statuses" table of
notes/specs/2026-10-05-source-freshness-check-design.md. `run_checks` reports
what one run observed. `resolve` turns observations into statuses across runs
with the two-run rule, the render functions write the committed files, and
`main` is the command-line entry point.

The script isn't shipped with the package, and it uses only what
`commoner-probe[all]` already installs.
"""

from __future__ import annotations

import argparse
import copy
import http.client
import importlib
import json
import math
import re
import socket
import ssl
import sys
import time
import urllib.error
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from commoner_probe import reachability
from commoner_probe.http_client import ChallengeDetected, make_session
from commoner_probe.url_safety import is_safe_url

FRESH, STALE, BROKEN, DOWN, UNREACHABLE, GEO_FENCED = (
    "fresh", "stale", "broken", "down", "unreachable", "geo-fenced")
HTTP_ERROR, NO_RESPONSE, CONTROL_FAILED = "http_error", "no_response", "control_failed"

_REASON_MAX = 200
_DOCUMENT_HEAD_BYTES = 2048
_NO_DOCUMENT_STATUSES = frozenset({202, 204})
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_HTTP_MESSAGE = re.compile(r"^HTTP (\d{3})\b")
# A TLS error whose text says the peer closed the connection mid-handshake is a
# dropped connection, not a rejected certificate or protocol.
_TLS_DROPPED = re.compile(r"EOF occurred in violation of protocol|UNEXPECTED_EOF", re.IGNORECASE)
# http_client raises these as ValueError. is_safe_url also returns False when the
# host doesn't resolve, so a DNS failure arrives in the same form.
_SSRF_REJECTED = re.compile(r"^(?:URL|Redirect target) rejected by SSRF guard: (\S+)")
# http_client gives up at once when a 429 or 5xx asks for a longer wait than it allows.
_RETRY_AFTER_CAP = re.compile(r"^server asked for Retry-After")


@dataclass(frozen=True)
class Source:
    id: str
    label: str
    host: str
    fetch: Callable[[], list[dict]]
    required: tuple[str, ...]
    record_date: Callable[[dict], str] | None = None  # returns ISO YYYY-MM-DD or ""
    document: Callable[[dict], str] | None = None  # returns a URL
    document_kind: str = "pdf"  # "pdf" | "spreadsheet" | "zip" | "any-non-html"
    document_headers: Mapping[str, str] = field(default_factory=dict)
    # False skips robots.txt for the document fetch. A function decides per document URL.
    document_respect_robots: bool | Callable[[str], bool] = True
    # Seconds between requests to the document's host, when the source needs
    # more than http_client's default. None keeps the default.
    document_rate_limit_sec: float | None = None
    total: Callable[[], int] | None = None  # total count, for CountFloor
    freshness: tuple[Rule, ...] = ()


@dataclass
class Observation:
    source_id: str
    outcome: str  # FRESH | STALE | BROKEN | GEO_FENCED | HTTP_ERROR | NO_RESPONSE | CONTROL_FAILED
    reason: str = ""
    newest: str = ""  # ISO date of the newest usable record date, or ""
    count: int | None = None
    http_status: int | None = None


@dataclass(frozen=True)
class RuleResult:
    ok: bool
    reason: str = ""


@dataclass
class Context:
    today: date
    results: dict[str, Observation]  # observations made earlier in this run
    records: dict[str, list[dict]]  # valid records of sources checked earlier in this run
    state: dict  # rolling state["sources"], keyed by source id
    geo_fenced: frozenset[str]  # hosts on the manual list


class Rule(Protocol):
    def depends_on(self) -> tuple[str, ...]: ...

    def evaluate(self, *, source_id: str, records: list[dict], newest: str,
                 count: int | None, ctx: Context) -> RuleResult: ...


# -- Freshness rules --------------------------------------------------------
# Every rule reads "today" from ctx.today, never from the system clock.


@dataclass(frozen=True)
class MaxAge:
    days: int

    def depends_on(self) -> tuple[str, ...]:
        return ()

    def evaluate(self, *, source_id: str, records: list[dict], newest: str,
                 count: int | None, ctx: Context) -> RuleResult:
        if not newest:
            return RuleResult(False, "no newest date to measure")
        age = (ctx.today - date.fromisoformat(newest)).days
        if age <= self.days:
            return RuleResult(True)
        return RuleResult(False, f"newest {newest} is {age} days old (limit {self.days})")


@dataclass(frozen=True)
class SessionAware:
    calendar_id: str = "sessions-ls"
    fallback_days: int = 120

    def depends_on(self) -> tuple[str, ...]:
        return (self.calendar_id,)

    def evaluate(self, *, source_id: str, records: list[dict], newest: str,
                 count: int | None, ctx: Context) -> RuleResult:
        threshold = self._last_ended_session_start(ctx)
        if threshold is None:
            fallback = MaxAge(self.fallback_days).evaluate(
                source_id=source_id, records=records, newest=newest, count=count, ctx=ctx)
            return RuleResult(fallback.ok, "calendar unavailable; " + fallback.reason)
        if newest >= threshold:
            return RuleResult(True)
        return RuleResult(
            False, f"newest {newest} is before the start of the last ended session ({threshold})")

    def _last_ended_session_start(self, ctx: Context) -> str | None:
        """Return the first sitting of the latest ended session, or None when the
        calendar isn't usable (not fresh this run, empty, or no ended session)."""
        obs = ctx.results.get(self.calendar_id)
        calendar = ctx.records.get(self.calendar_id)
        if obs is None or obs.outcome != FRESH or not calendar:
            return None
        # Skip a record whose dates aren't valid ISO dates: a calendar fault must
        # not break the sources that read it.
        ended: list[tuple[date, date]] = []
        for rec in calendar:
            if not isinstance(rec, dict):
                continue
            first, last = _parse_iso(rec.get("first_sitting")), _parse_iso(rec.get("last_sitting"))
            if first is not None and last is not None and last < ctx.today:
                ended.append((last, first))
        if not ended:
            return None
        return max(ended)[1].isoformat()


@dataclass(frozen=True)
class SiblingLag:
    other_id: str
    max_days: int

    def depends_on(self) -> tuple[str, ...]:
        return (self.other_id,)

    def evaluate(self, *, source_id: str, records: list[dict], newest: str,
                 count: int | None, ctx: Context) -> RuleResult:
        other = ctx.results.get(self.other_id)
        # A sibling counts only if it reached the freshness step. A BROKEN sibling
        # (for example a document failure) can still carry a newest date.
        if other is None or other.outcome not in (FRESH, STALE) or not other.newest:
            return RuleResult(True, "sibling unavailable")
        lag = (date.fromisoformat(other.newest) - date.fromisoformat(newest)).days
        if lag <= self.max_days:
            return RuleResult(True)
        return RuleResult(
            False,
            f"newest {newest} trails {self.other_id} ({other.newest}) by {lag} days "
            f"(limit {self.max_days})")


@dataclass(frozen=True)
class ExpectedEdition:
    release_month: int  # 1-12
    grace_days: int
    edition: Callable[[int], str]  # cycle year -> edition label, for example 2026 -> "2026-27"
    edition_of: Callable[[dict], str]  # record -> its edition label

    def depends_on(self) -> tuple[str, ...]:
        return ()

    def evaluate(self, *, source_id: str, records: list[dict], newest: str,
                 count: int | None, ctx: Context) -> RuleResult:
        this_year = self._deadline(ctx.today.year)
        cycle = ctx.today.year if ctx.today >= this_year else ctx.today.year - 1
        want = self.edition(cycle)
        # Before the next deadline, the next edition counts too: a source that
        # publishes early may already list only its replacement.
        accepted = {want, self.edition(cycle + 1)}
        for rec in records:
            try:
                if self.edition_of(rec) in accepted:
                    return RuleResult(True)
            except Exception:  # noqa: BLE001 - an odd record doesn't match
                continue
        return RuleResult(False, f"expected edition {want} (due {self._deadline(cycle)}) not found")

    def _deadline(self, year: int) -> date:
        return date(year, self.release_month, 1) + timedelta(days=self.grace_days)


@dataclass(frozen=True)
class Sentinel:
    lookup: Callable[[], dict | None]  # fetches one known record live
    title_of: Callable[[dict], str]
    title_contains: str

    def depends_on(self) -> tuple[str, ...]:
        return ()

    def evaluate(self, *, source_id: str, records: list[dict], newest: str,
                 count: int | None, ctx: Context) -> RuleResult:
        rec = self.lookup()  # an exception propagates, and check_source reports BROKEN
        if not rec:
            return RuleResult(False, "sentinel record not found")
        title = self.title_of(rec)
        if self.title_contains.lower() in title.lower():
            return RuleResult(True)
        return RuleResult(False, f"sentinel title changed: {title[:80]}")


@dataclass(frozen=True)
class CountFloor:
    ratio: float = 0.95

    def depends_on(self) -> tuple[str, ...]:
        return ()

    def evaluate(self, *, source_id: str, records: list[dict], newest: str,
                 count: int | None, ctx: Context) -> RuleResult:
        if count is None:
            raise ValueError("CountFloor needs a count")
        best = ctx.state.get(source_id, {}).get("max_count")
        if not best:
            return RuleResult(True, "no count baseline yet")
        if count >= math.floor(best * self.ratio):
            return RuleResult(True)
        return RuleResult(
            False, f"count {count} is below {self.ratio:.0%} of the highest seen ({best})")


def _truncate(text: str) -> str:
    return text[:_REASON_MAX]


def _describe(exc: BaseException) -> str:
    return _truncate(f"{type(exc).__name__}: {exc}")


def _tls_failure(exc: BaseException, requests: object) -> bool | None:
    """Return True for a TLS failure that repeats every run (a rejected
    certificate or protocol), False for a dropped TLS connection, and None when
    *exc* isn't a TLS error."""
    chain: list[BaseException] = []
    seen: BaseException | None = exc
    while seen is not None and seen not in chain:
        chain.append(seen)
        seen = seen.reason if isinstance(seen, urllib.error.URLError) and isinstance(
            seen.reason, BaseException) else (seen.__cause__ or seen.__context__)
    is_tls = any(isinstance(e, ssl.SSLError) for e in chain) or (
        requests is not None and isinstance(exc, requests.exceptions.SSLError))
    if not is_tls:
        return None
    dropped = any(isinstance(e, ssl.SSLEOFError) or _TLS_DROPPED.search(str(e)) for e in chain)
    return not dropped


def _rejected_for_dns(url: str) -> bool:
    """True when an SSRF-guard rejection of *url* came from a failed DNS lookup,
    or from one that has since recovered, rather than from an unsafe address."""
    host = urlparse(url).hostname
    if not host:
        return False
    try:
        socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return True
    return is_safe_url(url)


def classify_exception(exc: BaseException) -> tuple[str, int | None, str]:
    """Return (kind, http_status, reason). kind is HTTP_ERROR, NO_RESPONSE or BROKEN."""
    try:
        import requests
    except ImportError:  # the checker still classifies urllib and stdlib errors
        requests = None

    status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        return HTTP_ERROR, status, f"HTTP {status}"
    if isinstance(exc, urllib.error.HTTPError):
        return HTTP_ERROR, exc.code, f"HTTP {exc.code}"
    # A challenge page is a RuntimeError whose text can mention a status. It's a
    # 200 response that isn't data, so it's broken, never down.
    if isinstance(exc, ChallengeDetected):
        return BROKEN, None, _describe(exc)
    if isinstance(exc, RuntimeError):
        # The retry wrapper raises "HTTP 503 <url>" and the stdlib session's
        # raise_for_status raises "HTTP 503 for <url>".
        match = _HTTP_MESSAGE.match(str(exc))
        if match:
            code = int(match.group(1))
            return HTTP_ERROR, code, f"HTTP {code}"
        if _RETRY_AFTER_CAP.match(str(exc)):
            return HTTP_ERROR, None, _describe(exc)
    if isinstance(exc, ValueError):
        match = _SSRF_REJECTED.match(str(exc))
        if match and _rejected_for_dns(match.group(1)):
            host = urlparse(match.group(1)).hostname or match.group(1)
            return NO_RESPONSE, None, _truncate(f"DNS lookup failed for {host}: {_describe(exc)}")
    if requests is not None and isinstance(exc, requests.exceptions.RetryError):
        return HTTP_ERROR, None, _describe(exc)
    # requests' SSLError subclasses its ConnectionError, but a rejected
    # certificate or protocol means the host answered and fails the same way
    # every run, so it's broken. A dropped TLS connection is still no response.
    tls = _tls_failure(exc, requests)
    if tls:
        return BROKEN, None, _truncate("TLS handshake failed: " + _describe(exc))
    if tls is False:
        return NO_RESPONSE, None, _describe(exc)
    # A connection dropped mid-body is a network failure too. requests raises
    # ChunkedEncodingError for it, which isn't one of its ConnectionErrors.
    no_response: tuple[type[BaseException], ...] = (
        urllib.error.URLError, socket.gaierror, TimeoutError, ConnectionError,
        http.client.IncompleteRead)
    if requests is not None:
        no_response += (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                        requests.exceptions.ChunkedEncodingError)
    if isinstance(exc, no_response):
        return NO_RESPONSE, None, _describe(exc)
    return BROKEN, None, _describe(exc)


def _parse_iso(text: object) -> date | None:
    if not isinstance(text, str) or not _ISO_DATE.fullmatch(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def newest_date(records: list[dict], record_date: Callable[[dict], str], today: date) -> str:
    """Latest date that is valid ISO YYYY-MM-DD and not after today; "" if none."""
    best: date | None = None
    for rec in records:
        try:
            parsed = _parse_iso(record_date(rec))
        except Exception:  # noqa: BLE001 - a record that can't give a date is skipped
            continue
        if parsed is None or parsed > today:
            continue
        if best is None or parsed > best:
            best = parsed
    return best.isoformat() if best else ""


def order_sources(sources: Sequence[Source]) -> list[Source]:
    """Dependency order: every id a source's rules depend_on comes before it.
    Raise ValueError on a duplicate id, an unknown dependency, or a cycle."""
    by_id: dict[str, Source] = {}
    for source in sources:
        if source.id in by_id:
            raise ValueError(f"duplicate source id: {source.id}")
        by_id[source.id] = source

    def deps_of(source: Source) -> list[str]:
        found: list[str] = []
        for rule in source.freshness:
            for dep in rule.depends_on():
                if dep not in by_id:
                    raise ValueError(f"source {source.id} depends on unknown source: {dep}")
                if dep not in found:
                    found.append(dep)
        return found

    ordered: list[Source] = []
    done: set[str] = set()
    in_progress: list[str] = []

    def visit(source: Source) -> None:
        if source.id in done:
            return
        if source.id in in_progress:
            loop = in_progress[in_progress.index(source.id):] + [source.id]
            raise ValueError("dependency cycle: " + " -> ".join(loop))
        in_progress.append(source.id)
        for dep in deps_of(source):
            visit(by_id[dep])
        in_progress.pop()
        done.add(source.id)
        ordered.append(source)

    for source in sources:
        visit(source)
    return ordered


def _document_problem(kind: str, content_type: str, head: bytes) -> str:
    """Return why *head* isn't a document of *kind*, or "" when it is."""
    if kind not in ("pdf", "spreadsheet", "zip", "any-non-html"):
        raise ValueError(f"unknown document_kind: {kind}")
    if not head.strip():
        return "empty body"
    if kind == "pdf":
        return "" if head.startswith(b"%PDF") else "body isn't a PDF"
    if kind == "spreadsheet":
        ok = head.startswith(b"PK") or head.startswith(b"\xd0\xcf\x11\xe0")
        return "" if ok else "body isn't a spreadsheet"
    if kind == "zip":
        return "" if head.startswith(b"PK") else "body isn't a ZIP archive"
    if kind == "any-non-html":
        start = head.lstrip().lower()
        if "text/html" in content_type.lower() or start.startswith((b"<!doctype html", b"<html")):
            return "body is HTML"
    return ""


def check_source(source: Source, ctx: Context, *,
                 control: Callable[[], bool],
                 fetch_document: Callable[[str, Mapping[str, str]], tuple[int, str, bytes]],
                 ) -> tuple[Observation, list[dict]]:
    """Run the spec's check sequence for one source. Never raises.

    Return the observation and the valid records (empty when the check stopped
    before the contract step)."""
    valid: list[dict] = []
    try:
        return _check(source, ctx, control, fetch_document, valid)
    except Exception as exc:  # noqa: BLE001 - check_source never raises
        return Observation(source.id, BROKEN, _truncate(f"checker error: {_describe(exc)}")), valid


def _check(source: Source, ctx: Context, control: Callable[[], bool],
           fetch_document: Callable[[str, Mapping[str, str]], tuple[int, str, bytes]],
           valid: list[dict]) -> tuple[Observation, list[dict]]:
    sid = source.id
    if source.host in ctx.geo_fenced:
        return Observation(sid, GEO_FENCED), []

    try:
        records = source.fetch()
        count = source.total() if source.total is not None else None
    except Exception as exc:  # noqa: BLE001 - classified below
        kind, status, reason = classify_exception(exc)
        return Observation(sid, _confirm_no_response(kind, control), reason,
                           http_status=status), []
    if not isinstance(records, list):
        return Observation(
            sid, BROKEN, f"fetch returned {type(records).__name__}, not a list"), []
    if count is None:
        count = len(records)

    valid.extend(
        r for r in records
        if isinstance(r, dict) and all(r.get(f) not in (None, "") for f in source.required)
    )
    if not valid:
        fields = ", ".join(source.required)
        return Observation(
            sid, BROKEN, f"no record has all required fields: {fields}", count=count), []

    newest = ""
    if source.record_date is not None:
        newest = newest_date(valid, source.record_date, ctx.today)
        if not newest:
            return Observation(sid, BROKEN, "no parseable dates", count=count), valid

    if source.document is not None:
        kind, status, problem = _fetch_and_check_document(source, valid, newest, fetch_document)
        if problem:
            return Observation(
                sid, _confirm_no_response(kind, control), _truncate(f"document: {problem}"),
                newest=newest, count=count, http_status=status), valid

    failures: list[str] = []
    for rule in source.freshness:
        try:
            result = rule.evaluate(
                source_id=sid, records=valid, newest=newest, count=count, ctx=ctx)
        except Exception as exc:  # noqa: BLE001 - a broken rule is a checker problem
            reason = _truncate(f"rule {type(rule).__name__} raised {_describe(exc)}")
            return Observation(sid, BROKEN, reason, newest=newest, count=count), valid
        if not result.ok:
            failures.append(result.reason)
    if failures:
        return Observation(sid, STALE, "; ".join(failures), newest=newest, count=count), valid
    return Observation(sid, FRESH, newest=newest, count=count), valid


def _confirm_no_response(kind: str, control: Callable[[], bool]) -> str:
    """Run the positive control for a NO_RESPONSE kind; CONTROL_FAILED when it fails."""
    if kind != NO_RESPONSE:
        return kind
    try:
        reachable = bool(control())
    except Exception:  # noqa: BLE001 - a failing control is inconclusive
        reachable = False
    return NO_RESPONSE if reachable else CONTROL_FAILED


def _transient_status(status: int | None) -> bool:
    """A server error, a rate limit, or an exhausted retry budget (no status)."""
    return status is None or status >= 500 or status == 429


def _fetch_and_check_document(source: Source, valid: list[dict], newest: str,
                              fetch_document: Callable[[str, Mapping[str, str]],
                                                       tuple[int, str, bytes]],
                              ) -> tuple[str, int | None, str]:
    """Fetch the newest record's document. Return (kind, http_status, problem),
    with problem "" when the document is fine.

    A server error or no response is HTTP_ERROR or NO_RESPONSE, so the two-run
    rule applies to it as it does to the record fetch. Every other failure (a
    4xx status, a robots.txt refusal, a TLS failure, a wrong body) is BROKEN."""
    assert source.document is not None
    rec = valid[0]
    if source.record_date is not None:
        rec = next(
            (r for r in valid if _safe_date(source.record_date, r) == newest), valid[0])
    try:
        url = source.document(rec)
        respect = source.document_respect_robots
        if callable(respect):
            respect = respect(url)
        options: dict = {} if respect else {"respect_robots": False}
        if source.document_rate_limit_sec is not None:
            options["rate_limit_sec"] = source.document_rate_limit_sec
        status, content_type, head = fetch_document(url, source.document_headers, **options)
    except Exception as exc:  # noqa: BLE001 - classified below
        kind, code, reason = classify_exception(exc)
        if kind == HTTP_ERROR and not _transient_status(code):
            kind = BROKEN
        return kind, code, reason
    if status >= 400:
        kind = HTTP_ERROR if _transient_status(status) else BROKEN
        return kind, status, f"HTTP {status}"
    if status in _NO_DOCUMENT_STATUSES or status >= 300:
        # A WAF challenge answers 202 with no document. A 3xx reaching here is
        # a redirect the client didn't follow.
        return BROKEN, status, f"HTTP {status}: no document served"
    return BROKEN, None, _document_problem(source.document_kind, content_type or "", head)


def _safe_date(record_date: Callable[[dict], str], rec: dict) -> str:
    try:
        return record_date(rec)
    except Exception:  # noqa: BLE001
        return ""


def default_control() -> bool:
    """GET reachability.DEFAULT_CONTROL_URL through commoner_probe.http_client.make_session();
    True on a 2xx or 3xx status."""
    # respect_robots=False because the control retrieves no record: it asks only
    # whether the network answers, as reachability.status_via_session does.
    resp = make_session().get(
        reachability.DEFAULT_CONTROL_URL, timeout=30, respect_robots=False)
    try:
        return 200 <= resp.status_code < 400
    finally:
        _close(resp)


def _close(resp: object) -> None:
    # The stdlib fallback's StdlibResponse has no close method.
    close = getattr(resp, "close", None)
    if close:
        close()


def default_fetch_document(url: str, headers: Mapping[str, str], *,
                           respect_robots: bool = True,
                           rate_limit_sec: float | None = None) -> tuple[int, str, bytes]:
    """Return (status, content_type, first ≤2048 bytes) without downloading the whole file."""
    # http_client paces each domain across sessions, so a longer limit here also
    # spaces this request from the adapter's own requests to the same host.
    session = make_session() if rate_limit_sec is None else make_session(rate_limit_sec=rate_limit_sec)
    resp = session.get(url, headers=dict(headers), timeout=60, stream=True,
                       respect_robots=respect_robots)
    try:
        # A chunked response can yield a first chunk shorter than the magic bytes.
        head = b""
        for chunk in resp.iter_content(_DOCUMENT_HEAD_BYTES):
            head += chunk
            if len(head) >= _DOCUMENT_HEAD_BYTES:
                break
        content_type = resp.headers.get("Content-Type", "")
        return resp.status_code, content_type, head[:_DOCUMENT_HEAD_BYTES]
    finally:
        _close(resp)


def run_checks(sources: Sequence[Source], *, today: date, state: dict,
               geo_fenced: frozenset[str],
               control: Callable[[], bool] | None = None,
               fetch_document: Callable[[str, Mapping[str, str]], tuple[int, str, bytes]] | None = None,
               only: Collection[str] | None = None,
               progress: Callable[[str], None] | None = None,
               ) -> dict[str, Observation]:
    """Order the sources, restrict them to `only` plus transitive dependencies,
    and check each one, filling ctx.results and ctx.records as it goes.

    As each source finishes, `progress` gets one line with its position, ID,
    outcome, and time taken, so a CI log shows how far a run has got. It
    defaults to printing to stderr.

    `state` is the whole rolling-state dict (the state.json shape). The rules see
    its "sources" mapping as `ctx.state`.

    Raise ValueError for a registry error or an unknown ID in `only`, before any
    network call."""
    ordered = order_sources(sources)
    if only is not None:
        by_id = {s.id: s for s in ordered}
        unknown = sorted(set(only) - set(by_id))
        if unknown:
            raise ValueError("unknown source id in only: " + ", ".join(unknown))
        wanted: set[str] = set()
        pending = list(only)
        while pending:
            sid = pending.pop()
            if sid in wanted:
                continue
            wanted.add(sid)
            for rule in by_id[sid].freshness:
                pending.extend(rule.depends_on())
        ordered = [s for s in ordered if s.id in wanted]

    control = control or default_control
    fetch_document = fetch_document or default_fetch_document
    progress = progress or _print_progress
    ctx = Context(today=today, results={}, records={}, state=state.get("sources", {}),
                  geo_fenced=geo_fenced)
    for position, source in enumerate(ordered, 1):
        started = time.monotonic()
        obs, valid = check_source(source, ctx, control=control, fetch_document=fetch_document)
        progress(_progress_line(position, len(ordered), obs, time.monotonic() - started))
        ctx.results[source.id] = obs
        if obs.outcome != GEO_FENCED:
            ctx.records[source.id] = valid
    return ctx.results


_PROGRESS_REASON_MAX = 100


def _duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes}m {rest}s"


def _progress_line(position: int, total: int, obs: Observation, seconds: float) -> str:
    line = f"[{position}/{total}] {obs.source_id}: {obs.outcome.replace('_', ' ')} ({_duration(seconds)})"
    if obs.reason:
        line += ": " + " ".join(obs.reason.split())[:_PROGRESS_REASON_MAX]
    return line


def _print_progress(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


# -- Run-level resolution ---------------------------------------------------

_STATUS_ORDER = (BROKEN, STALE, DOWN, UNREACHABLE, GEO_FENCED, FRESH)
_NO_RESPONSE_RATIO = 0.8
_NO_RESPONSE_MIN_SOURCES = 5
_FAILS_BEFORE_REPORTING = 2
_VOLATILE_URL = re.compile(r"https?://\S+|(?<=url: )\S+")
_VOLATILE_NUMBER = re.compile(r"0x[0-9a-f]+|(?<!\d)(?<!HTTP )\d+", re.IGNORECASE)


def _reason_category(reason: str) -> str:
    """The kind of failure a reason describes: the reason with URLs and numbers
    masked, but HTTP statuses kept. A growing lag, a moving date, and a new
    document URL stay in one category; a different status or exception starts
    a new one."""
    return _VOLATILE_NUMBER.sub("#", _VOLATILE_URL.sub("<url>", reason))


@dataclass(frozen=True)
class Transition:
    source_id: str
    old: str | None  # status in master's committed sources.json, None if absent
    new: str | None  # None means removed from the registry
    reason: str


def inconclusive_reason(observations: Mapping[str, Observation]) -> str | None:
    """Return why the run can't be trusted, or None when it can."""
    if any(o.outcome == CONTROL_FAILED for o in observations.values()):
        return "the positive control failed: this runner's network is broken"
    checked = [o for o in observations.values() if o.outcome != GEO_FENCED]
    silent = sum(1 for o in checked if o.outcome == NO_RESPONSE)
    if len(checked) >= _NO_RESPONSE_MIN_SOURCES and silent > _NO_RESPONSE_RATIO * len(checked):
        return f"{silent} of {len(checked)} sources gave no response"
    return None


def _previous_fail_kind(prev: Mapping) -> str | None:
    """The kind of the failures *prev* counts, or None when it counts none.

    State written before fail_kind existed has only a status, which names the
    kind when it's down or unreachable."""
    if not prev.get("fail_count"):
        return None
    kind = prev.get("fail_kind")
    if kind in (HTTP_ERROR, NO_RESPONSE):
        return kind
    return {DOWN: HTTP_ERROR, UNREACHABLE: NO_RESPONSE}.get(prev.get("status"))


def resolve(observations: Mapping[str, Observation], *, committed: dict, state: dict,
            registry_ids: Collection[str], today: date,
            ) -> tuple[dict, dict, list[Transition], list[str]]:
    """Return (new_committed, new_state, transitions_vs_committed, pending_ids).

    `since` and `reason` come from state.json when it holds the new status, then
    from the committed entry when it holds the new status, and otherwise from
    this run. A remembered reason gives way to this run's reason when its
    category changes (see _reason_category), so a source that stays stale with
    a growing lag produces no diff. Neither input is modified."""
    old_sources = committed["sources"]
    new_sources = {sid: dict(entry) for sid, entry in old_sources.items()
                   if sid in registry_ids}
    new_state = copy.deepcopy(state) if state else {}
    new_state["version"] = 1
    new_state["last_run"] = today.isoformat()
    state_sources = new_state.setdefault("sources", {})
    pending: list[str] = []

    for sid, obs in observations.items():
        prev = state_sources.get(sid, {})
        committed_entry = old_sources.get(sid, {})
        prev_status = prev.get("status") or committed_entry.get("status")
        fail_count = prev.get("fail_count", 0)
        fail_kind: str | None = None

        if obs.outcome == GEO_FENCED:
            status, fail_count = GEO_FENCED, 0
            reason: str | None = obs.reason or "verify by hand"
        elif obs.outcome in (HTTP_ERROR, NO_RESPONSE):
            # down and unreachable each need consecutive failures of their own
            # kind, so a failure of the other kind, or of an unknown kind,
            # restarts the count.
            fail_kind = obs.outcome
            fail_count = fail_count + 1 if _previous_fail_kind(prev) == fail_kind else 1
            candidate = DOWN if obs.outcome == HTTP_ERROR else UNREACHABLE
            if fail_count >= _FAILS_BEFORE_REPORTING:
                status, reason = candidate, obs.reason
            else:
                status, reason = prev_status, None  # keep the previous status
        else:  # FRESH, STALE or BROKEN
            status, reason, fail_count = obs.outcome, obs.reason, 0

        entry: dict = {}
        if status is None:
            pending.append(sid)
            new_sources.pop(sid, None)
        else:
            # Memory order: state.json when it holds this status, then the
            # committed entry when it holds this status, else this run.
            if status == prev.get("status") and "since" in prev:
                memory: dict | None = prev
            elif status == committed_entry.get("status"):
                memory = committed_entry
            else:
                memory = None
            if memory is None:
                since = today.isoformat()
            else:
                since = memory.get("since", today.isoformat())
                # The manual reason comes from the hand-edited geo-fenced list, so
                # it's never volatile. Every other reason keeps its first wording
                # until its category changes, so the committed file changes only
                # when the cause does.
                remembered = memory.get("reason", "")
                if reason is None or (
                        status != GEO_FENCED
                        and _reason_category(reason) == _reason_category(remembered)):
                    reason = remembered
            new_sources[sid] = {"reason": reason or "", "since": since, "status": status}
            entry.update(since=since, reason=reason or "")
        entry.update(status=status, fail_count=fail_count)
        if fail_kind is not None:
            entry["fail_kind"] = fail_kind
        newest = obs.newest or prev.get("newest")
        if newest:
            entry["newest"] = newest
        counts = [c for c in (prev.get("max_count"), obs.count) if c is not None]
        if counts:
            entry["max_count"] = max(counts)
        state_sources[sid] = entry

    transitions: list[Transition] = []
    for sid, entry in new_sources.items():
        old = old_sources.get(sid, {}).get("status")
        if entry["status"] != old:
            transitions.append(Transition(sid, old, entry["status"], entry["reason"]))
    for sid, entry in old_sources.items():
        if sid not in registry_ids:
            transitions.append(
                Transition(sid, entry.get("status"), None, "removed from the registry"))
    transitions.sort(key=lambda t: t.source_id)

    new_committed = {"geo_fenced": committed["geo_fenced"], "sources": new_sources}
    return new_committed, new_state, transitions, pending


# -- Files ------------------------------------------------------------------


def load_committed(path: Path) -> dict:
    """Read staleness/sources.json. Raise ValueError when it's malformed."""
    if not path.exists():
        return {"geo_fenced": {}, "sources": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path}: can't read it as JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    for key in ("geo_fenced", "sources"):
        if key not in data:
            raise ValueError(f"{path}: missing the \"{key}\" key")
        if not isinstance(data[key], dict):
            raise ValueError(f"{path}: \"{key}\" must be an object")
    for sid, entry in data["sources"].items():
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: the entry for \"{sid}\" must be an object")
    return data


def load_state(path: Path) -> dict:
    """Read the rolling state. Return {} when it's missing or unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("sources", {}), dict):
        return {}
    # The artifact comes from an earlier run, so read it defensively: drop an
    # entry that isn't an object and a field of the wrong type.
    sources = {}
    for sid, entry in data.get("sources", {}).items():
        if not isinstance(entry, dict):
            continue
        sources[sid] = {
            key: value for key, value in entry.items()
            if (key in _STATE_TEXT_FIELDS and isinstance(value, str))
            or (key in _STATE_INT_FIELDS and isinstance(value, int)
                and not isinstance(value, bool))
        }
    data["sources"] = sources
    return data


_STATE_TEXT_FIELDS = frozenset({"status", "since", "reason", "newest", "fail_kind"})
_STATE_INT_FIELDS = frozenset({"fail_count", "max_count"})


def render_committed(committed: dict) -> str:
    return json.dumps(committed, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _table_order(by_id: Mapping[str, Source]) -> Callable[[tuple[str, dict]], tuple[int, str, str]]:
    """Sort key for status tables: non-fresh statuses first, then by label."""
    def order(item: tuple[str, dict]) -> tuple[int, str, str]:
        sid, entry = item
        status = entry.get("status")
        rank = _STATUS_ORDER.index(status) if status in _STATUS_ORDER else len(_STATUS_ORDER)
        source = by_id.get(sid)
        return rank, source.label if source else sid, sid

    return order


def render_sources_md(committed: dict, sources: Sequence[Source]) -> str:
    """Render SOURCES.md. The output is deterministic, with no timestamps."""
    by_id = {s.id: s for s in sources}
    order = _table_order(by_id)

    lines = [
        "<!-- Generated by scripts/check_sources.py. Don't edit this file. "
        "To change the geo-fenced list, edit staleness/sources.json. -->",
        "# Source freshness",
        "",
        "The weekly source freshness check writes this table. For what each status means, "
        'see the "Source freshness" section of the README.',
        "",
        "| Source | Host | Status | Since | Reason |",
        "|---|---|---|---|---|",
    ]
    for sid, entry in sorted(committed["sources"].items(), key=order):
        source = by_id.get(sid)
        lines.append(
            f"| {_cell(source.label if source else sid)} | {_cell(source.host if source else '')} "
            f"| `{_cell(entry.get('status', ''))}` | {_cell(entry.get('since', ''))} "
            f"| {_cell(entry.get('reason', ''))} |")
    if committed["geo_fenced"]:
        lines += ["", "## Geo-fenced hosts", "", "| Host | Note | Last verified |", "|---|---|---|"]
        for host, value in sorted(committed["geo_fenced"].items()):
            info = _geo_info(value)
            lines.append(
                f"| {_cell(host)} | {_cell(info.get('note', ''))} | {_cell(info.get('verified', ''))} |")
    return "\n".join(lines) + "\n"


STATUS_ICONS = {FRESH: "✅", STALE: "🟡", BROKEN: "❌", DOWN: "🔴", UNREACHABLE: "❓",
                GEO_FENCED: "🌐"}
_UNKNOWN_ICON = "⚪"
README_STATUS_START = "<!-- source-status:start -->"
README_STATUS_END = "<!-- source-status:end -->"


def render_readme_status(committed: dict, sources: Sequence[Source]) -> str:
    """Render the README's status block, markers included: a visible count of
    each status, then the full table, collapsed.

    The table mirrors SOURCES.md without the reasons, with an icon beside each
    status. The output is deterministic, with no timestamps."""
    by_id = {s.id: s for s in sources}
    entries = sorted(committed["sources"].items(), key=_table_order(by_id))
    counts: dict[str, int] = {}
    for _, entry in entries:
        status = str(entry.get("status", ""))
        counts[status] = counts.get(status, 0) + 1
    known = [FRESH] + [s for s in _STATUS_ORDER if s != FRESH]
    ordered = [s for s in known if s in counts] + sorted(s for s in counts if s not in known)
    tally = " · ".join(
        f"{counts[s]} {STATUS_ICONS.get(s, _UNKNOWN_ICON)} {_cell(s)}" for s in ordered)

    lines = [
        README_STATUS_START,
        "<!-- Generated by scripts/check_sources.py from staleness/sources.json. "
        "Don't edit this block. -->",
        f"**{tally}**",
        "",
        "<details>",
        f"<summary>All {len(entries)} sources</summary>",
        "",
        "| Status | Source | Host | Since |",
        "|---|---|---|---|",
    ]
    for sid, entry in entries:
        source = by_id.get(sid)
        status = str(entry.get("status", ""))
        lines.append(
            f"| {STATUS_ICONS.get(status, _UNKNOWN_ICON)} {_cell(status)} "
            f"| {_cell(source.label if source else sid)} | {_cell(source.host if source else '')} "
            f"| {_cell(entry.get('since', ''))} |")
    lines += [
        "",
        "For the reason behind each status, see [SOURCES.md](SOURCES.md).",
        "",
        "</details>",
        README_STATUS_END,
    ]
    return "\n".join(lines)


def replace_readme_status(text: str, block: str) -> str | None:
    """Return *text* with its marked status block replaced by *block*, or None
    when it has no start marker followed by an end marker."""
    start = text.find(README_STATUS_START)
    end = text.find(README_STATUS_END, start + 1) if start != -1 else -1
    if start == -1 or end == -1:
        return None
    return text[:start] + block + text[end + len(README_STATUS_END):]


def _update_readme(path: Path, block: str) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return
    updated = replace_readme_status(text, block)
    if updated is None:
        print(f"{path}: no source-status markers, so the status table isn't updated",
              file=sys.stderr)
        return
    _write_if_changed(path, updated)


def render_summary(observations: Mapping[str, Observation], pending: Sequence[str],
                   state: dict) -> str:
    """Render the per-source detail for the job summary. It's never committed."""
    lines = [
        "# Source freshness run",
        "",
        "| Source | Outcome | Newest | Count | HTTP status | Reason |",
        "|---|---|---|---|---|---|",
    ]
    for sid, obs in observations.items():
        lines.append(
            f"| `{sid}` | {obs.outcome.replace('_', ' ')} | {obs.newest} "
            f"| {'' if obs.count is None else obs.count} "
            f"| {'' if obs.http_status is None else obs.http_status} | {_cell(obs.reason)} |")
    lines += ["", "## Pending (first failure)", ""]
    if pending:
        fails = state.get("sources", {})
        for sid in pending:
            reason = observations[sid].reason if sid in observations else ""
            count = fails.get(sid, {}).get("fail_count", 1)
            lines.append(f"- `{sid}`: {_cell(reason)} (failures so far: {count})")
    else:
        lines.append("None.")
    return "\n".join(lines) + "\n"


def render_pr_body(transitions: Sequence[Transition]) -> str:
    lines = ["## Status changes", ""]
    if not transitions:
        lines.append("No source changed status.")
    for t in transitions:
        old = t.old if t.old is not None else "(new)"
        new = t.new if t.new is not None else "(removed)"
        detail = f" ({_cell(t.reason)})" if t.reason and t.new is not None else ""
        lines.append(f"- `{t.source_id}`: {old} → {new}{detail}")
    lines += [
        "",
        "`SOURCES.md` has the full table. Geo-fenced and unreachable hosts need manual "
        "verification.",
    ]
    return "\n".join(lines) + "\n"


# -- Command line -----------------------------------------------------------


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # type: ignore[override]
        # argparse exits 2 by default, but exit code 2 means "inconclusive" here.
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(1)


def _iso_date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} isn't an ISO date (YYYY-MM-DD)") from None


def _write_if_changed(path: Path, text: str) -> None:
    data = text.encode("utf-8")
    try:
        if path.read_bytes() == data:
            return
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _load_registry() -> Sequence[Source]:
    # Registry modules run `from check_sources import ...`. Register this module
    # under that name so they get this module object, even when it runs as __main__.
    sys.modules.setdefault("check_sources", sys.modules[__name__])
    script_dir = str(Path(__file__).resolve().parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    return importlib.import_module("source_registry").SOURCES


def _geo_info(value: object) -> dict:
    """Read one hand-edited geo-fenced entry. The expected shape is
    {"note": ..., "verified": "YYYY-MM-DD"}; a bare string is read as the note."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return {"note": value}
    return {}


def _manual_reason(committed: dict, host: str) -> str:
    verified = _geo_info(committed["geo_fenced"].get(host)).get("verified") or "unknown"
    return f"verify by hand (last verified {verified})"


def main(argv: Sequence[str] | None = None, *, registry: Sequence[Source] | None = None,
         control: Callable[[], bool] | None = None,
         fetch_document: Callable[[str, Mapping[str, str]], tuple[int, str, bytes]] | None = None,
         ) -> int:
    parser = _Parser(description="Check that each data source still returns current data.")
    parser.add_argument("--state", type=Path, default=Path("state.json"))
    parser.add_argument("--sources-json", type=Path, default=Path("staleness/sources.json"))
    parser.add_argument("--sources-md", type=Path, default=Path("SOURCES.md"))
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--pr-body", type=Path)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--only", action="append", metavar="ID")
    parser.add_argument("--today", type=_iso_date, default=None)
    args = parser.parse_args(argv)
    today = args.today or date.today()

    if registry is None:
        try:
            registry = _load_registry()
        except ImportError as exc:
            print(f"can't import the source registry (scripts/source_registry): {exc}",
                  file=sys.stderr)
            return 1
    try:
        committed = load_committed(args.sources_json)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    state = load_state(args.state)

    try:
        observations = run_checks(
            registry, today=today, state=state, geo_fenced=frozenset(committed["geo_fenced"]),
            control=control, fetch_document=fetch_document, only=args.only)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    hosts = {s.id: s.host for s in registry}
    for sid, obs in observations.items():
        if obs.outcome == GEO_FENCED:
            obs.reason = _manual_reason(committed, hosts[sid])

    inconclusive = inconclusive_reason(observations)
    if inconclusive:
        print(f"inconclusive: {inconclusive}", file=sys.stderr)
        if args.summary:
            _write_if_changed(args.summary, render_summary(observations, [], state))
        return 2

    new_committed, new_state, transitions, pending = resolve(
        observations, committed=committed, state=state,
        registry_ids={s.id for s in registry}, today=today)
    _write_if_changed(args.sources_json, render_committed(new_committed))
    _write_if_changed(args.sources_md, render_sources_md(new_committed, registry))
    _update_readme(args.readme, render_readme_status(new_committed, registry))
    _write_if_changed(args.state, json.dumps(new_state, indent=2, sort_keys=True) + "\n")
    if args.summary:
        _write_if_changed(args.summary, render_summary(observations, pending, new_state))
    if args.pr_body:
        _write_if_changed(args.pr_body, render_pr_body(transitions))
    if args.github_output:
        args.github_output.parent.mkdir(parents=True, exist_ok=True)
        with args.github_output.open("a", encoding="utf-8") as out:
            out.write(f"changes={len(transitions)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
