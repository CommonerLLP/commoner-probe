# SPDX-License-Identifier: AGPL-3.0-or-later
"""ATR → original-report linkage extraction.

Action Taken Reports cite the original substantive report they respond
to. The citation lives in the title: ``"Action Taken by the Government
on the Observations/Recommendations contained in the 24th Report of
the Standing Committee on Finance"``. The number ``24`` is the
identifier of the original report in the same committee's series.

Recovering this linkage at extraction time turns a flat corpus into a
graph: every recommendation has a life cycle (original report →
government's first response → ATR → ATR-on-ATR), and that life cycle
is the unit of analysis a researcher actually wants. Without the
linkage, "the ministry rejected this in 2022" is a single record;
with the linkage, it's a sequence of moves you can follow.

This module reads ``manifest.jsonl`` and writes ``atr_linkage.jsonl``;
nothing in the pipeline depends on it being run, so it can be added
to existing corpora without re-crawling.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

EXTRACTOR_VERSION = "atr_linkage_v1"

# The canonical anchor in sansad.in ATR titles is the phrase "contained
# in the" — the *referenced* report number lives in the words that
# follow it. The ATR's own number usually appears earlier in the title
# (e.g. "374th Report on Action Taken ... contained in the Three Hundred
# And Sixty Sixth Report ..."), so anchor-matching is required to avoid
# returning the ATR's own number as if it were the linkage target.
#
# We capture up to ~80 chars of "Nth/words Report" context after the
# anchor, then normalise it to an integer.
_CONTAINED_IN_RE = re.compile(
    r"contained\s+in\s+the\s+(?P<phrase>.{1,80}?)\s+Report\b",
    re.IGNORECASE | re.DOTALL,
)
# Fallback: "Report No. N" form used by some older titles.
_ATR_REPORT_NO_RE = re.compile(
    r"\bReport\s+No\.?\s*(\d+)\b",
    re.IGNORECASE,
)
# Last-resort fallback: any "Nth Report" in the title (digit form only).
# Used when neither anchor above hits — accepts that the result may be
# the ATR's own number; better than nothing for diagnostic linkage.
_ANY_ORDINAL_REPORT_RE = re.compile(
    r"(\d+)(?:st|nd|rd|th)?\s+Report\b",
    re.IGNORECASE,
)


# Number-word vocabulary covering the range that actually shows up in
# Indian parliamentary committee numbering (0-499). Beyond that, the
# committee's own counter rarely passes — RS committees with the
# highest counts (Education, Health) are in the 300s as of 2026.
_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    # ordinal forms (for "Sixty Sixth", "Eighty Third", etc.)
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9,
    "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13,
    "fourteenth": 14, "fifteenth": 15, "sixteenth": 16, "seventeenth": 17,
    "eighteenth": 18, "nineteenth": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    # ordinal forms — "Twentieth", "Sixtieth"
    "twentieth": 20, "thirtieth": 30, "fortieth": 40, "fiftieth": 50,
    "sixtieth": 60, "seventieth": 70, "eightieth": 80, "ninetieth": 90,
}
_SCALES = {"hundred": 100, "thousand": 1000}


def _words_to_int(phrase: str) -> int | None:
    """Convert an English ordinal/cardinal up to 999 to int, or None.

    Handles:
      ``"twenty four"`` → 24
      ``"sixty sixth"`` → 66
      ``"three hundred and sixty sixth"`` → 366
      ``"one hundred"`` → 100
      ``"third"`` → 3

    Returns None on unrecognised input — caller falls back to digit
    parsing or the no-match path.
    """
    if not phrase:
        return None
    tokens = re.findall(r"[A-Za-z]+", phrase.lower())
    if not tokens:
        return None
    # Drop "and" connectors ("three hundred and sixty sixth").
    tokens = [t for t in tokens if t != "and"]
    if not tokens:
        return None
    total = 0
    current = 0
    for tok in tokens:
        if tok in _UNITS:
            current += _UNITS[tok]
        elif tok in _TENS:
            current += _TENS[tok]
        elif tok in _SCALES:
            scale = _SCALES[tok]
            if current == 0:
                current = 1
            current *= scale
            if scale >= 1000:
                total += current
                current = 0
        else:
            return None
    return total + current if (total + current) > 0 else None


def _extract_referenced_report_no(title: str) -> int | None:
    """Return the referenced report number from an ATR title, or None.

    Strategy (priority order):

    1. ``contained in the <phrase> Report`` — the canonical anchor.
       ``<phrase>`` may be a digit form ("24th", "366") or words
       ("Three Hundred And Sixty Sixth"); we try digit first, then
       words.
    2. ``Report No. N`` — older RS committee form.
    3. Any ordinal ``Nth Report`` digit form, anywhere in the title —
       last-resort fallback. Accepts that this may be the ATR's own
       number.

    Returns None when no anchor produces a usable integer.
    """
    if not title:
        return None
    # Step 1: anchored match.
    anchor = _CONTAINED_IN_RE.search(title)
    if anchor:
        phrase = anchor.group("phrase").strip()
        # Try digit form first.
        m = re.search(r"(\d+)(?:st|nd|rd|th)?$", phrase)
        if m:
            return int(m.group(1))
        # Try word form.
        n = _words_to_int(phrase)
        if n is not None:
            return n
    # Step 2: "Report No. N" form.
    m = _ATR_REPORT_NO_RE.search(title)
    if m:
        return int(m.group(1))
    # Step 3: any ordinal — last resort.
    m = _ANY_ORDINAL_REPORT_RE.search(title)
    if m:
        return int(m.group(1))
    return None


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class AtrLinkage:
    atr_key: str
    atr_no: int | str | None
    house: str | None
    committee_slug: str | None
    atr_title: str
    references_report_no: int
    references_report_key: str | None
    extracted_at: str
    extractor: str = EXTRACTOR_VERSION

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LinkageStats:
    atr_records_seen: int = 0
    linkages_extracted: int = 0
    titles_without_match: int = 0


def _compute_referenced_key(rec: dict, referenced_no: int) -> str | None:
    """Compute the manifest key of the original report being referenced.

    Mirrors the key construction in ``committees.report_key``: LS keys
    include lokSabha_no; RS keys do not. Returns None when we don't
    have enough metadata to compute the key.
    """
    house = rec.get("house") or ""
    slug = rec.get("committee_slug")
    if not slug:
        return None
    if house.lower().startswith("lok") or house.lower() == "ls":
        ls_no = rec.get("loksabha_no") or rec.get("lokSabha_no")
        if not ls_no:
            return None
        return f"LS|{slug}|{referenced_no}|{ls_no}"
    if house.lower().startswith("raj") or house.lower() == "rs":
        return f"RS|{slug}|{referenced_no}"
    return None


def extract_atr_linkages(
    out_dir: Path,
    *,
    log_fn: Callable[..., None] = print,
) -> LinkageStats:
    """Walk ``manifest.jsonl``; for each ``report_type == 'action_taken'``
    record, parse the title and write an ``atr_linkage.jsonl`` row.

    Atomic write: writes to a sibling ``.tmp`` and renames on success
    so a partial file is never visible.
    """
    stats = LinkageStats()
    manifest_path = out_dir / "manifest.jsonl"
    out_path = out_dir / "atr_linkage.jsonl"
    if not manifest_path.exists():
        log_fn(f"no manifest at {manifest_path} — run 'committees' first")
        return stats

    out_records: list[dict] = []
    with manifest_path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("report_type") != "action_taken":
                continue
            stats.atr_records_seen += 1
            referenced = _extract_referenced_report_no(rec.get("title") or "")
            if referenced is None:
                stats.titles_without_match += 1
                continue
            link = AtrLinkage(
                atr_key=rec.get("key", ""),
                atr_no=rec.get("report_no"),
                house=rec.get("house"),
                committee_slug=rec.get("committee_slug"),
                atr_title=(rec.get("title") or "")[:200],
                references_report_no=referenced,
                references_report_key=_compute_referenced_key(rec, referenced),
                extracted_at=_now(),
            )
            out_records.append(link.to_record())
            stats.linkages_extracted += 1

    tmp = out_path.with_name(out_path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for rec in out_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    tmp.replace(out_path)

    log_fn(
        f"atr_linkage.jsonl: atr_records_seen={stats.atr_records_seen} "
        f"linkages_extracted={stats.linkages_extracted} "
        f"titles_without_match={stats.titles_without_match}"
    )
    return stats
