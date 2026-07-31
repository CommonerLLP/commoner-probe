# SPDX-License-Identifier: MIT
"""HTTP client for probe — state mandatory-disclosure portals.

Design (after academiaindia/scraper/fetch.py)
---------------------------------------------
- SSRF guard: every URL is checked against url_safety.is_safe_url() before
  the first request. Rejects non-http(s), unresolvable hosts, and any URL
  resolving to private/loopback/link-local/reserved IP space.
- robots.txt: checked per domain before the first request to that domain.
  Fail-open — if robots.txt cannot be fetched, the request proceeds. A URL
  explicitly disallowed raises PermissionError. Cached per (domain,
  User-Agent) for the lifetime of the session — two request identities
  against one host get independent parsers, so the first UA's result
  (e.g. a WAF 403 read as disallow-all) never leaks into another UA's
  session (Codex review, PR #41).
- Per-domain rate limiting: 1 req/s default, enforced globally across all
  sessions via a module-level last-request dict.
- Exponential backoff with equal jitter on 5xx, 429 and network errors: up to
  MAX_RETRIES attempts, sleep capped at 30s. `Retry-After` is honoured when the
  server sends one; a value above RETRY_AFTER_MAX_SEC raises rather than
  blocking the process. Government portals 429/503 without warning, and a 429
  returned to the caller unretried is how a polite crawler gets blocked.
- requests_cache (optional, 6h TTL, stale_if_error=True): if the upstream
  returns 5xx or raises a network error AND a stale cached copy exists, the
  stale copy is served — corpora must survive portal downtime.
  Install via: pip install commoner-probe[cache]
  Without it, a plain requests.Session is used (no caching).
- User-Agent identifies the library so portal operators can reach us.
- Stdlib fallback: if requests is not installed at all, a minimal urllib-based
  implementation is used (no retry, no cache) for zero-dependency environments.
  No retry means no 429 or `Retry-After` handling either — the fallback exists
  for zero-dependency installs, not for crawling government portals at volume.

Call-site contract
------------------
All existing callers use `session.get(url, ...)`. RetrySession preserves this
interface exactly — no call-site changes required.

Cache location
--------------
Defaults to $TMPDIR/commoner_probe_http_cache/. Override via COMMONER_CACHE_DIR (deprecated: SANSAD_CACHE_DIR).
"""

from __future__ import annotations

import json
import os
import random
import time
import types
import urllib.error
import urllib.request
import urllib.robotparser
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

from .url_safety import is_safe_url

try:
    from importlib.metadata import version as _importlib_version
    TOOL_VERSION = _importlib_version("commoner-probe")
except Exception:
    TOOL_VERSION = "0.0.0"
USER_AGENT = (
    f"commoner-probe/{TOOL_VERSION} "
    "(+https://github.com/CommonerLLP/commoner-probe; "
    "public-interest research; rate-limited)"
)

DEFAULT_RATE_LIMIT_SEC = 1.0
CACHE_TTL_SEC = 6 * 3600
MAX_RETRIES = 3
# Statuses worth retrying beyond 5xx. A 429 is the portal asking for a slower
# rate; returning it to the caller unretried, and then continuing at the normal
# cadence, is how a polite crawler becomes a blocked one.
RETRYABLE_STATUSES = frozenset({429})
# Longest `Retry-After` this client will wait out. A portal asking for more than
# this is telling us to stop, not to sleep through it holding the process.
RETRY_AFTER_MAX_SEC = 30.0
# Bound on the robots.txt fetch. urllib.robotparser.RobotFileParser.read()
# calls urlopen() with no timeout and will hang indefinitely against a host
# that accepts the connection but never responds (observed against some
# government portals). We fetch robots.txt ourselves with this timeout instead.
ROBOTS_TIMEOUT_SEC = 10.0

_last_request_by_domain: dict[str, float] = {}
# Keyed by (domain, user_agent): the robots.txt fetch is made with a specific
# identity and the parsed rules are UA-specific, so keying by domain alone
# let the first session's result stick for every later session with a
# different UA against the same host (Codex review, PR #41).
_robot_parsers: dict[tuple[str, str], urllib.robotparser.RobotFileParser] = {}


def _get_robot_parser(url: str, *, user_agent: str = USER_AGENT) -> urllib.robotparser.RobotFileParser:
    """Return a cached RobotFileParser for the domain of *url*.

    Fail-open: if robots.txt cannot be fetched (network error, timeout, 404,
    etc.) the returned parser allows all paths. Government portals routinely
    omit robots.txt; a fetch failure must never block legitimate archival work.

    Unlike ``RobotFileParser.read()`` — which calls ``urlopen`` with no timeout
    and can hang indefinitely against a host that never responds — this fetches
    robots.txt with a bounded ``ROBOTS_TIMEOUT_SEC`` timeout, then hands the body
    to ``RobotFileParser.parse``. HTTP-status handling mirrors ``read()``:
    401/403 disallow everything, other failures fail open.

    *user_agent* is used for the robots.txt fetch itself and is part of the
    cache key — the fetch identity must match the identity that will actually
    request pages, or the check is meaningless. One WAF-gated government
    portal (mha.gov.in, verified 2026-07-09) returns 403 for commoner-probe's
    default URL-bearing User-Agent specifically (the same class of
    false-positive documented for indiabudget.gov.in in ``budget/probe.py``'s
    ``BUDGET_USER_AGENT``), which this fail-open design would otherwise turn
    into a real robots block via the 401/403 branch below — even though the
    site has no robots.txt at all.
    """
    parsed = urlparse(url)
    key = (parsed.netloc, user_agent)
    if key not in _robot_parsers:
        rp = urllib.robotparser.RobotFileParser()
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        rp.set_url(robots_url)
        try:
            req = urllib.request.Request(robots_url, headers={"User-Agent": user_agent})
            with urllib.request.urlopen(req, timeout=ROBOTS_TIMEOUT_SEC) as resp:
                raw = resp.read()
            rp.parse(raw.decode("utf-8", errors="replace").splitlines())
        except urllib.error.HTTPError as err:
            # Mirror RobotFileParser.read(): unauthorized/forbidden robots.txt
            # means "disallow all"; any other 4xx/5xx falls through to fail-open.
            if err.code in (401, 403):
                rp.disallow_all = True
            else:
                rp.allow_all = True
        except Exception:
            # Network error, timeout, or malformed body — fail open.
            rp.allow_all = True
        _robot_parsers[key] = rp
    return _robot_parsers[key]


def _cache_dir() -> Path:
    override = os.environ.get("COMMONER_CACHE_DIR") or os.environ.get("SANSAD_CACHE_DIR")
    p = Path(override) if override else Path(os.environ.get("TMPDIR", "/tmp")) / "commoner_probe_http_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _retry_delay(attempt: int, resp: Any) -> float:
    """Seconds to wait before retry *attempt*.

    Honours ``Retry-After`` when the server sends one, capped at
    ``RETRY_AFTER_MAX_SEC`` — above that this raises rather than blocking the
    process for however long the portal named.

    Otherwise exponential backoff with *equal* jitter: half the window fixed,
    half random, so concurrent clients do not retry in lockstep. Full jitter
    (``uniform(0, base)``) is deliberately not used — a delay that can round to
    zero is not a backoff.
    """
    after = None
    if resp is not None:
        headers = getattr(resp, "headers", None) or {}
        after = headers.get("Retry-After") or headers.get("retry-after")
    if after is not None:
        try:
            seconds = float(str(after).strip())
        except ValueError:
            seconds = None  # HTTP-date form; fall through to plain backoff
        if seconds is not None:
            if seconds > RETRY_AFTER_MAX_SEC:
                raise RuntimeError(
                    f"server asked for Retry-After: {seconds:g}s, above the "
                    f"{RETRY_AFTER_MAX_SEC:g}s cap — stopping rather than blocking"
                )
            return max(0.0, seconds)
    base = min(30.0, 2.0 ** attempt)
    return base / 2 + random.uniform(0, base / 2)


def _rate_limit(domain: str, min_interval_sec: float) -> None:
    last = _last_request_by_domain.get(domain, 0.0)
    wait = min_interval_sec - (time.monotonic() - last)
    if wait > 0:
        time.sleep(wait)
    _last_request_by_domain[domain] = time.monotonic()


try:
    import requests  # type: ignore[import]
except ModuleNotFoundError:
    requests = None  # type: ignore[assignment]


class StdlibResponse:
    """Zero-dependency stand-in for ``requests.Response``.

    Used when ``requests`` is not installed at all. Defined unconditionally
    (not inside the fallback branch) so the fallback contract is importable
    and testable in every environment. ``content`` is part of that contract:
    PDF adapters (ddg, doe, dspace, debates) read ``r.content`` for binary
    downloads — without it the stdlib fallback crashed with AttributeError
    on every document fetch (Codex review, PR #41).
    """

    def __init__(self, url: str, status_code: int, body: bytes) -> None:
        self.url = url
        self.status_code = status_code
        self._body = body
        self.text = body.decode("utf-8", errors="replace")

    @property
    def content(self) -> bytes:
        return self._body

    def json(self) -> dict | list:
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code} for {self.url}")

    def iter_content(self, chunk_size: int = 16384):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i : i + chunk_size]


class StdlibSession:
    def __init__(self, *, user_agent: str | None = None) -> None:
        self.headers: dict[str, str] = {"User-Agent": user_agent or USER_AGENT}

    def get(self, url: str, **kwargs: Any) -> StdlibResponse:
        return self._request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> StdlibResponse:
        return self._request("POST", url, **kwargs)

    def _request(self, method: str, url: str, **kwargs: Any) -> StdlibResponse:
        params = kwargs.get("params")
        if params:
            sep = "&" if "?" in url else "?"
            url = url + sep + urlencode(params)
        headers = {**self.headers, **(kwargs.get("headers") or {})}
        timeout = kwargs.get("timeout") or 60
        body = kwargs.get("data")
        if isinstance(body, str):
            body = body.encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return StdlibResponse(url, resp.status, resp.read())
        except urllib.error.HTTPError as exc:
            return StdlibResponse(url, exc.code, exc.read())


if requests is not None:
    try:
        from requests_cache import CachedSession  # type: ignore[import]

        def _make_base_session() -> Any:
            s = CachedSession(
                cache_name=str(_cache_dir() / "http_cache"),
                expire_after=CACHE_TTL_SEC,
                allowable_methods=("GET", "HEAD"),
                stale_if_error=True,
            )
            s.headers.update({"User-Agent": USER_AGENT})
            return s

    except ImportError:

        def _make_base_session() -> Any:  # type: ignore[misc]
            s = requests.Session()
            s.headers.update({"User-Agent": USER_AGENT})
            return s

    class RetrySession:
        """requests.Session wrapper with SSRF guard, per-domain rate-limit,
        and 5xx backoff. Preserves the .get() / .headers interface.
        """

        def __init__(self, rate_limit_sec: float = DEFAULT_RATE_LIMIT_SEC, *, user_agent: str | None = None) -> None:
            self._session = _make_base_session()
            self.rate_limit_sec = rate_limit_sec
            self._user_agent = user_agent or USER_AGENT
            if user_agent:
                self._session.headers["User-Agent"] = user_agent
            self.headers = self._session.headers

        def get(self, url: str, *, respect_robots: bool = True, **kwargs: Any) -> Any:
            return self._request("GET", url, respect_robots=respect_robots, **kwargs)

        def post(self, url: str, *, respect_robots: bool = True, **kwargs: Any) -> Any:
            # Explicit, so it shadows __getattr__'s passthrough to the bare
            # requests.Session — which would silently skip the SSRF guard,
            # rate limit, and 5xx backoff that every other request gets.
            # Needed by API sources that take POST (api.indiankanoon.org).
            return self._request("POST", url, respect_robots=respect_robots, **kwargs)

        def _request(self, method: str, url: str, *, respect_robots: bool = True, **kwargs: Any) -> Any:
            if not is_safe_url(url):
                raise ValueError(f"URL rejected by SSRF guard: {url}")
            # ``respect_robots=False`` is an explicit, per-call opt-out for
            # public-interest official sources (e.g. a recruitment portal that
            # blanket-disallows crawlers); callers gate it on registry config.
            if respect_robots:
                rp = _get_robot_parser(url, user_agent=self._user_agent)
                if not rp.can_fetch(self._user_agent, url):
                    raise PermissionError(f"Disallowed by robots.txt: {url}")
            domain = urlparse(url).netloc
            _rate_limit(domain, self.rate_limit_sec)
            last_exc: Exception | None = None
            for attempt in range(MAX_RETRIES):
                try:
                    resp = self._session.request(method, url, **kwargs)
                    if (
                        500 <= resp.status_code < 600
                        or resp.status_code in RETRYABLE_STATUSES
                    ):
                        last_exc = RuntimeError(f"HTTP {resp.status_code} {url}")
                        time.sleep(_retry_delay(attempt, resp))
                        continue
                    return resp
                except requests.RequestException as exc:
                    last_exc = exc
                    time.sleep(_retry_delay(attempt, None))
            raise last_exc or RuntimeError(f"max retries exceeded for {url}")

        def __getattr__(self, name: str) -> Any:
            return getattr(self._session, name)

    def make_session(rate_limit_sec: float = DEFAULT_RATE_LIMIT_SEC, *, user_agent: str | None = None) -> RetrySession:
        return RetrySession(rate_limit_sec=rate_limit_sec, user_agent=user_agent)

else:
    # Stdlib fallback — no SSRF guard, no retry, no cache, no rate-limit.
    # Sufficient for zero-dependency installs and test environments.
    # StdlibResponse/StdlibSession are defined at module level above.
    requests = types.SimpleNamespace(Session=StdlibSession)  # type: ignore[assignment]

    def make_session(rate_limit_sec: float = DEFAULT_RATE_LIMIT_SEC, *, user_agent: str | None = None) -> StdlibSession:  # type: ignore[misc]
        return StdlibSession(user_agent=user_agent)
