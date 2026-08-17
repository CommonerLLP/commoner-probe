# SPDX-License-Identifier: MIT
"""Parse and sanity-check ASP.NET GridView reports.

Most Indian government portals render tabular reports as WebForms GridViews,
and three failure modes recur across all of them. Each produces a full,
plausible table, so none announces itself:

1. **The header is not the first row.** GridViews emit filter widgets, caption
   rows and totals as ``<tr>`` elements indistinguishable from data. Taking
   row 0 as the header silently mislabels every column. On Bihar's Aangan MIS
   this turned a 15-column pending-honorarium report into a 3-column one
   labelled ``All / Sevika / Sahaika`` — the filter widget — and the numbers
   read fine.

2. **A filter that changes nothing still returns a table.** Selecting a
   different financial year re-renders the page and returns 545 rows either
   way. Four of Bihar's ten columns were current-state counters that ignored
   the year filter entirely, so a decade "trend" carried the same constant in
   every year.

3. **A filter dimension that is not a dimension.** Bihar's month dropdown had
   13 options and no effect: 70,850 rows were 5,450 observations repeated
   thirteenfold. Summing them inflated every total by 13x.

The remedy for 1 is structural (anchor the header). The remedy for 2 and 3 is
empirical — vary the filter and check the numbers move — which is why they are
functions here rather than advice in a docstring.
"""

from __future__ import annotations

import collections
import html as _html
import re
from typing import Any, Iterable, Sequence

_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)


def _cells(row_html: str) -> list[str]:
    return [re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
            for c in _CELL.findall(row_html)]


def parse_grid(page: str, *, header_startswith: str = "sl",
               key: bool = True) -> tuple[list[str], list[list[str]]]:
    """Return (header, data_rows) from a GridView page.

    The header is found by anchoring on a cell that starts with
    `header_startswith` (default "sl", for the near-universal "Sl. No."
    column) AND matching the modal row width, so a caption row that happens to
    contain the word is not mistaken for it. Data rows are those of the same
    width whose first cell is a serial number.

    Raises LookupError rather than guessing when no anchored header exists —
    a wrong header is worse than no parse, because it is silent.
    """
    rows = [_cells(r) for r in _TR.findall(page)]
    widths = collections.Counter(len(r) for r in rows if len(r) > 2)
    if not widths:
        return [], []
    width = widths.most_common(1)[0][0]
    header = next((r for r in rows
                   if len(r) == width and r
                   and r[0].strip().lower().startswith(header_startswith)), None)
    if header is None:
        raise LookupError(
            f"no header row of width {width} starting with {header_startswith!r}; "
            "refusing to guess — check the page or pass header_startswith")
    data = [r for r in rows
            if len(r) == width and r and r[0].strip().rstrip(".").isdigit()]
    return header, data


def to_records(header: Sequence[str], rows: Iterable[Sequence[str]]) -> list[dict]:
    """Zip rows against a header into dicts with snake_case keys."""
    keys = [re.sub(r"[^a-z0-9]+", "_", h.lower()).strip("_") or f"c{i}"
            for i, h in enumerate(header)]
    return [dict(zip(keys, r)) for r in rows]


def numeric_columns(records: Sequence[dict]) -> list[str]:
    """Columns whose every non-empty value is an integer.

    Phone numbers pass this test and are the reason `responsive_columns` exists:
    a contact column is numeric, constant, and catastrophic to sum. Use this to
    find candidates, never as a licence to aggregate.
    """
    if not records:
        return []
    out = []
    for k in records[0]:
        vals = [str(r.get(k, "")).replace(",", "").strip() for r in records]
        vals = [v for v in vals if v]
        if vals and all(v.isdigit() for v in vals):
            out.append(k)
    return out


def responsive_columns(series: dict[Any, Sequence[dict]],
                       columns: Sequence[str] | None = None) -> dict[str, bool]:
    """Which columns actually respond to a filter.

    `series` maps each filter value (a year, a month) to the records fetched
    under it. A column is responsive if its total differs across at least two
    filter values. Anything constant is a current-state counter that has no
    dimension along this filter and MUST NOT be trended along it.
    """
    keys = list(columns or (numeric_columns(next(iter(series.values()))) if series else []))
    out = {}
    for k in keys:
        totals = {
            sum(int(str(r[k]).replace(",", "")) for r in recs
                if str(r.get(k, "")).replace(",", "").strip().isdigit())
            for recs in series.values()
        }
        out[k] = len(totals) > 1
    return out


def duplicate_fold(records: Sequence[dict], identity: Sequence[str]) -> int:
    """How many times each distinct observation is repeated.

    `identity` names the fields that genuinely identify one observation. A
    return of 13 means every row appears thirteen times and any sum is
    inflated thirteenfold. 1 means no duplication.
    """
    if not records:
        return 0
    seen = collections.Counter(tuple(r.get(k) for k in identity) for r in records)
    folds = collections.Counter(seen.values())
    return folds.most_common(1)[0][0]


def dedupe(records: Sequence[dict], identity: Sequence[str]) -> list[dict]:
    """First record per distinct identity, dropping duplicate folds."""
    seen = set()
    out = []
    for r in records:
        k = tuple(r.get(f) for f in identity)
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def absent_periods(series: dict[Any, Sequence[dict]]) -> dict[str, list]:
    """Periods where a field is present in NO record, though present elsewhere.

    A whole endpoint can vanish for one period while its neighbours are intact.
    Nothing errors: the key is simply missing, `.get()` returns None, and a sum
    or a mean silently treats the period as ZERO rather than as absent. It looks
    like a real collapse in the series.

    Observed on Poshan Tracker: `keyServices_v3` returns nothing for 2024-11 in
    0 of 772 districts, while the registration and growth endpoints are complete
    for the same month. It was caught only because an unrelated cross-check put
    a zero in front of a human — no automated check in the pipeline saw it.

    Returns {field: [periods where it is wholly absent]}, empty when clean.
    Callers must EXCLUDE those periods, not treat them as zero.
    """
    seen: dict[str, set] = {}
    for period, records in series.items():
        for r in records:
            for k in r:
                seen.setdefault(k, set()).add(period)
    periods = set(series)
    return {k: sorted(periods - present, key=str)
            for k, present in seen.items() if periods - present}


def audit(series: dict[Any, Sequence[dict]], identity: Sequence[str]) -> dict:
    """Run every check at once and return what a caller must know before summing.

    Returns `fold` (duplication factor), `responsive` and `static` column lists,
    and `distinct` (real observation count). Report `static` alongside any
    trend rather than silently dropping it: those cases are real, they simply
    have no position on this axis.
    """
    flat = [r for recs in series.values() for r in recs]
    resp = responsive_columns(series)
    return {
        "rows": len(flat),
        "distinct": len(dedupe(flat, identity)),
        "fold": duplicate_fold(flat, identity),
        "responsive": sorted(k for k, v in resp.items() if v),
        "static": sorted(k for k, v in resp.items() if not v),
        "absent_periods": absent_periods(series),
    }
