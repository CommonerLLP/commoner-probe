# SPDX-License-Identifier: MIT
"""Extract town-level amenities from a Census release workbook.

CONTEXT
=======
ORGI produces the data. ORGI is the Office of the Registrar General and
Census Commissioner, Ministry of Home Affairs.
The source is the Town Release of the District Census Handbook.
This module reads it through the Open Government Data API. It does not read the
handbook PDFs.

The request asked for every public library in the 2011 Census. The rural half is
served by Village Amenities (see :mod:`commoner_probe.ogd_resource_api`). The urban half
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

from .base import safe_filename_segment

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


#: Column headers in a Statement V sheet, which Sikkim publishes instead of a
#: state-level Town Release. Verified against the real North District file.
STATEMENT_V_HEADERS = {
    "public libraries": "public_library",
    "reading rooms": "reading_room",
}


def read_capped(archive: zipfile.ZipFile, member: str, limit: int | None = None) -> bytes:
    """Read one zip member, stopping if it expands past *limit*.

    Decompresses in chunks through ``archive.open`` and counts the bytes that
    actually arrive. ``ZipFile.read`` cannot do this: it expands the whole
    member into memory and only then compares the result against the CRC and
    size in the archive's own directory.

    The version this replaced tested ``info.file_size`` and then called
    ``ZipFile.read``. ``file_size`` is a number the archive supplies about
    itself, so an attacker writes it. Measured on a forged file whose headers
    declared 1000 bytes and whose stream expanded to 300 MB:

        declared file_size: 1000     -> the check passed
        archive.read(...)            -> raised BadZipFile (bad CRC)
        peak RSS during the call     -> +432 MB

    The exception arrived after the allocation, so it was not a defence.

    Two bounds hold here instead, covering the honest and the dishonest case.
    An oversized DECLARED size is refused before any decompression. A declared
    size that lies low bounds ``ZipExtFile`` itself, which stops at the claim
    and then fails its CRC — so the forged member above yields 1000 bytes and
    an error rather than 300 MB. The running total is the belt to that
    braces: it stops a member that outruns the cap whatever the header said.
    """
    # Resolved here, not as a default argument: a default binds at def time,
    # so MAX_XML_BYTES could not be overridden afterwards — which silently
    # broke the existing bomb test that lowers it.
    limit = MAX_XML_BYTES if limit is None else limit
    declared = archive.getinfo(member).file_size
    if declared > limit:
        raise DchbTownError(
            f"{member}: declares {declared} uncompressed bytes, above the "
            f"{limit} cap — refusing to expand a possible decompression bomb"
        )
    chunks: list[bytes] = []
    total = 0
    try:
        with archive.open(member) as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise DchbTownError(
                        f"{member}: expanded past the {limit}-byte cap while declaring "
                        f"{declared} — refusing to expand a decompression bomb"
                    )
                chunks.append(chunk)
    except zipfile.BadZipFile as exc:
        # Reached when the member's real content disagrees with the size or
        # CRC the archive declared for it — i.e. exactly the forged case.
        raise DchbTownError(
            f"{member}: the archive's declared size or checksum does not match its "
            f"contents ({exc}) — refusing to use it"
        ) from exc
    return b"".join(chunks)


def _load_xlrd():
    """The optional xls reader, or None. Kept lazy: the core declares
    `dependencies = []` and 34 of 35 states need no .xls path at all."""
    try:
        import xlrd  # type: ignore
    except ImportError:
        return None
    return xlrd


def parse_facility_cell(value: Any) -> tuple[int | None, str | None, float | None]:
    """Return (count, nearest_place, distance_km) for one Statement V cell.

    A cell holds EITHER a count OR the nearest town and its distance —
    `GANGTOK(67)` means there is none in this town and the nearest is 67 km
    away. That is a count of **zero with a location**, not a missing value.
    Parsing the cell as an integer silently drops exactly the towns that lack
    the facility, which is the trap the request spec names.
    """
    if value is None:
        return None, None, None
    text = str(value).strip()
    if not text:
        return None, None, None
    try:
        return int(float(text)), None, None
    except ValueError:
        pass
    match = re.match(r"^(.+?)\s*\((\d+(?:\.\d+)?)\)$", text)
    if match:
        return 0, match.group(1).strip(), float(match.group(2))
    return None, None, None


def district_from_zip(zip_path: Path | str) -> tuple[str, str, str]:
    """(state_code, census_district_code, district_name) from a DCHB district ZIP.

    **The filename is not the source of the district code.** ORGI names these
    `DH_2011_1101-North_District.zip`, where `1101` is the state code plus a
    district ORDINAL — not the 2011 Census district code the rest of this corpus
    joins on. North District's census code is 241. The only in-band source is the
    `Appendix_I` header cell, `District: North  District (241)`.

    Copying the ordinal writes a key that silently fails to join, so a ZIP
    without that header is refused rather than guessed at.
    """
    xlrd = _load_xlrd()
    if xlrd is None:
        raise DchbTownError(
            "reading a DCHB district ZIP needs the optional xls reader — "
            "run: pip install commoner-probe[xls]. Only Sikkim publishes this "
            "shape; the other 34 states ship an .xlsx that needs no extra."
        )
    zip_path = Path(zip_path)
    ordinal = re.search(r"DH_2011_(\d{4})", zip_path.name)
    if not ordinal:
        raise DchbTownError(f"{zip_path.name}: not a DH_2011 district ZIP")
    state = ordinal.group(1)[:2]

    with zipfile.ZipFile(zip_path) as archive:
        names = [n for n in archive.namelist() if re.search(r"Appendix_I_\d+\.xls$", n)]
        if not names:
            raise DchbTownError(
                f"{zip_path.name}: no Appendix_I member, so the census district code "
                "cannot be read. The filename carries ORGI's ordinal, not the census "
                "code, and guessing it would write rows that join to the wrong district."
            )
        book = xlrd.open_workbook(file_contents=read_capped(archive, names[0]))
    sheet = book.sheet_by_index(0)
    for r in range(min(sheet.nrows, 8)):
        for c in range(min(sheet.ncols, 4)):
            match = re.search(r"District:\s*(.+?)\s*\((\d+)\)", str(sheet.cell_value(r, c)))
            if match:
                return state, match.group(2), re.sub(r"\s+", " ", match.group(1)).strip()
    raise DchbTownError(
        f"{zip_path.name}: Appendix_I carries no 'District: <name> (<code>)' header, "
        "so the census district code is unavailable"
    )


def read_statement_v(path: Path | str) -> list[dict]:
    """Read a `Town Statement-V_<district>.xls` into town dicts.

    Sikkim is the only state that publishes this shape rather than a
    state-level Town Release; the columns carry the same facts.
    """
    xlrd = _load_xlrd()
    if xlrd is None:
        raise DchbTownError(
            "reading Statement V needs the optional xls reader — "
            "run: pip install commoner-probe[xls]. Only Sikkim publishes this "
            "shape; the other 34 states ship an .xlsx that needs no extra."
        )
    if isinstance(path, (bytes, bytearray)):
        book = xlrd.open_workbook(file_contents=bytes(path))
    else:
        book = xlrd.open_workbook(str(path))
    sheet = book.sheet_by_index(0)

    header_row = None
    columns: dict[str, int] = {}
    for r in range(min(sheet.nrows, 10)):
        found = {}
        for c in range(sheet.ncols):
            field = STATEMENT_V_HEADERS.get(_norm(sheet.cell_value(r, c)))
            if field:
                found[field] = c
        if len(found) == len(STATEMENT_V_HEADERS):
            header_row, columns = r, found
            break
    if header_row is None:
        raise DchbTownError(
            f"{Path(path).name}: no Statement V library columns found — refusing "
            "to emit rows rather than record absent columns as zero libraries"
        )

    rows: list[dict] = []
    for r in range(header_row + 1, sheet.nrows):
        name = str(sheet.cell_value(r, 1)).strip()
        # Skip the column-number band (1.0, 2.0, ...) and the footnote row.
        if not name or name.replace(".", "").isdigit() or name.startswith("*"):
            continue
        row = {"town_name": name}
        for field, col in columns.items():
            count, place, dist = parse_facility_cell(sheet.cell_value(r, col))
            row[field] = count
            row[f"{field}_nearest_place"] = place
            row[f"{field}_nearest_km"] = dist
        rows.append(row)
    return rows


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _slug(value: str) -> str:
    """Filesystem- and key-safe form of a town name."""
    return safe_filename_segment(value, collapse=True)


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _safe_xml(archive: zipfile.ZipFile, member: str) -> ET.Element:
    """Parse one zip member, refusing the two shapes stdlib XML is unsafe against.

    See MAX_XML_BYTES. Raises :class:`DchbTownError` rather than letting a
    hostile file reach the parser.
    """
    raw = read_capped(archive, member)
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

    def ingest_district_zip(self, zip_path: Path | str) -> list[dict]:
        """Ingest one DCHB district ZIP — Sikkim's shape — end to end.

        Takes the ZIP because that is what ORGI serves AND because the census
        district code lives in a sibling member; a loose Statement V file cannot
        supply it.
        """
        zip_path = Path(zip_path)
        state, district, district_name = district_from_zip(zip_path)
        with zipfile.ZipFile(zip_path) as archive:
            members = [n for n in archive.namelist() if "Town Statement-V_" in n]
            if not members:
                raise DchbTownError(f"{zip_path.name}: no Town Statement-V member")
            payload = read_capped(archive, members[0])
            member_name = Path(members[0]).name
        return self.ingest_statement_v(
            payload,
            state_code=state,
            district_code=district,
            district_name=district_name,
            source_filename=f"{zip_path.name}::{member_name}",
        )

    def ingest_statement_v(self, xls, *, state_code: str, district_code: str,
                           district_name: str | None = None,
                           source_filename: str | None = None) -> list[dict]:
        """Ingest one `Town Statement-V_<district>.xls` — Sikkim's shape.

        Emits the SAME `dchb_town_amenity` rows as the xlsx path, so a consumer
        never has to know which format a state happened to publish. The state
        and district codes are not in the sheet, so the caller supplies them
        from the filename or the record.
        """
        if isinstance(xls, (bytes, bytearray)):
            blob = bytes(xls)
            name = source_filename or "Town Statement-V.xls"
        else:
            blob = Path(xls).read_bytes()
            name = source_filename or Path(xls).name
        towns = read_statement_v(blob)
        sha = hashlib.sha256(blob).hexdigest()
        rows = []
        for town in towns:
            lib, room = town.get("public_library"), town.get("reading_room")
            rows.append({
                "key": f"DCHB|{state_code}|{district_code}|{_slug(town['town_name'])}",
                "kind": "dchb_town_amenity",
                "record_type": "dchb_town_amenity",
                "source": "censusindia.gov.in",
                "census_year": "2011",
                "measure": "count",
                "state_code": state_code,
                "state_name": None,
                "district_code": district_code,
                "district_name": district_name,
                "subdistrict_code": None,
                "subdistrict_name": None,
                # Statement V has no town code; the name is the identity here.
                "town_code": None,
                "town_name": town["town_name"],
                "total_households": None,
                "total_population": None,
                # Statement V does not split govt from private, so the split
                # fields stay null rather than guessing which side a count is.
                "public_library_govt": None,
                "public_library_private": None,
                "public_library_total": lib,
                "public_library_govt_status": None,
                "public_library_private_status": None,
                "reading_room_govt": None,
                "reading_room_private": None,
                "reading_room_total": room,
                "reading_room_govt_status": None,
                "reading_room_private_status": None,
                "source_filename": name,
                "source_sha256": sha,
                "extracted_at": _now(),
            })
        distinct = {r["key"] for r in rows}
        if len(distinct) != len(rows):
            raise DchbTownError(
                f"{name}: {len(rows)} towns collapse to {len(distinct)} keys"
            )
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._write(self.rows_path, rows, key="key")
        self._write(self.manifest, [{
            "key": f"DCHB|{state_code}|{name}",
            "kind": "dchb_town_release",
            "record_type": "dchb_town_release",
            "source": "censusindia.gov.in",
            "census_year": "2011",
            "state_code": state_code,
            "source_filename": name,
            "sha256": sha,
            "bytes": len(blob),
            "towns": len(rows),
            "districts": 1,
            "ingested_at": _now(),
        }], key="key")
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
