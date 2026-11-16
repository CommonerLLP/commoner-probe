# SPDX-License-Identifier: MIT
"""Fetch the SEC EDGAR submissions JSON for one company.

CONTEXT
=======
``https://data.sec.gov/submissions/CIK{cik:010}.json`` lists a company's
filings. SEC's fair-access policy requires a User-Agent that names the
requester and gives contact details, and caps requests at 10 per second. This
adapter refuses to run without a contact and sends
``commoner-probe/<version> (<contact>)`` on every request. The default session
rate limit (one request per second) is already inside the SEC cap; a caller
must not pass a faster one.

A submissions file is a live snapshot. Every call fetches fresh into a new run
directory; nothing is skipped because an earlier run exists.

Acquisition only. The body is stored as received, with a sha256 in the manifest.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from commoner_probe.base import _private_tmp_path
from commoner_probe.http_client import TOOL_VERSION, get_capped

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"


def user_agent(contact: str | None) -> str:
    """Return the SEC-compliant User-Agent, or raise when *contact* is empty."""
    if not contact or not contact.strip():
        raise ValueError("SEC EDGAR requires a contact (email or URL) in the User-Agent; pass contact=")
    return f"commoner-probe/{TOOL_VERSION} ({contact.strip()})"


def fetch_submissions(
    session: Any,
    cik: str,
    out_dir: Path,
    *,
    contact: str | None,
    max_bytes: int = 50_000_000,
) -> dict:
    """Fetch and store the submissions JSON for *cik*; return the parsed body.

    The file is written to ``out_dir/run-<UTC time>/CIK<10 digits>.json``, so a
    rerun never overwrites an earlier file. The record is appended to
    ``out_dir/manifest.jsonl`` only after the body is fully received, parsed and
    stored. Raises ``ValueError`` for a missing contact, a non-numeric CIK, or a
    body that is not a submissions object.
    """
    ua = user_agent(contact)
    cik_text = str(cik).strip()
    if not cik_text.isdigit() or int(cik_text) == 0 or len(cik_text) > 10:
        raise ValueError(f"CIK must be 1 to 10 digits, got {cik!r}")
    cik_int = int(cik_text)
    url = SUBMISSIONS_URL.format(cik=cik_int)
    body = get_capped(session, url, max_bytes=max_bytes, timeout=60, headers={"User-Agent": ua})
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise ValueError(f"response is not JSON: {url}") from exc
    if not isinstance(data, dict) or "name" not in data:
        raise ValueError(f"response is not a submissions object: {url}")

    out_dir = Path(out_dir)
    run_dir = out_dir / datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%S%fZ")
    run_dir.mkdir(parents=True, exist_ok=True)
    dest = run_dir / f"CIK{cik_int:010d}.json"
    tmp = _private_tmp_path(dest)
    try:
        tmp.write_bytes(body)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    record = {
        "kind": "sec_edgar_submissions",
        "source_family": "sec_edgar",
        "source_url": url,
        "cik": f"{cik_int:010d}",
        "entity_name": data["name"],
        "dest": str(dest),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "sha256": hashlib.sha256(body).hexdigest(),
        "bytes": len(body),
        "status": "downloaded",
    }
    with (out_dir / "manifest.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return data
