"""RERA project row and listing manifest schemas, records, and Corpus readers.

The rows here are hand-written to match the shapes verified against the
Karnataka RERA listing on November 11, 2026 (see
`tests/fixtures/rera_karnataka/README.md`). No test touches the network.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import fields

import pytest

pytest.importorskip("jsonschema")
from jsonschema import Draft202012Validator  # noqa: E402

from commoner_probe import records as R  # noqa: E402
from commoner_probe import schemas as sc  # noqa: E402
from commoner_probe.corpus import Corpus  # noqa: E402
from commoner_probe.validate import _pick_schema_name, validate_corpus  # noqa: E402

SHA = "a" * 64

BASE_ROW = {
    "key": "RERA|IN-KA|PR/KN/170729/000003",
    "kind": "rera_project_row",
    "record_type": "rera_project_row",
    "source": "rera-karnataka",
    "state": "IN-KA",
    "registration_no": "PRM/KA/RERA/1251/309/PR/170729/000003",
    "promoter": "Example Builders",
    "project_name": "Example Heights",
    "district": "Bengaluru Urban",
    "listing_url": "https://rera.karnataka.gov.in/projectViewDetails",
    "source_sha256": SHA,
    "extracted_at": "2026-11-11T00:00:00+00:00",
    "fields": {"STATUS": "APPROVED", "TALUK": "Bangalore North"},
}

ALL_DATES = {
    "approved_on": "2018-03-27",
    "proposed_completion_date_at_registration": "2021-12-31",
    "proposed_completion_date_current": "2023-06-30",
    "covid_extension_start": "2020-04-01",
    "covid_extension_end": "2020-09-30",
    "section6_extension_start": "2021-01-01",
    "section6_extension_end": "2021-06-30",
    "further_extension_start": "2022-01-01",
    "further_extension_end": "2022-12-31",
}

BASE_MANIFEST = {
    "key": "RERA|IN-KA|Bengaluru Urban",
    "kind": "rera_listing",
    "state": "IN-KA",
    "url": "https://rera.karnataka.gov.in/projectViewDetails",
    "method": "POST",
    "form": {"district": "Bengaluru Urban", "subdistrict": "0", "btn1": "Search"},
    "http_status": 200,
    "path": "raw/rera_karnataka_bengaluru_urban.html",
    "sha256": SHA,
    "bytes": 32861637,
    "row_count": 4460,
    "fetched_at": "2026-11-11T00:00:00+00:00",
    "status": "downloaded",
    "error": None,
}


def _errors(schema_name, doc):
    return list(Draft202012Validator(sc.load(schema_name)).iter_errors(doc))


def _write(tmp_path, name, rows):
    (tmp_path / name).write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def test_both_schemas_load_and_are_valid():
    for name in ("rera_project_row", "manifest_rera_listing"):
        Draft202012Validator.check_schema(sc.load(name))


def test_row_with_every_extension_date_validates():
    assert _errors("rera_project_row", {**BASE_ROW, **ALL_DATES}) == []


def test_row_with_no_dates_validates_when_absent_or_null():
    assert _errors("rera_project_row", BASE_ROW) == []
    nulls = {k: None for k in ALL_DATES}
    assert _errors("rera_project_row", {**BASE_ROW, **nulls}) == []


def test_covid_extension_with_a_start_and_no_end_validates():
    """47 Bengaluru Urban rows list `dd-mm-yyyy -` with no end date."""
    row = {**BASE_ROW, "covid_extension_start": "2020-04-01", "covid_extension_end": None}
    assert _errors("rera_project_row", row) == []


def test_row_without_a_registration_number_validates():
    row = {**BASE_ROW, "registration_no": None}
    assert _errors("rera_project_row", row) == []


def test_key_does_not_depend_on_the_registration_number():
    """The key is the acknowledgement number, so it survives registration."""
    before = {**BASE_ROW, "registration_no": None}
    after = {**BASE_ROW, "registration_no": "PRM/KA/RERA/1/2/3"}
    assert before["key"] == after["key"]
    assert _errors("rera_project_row", before) == []
    assert _errors("rera_project_row", after) == []


@pytest.mark.parametrize("missing", ["kind", "state", "record_type", "key", "extracted_at"])
def test_row_missing_a_required_field_is_rejected(missing):
    row = deepcopy(BASE_ROW)
    del row[missing]
    assert _errors("rera_project_row", row)


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "something_else"},
        {"key": "DCHB|IN-KA|x"},
        {"key": "RERA|KA|x"},
        {"state": "Karnataka"},
        {"approved_on": "27/03/2018"},
        {"covid_extension_start": "01-04-2020"},
    ],
)
def test_row_with_a_wrong_shape_is_rejected(bad):
    assert _errors("rera_project_row", {**BASE_ROW, **bad})


def test_haryana_registration_validity_and_lapsed_flag_validate():
    row = {**BASE_ROW, "registration_valid_until": "2029-11-27", "lapsed": True}
    assert _errors("rera_project_row", row) == []
    assert _errors("rera_project_row", {**row, "registration_valid_until": None, "lapsed": None}) == []


@pytest.mark.parametrize(
    "bad",
    [{"registration_valid_until": "27/11/2029"}, {"lapsed": "yes"}, {"lapsed": 1}],
)
def test_haryana_typed_fields_reject_a_wrong_shape(bad):
    assert _errors("rera_project_row", {**BASE_ROW, **bad})


def test_tamil_nadu_permit_numbers_and_coordinates_validate():
    row = {
        **BASE_ROW,
        "cmda_permission_no": "OL-PP/NHRB/0235/2025",
        "gcc_permission_no": "CEBA/WDCN10/00287/2025",
        "latitude": 13.04864,
        "longitude": 80.211372,
    }
    assert _errors("rera_project_row", row) == []
    nulls = {k: None for k in ("cmda_permission_no", "gcc_permission_no", "latitude", "longitude")}
    assert _errors("rera_project_row", {**BASE_ROW, **nulls}) == []


@pytest.mark.parametrize(
    "bad",
    [{"cmda_permission_no": 5}, {"latitude": "13.0"}, {"longitude": 181}, {"latitude": -91}],
)
def test_tamil_nadu_typed_fields_reject_a_wrong_shape(bad):
    assert _errors("rera_project_row", {**BASE_ROW, **bad})


def test_row_accepts_a_state_specific_typed_field():
    assert _errors("rera_project_row", {**BASE_ROW, "quarterly_report_url": "https://x"}) == []


def test_listing_manifest_validates_downloaded_and_error():
    assert _errors("manifest_rera_listing", BASE_MANIFEST) == []
    failed = {
        **BASE_MANIFEST,
        "http_status": None, "path": None, "sha256": None, "bytes": None,
        "row_count": None, "status": "error", "error": "ConnectionError",
    }
    assert _errors("manifest_rera_listing", failed) == []


@pytest.mark.parametrize("missing", ["kind", "state", "status", "error", "form"])
def test_listing_manifest_missing_a_required_field_is_rejected(missing):
    doc = deepcopy(BASE_MANIFEST)
    del doc[missing]
    assert _errors("manifest_rera_listing", doc)


def test_listing_manifest_rejects_an_unknown_status():
    assert _errors("manifest_rera_listing", {**BASE_MANIFEST, "status": "pending"})


def test_manifest_kind_is_registered():
    assert _pick_schema_name({"kind": "rera_listing"}) == "manifest_rera_listing"


def test_validate_checks_rera_project_rows_file(tmp_path):
    _write(tmp_path, "rera_project_rows.jsonl", [{**BASE_ROW, **ALL_DATES}])
    assert validate_corpus(tmp_path, log=lambda _m: None)


def test_validate_rejects_a_bad_rera_project_rows_file(tmp_path):
    bad = deepcopy(BASE_ROW)
    del bad["state"]
    _write(tmp_path, "rera_project_rows.jsonl", [bad])
    assert not validate_corpus(tmp_path, log=lambda _m: None)


def test_validate_checks_the_listing_manifest(tmp_path):
    _write(tmp_path, "manifest.jsonl", [BASE_MANIFEST])
    assert validate_corpus(tmp_path, log=lambda _m: None)
    _write(tmp_path, "manifest.jsonl", [{**BASE_MANIFEST, "status": "pending"}])
    assert not validate_corpus(tmp_path, log=lambda _m: None)


def test_corpus_readers_yield_typed_records(tmp_path):
    _write(tmp_path, "rera_project_rows.jsonl", [{**BASE_ROW, **ALL_DATES}, BASE_ROW])
    _write(tmp_path, "manifest.jsonl", [BASE_MANIFEST])
    rows = list(Corpus(tmp_path).rera_project_rows())
    listings = list(Corpus(tmp_path).manifest_rera_listings())
    assert len(rows) == 2 and len(listings) == 1
    assert isinstance(rows[0], R.ReraProjectRowRecord)
    assert rows[0].covid_extension_end == "2020-09-30"
    assert rows[0].fields["STATUS"] == "APPROVED"
    assert rows[1].further_extension_start is None
    assert isinstance(listings[0], R.ManifestReraListingRecord)
    assert listings[0].row_count == 4460


def test_every_schema_field_survives_the_typed_api():
    for schema_name, cls in (
        ("rera_project_row", R.ReraProjectRowRecord),
        ("manifest_rera_listing", R.ManifestReraListingRecord),
    ):
        declared = set(sc.load(schema_name)["properties"])
        assert declared <= {f.name for f in fields(cls)}
