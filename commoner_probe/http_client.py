# SPDX-License-Identifier: MIT
"""HTTP client for probe — state mandatory-disclosure portals.

Design (after the origin project's scraper/fetch.py)
---------------------------------------------
Everything in the first three bullets holds on BOTH sessions — the requests-
backed RetrySession and the zero-dependency StdlibSession. That is deliberate:
`dependencies = []` makes the stdlib path the DEFAULT install, so a guarantee
that held only with an extra installed would be false for most users. It was
false until 2026-08-01, when the fallback had none of these and this docstring
claimed all of them.

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
  implementation is used. It carries the SSRF guard, the robots check and the
  rate limit, but has no retry, no backoff and no cache — which also means no
  429 or `Retry-After` handling. It is safe to point at a government portal;
  it is not equipped to crawl one at volume. Use commoner-probe[http] for that.

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


#: Ceiling for a response body read into memory or written to disk. Government
#: portals mis-serve: a redirect to a video, a directory listing generated
#: forever, a 20 GB dump behind a link that looks like a PDF. Large enough for
#: the biggest artefact this package legitimately fetches (a ~200 MB census
#: ZIP), small enough that the failure is an exception rather than the machine.
MAX_RESPONSE_BYTES = 512 * 1024 * 1024


class ResponseTooLarge(RuntimeError):
    """A response body outran its ceiling. Raised, never truncated.

    Truncating would hand the caller a short file that looks complete — the
    same silent success the atomic-PDF-write fix exists to prevent.
    """


def iter_capped(resp: Any, *, max_bytes: int | None = None, chunk_size: int = 16384):
    """Yield a response's chunks, refusing once *max_bytes* have arrived.

    Counts the bytes that actually arrive rather than trusting Content-Length,
    which is a number the server supplies about itself.

    The ceiling defaults to ``MAX_RESPONSE_BYTES`` read at CALL time. A default
    argument would freeze the value at import, so raising or lowering the
    constant would change nothing — a knob that cannot turn.
    """
    if max_bytes is None:
        max_bytes = MAX_RESPONSE_BYTES
    seen = 0
    for chunk in resp.iter_content(chunk_size=chunk_size):
        if not chunk:
            continue
        seen += len(chunk)
        if seen > max_bytes:
            raise ResponseTooLarge(
                f"response exceeds the {max_bytes} byte ceiling (stopped at {seen})"
            )
        yield chunk


def read_capped_response(resp: Any, *, max_bytes: int | None = None, chunk_size: int = 16384) -> bytes:
    """The whole body, or ``ResponseTooLarge`` before it is all in memory.

    ``b"".join(resp.iter_content(...))`` passes ``stream=True`` and then
    buffers everything anyway, so streaming bought nothing.
    """
    return b"".join(iter_capped(resp, max_bytes=max_bytes, chunk_size=chunk_size))


def get_capped(session: Any, url: str, *, max_bytes: int | None = None, **kwargs: Any) -> bytes:
    """GET *url* and return the body, bounded by *max_bytes*.

    For the ``body = session.get(...).content`` shape, which buffers whatever
    the server sends. Requests streams; :class:`StdlibSession` cannot, so it
    applies the same ceiling to its own read.
    """
    kwargs.setdefault("stream", True)
    resp = session.get(url, **kwargs)
    resp.raise_for_status()
    return read_capped_response(resp, max_bytes=max_bytes)


class StdlibSession:
    """The session a default ``pip install commoner-probe`` gets.

    ``dependencies = []`` means this is not an edge case — with no extras
    installed there is no ``requests``, so every probe in the package runs
    through this class. It therefore applies the same SSRF, robots and
    rate-limit policy as :class:`RetrySession`; the module docstring's
    guarantees hold on both paths or they are not guarantees.

    None of that costs a dependency. ``is_safe_url`` is stdlib-only, and
    ``_get_robot_parser`` / ``_rate_limit`` are defined above the ``import
    requests`` in this module so both sessions can reach them.

    What this still does NOT do, and callers should know: no retry, no
    backoff, no response cache. Those need ``commoner-probe[http]``.
    """

    def __init__(
        self,
        *,
        user_agent: str | None = None,
        rate_limit_sec: float = DEFAULT_RATE_LIMIT_SEC,
    ) -> None:
        self.headers: dict[str, str] = {"User-Agent": user_agent or USER_AGENT}
        self._user_agent = user_agent or USER_AGENT
        self.rate_limit_sec = rate_limit_sec

    def get(self, url: str, *, respect_robots: bool = True, **kwargs: Any) -> StdlibResponse:
        return self._request("GET", url, respect_robots=respect_robots, **kwargs)

    def post(self, url: str, *, respect_robots: bool = True, **kwargs: Any) -> StdlibResponse:
        return self._request("POST", url, respect_robots=respect_robots, **kwargs)

    def _request(
        self, method: str, url: str, *, respect_robots: bool = True, **kwargs: Any
    ) -> StdlibResponse:
        # Same order as RetrySession._request: reject, then ask permission,
        # then wait our turn. Checked BEFORE the params are appended, because
        # the scheme and host are what the guard judges and neither changes.
        headers = {**self.headers, **(kwargs.get("headers") or {})}
        # Ask permission as the identity we will actually send. A per-request
        # or mutated User-Agent made the robots check answer for a crawler that
        # never made the request.
        user_agent = headers.get("User-Agent") or self._user_agent
        if not is_safe_url(url):
            raise ValueError(f"URL rejected by SSRF guard: {url}")
        if respect_robots:
            rp = _get_robot_parser(url, user_agent=user_agent)
            if not rp.can_fetch(user_agent, url):
                raise PermissionError(f"Disallowed by robots.txt: {url}")
        _rate_limit(urlparse(url).netloc, self.rate_limit_sec)

        params = kwargs.get("params")
        if params:
            sep = "&" if "?" in url else "?"
            url = url + sep + urlencode(params)
        timeout = kwargs.get("timeout") or 60
        body = kwargs.get("data")
        if body is None and kwargs.get("json") is not None:
            # The requests path reads `json=`; this one read only `data=` and
            # dropped it in silence, so a default install posted an EMPTY body
            # and the server answered as if the caller had sent nothing. Encode
            # it here rather than in each caller: every adapter shares this
            # client, and the next one would repeat the bug.
            body = json.dumps(kwargs["json"]).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        if isinstance(body, str):
            body = body.encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        opener = urllib.request.build_opener(self._redirect_handler())
        try:
            with opener.open(req, timeout=timeout) as resp:
                # Bounded read: urllib buffers, so `stream=True` cannot help
                # here. One byte past the ceiling is enough to tell.
                body = resp.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise ResponseTooLarge(
                        f"{url} exceeds the {MAX_RESPONSE_BYTES} byte ceiling"
                    )
                return StdlibResponse(url, resp.status, body)
        except urllib.error.HTTPError as exc:
            return StdlibResponse(url, exc.code, exc.read())

    @staticmethod
    def _redirect_handler() -> urllib.request.HTTPRedirectHandler:
        """Re-run the SSRF guard on every redirect target.

        urllib follows redirects inside ``urlopen``, so the guard saw only the
        first URL. A public host answering 302 to ``http://169.254.169.254/``
        reached the cloud metadata service with the guard none the wiser.
        """

        class _GuardedRedirects(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                if not is_safe_url(newurl):
                    raise ValueError(f"Redirect target rejected by SSRF guard: {newurl}")
                return super().redirect_request(req, fp, code, msg, headers, newurl)

        return _GuardedRedirects()


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
                        # Only wait when another attempt remains. Sleeping after
                        # the last one costs the caller the full delay with no
                        # request left to make — a persistent 429 with
                        # Retry-After: 30 burned 30s after the budget was spent
                        # (Codex, PR #97).
                        if attempt < MAX_RETRIES - 1:
                            time.sleep(_retry_delay(attempt, resp))
                        continue
                    return resp
                except requests.RequestException as exc:
                    last_exc = exc
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(_retry_delay(attempt, None))
            raise last_exc or RuntimeError(f"max retries exceeded for {url}")

        # Every verb that makes a request must go through _request. Only get
        # and post were wrapped, so `session.head(url)` — the natural way to
        # check a document's size or type before downloading it — fell through
        # __getattr__ to the bare requests.Session and skipped the SSRF guard,
        # the robots check, the rate limit and the backoff. The comment above
        # post records that this trap was already hit once; wrapping the rest
        # closes the general case rather than the next instance of it.
        def head(self, url: str, *, respect_robots: bool = True, **kwargs: Any) -> Any:
            # requests.Session.head defaults allow_redirects to False and
            # Session.request defaults it to True. Routing HEAD through request
            # silently flipped it, so a size check on a redirecting URL started
            # fetching the target instead of reporting the 302.
            kwargs.setdefault("allow_redirects", False)
            return self._request("HEAD", url, respect_robots=respect_robots, **kwargs)

        def put(self, url: str, *, respect_robots: bool = True, **kwargs: Any) -> Any:
            return self._request("PUT", url, respect_robots=respect_robots, **kwargs)

        def patch(self, url: str, *, respect_robots: bool = True, **kwargs: Any) -> Any:
            return self._request("PATCH", url, respect_robots=respect_robots, **kwargs)

        def delete(self, url: str, *, respect_robots: bool = True, **kwargs: Any) -> Any:
            return self._request("DELETE", url, respect_robots=respect_robots, **kwargs)

        def request(self, method: str, url: str, *, respect_robots: bool = True, **kwargs: Any) -> Any:
            return self._request(method, url, respect_robots=respect_robots, **kwargs)

        def __getattr__(self, name: str) -> Any:
            # A future requests verb would still slip past the guards here, so
            # refuse rather than forward it silently. Non-request attributes
            # (headers, cookies, mount, close, ...) pass through as before.
            if name in {"options", "connect", "trace"}:
                raise AttributeError(
                    f"{name!r} is not wrapped by RetrySession, so it would bypass the "
                    "SSRF guard, robots check and rate limit. Use .request(method, url) "
                    "or add an explicit wrapper."
                )
            return getattr(self._session, name)

    def make_session(rate_limit_sec: float = DEFAULT_RATE_LIMIT_SEC, *, user_agent: str | None = None) -> RetrySession:
        return RetrySession(rate_limit_sec=rate_limit_sec, user_agent=user_agent)

else:
    # Stdlib fallback. Same SSRF guard, robots check and rate limit as
    # RetrySession — see StdlibSession's docstring for why that is not
    # optional. Still no retry, no backoff and no cache: those need
    # `commoner-probe[http]`.
    # StdlibResponse/StdlibSession are defined at module level above.
    requests = types.SimpleNamespace(Session=StdlibSession)  # type: ignore[assignment]

    def make_session(rate_limit_sec: float = DEFAULT_RATE_LIMIT_SEC, *, user_agent: str | None = None) -> StdlibSession:  # type: ignore[misc]
        return StdlibSession(user_agent=user_agent, rate_limit_sec=rate_limit_sec)
