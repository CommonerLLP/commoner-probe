# SPDX-License-Identifier: MIT
"""Acquire case law from a documented judgment API.

CONTEXT
=======
Indian Kanoon operates the source. It is a private legal-search service.
The hosts are indiankanoon.org and api.indiankanoon.org.
The licence decides the architecture. The reference client is MIT. This module
therefore reimplements its wire contract.
The government eCourts service carries no such licence. This repo keeps that
service at arm's length.

Two sources were surveyed; **their licences decide the architecture**,
so the split below is legal, not stylistic:

* **Indian Kanoon** — ``sushant354/IKAPI`` is **MIT**, the same licence
  commoner-probe ships to PyPI under. Its API contract may be freely read,
  reimplemented, and redistributed. This module reimplements the wire contract
  directly against ``api.indiankanoon.org`` (no vendored code, no dependency)
  so the probe's own HTTP discipline — SSRF guard, rate limit, retry, manifest
  provenance — applies to court fetches like every other source.
* **eCourts** — ``openjustice-in/ecourts`` is **GPL-3.0**, which is copyleft.
  Importing it, vendoring it, or declaring it a dependency would relicense all
  of commoner-probe to GPL-3.0, for every installer who already has it under
  MIT. So it is reached **only across a process boundary**: a separately
  installed executable, invoked via ``subprocess``, its JSON read from stdout.
  There is deliberately no ``import ecourts`` in this repo and no entry for it
  in ``pyproject.toml`` — not even an optional extra. See ``run_ecourts``.

Wire contract for Indian Kanoon, read from IKAPI's own source 2026-07-26
(``python/ikapi.py``), not from memory:

* Host ``api.indiankanoon.org``, HTTPS.
* **Every endpoint is POST**, with parameters in the query string and no
  request body. A GET returns nothing useful.
* Headers ``Authorization: Token <token>`` and ``Accept: application/json``.
* ``/search/?formInput=<q>&pagenum=<n>&maxpages=<m>`` →
  ``{"found": "...", "docs": [{"tid", "title", "publishdate", "docsource",
  "numcites", "numcitedby", "headline"}, ...]}``; ``pagenum`` is 0-indexed and
  advances by ``maxpages``, which the API caps at 100.
* ``/docmeta/<tid>/``, ``/doc/<tid>/`` (both accept ``maxcites``/``maxcitedby``),
  ``/docfragment/<tid>/?formInput=<q>``, and ``/origdoc/<tid>/`` — the last
  returns the source file base64-encoded under a ``doc`` key.
* Errors do **not** reliably arrive as HTTP status codes: the body may be JSON
  carrying ``errmsg``, or a bare string starting ``error code:``. Both are
  checked — asserting on the response shape rather than the status code, the
  rule this repo keeps relearning.

Search filters are query *text*, not parameters — ``fromdate: DD-MM-YYYY``,
``todate: DD-MM-YYYY``, ``doctypes: <court>``, ``sortby: mostrecent`` are
appended to the query string itself (see ``build_query``).

**Egress**: ``api.indiankanoon.org`` is behind Cloudflare, and it answered a
live check from this repo's development egress on 2026-07-26 with HTTP 403
``Error 1010: Access denied`` — to both POST and GET, *before* looking at the
token. That is an edge-level client block, not an authentication failure, and
the two need opposite fixes, so ``call`` always reports the response body
rather than a bare status code. Same class of problem as MoSPI's India-egress
requirement: run from a network path the source accepts.

**Token**: paid, third-party, and read only from the ``INDIAN_KANOON_TOKEN``
environment variable. Never a CLI argument (it would land in shell history and
process listings) and never a file in this repo. Queries and the fact of them
leave the org — Indian Kanoon is a commercial index, not a primary source.
Judgments themselves are public record; the retrieval is what is paid for.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote_plus, urlparse

from .http_client import make_session

IK_API_BASE = "https://api.indiankanoon.org"
IK_TOKEN_ENV = "INDIAN_KANOON_TOKEN"
#: Public web permalink for a document id — the citable URL, not the API one.
IK_DOC_URL = "https://indiankanoon.org/doc/{docid}/"
#: The API caps maxpages at 100; IKAPI clamps client-side and so does this.
IK_MAX_PAGES_CAP = 100
DEFAULT_SLEEP = 1.0

#: Sort orders the API accepts. Anything else is silently ignored upstream,
#: which would look like "sorting did nothing" rather than an error.
IK_SORT_ORDERS = ("mostrecent", "leastrecent")

_ERROR_PREFIX_RE = re.compile(r"^\s*error code:", re.IGNORECASE)


class IndianKanoonError(RuntimeError):
    """The API refused, or returned something that is not a result set."""


class ECourtsUnavailable(RuntimeError):
    """No external eCourts executable is installed / configured."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def ik_token(explicit: str | None = None) -> str:
    """Resolve the Indian Kanoon API token, env var first.

    ``explicit`` exists for tests and for callers that already hold the token
    in memory; the CLI does not expose it, deliberately.
    """
    token = explicit or os.environ.get(IK_TOKEN_ENV, "")
    token = token.strip()
    if not token:
        raise IndianKanoonError(
            f"no Indian Kanoon API token — export {IK_TOKEN_ENV}=<token>. "
            "The token is paid and personal; it is never stored in this repo."
        )
    return token


def build_query(
    query: str,
    *,
    doctypes: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    sort_by: str | None = None,
) -> str:
    """Compose an Indian Kanoon query string.

    The API takes filters as query *text*, in the exact token forms below —
    they are not separate request parameters. Dates are ``DD-MM-YYYY``, which
    is the API's format and deliberately not normalised here: silently
    reformatting a date the caller typed would hide a mistake rather than
    surface it.
    """
    if sort_by and sort_by not in IK_SORT_ORDERS:
        raise ValueError(f"sort_by must be one of {IK_SORT_ORDERS}; got {sort_by!r}")
    parts = [query.strip()]
    if doctypes:
        parts.append(f"doctypes: {doctypes}")
    if from_date:
        parts.append(f"fromdate: {from_date}")
    if to_date:
        parts.append(f"todate: {to_date}")
    if sort_by:
        parts.append(f"sortby: {sort_by}")
    composed = " ".join(p for p in parts if p)
    if not composed:
        raise ValueError("empty query")
    return composed


def _decode_payload(body: str, *, url: str) -> Any:
    """Turn a response body into JSON, or raise with what actually came back.

    The API's failure modes are shape-level, not status-level: a bare
    ``error code: ...`` string, or JSON carrying ``errmsg``. Both are caught
    here so no caller can mistake either for a result set.
    """
    if _ERROR_PREFIX_RE.match(body or ""):
        raise IndianKanoonError(f"{url}: {body.strip()[:200]}")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        snippet = (body or "").strip()[:200]
        raise IndianKanoonError(f"{url}: response is not JSON — {snippet!r}") from exc
    if isinstance(payload, dict) and payload.get("errmsg"):
        raise IndianKanoonError(f"{url}: {payload['errmsg']}")
    return payload


def parse_search_response(payload: Any, *, query: str = "", start_position: int = 0) -> list[dict[str, Any]]:
    """Normalise one ``/search/`` payload into document rows.

    Pure function, unit-testable with a canned payload. An absent or empty
    ``docs`` list is the API's end-of-results signal and yields ``[]`` — not
    an error.
    """
    if not isinstance(payload, dict):
        raise IndianKanoonError(f"search payload is {type(payload).__name__}, expected an object")
    docs = payload.get("docs") or []
    rows: list[dict[str, Any]] = []
    for offset, doc in enumerate(docs):
        docid = doc.get("tid")
        if docid is None:
            continue
        rows.append(
            {
                "docid": int(docid),
                "title": (doc.get("title") or "").strip(),
                "court": (doc.get("docsource") or "").strip(),
                "judgment_date": doc.get("publishdate") or None,
                "num_cites": doc.get("numcites"),
                "num_cited_by": doc.get("numcitedby"),
                "headline": doc.get("headline") or None,
                "position": start_position + offset,
                "query": query,
                "url": IK_DOC_URL.format(docid=int(docid)),
            }
        )
    return rows


class IndianKanoonClient:
    """Thin POST client for ``api.indiankanoon.org``.

    Reimplements IKAPI's (MIT) wire contract on top of this repo's own session
    so court fetches inherit the SSRF guard, per-domain rate limit and 5xx
    backoff. ``robots.txt`` is not consulted: this is an authenticated,
    paid-for API call made under the caller's own credential, not crawling of
    a public web surface, and the API host serves no crawlable pages.
    """

    def __init__(
        self,
        token: str | None = None,
        *,
        base_url: str = IK_API_BASE,
        sleep: float = DEFAULT_SLEEP,
    ) -> None:
        self.token = ik_token(token)
        self.base_url = base_url.rstrip("/")
        self.sleep = sleep
        self.session = make_session(rate_limit_sec=sleep)

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Token {self.token}", "Accept": "application/json"}

    def call(self, path: str) -> Any:
        url = f"{self.base_url}{path}"
        r = self.session.post(url, headers=self._headers, timeout=120, respect_robots=False)
        body = r.text or ""
        if r.status_code >= 400:
            # Always carry the body. A bare "HTTP 403" is indistinguishable
            # between a bad token and Cloudflare's edge block (see the
            # egress note above), and those need opposite fixes.
            raise IndianKanoonError(f"{url}: HTTP {r.status_code} — {body.strip()[:200]}")
        # A 200 is not success on its own: the API reports real failures in
        # the body. Assert on shape, never the status code.
        return _decode_payload(body, url=url)

    def search(self, query: str, *, page_num: int = 0, max_pages: int = 1) -> Any:
        if page_num < 0:
            raise ValueError(f"page_num is 0-indexed and cannot be negative; got {page_num}")
        max_pages = max(1, min(max_pages, IK_MAX_PAGES_CAP))
        return self.call(
            f"/search/?formInput={quote_plus(query.encode('utf-8'))}"
            f"&pagenum={page_num}&maxpages={max_pages}"
        )

    def iter_search(
        self,
        query: str,
        *,
        max_records: int | None = None,
        max_pages: int = 1,
    ) -> Iterator[dict[str, Any]]:
        """Yield normalised rows, paging until the API runs out or the cap hits.

        ``pagenum`` advances by ``max_pages`` because that is what the API
        returns per call — incrementing by 1 instead re-fetches overlapping
        result windows (IKAPI's own loop does the same).
        """
        page_num = 0
        emitted = 0
        seen: set[int] = set()
        while True:
            rows = parse_search_response(
                self.search(query, page_num=page_num, max_pages=max_pages),
                query=query,
                start_position=emitted,
            )
            if not rows:
                return
            for row in rows:
                if row["docid"] in seen:
                    continue
                seen.add(row["docid"])
                yield row
                emitted += 1
                if max_records is not None and emitted >= max_records:
                    return
            page_num += max_pages
            if self.sleep:
                time.sleep(self.sleep)

    def doc(self, docid: int, *, max_cites: int = 0, max_cited_by: int = 0) -> Any:
        return self.call(f"/doc/{int(docid)}/{_cite_args(max_cites, max_cited_by)}")

    def doc_meta(self, docid: int, *, max_cites: int = 0, max_cited_by: int = 0) -> Any:
        return self.call(f"/docmeta/{int(docid)}/{_cite_args(max_cites, max_cited_by)}")

    def doc_fragment(self, docid: int, query: str) -> Any:
        return self.call(f"/docfragment/{int(docid)}/?formInput={quote_plus(query.encode('utf-8'))}")

    def original_document(self, docid: int) -> bytes:
        """Fetch the source file (usually a PDF), base64-decoded.

        The API returns it as base64 text under ``doc``; a missing or
        undecodable value is an error, not an empty document.
        """
        payload = self.call(f"/origdoc/{int(docid)}/")
        if not isinstance(payload, dict) or not payload.get("doc"):
            raise IndianKanoonError(f"origdoc {docid}: no 'doc' field in response")
        try:
            return base64.b64decode(payload["doc"], validate=True)
        except (binascii.Error, ValueError) as exc:
            raise IndianKanoonError(f"origdoc {docid}: 'doc' is not valid base64") from exc


def _cite_args(max_cites: int, max_cited_by: int) -> str:
    args = []
    if max_cites > 0:
        args.append(f"maxcites={int(max_cites)}")
    if max_cited_by > 0:
        args.append(f"maxcitedby={int(max_cited_by)}")
    return "?" + "&".join(args) if args else ""


class CourtProbe:
    """Acquire Indian Kanoon search results with provenance manifest rows."""

    def __init__(
        self,
        out_dir: Path,
        *,
        client: IndianKanoonClient | None = None,
        token: str | None = None,
        sleep: float = DEFAULT_SLEEP,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.client = client or IndianKanoonClient(token, sleep=sleep)
        self.manifest = self.out_dir / "manifest.jsonl"

    def _record(self, row: dict[str, Any], *, status: str) -> dict[str, Any]:
        now = _now_iso()
        filename = f"indiankanoon_{row['docid']}.pdf"
        return {
            "key": f"COURT|indiankanoon|{row['docid']}",
            "kind": "court_record",
            "record_type": "court_record",
            "source_family": "court",
            "provider": "indiankanoon",
            "source": urlparse(self.client.base_url).netloc,
            "docid": row["docid"],
            "title": row["title"],
            "court": row["court"],
            "judgment_date": row["judgment_date"],
            "num_cites": row["num_cites"],
            "num_cited_by": row["num_cited_by"],
            "headline": row["headline"],
            "position": row["position"],
            "query": row["query"],
            "url": row["url"],
            "filename": filename,
            "dest": str(self.out_dir / "courts" / filename),
            "status": status,
            "fetched_at": now,
            "probed_at": now,
        }

    def download_document(self, row: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
        """Fetch the original source file for one result, if asked.

        Metadata-only is the default everywhere else in this repo and is the
        default here too: ``origdoc`` is a separate billed call per document.
        """
        record = self._record(row, status="dry_run" if dry_run else "metadata_only")
        if dry_run:
            return record
        dest = Path(record["dest"])
        if dest.exists() and dest.stat().st_size > 0:
            body = dest.read_bytes()
            record["status"] = "skipped_exists"
            record["sha256"] = hashlib.sha256(body).hexdigest()
            record["bytes"] = len(body)
            return record
        try:
            body = self.client.original_document(row["docid"])
        except IndianKanoonError as exc:
            record["status"] = "error"
            record["error"] = str(exc)
            return record
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        record["status"] = "downloaded"
        record["sha256"] = hashlib.sha256(body).hexdigest()
        record["bytes"] = len(body)
        return record

    def append_manifest(self, record: dict[str, Any]) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def probe(
        self,
        query: str,
        *,
        max_records: int | None = None,
        max_pages: int = 1,
        download: bool = False,
        dry_run: bool = False,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for row in self.client.iter_search(query, max_records=max_records, max_pages=max_pages):
            if download:
                record = self.download_document(row, dry_run=dry_run)
            else:
                record = self._record(row, status="dry_run" if dry_run else "metadata_only")
            records.append(record)
            if not dry_run:
                self.append_manifest(record)
        return records


# ---------------------------------------------------------------------------
# eCourts — arm's-length only. Read the licence note at the top of this file
# before touching anything below.
# ---------------------------------------------------------------------------

#: Absolute path or command name of a separately-installed eCourts executable.
ECOURTS_CMD_ENV = "COMMONER_PROBE_ECOURTS_CMD"
ECOURTS_DEFAULT_CMD = "ecourts"


def ecourts_command(explicit: str | None = None) -> list[str] | None:
    """Resolve the external eCourts executable, or ``None`` if not installed.

    Honours ``$COMMONER_PROBE_ECOURTS_CMD``, which may carry arguments
    (``"python -m ecourts"``). Returns ``None`` rather than raising so callers
    can report "not installed" as a state instead of a crash.
    """
    raw = (explicit or os.environ.get(ECOURTS_CMD_ENV) or "").strip()
    if raw:
        # shlex, not split(): a path like "/Applications/My Tools/ecourts"
        # is one argument, and str.split would both break it in two and keep
        # the quote characters that were there to hold it together.
        try:
            parts = shlex.split(raw)
        except ValueError:
            return None
        if not parts:
            return None
        return parts if (Path(parts[0]).exists() or shutil.which(parts[0])) else None
    found = shutil.which(ECOURTS_DEFAULT_CMD)
    return [found] if found else None


def ecourts_available(explicit: str | None = None) -> bool:
    return ecourts_command(explicit) is not None


def _parse_ecourts_stdout(stdout: str) -> list[dict[str, Any]]:
    """Read the tool's stdout as JSON — an array, an object, or JSONL.

    The exact record shape is **not** normalised, because this repo has not
    verified it against a live run. Whatever the tool emits is carried through
    verbatim under ``raw``; inventing a mapping for unseen fields would be a
    guess wearing a schema.
    """
    text = (stdout or "").strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        rows = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ECourtsUnavailable(
                    f"eCourts output is neither JSON nor JSONL: {line[:120]!r}"
                ) from exc
            rows.append(obj)
        return [r for r in rows if isinstance(r, dict)]
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("results", "cases", "records", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
        return [payload]
    return []


def run_ecourts(
    args: list[str],
    *,
    command: str | None = None,
    timeout: float = 600.0,
) -> list[dict[str, Any]]:
    """Invoke a separately-installed eCourts tool and read its JSON stdout.

    **This is a process boundary, and the boundary is the point.**
    ``openjustice-in/ecourts`` is GPL-3.0; commoner-probe is MIT and published
    to PyPI. Importing or vendoring it would relicense this package for every
    installer. Running an executable the user installed themselves and reading
    its output is mere aggregation, which is what keeps the two licences apart.

    So: never add an ``import`` for it, never add it to ``pyproject.toml``, and
    never bundle it. If it is not installed, this raises — it does not fall
    back to something that looks like it worked.
    """
    cmd = ecourts_command(command)
    if cmd is None:
        raise ECourtsUnavailable(
            "no eCourts executable found. It is GPL-3.0 and deliberately NOT a "
            "dependency of this MIT package — install it yourself "
            "(github.com/openjustice-in/ecourts) and point "
            f"{ECOURTS_CMD_ENV} at it."
        )
    proc = subprocess.run(  # noqa: S603 — operator-configured command, not user input
        [*cmd, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip()[-400:]
        raise ECourtsUnavailable(f"eCourts exited {proc.returncode}: {tail}")
    return _parse_ecourts_stdout(proc.stdout)


def ecourts_record(raw: dict[str, Any], *, args: list[str], command: list[str] | None = None) -> dict[str, Any]:
    """Wrap one external eCourts record in this repo's provenance envelope.

    The tool's own fields are preserved verbatim under ``raw`` and hashed, so a
    downstream consumer can tell exactly what the external tool said and detect
    drift. Only the executable's basename is recorded — a full path would leak
    a local filesystem layout into a published manifest.
    """
    blob = json.dumps(raw, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    now = _now_iso()
    return {
        "key": f"COURT|ecourts|{digest[:16]}",
        "kind": "court_record",
        "record_type": "court_record",
        "source_family": "court",
        "provider": "ecourts",
        "source": "openjustice-in/ecourts",
        "acquired_via": "external-tool-subprocess",
        "tool_command": Path((command or ecourts_command() or ["ecourts"])[0]).name,
        "tool_args": list(args),
        "raw": raw,
        "raw_sha256": digest,
        "status": "metadata_only",
        "fetched_at": now,
        "probed_at": now,
    }
