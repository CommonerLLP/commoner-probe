# SPDX-License-Identifier: MIT
"""Shared ad-dict factory for the academia parsers.

Ported from academiaindia/scraper/ad_factory.py. Builds a consistently-shaped
ad ``dict`` (the probe's manifest convention — ``pdf_parsed`` not ``_pdf_parsed``)
so parsers only pass the fields they actually know about.
"""

from __future__ import annotations

from typing import Any

from ._common import PLACEHOLDER_INSTITUTION_ID, iso, stable_id

__all__ = ["make_ad", "stable_id"]


def make_ad(
    *,
    id: str,
    title: str,
    original_url: str,
    snapshot_fetched_at: Any,
    institution_id: str = PLACEHOLDER_INSTITUTION_ID,
    ad_number: str | None = None,
    department: str | None = None,
    discipline: str | None = None,
    post_type: str = "Faculty",
    contract_status: str = "Unknown",
    category_breakdown: dict | None = None,
    number_of_posts: int | None = None,
    pay_scale: str | None = None,
    publication_date: str | None = None,
    closing_date: Any = None,
    parse_confidence: float = 0.5,
    raw_text_excerpt: str | None = None,
    apply_url: str | None = None,
    info_url: str | None = None,
    annexure_pdf_url: str | None = None,
    publications_required: str | None = None,
    unit_eligibility: str | None = None,
    pdf_path: str | None = None,
    pdf_parsed: bool = False,
) -> dict:
    """Build an ad dict with a consistent shape across parsers."""
    closing_str = closing_date.isoformat() if hasattr(closing_date, "isoformat") else closing_date
    pub_str = publication_date.isoformat() if hasattr(publication_date, "isoformat") else publication_date
    return {
        "id": id,
        "institution_id": institution_id,
        "ad_number": ad_number,
        "title": title,
        "department": department,
        "discipline": discipline,
        "post_type": post_type,
        "contract_status": contract_status,
        "category_breakdown": category_breakdown,
        "number_of_posts": number_of_posts,
        "pay_scale": pay_scale,
        "publication_date": pub_str,
        "closing_date": closing_str,
        "original_url": original_url,
        "snapshot_fetched_at": iso(snapshot_fetched_at),
        "parse_confidence": parse_confidence,
        "raw_text_excerpt": raw_text_excerpt,
        "apply_url": apply_url,
        "info_url": info_url,
        "annexure_pdf_url": annexure_pdf_url,
        "publications_required": publications_required,
        "unit_eligibility": unit_eligibility,
        "pdf_path": pdf_path,
        "pdf_parsed": pdf_parsed,
    }
