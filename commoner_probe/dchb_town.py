# SPDX-License-Identifier: MIT
"""District Census Handbook Town Release — the urban public-library COUNT.

REQ-0045 asked for every public library in the 2011 Census. The rural half is
served by Village Amenities (see :mod:`commoner_probe.census`). The urban half
was believed to live only inside ~640 DCHB Part A PDFs, which is why a Town
Directory Statement V PDF parser was attempted and removed at 4-of-24 town
recall.

It does not. Every DCHB record in ORGI's NADA catalogue ships
``DH_2011_DCHB_Town_Release_<statecode>00.xlsx`` alongside the PDF — a
**state-level** spreadsheet carrying the counts as ordinary columns. Verified
2026-07-31 by acquiring DCHB records through :mod:`commoner_probe.nada`: the
file was present in 10 of 10 sampled records spanning 8 states (Kerala, Madhya
Pradesh, Maharashtra, Meghalaya, Nagaland, Odisha, Punjab, Rajasthan). That is
~36 state files rather than ~640 district PDFs, and no table extraction.

**The rule this module exists to enforce.** The rural value is an availability
FLAG per village (A=1/NA=2 — "does this village have a library?"). The urban
value here is a COUNT per town ("how many libraries are in this town?"). Adding
them produces the widely-cited and wrong "~75,000 public libraries", because
the rural figure counts *villages that have a library*, not libraries. Every row
this module writes therefore declares ``measure: "count"``, pinned by a schema
``const``, so a consumer combining the two has to override an explicit field
rather than merely fail to notice.

Reading rooms are a separate facility from libraries in the source and stay
separate here: there is no field combining them, and that absence is deliberate.

Column layout, verified identical across Maharashtra (2700) and Nagaland (1300).
Columns are located by HEADER TEXT, not by these letters — the letters are
recorded for orientation, and a reader keyed on them would silently read the
wrong column the day ORGI inserts one:

    A/B State Code/Name   C/D District Code/Name   E/F Sub District Code/Name
    G/H Town Code/Name    I/J Households/Population
    OR/OS Govt library status/COUNT      OT/OU Private library status/COUNT
    OY/OZ Govt reading room status/COUNT PA/PB Private reading room status/COUNT

Reading is stdlib ``zipfile`` + ``ElementTree``: this package declares
``dependencies = []`` and openpyxl is not available to it.
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

#: An XLSX is a zip of XML, fetched over the network from a government portal —
#: not trusted input. `xml.etree.ElementTree` is documented as unsafe against
#: maliciously constructed XML (entity-expansion "billion laughs", quadratic
#: blowup), and `defusedxml` is not available: this package declares
#: `dependencies = []`. Two stdlib-only guards instead, both cheap:
#:
#: 1. Refuse any member declaring a DTD. A legitimate XLSX part never carries
#:    `<!DOCTYPE`, and the entity bomb needs one to define its entities.
#: 2. Cap the DECLARED UNCOMPRESSED size before reading, so a decompression
#:    bomb is rejected without being expanded into memory.
#:
#: The real Maharashtra sheet — the largest seen, 535 towns — is ~7 MB
#: uncompressed, so the cap has ample headroom for a genuine state file.
MAX_XML_BYTES = 256 * 1024 * 1024

#: Header text -> the field it populates. Matching is on the header, not the
#: column letter, and every one of these must be present or the file is refused.
HEADER_MAP = {
    "state code": "state_code",
    "state name": "state_name",
    "district code": "district_code",
    "district name": "district_name",
    "sub district code": "subdistrict_code",
    "sub district name": "subdistrict_name",
    "town code": "town_code",
    "town name": "town_name",
    "total households": "total_households",
    "total population of town": "total_population",
    "govt.-public library (status a(1)/na(2))": "public_library_govt_status",
    "govt.-public library (numbers))": "public_library_govt",
    "private-public library (status a(1)/na(2))": "public_library_private_status",
    "private-public library (numbers)": "public_library_private",
    "govt.-public reading room (status a(1)/na(2))": "reading_room_govt_status",
    "govt.-public reading room (numbers))": "reading_room_govt",
    "private-public reading room (status a(1)/na(2))": "reading_room_private_status",
    "private-public reading room (numbers)": "reading_room_private",
}

#: Without these the file is not a Town Release and must not yield rows.
REQUIRED = ("town_code", "town_name", "public_library_govt", "public_library_private")

#: Census code widths, verified against the Maharashtra and Nagaland files —
#: uniform across all 561 towns. These cells are stored as NUMBERS (no `t="s"`),
#: so a raw read drops the leading zero: Punjab's state code 03 arrives as "3".
#: Nine of the 35 states have a code below 10, and the rural corpus this joins
#: against is zero-padded, so unpadded codes would silently miss a quarter of
#: India.
CODE_WIDTHS = {
    "state_code": 2,
    "district_code": 3,
    "subdistrict_code": 5,
    "town_code": 6,
}


class DchbTownError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _safe_xml(archive: zipfile.ZipFile, member: str) -> ET.Element:
    """Parse one zip member, refusing the two shapes stdlib XML is unsafe against.

    See MAX_XML_BYTES. Raises :class:`DchbTownError` rather than letting a
    hostile file reach the parser.
    """
    info = archive.getinfo(member)
    if info.file_size > MAX_XML_BYTES:
        raise DchbTownError(
            f"{member}: declares {info.file_size} uncompressed bytes, above the "
            f"{MAX_XML_BYTES} cap — refusing to expand a possible decompression bomb"
        )
    raw = archive.read(member)
    # A legitimate XLSX part carries no DTD, and the entity-expansion attack
    # needs one. Checked before parsing, on the bytes.
    # The WHOLE buffer, not a prefix: >4 KiB of legal comment before the
    # DOCTYPE walked the entity bomb past a windowed check (Codex, PR #101).
    if re.search(rb"<!DOCTYPE", raw, re.I):
        raise DchbTownError(
            f"{member}: declares a DTD, which a spreadsheet part never legitimately "
            "does — refusing to parse (entity-expansion vector)"
        )
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise DchbTownError(f"{member}: not parseable as XML — {exc}") from exc


def read_sheet(path: Path | str) -> list[dict[str, str]]:
    """Return the first worksheet as a list of {column_letter: value} dicts."""
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = [
                (node.text or "")
                for node in _safe_xml(archive, "xl/sharedStrings.xml").iter(NS + "t")
            ]
        sheet = _safe_xml(archive, "xl/worksheets/sheet1.xml")
    rows: list[dict[str, str]] = []
    for row in sheet.iter(NS + "row"):
        cells: dict[str, str] = {}
        for cell in row.iter(NS + "c"):
            ref = re.match(r"([A-Z]+)", cell.get("r") or "A")
            value = cell.find(NS + "v")
            if ref is None or value is None:
                continue
            raw = value.text or ""
            cells[ref.group(1)] = shared[int(raw)] if cell.get("t") == "s" else raw
        rows.append(cells)
    return rows


def locate_columns(header: dict[str, str]) -> dict[str, str]:
    """Map field name -> column letter, by header text.

    Raises :class:`DchbTownError` when the library columns are absent — a
    Village Release, or a future layout, must not yield rows full of nulls that
    read as "this town has no libraries".
    """
    found: dict[str, str] = {}
    for letter, text in header.items():
        field = HEADER_MAP.get(_norm(text))
        if field and field not in found:
            found[field] = letter
    missing = [f for f in REQUIRED if f not in found]
    if missing:
        raise DchbTownError(
            "not a DCHB Town Release — no library count columns found "
            f"(missing {', '.join(missing)}). Refusing to emit rows rather than "
            "record absent columns as zero libraries."
        )
    return found


def _int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _status(value: Any) -> str | None:
    """A(1)/NA(2) as published. Anything else is unknown, not 'absent'."""
    return {"1": "available", "2": "not_available"}.get(str(value or "").strip())


class DchbTownProbe:
    """Read a Town Release into typed rows plus a provenance manifest row."""

    def __init__(self, out_dir: Path | str) -> None:
        self.out_dir = Path(out_dir)
        self.manifest = self.out_dir / "manifest.jsonl"
        self.rows_path = self.out_dir / "town_amenity_rows.jsonl"

    def ingest(self, xlsx: Path | str) -> list[dict]:
        xlsx = Path(xlsx)
        sheet = read_sheet(xlsx)
        if not sheet:
            raise DchbTownError(f"{xlsx.name}: empty worksheet")
        cols = locate_columns(sheet[0])

        blob = xlsx.read_bytes()
        sha = hashlib.sha256(blob).hexdigest()
        rows: list[dict] = []
        for raw in sheet[1:]:
            if not raw.get(cols["town_name"]):
                continue
            rows.append(self._row(raw, cols, xlsx.name, sha))

        distinct = {r["key"] for r in rows}
        if len(distinct) != len(rows):
            collisions = sorted({r["key"] for r in rows if [x["key"] for x in rows].count(r["key"]) > 1})
            raise DchbTownError(
                f"{xlsx.name}: {len(rows)} town rows collapse to {len(distinct)} distinct "
                f"keys — colliding: {collisions[:5]}. Refusing to persist fewer rows than "
                "were parsed; a summary counted from memory would report the full figure "
                "over a short corpus."
            )

        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._write(self.rows_path, rows, key="key")
        state = rows[0]["state_code"] if rows else None
        record = {
            "key": f"DCHB|{state}|{xlsx.name}",
            "kind": "dchb_town_release",
            "record_type": "dchb_town_release",
            "source": "censusindia.gov.in",
            "census_year": "2011",
            "state_code": state,
            "source_filename": xlsx.name,
            "sha256": sha,
            "bytes": len(blob),
            "towns": len(rows),
            "districts": len({r["district_code"] for r in rows if r["district_code"]}),
            "ingested_at": _now(),
        }
        self._write(self.manifest, [record], key="key")
        return rows

    def _row(self, raw: dict, cols: dict, filename: str, sha: str) -> dict:
        def text(field: str) -> str | None:
            letter = cols.get(field)
            value = raw.get(letter) if letter else None
            if value in (None, ""):
                return None
            value = str(value).strip()
            width = CODE_WIDTHS.get(field)
            # Idempotent: a code already at or above its width is untouched.
            if width and value.isdigit():
                value = value.zfill(width)
            return value

        def count(field: str) -> int | None:
            letter = cols.get(field)
            return _int(raw.get(letter)) if letter else None

        def counted(value_field: str, status_field: str) -> int | None:
            """The count, or 0 when the status column says the facility is absent.

            Two states, two conventions, both verified against the real files:
            Maharashtra writes an explicit 0 for an absent facility (535/535
            towns carry a number); Nagaland leaves the cell EMPTY and marks the
            status `not_available` (21/26 towns). Reading only the count column
            would make Nagaland's 21 look unrecorded when the source plainly
            says the facility is not there. An empty cell with NO status stays
            None — that one is genuinely unknown.
            """
            value = count(value_field)
            if value is not None:
                return value
            letter = cols.get(status_field)
            if letter and _status(raw.get(letter)) == "not_available":
                return 0
            return None

        gov_lib = counted("public_library_govt", "public_library_govt_status")
        priv_lib = counted("public_library_private", "public_library_private_status")
        gov_rr = counted("reading_room_govt", "reading_room_govt_status")
        priv_rr = counted("reading_room_private", "reading_room_private_status")

        def total(a: int | None, b: int | None) -> int | None:
            """Sum, or None when neither value was recorded.

            `(a or 0) + (b or 0)` turned "these columns are absent from this
            file" into "this town has zero facilities" — the conflation this
            module exists to prevent, in its own arithmetic.
            """
            return None if a is None and b is None else (a or 0) + (b or 0)
        state, town = text("state_code"), text("town_code")
        district = text("district_code")
        # The district is IN the key because a town code is not unique within a
        # state: Greater Mumbai is one municipal corporation split across Mumbai
        # and Mumbai Suburban, both carrying town code 802794. Keying on
        # state+town collapsed them and dropped 9.36 million people.
        return {
            "key": f"DCHB|{state}|{district}|{town}",
            "kind": "dchb_town_amenity",
            "record_type": "dchb_town_amenity",
            "source": "censusindia.gov.in",
            "census_year": "2011",
            # The anti-sum guard. The rural Village Amenities value is an
            # availability flag; this is a count. Schema pins the const.
            "measure": "count",
            "state_code": state,
            "state_name": text("state_name"),
            "district_code": text("district_code"),
            "district_name": text("district_name"),
            "subdistrict_code": text("subdistrict_code"),
            "subdistrict_name": text("subdistrict_name"),
            "town_code": town,
            "town_name": text("town_name"),
            "total_households": count("total_households"),
            "total_population": count("total_population"),
            "public_library_govt": gov_lib,
            "public_library_private": priv_lib,
            "public_library_total": total(gov_lib, priv_lib),
            "public_library_govt_status": _status(raw.get(cols.get("public_library_govt_status"))),
            "public_library_private_status": _status(
                raw.get(cols.get("public_library_private_status"))
            ),
            # A SEPARATE facility. Deliberately no field combining the two.
            "reading_room_govt": gov_rr,
            "reading_room_private": priv_rr,
            "reading_room_total": total(gov_rr, priv_rr),
            "reading_room_govt_status": _status(raw.get(cols.get("reading_room_govt_status"))),
            "reading_room_private_status": _status(
                raw.get(cols.get("reading_room_private_status"))
            ),
            "source_filename": filename,
            "source_sha256": sha,
            "extracted_at": _now(),
        }

    def _write(self, path: Path, new: list[dict], *, key: str) -> None:
        """Upsert by key — one row per artefact, so a re-ingest replaces rather
        than duplicates."""
        existing: dict[str, dict] = {}
        order: list[str] = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get(key) not in existing:
                    order.append(row.get(key))
                existing[row.get(key)] = row
        for row in new:
            if row[key] not in existing:
                order.append(row[key])
            existing[row[key]] = row
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            "".join(json.dumps(existing[k], ensure_ascii=False) + "\n" for k in order),
            encoding="utf-8",
        )
        tmp.replace(path)
