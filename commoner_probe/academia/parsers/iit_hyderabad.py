# SPDX-License-Identifier: MIT
"""Dedicated parser for IIT Hyderabad recruitment pages.

IITH posts two streams on the same careers page: permanent faculty positions
and rolling project/research positions (JRF, SRF, RA, postdoc, project staff).
The generic parser sees both but misclassifies and misses department info.

This parser adds department extraction and accurate post_type by delegating
post-type and department logic to parser_utils — no duplication.

Ported from a branch of the origin project that was never merged there, so
this parser did not exist anywhere in a released form until now.
"""

from __future__ import annotations

from typing import Any, Callable

from .._common import stable_id
from ..ad_factory import make_ad
from ..pdf_text import parse_deadline_iso, read_deadline
from .parser_utils import (
    classify_document,
    classify_post_type,
    extract_department,
    iter_recruitment_links,
)

# Result notifications, cancellations and the rest of a career page's
# paperwork are LABELLED, not dropped. This parser used to drop them with a
# private regex. A silent skip is the defect: a pattern that wrongly matches a
# real advertisement removes it from the corpus, and nothing anywhere says a
# record went missing. `classify_document` covers every shape the old regex
# did, plus manuals, forms, exam material and sanctioned-post tables, and the
# consumer filters on the label at render time.


def parse(html: str, url: str, fetched_at: Any, pdf: Callable | None = None) -> list[dict]:
    from bs4 import BeautifulSoup  # lazy: bs4 is the `academia` extra

    soup = BeautifulSoup(html, "html.parser")
    ads: list[dict] = []

    for abs_url, title, parent_text in iter_recruitment_links(soup, url):
        # The listing page carries no dates. The advertisement PDF behind each
        # link carries them, and this parser read only the link text until
        # 2026-08-23. `pdf` is None when download is disabled, and the status
        # then stays `not_examined` — which is the honest answer, not a miss.
        pdf_path = text = None
        if pdf is not None and abs_url.lower().endswith(".pdf"):
            pdf_path, text = pdf.pdf_text(abs_url)
        raw_deadline, deadline_status = read_deadline(text)
        # A raw date this parser cannot normalise is not published. The numeric
        # patterns accept a two-digit year and `parse_deadline_iso` reads only
        # four, so `22/04/26` would otherwise reach the corpus verbatim beside
        # every other parser's ISO value. `not_found` is the honest status: the
        # document was read and no usable date came out of it.
        closing_iso = parse_deadline_iso(raw_deadline)
        if raw_deadline and not closing_iso:
            deadline_status = "not_found"

        ads.append(make_ad(
            id=stable_id("iith", abs_url, title),
            title=title[:250],
            original_url=abs_url,
            snapshot_fetched_at=fetched_at,
            department=extract_department(title),
            post_type=classify_post_type(title),
            apply_url=abs_url if abs_url.lower().endswith(".pdf") else None,
            info_url=url,
            parse_confidence=0.55,
            raw_text_excerpt=parent_text[:500],
            closing_date=closing_iso,
            document_class=classify_document(title, parent_text),
            closing_date_status=deadline_status,
            pdf_path=pdf_path,
            pdf_parsed=bool(text and text.strip()),
        ))

    return ads
