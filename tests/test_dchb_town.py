"""Tests for the DCHB Town Release reader — the urban half of REQ-0045.

The fixture is the real Nagaland Town Release trimmed to three towns; see
`tests/fixtures/dchb/README.md`. No test here touches the network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from commoner_probe import dchb_town

FIX = Path(__file__).parent / "fixtures" / "dchb" / "town_release_1300_trimmed.xlsx"


def _rows(tmp_path):
    probe = dchb_town.DchbTownProbe(tmp_path)
    return probe.ingest(FIX)


def _row_file(tmp_path):
    return [
        json.loads(ln)
        for ln in (tmp_path / "town_amenity_rows.jsonl").read_text().splitlines()
        if ln.strip()
    ]


def _manifest(tmp_path):
    return [
        json.loads(ln)
        for ln in (tmp_path / "manifest.jsonl").read_text().splitlines()
        if ln.strip()
    ]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_columns_are_found_by_header_not_by_hardcoded_letter():
    """The refs are stable across the two states checked, but a reader keyed on
    the letter OS would silently read the wrong column the day ORGI inserts one.
    Headers are the contract; the letters are an implementation detail."""
    cols = dchb_town.locate_columns(dchb_town.read_sheet(FIX)[0])
    assert cols["public_library_govt"] == "OS"
    assert cols["public_library_private"] == "OU"
    assert cols["reading_room_govt"] == "OZ"
    assert cols["reading_room_private"] == "PB"
    assert cols["town_name"] == "H"


def test_a_file_missing_the_library_columns_is_refused():
    """A Village Release, or a future layout without these columns, must raise
    rather than emit rows with silent nulls that read as 'no libraries'."""
    header = {"A": "State Code", "H": "Town Name"}
    with pytest.raises(dchb_town.DchbTownError, match="library"):
        dchb_town.locate_columns(header)


def test_three_towns_are_parsed_with_their_codes(tmp_path):
    rows = _rows(tmp_path)
    assert len(rows) == 3
    first = rows[0]
    assert first["state_code"] == "13"
    assert first["town_name"]
    assert first["town_code"]
    assert first["district_name"]


def test_counts_are_integers_and_status_is_kept_separately(tmp_path):
    rows = _rows(tmp_path)
    for r in rows:
        for field in (
            "public_library_govt",
            "public_library_private",
            "reading_room_govt",
            "reading_room_private",
        ):
            assert r[field] is None or isinstance(r[field], int)
        assert r["public_library_govt_status"] in ("available", "not_available", None)


def test_library_and_reading_room_totals_are_separate_fields(tmp_path):
    """Govt + private are both counts of the same facility, so summing those is
    valid. Libraries + reading rooms are different facilities and get no
    combined field at all — the absence is the point."""
    row = _rows(tmp_path)[0]
    assert row["public_library_total"] == (
        (row["public_library_govt"] or 0) + (row["public_library_private"] or 0)
    )
    assert row["reading_room_total"] == (
        (row["reading_room_govt"] or 0) + (row["reading_room_private"] or 0)
    )
    combined = [k for k in row if "library_and_reading" in k or k == "facility_total"]
    assert not combined, f"no field may combine the two facilities: {combined}"


# ---------------------------------------------------------------------------
# Hostile input — an XLSX is a zip of XML fetched from a government portal
# ---------------------------------------------------------------------------


def _xlsx_with(tmp_path, member, payload):
    """Copy the real fixture, replacing one member with a hostile payload."""
    import zipfile as zf

    out = tmp_path / "hostile.xlsx"
    with zf.ZipFile(FIX) as src, zf.ZipFile(out, "w") as dst:
        for item in src.infolist():
            dst.writestr(item, payload if item.filename == member else src.read(item.filename))
    return out


def test_a_part_declaring_a_dtd_is_refused(tmp_path):
    """The billion-laughs entity bomb needs a DTD, and a spreadsheet part never
    legitimately carries one. stdlib ElementTree would happily expand it, and
    defusedxml is unavailable — this package declares dependencies = []."""
    bomb = (
        b'<?xml version="1.0"?>\n<!DOCTYPE lolz [<!ENTITY lol "lol">'
        b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>\n'
        b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        b"<sheetData><row><c r=\"A1\"><v>&lol2;</v></c></row></sheetData></worksheet>"
    )
    hostile = _xlsx_with(tmp_path, "xl/worksheets/sheet1.xml", bomb)
    with pytest.raises(dchb_town.DchbTownError, match="DTD"):
        dchb_town.read_sheet(hostile)


def test_a_part_declaring_an_absurd_uncompressed_size_is_refused(tmp_path, monkeypatch):
    """A decompression bomb is rejected on its DECLARED size, before it is
    expanded into memory."""
    monkeypatch.setattr(dchb_town, "MAX_XML_BYTES", 128)
    with pytest.raises(dchb_town.DchbTownError, match="decompression bomb"):
        dchb_town.read_sheet(FIX)


def test_a_malformed_part_raises_rather_than_yielding_nothing(tmp_path):
    hostile = _xlsx_with(tmp_path, "xl/worksheets/sheet1.xml", b"<worksheet><unclosed>")
    with pytest.raises(dchb_town.DchbTownError, match="not parseable"):
        dchb_town.read_sheet(hostile)


# ---------------------------------------------------------------------------
# Key uniqueness — found live, and it cost 9.36 million people
# ---------------------------------------------------------------------------


def test_a_town_code_repeated_across_districts_yields_two_rows(tmp_path):
    """Greater Mumbai is ONE municipal corporation split across TWO districts —
    Mumbai Suburban (pop 9,356,962) and Mumbai (pop 3,085,411) — sharing town
    code 802794. A key of state+town therefore collapsed them: the parser
    produced 535 Maharashtra towns and the corpus held 534, losing 11 libraries
    and 9.36 million people, while the CLI printed the right total because it
    summed the in-memory list rather than what it had written."""
    shared = {
        "A": "27", "C": "3520", "D": "Mumbai Suburban", "G": "802794",
        "H": "Greater Mumbai (M Corp.) Part", "J": "9356962", "OS": "5", "OU": "0",
    }
    other = {**shared, "C": "3521", "D": "Mumbai", "J": "3085411", "OS": "6"}
    probe = dchb_town.DchbTownProbe(tmp_path)
    cols = dchb_town.locate_columns(dchb_town.read_sheet(FIX)[0])
    rows = [probe._row(shared, cols, "x.xlsx", "0" * 64), probe._row(other, cols, "x.xlsx", "0" * 64)]
    assert rows[0]["key"] != rows[1]["key"], (
        "town_code is not unique within a state — the district must be in the key"
    )


def test_ingest_refuses_to_persist_fewer_rows_than_it_parsed(tmp_path):
    """The invariant that catches the NEXT collision, whatever causes it. A
    summary computed from the in-memory list while the corpus on disk is short
    is precisely the silent success this package keeps shipping."""
    probe = dchb_town.DchbTownProbe(tmp_path)
    original = probe._row

    def colliding(raw, cols, filename, sha):
        row = original(raw, cols, filename, sha)
        row["key"] = "DCHB|13|SAME"          # force every row onto one key
        return row

    probe._row = colliding
    with pytest.raises(dchb_town.DchbTownError, match="collid|distinct"):
        probe.ingest(FIX)


# ---------------------------------------------------------------------------
# The rule the request exists to protect
# ---------------------------------------------------------------------------


def test_every_row_declares_that_it_is_a_count(tmp_path):
    """The rural Village Amenities value is an availability FLAG per village;
    this is a COUNT per town. Adding them yields the wrong ~75,000. Each row
    says which it is, so a consumer that sums across both has to ignore an
    explicit field rather than merely fail to notice."""
    for row in _rows(tmp_path):
        assert row["measure"] == "count"


def test_the_schema_pins_the_measure_so_a_flag_cannot_be_written_here(tmp_path):
    from commoner_probe import schemas as sc

    schema = sc.load("dchb_town_amenity")
    assert schema["properties"]["measure"] == {"const": "count"}


def test_a_row_claiming_to_be_a_flag_fails_validation(tmp_path):
    """Proof the guard can actually reject — not merely that it is declared."""
    from commoner_probe.validate import validate_corpus

    _rows(tmp_path)
    path = tmp_path / "town_amenity_rows.jsonl"
    rows = _row_file(tmp_path)
    rows[0]["measure"] = "availability_flag"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert not validate_corpus(tmp_path, log=lambda _m: None)


# ---------------------------------------------------------------------------
# Provenance and corpus wiring
# ---------------------------------------------------------------------------


def test_the_manifest_records_the_source_file_with_its_hash(tmp_path):
    _rows(tmp_path)
    manifest = _manifest(tmp_path)
    assert len(manifest) == 1
    rec = manifest[0]
    assert rec["kind"] == "dchb_town_release"
    assert rec["state_code"] == "13"
    assert rec["towns"] == 3
    assert len(rec["sha256"]) == 64
    assert rec["source_filename"] == FIX.name


def test_rows_carry_the_join_key_back_to_the_rural_corpus(tmp_path):
    """The rural half is keyed on census state/district codes; these rows carry
    the same codes so the two can be joined without a name match."""
    for row in _rows(tmp_path):
        assert row["state_code"] and row["district_code"]
        assert row["key"].startswith("DCHB|13|")  # state, then district, then town
        assert row["key"].count("|") == 3


def test_the_written_corpus_validates(tmp_path):
    from commoner_probe.validate import validate_corpus

    _rows(tmp_path)
    assert validate_corpus(tmp_path, log=lambda _m: None)


def test_both_kinds_are_registered(tmp_path):
    from commoner_probe.validate import _pick_schema_name

    assert _pick_schema_name({"kind": "dchb_town_release"}) == "manifest_dchb_town_release"


def test_records_round_trip_through_the_corpus_streams(tmp_path):
    from commoner_probe.corpus import Corpus

    _rows(tmp_path)
    releases = list(Corpus(tmp_path).manifest_dchb_town_releases())
    towns = list(Corpus(tmp_path).dchb_town_amenities())
    assert len(releases) == 1 and releases[0].state_code == "13"
    assert len(towns) == 3
    assert towns[0].measure == "count"


def test_every_written_field_survives_the_typed_api(tmp_path):
    """_from_dict drops unknown keys, so a field the writer emits but the
    dataclass omits vanishes for typed consumers."""
    from commoner_probe.corpus import Corpus

    raw_row = _rows(tmp_path)[0]
    raw_manifest = _manifest(tmp_path)[0]
    typed_town = next(iter(Corpus(tmp_path).dchb_town_amenities()))
    typed_release = next(iter(Corpus(tmp_path).manifest_dchb_town_releases()))
    assert not set(raw_row) - set(vars(typed_town))
    assert not set(raw_manifest) - set(vars(typed_release))


def test_re_ingesting_does_not_duplicate_rows(tmp_path):
    probe = dchb_town.DchbTownProbe(tmp_path)
    probe.ingest(FIX)
    first = len(_row_file(tmp_path))
    probe.ingest(FIX)
    assert len(_row_file(tmp_path)) == first
    assert len(_manifest(tmp_path)) == 1
