# SPDX-License-Identifier: MIT
"""Store one public web page, HTML or JSON, exactly as received.

CONTEXT
=======
Some public records are a single page: an Ontario Environmental Registry
notice, a LEGISinfo bill in JSON. This adapter fetches that page and keeps the
bytes unchanged, with the content type and a sha256. It does not parse. The
caller reads the stored file.

A page is a live snapshot. Every call fetches fresh into a new run directory;
nothing is skipped because an earlier run exists.

The caller says what it expects. A JSON expectation that gets an HTML page, an
empty body, or a WAF challenge is refused rather than stored: a challenge page
saved under a ``.json`` name would pass for the record.

With ``expect="html"`` only two checks run: the WAF response headers and an
empty body. A WAF block page that comes back with status 200 and no WAF header,
for example Imperva's "Request unsuccessful", is stored as ``downloaded``.
The caller must check the stored content.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from commoner_probe.base import _private_tmp_path
from commoner_probe.http_client import ChallengeDetected, _uncached, get_capped_response

EXPECTATIONS = {"html": "page.html", "json": "page.json"}


def _header(headers: Any, name: str) -> str:
    if not hasattr(headers, "get"):
        return ""
    return str(headers.get(name) or headers.get(name.lower()) or "")


def fetch_page(
    session: Any,
    url: str,
    out_dir: Path,
    *,
    expect: str,
    max_bytes: int = 20_000_000,
) -> dict:
    """Fetch *url*, store the body unparsed, and return the manifest record.

    The file is written to ``out_dir/run-<UTC time>/page.<html|json>``, so a
    rerun never overwrites an earlier file. The record is appended to
    ``out_dir/manifest.jsonl`` only after the body is fully received, checked
    and stored. Raises ``ValueError`` for an unknown *expect* or a JSON
    expectation that did not parse, and ``ChallengeDetected`` for a WAF
    challenge, an empty body, or an HTML page where JSON was expected.
    """
    if expect not in EXPECTATIONS:
        raise ValueError(f"expect must be 'html' or 'json', got {expect!r}")
    with _uncached(session):
        resp, body = get_capped_response(session, url, max_bytes=max_bytes, refuse_challenges=True, timeout=60)
    arrived = datetime.now(timezone.utc)
    headers = getattr(resp, "headers", None) or {}
    text = body.decode("utf-8", errors="replace")
    if expect == "json":
        try:
            json.loads(body)
        except ValueError as exc:
            if text.lstrip().lower().startswith(("<!doctype", "<html")):
                raise ChallengeDetected(f"{url}: an HTML page came back where JSON was expected") from exc
            raise ValueError(f"response is not JSON: {url}") from exc

    out_dir = Path(out_dir)
    run_dir = out_dir / arrived.strftime("run-%Y%m%dT%H%M%S%fZ")
    run_dir.mkdir(parents=True, exist_ok=True)
    dest = run_dir / EXPECTATIONS[expect]
    tmp = _private_tmp_path(dest)
    try:
        tmp.write_bytes(body)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    record = {
        "kind": "portal_page",
        "source_family": "portal_page",
        "source_url": url,
        "expect": expect,
        "content_type": _header(headers, "Content-Type"),
        "dest": str(dest),
        "fetched_at": arrived.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "sha256": hashlib.sha256(body).hexdigest(),
        "bytes": len(body),
        "status": "downloaded",
    }
    with (out_dir / "manifest.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record
