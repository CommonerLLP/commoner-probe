# SPDX-License-Identifier: MIT
"""Four invariants that decide whether an acquisition result can be trusted.

Every one of these is drawn from a defect that produced a **plausible,
complete-looking result** rather than an error. That is the only failure class
worth building machinery against: an error announces itself, a wrong answer
arrives looking exactly like a right one, and a count that is quietly a floor
survives every consistency check downstream.

They are callables rather than advice because advice in a docstring is executed
only by whoever remembers it. `geoserver.py` implements the saturation and
partial-result invariants for the GIS case; these are the general forms, so the
next acquisition inherits them instead of relearning them.

1. **Enumerate what the source offers.** :func:`unmapped`,
   :func:`require_full_coverage`.
2. **Verify saturation with a different query shape.** :func:`saturation`.
3. **One bad unit must not zero the collection.** :func:`collect`.
4. **A positive control precedes any claim of absence.** :func:`assert_finds`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any


class PartialCoverage(RuntimeError):
    """A column map and the source disagree about which columns exist."""


class ControlFailed(RuntimeError):
    """A positive control did not come back, so a null result is about the query."""


def _named(offered: Iterable[str]) -> list[str]:
    """The distinct, non-blank column names, in the order the source gives them.

    GridViews emit empty header cells for drill-down link columns and repeat a
    name across a spanned header, and neither is a column a caller can map.
    """
    seen: set[str] = set()
    out: list[str] = []
    for name in offered:
        label = (name or "").strip()
        if not label or label in seen:
            continue
        seen.add(label)
        out.append(label)
    return out


def unmapped(offered: Iterable[str], mapping: Mapping[str, Any]) -> list[str]:
    """Columns the source publishes that the caller's map does not name.

    Bihar's ICDS pending-honorarium register offers ten drillable reason
    columns, each a reason a frontline worker's payment is stuck. The scraper's
    map listed six. Every run for months skipped four, and those four were the
    revealing ones, because they trace the payment through the administrative
    chain. Nothing failed: the row filter already expected the full 15-cell row,
    so the shape was right and the reading stopped early.
    """
    return [name for name in _named(offered) if name not in mapping]


def require_full_coverage(
    offered: Iterable[str], mapping: Mapping[str, Any], *, source: str = ""
) -> None:
    """Raise unless the map and the source name the same columns.

    Checked in BOTH directions on purpose. A column the map misses is data
    silently dropped. A column the map names and the source no longer serves is
    a mapping reading a name nothing answers to, which is how a positional
    reader ends up returning correct-looking numbers for the wrong column.

    Call it before the run, against the live header. After the run the numbers
    already look fine.
    """
    columns = _named(offered)
    missing = [name for name in columns if name not in mapping]
    vanished = [name for name in mapping if name not in set(columns)]
    if not missing and not vanished:
        return
    where = f" on {source}" if source else ""
    parts = [f"the source{where} offers {len(columns)} column(s) and the map covers "
             f"{len(columns) - len(missing)}"]
    if missing:
        parts.append("unmapped: " + ", ".join(missing))
    if vanished:
        parts.append("mapped but not served: " + ", ".join(vanished))
    raise PartialCoverage("; ".join(parts))


@dataclass(frozen=True)
class Saturation:
    """What a second pass of a different shape found that the first missed."""

    first_pass: int
    second_pass: int
    new: int
    recall: float
    saturated: bool
    partial: bool
    new_ids: list[str] = field(default_factory=list)


def saturation(
    known: Iterable[Any],
    got: Iterable[Any],
    *,
    partial: bool = False,
    sample: int = 50,
) -> Saturation:
    """Compare a first pass against a re-query of a DIFFERENT shape.

    Re-running an identical query re-asks the same question and gets the same
    answer. That is repetition, and it will confirm any systematic miss. The
    re-query must change shape: an offset grid rather than the same grid, a
    different sort order or page size for a paginated API, different partition
    boundaries for a date-partitioned crawl, a disjoint keyword set for a
    keyword sweep.

    What it licensed, measured: a WMS point sweep returned 58,301 features, and
    a re-sweep on a grid offset by half a tile — querying the ground *between*
    the original query points — returned 58,301 with zero new features. That is
    what turns a floor into a count.

    ``partial`` is the caller's own verdict on the second pass. An empty ``new``
    proves saturation only when the second pass actually asked every question; a
    pass with holes produces the same empty set for the opposite reason.
    """
    first = set(known)
    second = set(got)
    fresh = second - first
    return Saturation(
        first_pass=len(first),
        second_pass=len(second),
        new=len(fresh),
        recall=(len(first & second) / len(first)) if first else 0.0,
        saturated=not fresh and not partial,
        partial=partial,
        new_ids=sorted(str(i) for i in fresh)[:sample],
    )


@dataclass
class Collection:
    """A unit-by-unit acquisition, and the units that failed inside it."""

    values: list[Any] = field(default_factory=list)
    failed_units: list[tuple[Any, str]] = field(default_factory=list)
    attempted: int = 0

    @property
    def partial(self) -> bool:
        return bool(self.failed_units)

    @property
    def report(self) -> str:
        """One line, loud enough to survive a log.

        "0 rows" in a summary is where this defect hides, so the word PARTIAL
        and the failure count go in the line itself.
        """
        if not self.failed_units:
            return f"complete: {self.attempted} unit(s), none failed"
        return (f"PARTIAL: {len(self.failed_units)} of {self.attempted} unit(s) failed — "
                f"the result is a floor, not a total "
                f"({', '.join(str(u) for u, _ in self.failed_units[:5])})")


def collect(
    units: Iterable[Any],
    fetch: Callable[[Any], Any],
    *,
    log: Callable[[str], None] | None = None,
) -> Collection:
    """Run ``fetch`` per unit, and let one bad unit degrade rather than empty it.

    A single tile in a layer's far corner returned a non-JSON body, the
    exception propagated out of the sweep, and the run recorded "0 rows" for two
    layers. In a results table "0 rows" is indistinguishable from "this layer is
    empty", and the two layers happened to be the ones the analysis most needed.

    The invariant holds for any unit of acquisition: a tile, a page, a date
    partition, a district, one PDF in a batch.

    ``KeyboardInterrupt`` and ``SystemExit`` are NOT unit failures and are not
    caught. An operator stopping a run is not a hole in the source, and
    recording it as one would make a cancelled crawl look like a partial one.
    """
    result = Collection()
    for unit in units:
        result.attempted += 1
        try:
            result.values.append(fetch(unit))
        except Exception as exc:  # noqa: BLE001 - the point is to survive one unit
            reason = f"{type(exc).__name__}: {exc}"[:200]
            result.failed_units.append((unit, reason))
            if log:
                log(f"  unit {unit}: {reason} — SKIPPED, the result is PARTIAL")
    if log and result.failed_units:
        log(result.report)
    return result


def assert_finds(
    query: Callable[[Any], Any], control: Any, *, describe: str = ""
) -> None:
    """Raise unless a record already held comes back from the same query.

    Run it before reporting that an archive is empty, that a record does not
    exist, or that a search returned nothing. If the control does not come back,
    the query is broken and the null is a statement about the query.

    Two live instances of the shape this defends against: a WMS layer that
    returns 19,090 of 58,301 features under the server's default style, with no
    error; and a search form that answers a wrong date format with the blank
    form, which is indistinguishable from "no such record".

    An exception is a failed control too. A query that cannot run has not
    established an absence either.
    """
    where = f" against {describe}" if describe else ""
    try:
        found = query(control)
    except Exception as exc:  # noqa: BLE001 - a broken query is a failed control
        raise ControlFailed(
            f"the positive control {control!r}{where} raised "
            f"{type(exc).__name__}: {exc} — the query is broken, so nothing is "
            "established about what the source holds"
        ) from exc
    if found is None or (hasattr(found, "__len__") and len(found) == 0):
        raise ControlFailed(
            f"the positive control {control!r}{where} returned nothing, and it is a "
            "record already held — so the query is broken and any empty result "
            "from it says nothing about the source"
        )
