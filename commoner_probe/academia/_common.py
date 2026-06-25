# SPDX-License-Identifier: MIT
"""Shared helpers for the academia parsers.

Parsers are probe-native: they emit plain ``dict`` ads (not the origin's
Pydantic ``JobAd``), so the manifest stays dict/JSON-Schema based with zero new
runtime dependencies. ``post_type`` / ``contract_status`` values are the same
string vocabulary the origin enums used.
"""

from __future__ import annotations

import hashlib
from typing import Any

#: Parsers don't know their own registry slug; they set this and the probe
#: rewrites it to the real institution_id in ``_record``.
PLACEHOLDER_INSTITUTION_ID = "__placeholder__"

POST_TYPES = {"Faculty", "NonFaculty", "Scientific", "Administrative", "Research", "Contract", "Unknown"}
CONTRACT_STATUSES = {
    "Regular", "TenureTrack", "Contractual", "Guest", "AdHoc", "Visiting", "TFPP", "TTAP", "Unknown",
}


def stable_id(*parts: str) -> str:
    """SHA-256 of NUL-joined parts, first 16 hex chars (origin-compatible)."""
    m = hashlib.sha256()
    for p in parts:
        m.update((p or "").encode("utf-8"))
        m.update(b"\x00")
    return m.hexdigest()[:16]


def iso(fetched_at: Any) -> str:
    """Coerce a datetime/str to an ISO string."""
    return fetched_at.isoformat() if hasattr(fetched_at, "isoformat") else str(fetched_at)
