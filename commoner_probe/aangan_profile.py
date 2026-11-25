# SPDX-License-Identifier: MIT
"""Bihar ICDS "Aangan" centre profile: enrolment and inspection records.

`ViewAWCProfile.aspx` shows one Anganwadi centre at a time, behind the cascade
financial year → district → project → sector → centre. The page holds four
GridViews:

- `gvDtl`: the centre's basic record (type, active, verified, Sevika mobile).
- `GVDPO`, `GVCDPO`, `GVLS`: inspections by the District Programme Officer, the
  Child Development Project Officer and the Lady Supervisor. Each row records
  whether the centre was open, whether food was cooked, the number of children
  enrolled, the number present at the inspection, a service rating, a remark,
  three timestamps, and the inspector's GPS position.

Checked live on 2026-11-25 (Araria, first sector, eight centres, two years):

1. **Dates are day-first.** The page printed `15/03/2018`. Timestamps are still
   returned exactly as printed, so a caller parses them knowingly.
2. **The financial-year filter filters.** The same centres showed their March
   2018 inspection under 2017-18 and their March 2019 one under 2018-19. Seven
   of eight differed; the eighth had no inspection in either year. Each
   inspector table showed at most one row per centre-year in that sample.
3. **Selecting the centre loads the profile.** `Button1` is the popup's Close
   button, not a submit. One centre-year took about 2 seconds.

Control names differ from `MonthWiseStatusReport.aspx` (`ddlDst`, `ddlProj`),
so this page has its own preset.
"""

from __future__ import annotations

import re
import unicodedata

from .gridview import parse_grid

BIHAR_ICDS_AWC_PROFILE = {
    "report_url": ("https://icdsaangan.bihar.gov.in/AanganMandey/eAccount/"
                   "ViewAWCProfile.aspx"),
    "controls": {
        "fy": "ctl00$MainContent$ddlFY",
        "district": "ctl00$MainContent$ddlDst",
        "project": "ctl00$MainContent$ddlProj",
        "sector": "ctl00$MainContent$ddlSector",
        "awc": "ctl00$MainContent$ddlAWC",
    },
    "levels": ["fy", "district", "project", "sector", "awc"],
}

INSPECTION_TABLES = {
    "dpo": "ctl00_MainContent_GVDPO",
    "cdpo": "ctl00_MainContent_GVCDPO",
    "ls": "ctl00_MainContent_GVLS",
}
DETAIL_TABLE = "ctl00_MainContent_gvDtl"

# (output key, a fragment the header cell must contain). Columns are read by
# position, and every position is checked against its header first: a layout
# change raises instead of shifting values into the wrong field.
INSPECTION_COLUMNS = (
    ("serial", "क्र"),
    ("awc", "आँगनवाड़ी"),
    ("centre_open", "खुला"),
    ("food_cooked", "भोजन बना है"),
    ("children_enrolled", "नामांकित"),
    ("morning_snacks_daily", "Morning"),
    ("service_level", "सेवाओं"),
    ("food_per_menu", "मेनू"),
    ("children_present", "उपस्थित"),
    ("remark", "अभियुक्ति"),
    ("inspected_at_mobile", "Mobile"),
    ("inspected_at_gps", "GPS"),
    ("uploaded_at", "अपलोड"),
    ("lat_lon", "अक्षांश"),
)
DETAIL_COLUMNS = (
    ("serial", "क्र"),
    ("awc", "आँगनवाड़ी"),
    ("awc_type", "प्रकार"),
    ("active", "सक्रिय"),
    ("verified", "सत्यापित"),
    ("sevika_mobile", "मोबाइल"),
)


_TABLE_TAG = re.compile(r"<(/?)table\b[^>]*>", re.I)


def _table(page: str, table_id: str) -> str | None:
    """The whole table, with any nested table removed.

    Photo cells hold a table of their own. A lazy `.*?</table>` stopped at the
    inner close and returned a truncated grid with no complete data row.
    """
    start = re.search(r'<table[^>]*id="%s"' % re.escape(table_id), page)
    if not start:
        return None
    pieces, depth, pos = [], 0, start.start()
    for tag in _TABLE_TAG.finditer(page, start.start()):
        closing = bool(tag.group(1))
        if depth <= 1:
            pieces.append(page[pos:tag.start()])
        if depth == 0 and not closing:
            pieces.append(tag.group(0))
        depth += -1 if closing else 1
        if depth == 0:
            pieces.append(tag.group(0))
            return "".join(pieces)
        pos = tag.end()
    return None


def _rows(page: str, table_id: str, columns: tuple) -> list[dict]:
    seg = _table(page, table_id)
    if seg is None:
        raise LookupError(f"table {table_id} not on the page")
    header, data = parse_grid(seg, header_startswith="क्र")
    for i, (key, fragment) in enumerate(columns):
        # The page and this source spell "ड़" with different code points.
        if i >= len(header) or _nfc(fragment) not in _nfc(header[i]):
            raise LookupError(
                f"{table_id} column {i} should contain {fragment!r} for {key}; "
                f"header is {header!r}")
    return [{key: row[i] for i, (key, _) in enumerate(columns)} for row in data]


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _int_or_none(text: str) -> int | None:
    return int(text) if text.strip().isdigit() else None


def parse_profile(page: str) -> dict:
    """Return `{"detail": [...], "inspections": [...]}` from a profile page.

    Each inspection carries `inspector` (dpo, cdpo or ls), counts as int or
    None, `lat` and `lon` as float or None, and every other field as printed.
    An inspector with no record contributes no rows, which is not the same as
    a failed parse: a missing table raises.
    """
    detail = _rows(page, DETAIL_TABLE, DETAIL_COLUMNS)
    inspections = []
    for inspector, table_id in INSPECTION_TABLES.items():
        for row in _rows(page, table_id, INSPECTION_COLUMNS):
            row["inspector"] = inspector
            for key in ("children_enrolled", "children_present"):
                row[key] = _int_or_none(row[key])
            parts = row.pop("lat_lon").split()
            try:
                row["lat"], row["lon"] = float(parts[0]), float(parts[1])
            except (IndexError, ValueError):
                row["lat"] = row["lon"] = None
            inspections.append(row)
    return {"detail": detail, "inspections": inspections}
