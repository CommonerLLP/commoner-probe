# SPDX-License-Identifier: MIT
"""District Census Handbook (DCHB) Part A — Town Directory Statement V.

The urban half of REQ-0045, and the half the OGD API does not carry. ORGI
publishes town amenity counts only inside the per-district handbooks, so the
public-library figure for towns has to come out of the PDF.

**The request's own description of this table is wrong, and the correction is
the point of this module.** REQ-0045 states that Statement V "merges two
facilities ORGI defines separately: 9.11 Public Library and 9.12 Public Reading
Room". Measured against the Pune 2011 handbook (2026-07-30), it does not:
they are **columns 22 and 23, counted separately**. So the widely-cited
"~75,000 libraries" arithmetic — 70,817 rural + 4,580 urban — is adding a rural
*availability flag* to an urban *public-library count*, and the urban term was
never a merged figure to begin with.

**The real trap is different.** A cell holds either a count OR the nearest town
and its distance when the facility is absent locally — `Pune(30)`,
`Pimpri-Chinchwad(16)` — exactly the convention the Village Directory uses in
its distance-range codes. An integer parse silently drops those towns, which
biases the total downward precisely for the towns that lack a library.

**Layout.** Statement V spans two facing pages. The left page carries
``Sr. No.``, ``Name of Town`` and the educational columns; the right page
carries the recreational columns and repeats ``Sr. No.``. The serial number is
the join key between the halves — town names appear only on the left.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

#: A left-page row: leading serial, then the town name, then numeric-ish cells.
_LEFT_ROW_RE = re.compile(r"^\s{0,6}(\d{1,3})\s{2,}([A-Z][^\d]{1,44}?)\s{2,}")

#: A right-page row: leading cells, trailing serial. Cells are counts or
#: `Place(distance)` strings, so they are captured as text and typed later.
_RIGHT_ROW_RE = re.compile(r"^\s{0,8}(\S.*?)\s{2,}(\d{1,3})\s*$")

_PLACE_DIST_RE = re.compile(r"^([A-Za-z][A-Za-z .'\-]*?)\s*\(\s*(\d+(?:\.\d+)?)\s*\)$")

_STATEMENT_V_MARK = re.compile(r"Educational,\s*Recreational\s*and\s*Cultural", re.I)
_LIB_HEADER_RE = re.compile(r"Public\s+libraries", re.I)


@dataclass
class TownFacility:
    """One town's Statement V library figures.

    ``public_libraries`` and ``reading_rooms`` are **separate counts and must not
    be summed with each other, nor with the Village Directory's rural
    availability flag** — the rural field records whether a settlement has a
    library at all, not how many.
    """

    serial: int
    town_name: str
    #: Count when the town has the facility; None when the cell instead names
    #: the nearest town, which is a real and different state.
    public_libraries: int | None
    reading_rooms: int | None
    #: Where the nearest facility is, when the town has none.
    nearest_place: str | None = None
    nearest_distance_km: float | None = None

    def to_record(self) -> dict:
        return {
            "serial": self.serial,
            "town_name": self.town_name,
            "public_libraries": self.public_libraries,
            "reading_rooms": self.reading_rooms,
            "nearest_place": self.nearest_place,
            "nearest_distance_km": self.nearest_distance_km,
        }


def _cell(value: str) -> tuple[int | None, str | None, float | None]:
    """A Statement V cell: a count, or the nearest place and its distance."""
    value = value.strip()
    if re.fullmatch(r"\d{1,4}", value):
        return int(value), None, None
    m = _PLACE_DIST_RE.match(value)
    if m:
        return None, m.group(1).strip(), float(m.group(2))
    return None, None, None


def find_statement_v_pages(pdf: Path, *, extract=None) -> list[int]:
    """Physical page numbers carrying Statement V.

    Found by content, never by the printed page number in the contents list:
    the handbook's printed numbers are offset from its physical pages (the Pune
    volume's printed 1264 is physical 1276), so a contents-driven lookup lands
    on the wrong statement entirely.
    """
    if extract is None:  # pragma: no cover - thin shim over the shared helper
        from .textparse import extract_pdf_page_text as extract

    pages: list[int] = []
    for page in range(1, _page_count(pdf) + 1):
        text = extract(pdf, page) or ""
        if _STATEMENT_V_MARK.search(text) or _LIB_HEADER_RE.search(text):
            pages.append(page)
    return pages


def _page_count(pdf: Path) -> int:
    import subprocess

    out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if line.startswith("Pages:"):
            return int(line.split()[1])
    raise ValueError(f"could not read a page count from {pdf}")


def parse_statement_v(left_text: str, right_text: str) -> Iterator[TownFacility]:
    """Join a facing pair of Statement V pages into per-town rows.

    Town names live only on the left page and the library counts only on the
    right, so the serial number carried by both is the join key. A right-hand
    row whose serial has no left-hand partner is dropped rather than guessed at.
    """
    names: dict[int, str] = {}
    for line in left_text.splitlines():
        m = _LEFT_ROW_RE.match(line)
        if m:
            names[int(m.group(1))] = re.sub(r"\s+", " ", m.group(2)).strip()

    for line in right_text.splitlines():
        m = _RIGHT_ROW_RE.match(line)
        if not m:
            continue
        serial = int(m.group(2))
        if serial not in names:
            continue
        cells = re.split(r"\s{2,}", m.group(1).strip())
        if len(cells) < 2:
            continue
        # Columns 22 and 23 are the last two before the trailing serial.
        lib_raw, read_raw = cells[-2], cells[-1]
        libs, place, dist = _cell(lib_raw)
        reads, place2, dist2 = _cell(read_raw)
        yield TownFacility(
            serial=serial,
            town_name=names[serial],
            public_libraries=libs,
            reading_rooms=reads,
            nearest_place=place or place2,
            nearest_distance_km=dist if dist is not None else dist2,
        )
