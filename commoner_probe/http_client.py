# SPDX-License-Identifier: MIT
"""HTTP client for probe — state mandatory-disclosure portals.

Design (after academiaindia/scraper/fetch.py)
---------------------------------------------
- SSRF guard: every URL is checked against url_safety.is_safe_url() before
  the first request. Rejects non-http(s), unresolvable hosts, and any URL
  resolving to private/loopback/link-local/reserved IP space.
- robots.txt: checked per domain before the first request to that domain.
  Fail-open — if robots.txt cannot be fetched, the request proceeds. A URL
  explicitly disallowed raises PermissionError. Cached per domain for the
  lifetime of the session.
- Per-domain rate limiting: 1 req/s default, enforced globally across all
  sessions via a module-level last-request dict.
- Exponential backoff on 5xx and network errors: up to MAX_RETRIES attempts,
  sleep capped at 30s. Government portals 429/503 without warning.
- requests_cache (optional, 6h TTL, stale_if_error=True): if the upstream
  returns 5xx or raises a network error AND a stale cached copy exists, the
  stale copy is served — corpora must survive portal downtime.
  Install via: pip install commoner-probe[cache]
  Without it, a plain requests.Session is used (no caching).
- User-Agent identifies the library so portal operators can reach us.
- Stdlib fallback: if requests is not installed at all, a minimal urllib-based
  implementation is used (no retry, no cache) for zero-dependency environments.

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
# Bound on the robots.txt fetch. urllib.robotparser.RobotFileParser.read()
# calls urlopen() with no timeout and will hang indefinitely against a host
# that accepts the connection but never responds (observed against some
# government portals). We fetch robots.txt ourselves with this timeout instead.
ROBOTS_TIMEOUT_SEC = 10.0

_last_request_by_domain: dict[str, float] = {}
_robot_parsers: dict[str, urllib.robotparser.RobotFileParser] = {}


def _get_robot_parser(url: str) -> urllib.robotparser.RobotFileParser:
    """Return a cached RobotFileParser for the domain of *url*.

    Fail-open: if robots.txt cannot be fetched (network error, timeout, 404,
    etc.) the returned parser allows all paths. Government portals routinely
    omit robots.txt; a fetch failure must never block legitimate archival work.

    Unlike ``RobotFileParser.read()`` — which calls ``urlopen`` with no timeout
    and can hang indefinitely against a host that never responds — this fetches
    robots.txt with a bounded ``ROBOTS_TIMEOUT_SEC`` timeout, then hands the body
    to ``RobotFileParser.parse``. HTTP-status handling mirrors ``read()``:
    401/403 disallow everything, other failures fail open.
    """
    parsed = urlparse(url)
    domain = parsed.netloc
    if domain not in _robot_parsers:
        rp = urllib.robotparser.RobotFileParser()
        robots_url = f"{parsed.scheme}://{domain}/robots.txt"
        rp.set_url(robots_url)
        try:
            req = urllib.request.Request(robots_url, headers={"User-Agent": USER_AGENT})
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
        _robot_parsers[domain] = rp
    return _robot_parsers[domain]


def _cache_dir() -> Path:
    override = os.environ.get("COMMONER_CACHE_DIR") or os.environ.get("SANSAD_CACHE_DIR")
    p = Path(override) if override else Path(os.environ.get("TMPDIR", "/tmp")) / "commoner_probe_http_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _rate_limit(domain: str, min_interval_sec: float) -> None:
    last = _last_request_by_domain.get(domain, 0.0)
    wait = min_interval_sec - (time.monotonic() - last)
    if wait > 0:
        time.sleep(wait)
    _last_request_by_domain[domain] = time.monotonic()


try:
    import requests  # type: ignore[import]

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

        def __init__(self, rate_limit_sec: float = DEFAULT_RATE_LIMIT_SEC) -> None:
            self._session = _make_base_session()
            self.rate_limit_sec = rate_limit_sec
            self.headers = self._session.headers

        def get(self, url: str, *, respect_robots: bool = True, **kwargs: Any) -> Any:
            if not is_safe_url(url):
                raise ValueError(f"URL rejected by SSRF guard: {url}")
            # ``respect_robots=False`` is an explicit, per-call opt-out for
            # public-interest official sources (e.g. a recruitment portal that
            # blanket-disallows crawlers); callers gate it on registry config.
            if respect_robots:
                rp = _get_robot_parser(url)
                if not rp.can_fetch(USER_AGENT, url):
                    raise PermissionError(f"Disallowed by robots.txt: {url}")
            domain = urlparse(url).netloc
            _rate_limit(domain, self.rate_limit_sec)
            last_exc: Exception | None = None
            for attempt in range(MAX_RETRIES):
                try:
                    resp = self._session.get(url, **kwargs)
                    if 500 <= resp.status_code < 600:
                        last_exc = RuntimeError(f"HTTP {resp.status_code} {url}")
                        time.sleep(min(30, 2 ** attempt))
                        continue
                    return resp
                except requests.RequestException as exc:
                    last_exc = exc
                    time.sleep(min(30, 2 ** attempt))
            raise last_exc or RuntimeError(f"max retries exceeded for {url}")

        def __getattr__(self, name: str) -> Any:
            return getattr(self._session, name)

    def make_session(rate_limit_sec: float = DEFAULT_RATE_LIMIT_SEC) -> RetrySession:
        return RetrySession(rate_limit_sec=rate_limit_sec)

except ModuleNotFoundError:
    # Stdlib fallback — no SSRF guard, no retry, no cache, no rate-limit.
    # Sufficient for zero-dependency installs and test environments.

    class StdlibResponse:  # type: ignore[no-redef]
        def __init__(self, url: str, status_code: int, body: bytes) -> None:
            self.url = url
            self.status_code = status_code
            self._body = body
            self.text = body.decode("utf-8", errors="replace")

        def json(self) -> dict | list:
            return json.loads(self.text)

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code} for {self.url}")

        def iter_content(self, chunk_size: int = 16384):
            for i in range(0, len(self._body), chunk_size):
                yield self._body[i : i + chunk_size]

    class StdlibSession:  # type: ignore[no-redef]
        def __init__(self) -> None:
            self.headers: dict[str, str] = {"User-Agent": USER_AGENT}

        def get(self, url: str, **kwargs: Any) -> StdlibResponse:
            params = kwargs.get("params")
            if params:
                sep = "&" if "?" in url else "?"
                url = url + sep + urlencode(params)
            headers = {**self.headers, **(kwargs.get("headers") or {})}
            timeout = kwargs.get("timeout") or 60
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return StdlibResponse(url, resp.status, resp.read())
            except urllib.error.HTTPError as exc:
                return StdlibResponse(url, exc.code, exc.read())

    requests = types.SimpleNamespace(Session=StdlibSession)  # type: ignore[assignment]

    def make_session(rate_limit_sec: float = DEFAULT_RATE_LIMIT_SEC) -> StdlibSession:  # type: ignore[misc]
        return StdlibSession()
