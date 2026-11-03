# SPDX-License-Identifier: MIT
"""Aligarh Muslim University: the JSON job list behind ``www.amu.ac.in``.

Target: ``https://api.amu.ac.in/api/v1/getjobs?lang=en``

The public jobs page is a JavaScript shell. It reads this endpoint, which
returns 20 entries per page, newest first. The registry fetches page 1 only.

``api.amu.ac.in`` publishes ``robots.txt`` with ``Disallow: /``, and no other
route to the list exists (checked 2026-11-03: the sitemap has no job pages).
The registry row therefore carries ``robots_override``, and that override is a
recorded decision, not a default.

Each entry's ``file`` is a path such as ``/file/8003/jobs/1793440897.pdf``. It
serves under ``/storage``. The bare path on the same host redirects to the
host root, so a link built without ``/storage`` reads as a dead link.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from .._common import stable_id
from ..ad_factory import make_ad
from ..pdf_text import parse_deadline_iso, read_deadline
from .parser_utils import classify_document, classify_post_type, extract_department

FILE_BASE = "https://api.amu.ac.in/storage"


def _rows(payload: Any) -> list[dict]:
    """The entry list, from ``{"data": {"data": {"data": [...]}}}``."""
    node = payload
    for _ in range(3):
        if not isinstance(node, dict):
            return []
        node = node.get("data")
    return [r for r in node if isinstance(r, dict)] if isinstance(node, list) else []


def parse(html: str, url: str, fetched_at: Any, pdf: Callable | None = None) -> list[dict]:
    try:
        payload = json.loads(html or "")
    except ValueError:
        return []

    ads: list[dict] = []
    for row in _rows(payload):
        title = " ".join(str(row.get("title") or "").split())
        path = row.get("file")
        if not title or not path or row.get("deleted_at"):
            continue
        if row.get("approval_status") not in (None, "Approved"):
            continue
        abs_url = FILE_BASE + "/" + str(path).lstrip("/")

        pdf_path = text = None
        if pdf is not None and abs_url.lower().endswith(".pdf"):
            pdf_path, text = pdf.pdf_text(abs_url)
        raw_deadline, deadline_status = read_deadline(text)
        closing_iso = parse_deadline_iso(raw_deadline)
        if raw_deadline and not closing_iso:
            deadline_status = "not_found"

        ads.append(make_ad(
            id=stable_id("amu", str(row.get("id") or abs_url)),
            title=title[:250],
            original_url=abs_url,
            snapshot_fetched_at=fetched_at,
            department=extract_department(title),
            post_type=classify_post_type(title),
            publication_date=(row.get("created_at") or "")[:10] or None,
            closing_date=closing_iso,
            closing_date_status=deadline_status,
            document_class=classify_document(title, abs_url),
            apply_url=abs_url,
            info_url="https://www.amu.ac.in/employment-news",
            parse_confidence=0.6,
            raw_text_excerpt=title[:500],
            pdf_path=pdf_path,
            pdf_parsed=bool(text and text.strip()),
        ))
    return ads
