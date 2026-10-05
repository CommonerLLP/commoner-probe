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
notes/specs/2026-10-05-source-freshness-check-design.md. The functions here
report what one run observed. Turning observations into statuses across runs
is a separate step.

The script isn't shipped with the package, and it uses only what
`commoner-probe[all]` already installs.
"""

from __future__ import annotations

import math
import re
import socket
import urllib.error
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Protocol

from commoner_probe import reachability
from commoner_probe.http_client import ChallengeDetected, make_session

FRESH, STALE, BROKEN, DOWN, UNREACHABLE, GEO_FENCED = (
    "fresh", "stale", "broken", "down", "unreachable", "geo-fenced")
HTTP_ERROR, NO_RESPONSE, CONTROL_FAILED = "http_error", "no_response", "control_failed"

_REASON_MAX = 200
_DOCUMENT_HEAD_BYTES = 2048
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_HTTP_MESSAGE = re.compile(r"^HTTP (\d{3})\b")


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
        for rec in records:
            try:
                if self.edition_of(rec) == want:
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
    if requests is not None and isinstance(exc, requests.exceptions.RetryError):
        return HTTP_ERROR, None, _describe(exc)
    no_response: tuple[type[BaseException], ...] = (
        urllib.error.URLError, socket.gaierror, TimeoutError, ConnectionError)
    if requests is not None:
        no_response += (requests.exceptions.ConnectionError, requests.exceptions.Timeout)
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
    raise ValueError(f"unknown document_kind: {kind}")


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
        if kind == NO_RESPONSE:
            try:
                reachable = bool(control())
            except Exception:  # noqa: BLE001 - a failing control is inconclusive
                reachable = False
            kind = NO_RESPONSE if reachable else CONTROL_FAILED
        return Observation(sid, kind, reason, http_status=status), []
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
        problem = _fetch_and_check_document(source, valid, newest, fetch_document)
        if problem:
            return Observation(
                sid, BROKEN, _truncate(f"document: {problem}"), newest=newest, count=count), valid

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


def _fetch_and_check_document(source: Source, valid: list[dict], newest: str,
                              fetch_document: Callable[[str, Mapping[str, str]],
                                                       tuple[int, str, bytes]]) -> str:
    """Fetch the newest record's document. Return the problem, or "" when it's fine."""
    assert source.document is not None
    rec = valid[0]
    if source.record_date is not None:
        rec = next(
            (r for r in valid if _safe_date(source.record_date, r) == newest), valid[0])
    try:
        status, content_type, head = fetch_document(source.document(rec), source.document_headers)
        if status >= 400:
            return f"HTTP {status}"
        return _document_problem(source.document_kind, content_type or "", head)
    except Exception as exc:  # noqa: BLE001 - any document failure is a parser problem
        return _describe(exc)


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
        close = getattr(resp, "close", None)
        if close:
            close()


def default_fetch_document(url: str, headers: Mapping[str, str]) -> tuple[int, str, bytes]:
    """Return (status, content_type, first ≤2048 bytes) without downloading the whole file."""
    resp = make_session().get(url, headers=dict(headers), timeout=60, stream=True)
    try:
        head = next(resp.iter_content(_DOCUMENT_HEAD_BYTES), b"")
        content_type = resp.headers.get("Content-Type", "")
        return resp.status_code, content_type, head[:_DOCUMENT_HEAD_BYTES]
    finally:
        resp.close()


def run_checks(sources: Sequence[Source], *, today: date, state: dict,
               geo_fenced: frozenset[str],
               control: Callable[[], bool] | None = None,
               fetch_document: Callable[[str, Mapping[str, str]], tuple[int, str, bytes]] | None = None,
               only: Collection[str] | None = None,
               ) -> dict[str, Observation]:
    """Order the sources, restrict them to `only` plus transitive dependencies,
    and check each one, filling ctx.results and ctx.records as it goes.

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
    ctx = Context(today=today, results={}, records={}, state=state.get("sources", {}),
                  geo_fenced=geo_fenced)
    for source in ordered:
        obs, valid = check_source(source, ctx, control=control, fetch_document=fetch_document)
        ctx.results[source.id] = obs
        if obs.outcome != GEO_FENCED:
            ctx.records[source.id] = valid
    return ctx.results
