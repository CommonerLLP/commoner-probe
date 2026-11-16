"""Tamil Nadu RERA parser, probe, and `rera --state TN` subcommand.

Parser tests read the trimmed captures in `tests/fixtures/rera_tamil_nadu/`
(10 of the 402 rows of the 2026 building list, 5 of the 428 rows of the 2025
list, and 5 of the 3,945 rows of the 2026 layout list, November 11, 2026). The
probe tests use a fake session. No test touches the network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from commoner_probe.cli import main
from commoner_probe.corpus import Corpus
from commoner_probe.http_client import StdlibResponse
from commoner_probe.rera import STATES
from commoner_probe.rera.common import ReraError
from commoner_probe.rera.tamil_nadu import (
    HEADERS,
    TamilNaduReraProbe,
    listing_url,
    parse_coordinates,
    parse_form,
    parse_listing,
    permit_numbers,
    refuse_disallowed_url,
    resolve_kind,
)

FIXTURES = Path(__file__).parent / "fixtures" / "rera_tamil_nadu"
BUILDING = (FIXTURES / "registered_building_tn_trimmed.html").read_text(encoding="utf-8")
BUILDING_2025 = (FIXTURES / "registered_building_tn_2025_trimmed.html").read_text(encoding="utf-8")
LAYOUT = (FIXTURES / "registered_layout_tn_trimmed.html").read_text(encoding="utf-8")


def _by_no(rows, registration_no):
    return next(r for r in rows if r["registration_no"] == registration_no)


def _table(*registrations: str, extra_header: str = "", width: int | None = None) -> str:
    """A minimal listing: one row per registration cell, every other cell empty."""
    head = "".join(f"<th>{h}</th>" for h in HEADERS) + extra_header
    cells = width if width is not None else len(HEADERS)
    body = ""
    for i, reg in enumerate(registrations, 1):
        row = [str(i), reg] + [""] * (cells - 2)
        body += "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>"
    return f'<table id="example1"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


# --- parser: the 2026 building list --------------------------------------------


def test_parses_every_kept_row():
    rows = parse_listing(BUILDING)
    assert [r["fields"]["S. No"] for r in rows] == [
        "1", "2", "3", "5", "8", "11", "18", "46", "290", "402"
    ]
    assert all(r["kind"] == "rera_project_row" and r["state"] == "IN-TN" for r in rows)
    assert all(r["source"] == "rera-tamil-nadu" and r["district"] is None for r in rows)


def test_registration_cell_splits_into_number_and_date():
    row = parse_listing(BUILDING)[0]
    assert row["key"] == "RERA|IN-TN|TNRERA/29/BLG/0001/2026"
    assert row["registration_no"] == "TNRERA/29/BLG/0001/2026"
    assert row["approved_on"] == "2026-01-02"
    assert row["fields"]["Project Registration No."] == "TNRERA/29/BLG/0001/2026 dated 02-01-2026"


def test_every_column_is_kept_in_fields():
    fields = parse_listing(BUILDING)[0]["fields"]
    assert set(HEADERS) <= set(fields)
    assert fields["Project Completion Date"] == "21.12.2030"
    assert fields["Name and Address of the Promoter"].startswith("TNUHDB, TNUHDB Division 1")


def test_project_name_is_the_first_line_of_the_details_cell():
    row = parse_listing(BUILDING)[0]
    assert row["project_name"] == "Thiruvottiyur Scheme"
    assert row["fields"]["Project Details and Address"].startswith(
        "Project Name: Thiruvottiyur Scheme Registration of additional 4 Blocks"
    )


def test_a_row_without_a_project_name_line_has_a_null_name():
    rows = parse_listing(BUILDING)
    assert any(r["project_name"] is None for r in rows)
    assert sum(r["project_name"] is not None for r in rows) >= 8


def test_promoter_is_the_whole_name_and_address_cell():
    row = parse_listing(BUILDING)[0]
    assert row["promoter"] == "TNUHDB, TNUHDB Division 1, Thiruvottiyur, Chennai, Tamil Nadu-600019."


def test_links_are_recorded_and_none_is_followed():
    fields = parse_listing(BUILDING)[0]["fields"]
    assert fields["Promoter Details URL"].startswith("https://rera.tn.gov.in/public-view1/building/")
    assert fields["Project Details URL"].startswith("https://rera.tn.gov.in/public-view2/building/")
    assert fields["Form-c"].startswith("https://rera.tn.gov.in/formcqr/")
    assert fields["Current Status URL"] is None


def test_a_status_pdf_link_is_recorded_in_fields_only():
    rows = parse_listing(BUILDING)
    pdfs = [r["fields"]["Current Status URL"] for r in rows if r["fields"]["Current Status URL"]]
    assert pdfs and all(u.endswith(".pdf") for u in pdfs)


def test_registration_numbers_are_unique_and_the_row_keys_too():
    rows = parse_listing(BUILDING)
    assert len({r["key"] for r in rows}) == len(rows)


# --- typed permit numbers ---------------------------------------------------------


def test_cmda_and_gcc_numbers_are_typed_when_the_text_gives_them():
    row = _by_no(parse_listing(BUILDING), "TNRERA/29/BLG/0002/2026")
    assert row["cmda_permission_no"] == "OL-PP/NHRB/0235/2025"
    assert row["gcc_permission_no"] == "CEBA/WDCN10/00287/2025"


def test_a_cmda_number_in_an_older_shape_is_null_and_the_text_stays_in_fields():
    row = parse_listing(BUILDING)[0]
    assert row["cmda_permission_no"] is None
    assert row["gcc_permission_no"] == "CEBA/WDCN01/00539/2025"
    assert "C/27/2025 A to D" in row["fields"]["Approval Details"]


def test_a_non_chennai_approval_has_no_cmda_or_gcc_number():
    rows = parse_listing(BUILDING)
    row = _by_no(rows, "TNRERA/16/BLG/0003/2026")
    assert row["cmda_permission_no"] is None and row["gcc_permission_no"] is None
    assert "T&CP" in row["fields"]["Approval Details"]


def test_permit_numbers_fall_back_to_the_cmda_letter_number():
    text = (
        "The Assistant Planner , CMDA , Chennai has issued planning permission vide letter "
        "No. CMDA/PP/NHRB/S/0123/2025 dated 01.02.2025. The Executive Engineer, Greater "
        "Chennai Corporation issued Building permission in Letter No. CEBA/WDCN01/00539/2025."
    )
    assert permit_numbers(text) == ("CMDA/PP/NHRB/S/0123/2025", "CEBA/WDCN01/00539/2025")


def test_permit_numbers_read_through_spaces_typed_around_slashes():
    text = (
        "CMDA issued Planning permission No. OL-PP/ IND/ 0028/ 2024 in Letter CMDA/ PP/ IND/ S/ 0674/ "
        "2023. The Greater Chennai Corporation issued Building permission in Letter No. "
        "CEBA/WDCN 12/ 00460/ 2024."
    )
    assert permit_numbers(text)[0] == "OL-PP/IND/0028/2024"


def test_gcc_number_needs_the_corporation_to_be_named():
    assert permit_numbers("Letter No. CEBA/WDCN01/00539/2025 dated 22/12/2025")[1] is None
    assert permit_numbers(None) == (None, None)
    assert permit_numbers("") == (None, None)


# --- typed coordinates ---------------------------------------------------------------


def test_decimal_degree_coordinates_are_typed():
    row = parse_listing(BUILDING)[0]
    assert (row["latitude"], row["longitude"]) == (13.163286, 80.301217)


@pytest.mark.parametrize(
    "text",
    [
        "Latitude-130503.6; Longitude-801029.8;",     # degrees, minutes, seconds run together
        "Latitude-12.893177; Longitude-12.893177;",   # the same number twice
        "Latitude-13.0; Longitude-80.0;",             # fewer than three decimals
        "Latitude-40.76; Longitude-73.984;",          # outside Tamil Nadu
        "Latitude-13.163286; Longitude--80.301217;",  # a stray second dash
        "Latitude-13.163286; Longitude-801029.8;",    # one good value voids the pair
        'Latitude-10°22\'27.4"N; Longitude-77°54\'12.4"E;',
        "no coordinates here",
        None,
    ],
)
def test_a_coordinate_pair_that_is_not_decimal_degrees_in_tamil_nadu_is_null(text):
    assert parse_coordinates(text) == (None, None)


def test_a_row_with_untyped_coordinates_keeps_the_text_in_fields():
    rows = parse_listing(BUILDING) + parse_listing(BUILDING_2025) + parse_listing(LAYOUT)
    untyped = [r for r in rows if r["latitude"] is None]
    assert untyped
    assert all("Latitude" in r["fields"]["Other Details"] for r in untyped)
    assert all(r["longitude"] is None for r in untyped)


# --- other lists ------------------------------------------------------------------------


def test_the_2025_list_has_both_registration_number_shapes():
    rows = parse_listing(BUILDING_2025)
    assert rows[0]["registration_no"] == "TN/29/Building/0001/2025"
    assert rows[-1]["registration_no"].startswith("TNRERA/")
    assert rows[0]["approved_on"] == "2025-01-03"
    assert rows[0]["cmda_permission_no"] == "OL-PP/IND/0056/2024"


def test_the_layout_list_parses_and_names_its_own_url():
    rows = parse_listing(LAYOUT, kind="layout")
    assert len(rows) == 5
    assert rows[0]["listing_url"] == "https://rera.tn.gov.in/registered-layout/tn"
    assert rows[0]["registration_no"] == "TNRERA/11/LO/0001/2026"
    assert rows[0]["cmda_permission_no"] is None


# --- parser: failure modes -------------------------------------------------------------------


def test_a_missing_table_raises():
    with pytest.raises(ReraError, match='no <table id="example1">'):
        parse_listing("<html><body>Please wait</body></html>")


def test_a_truncated_table_raises():
    with pytest.raises(ReraError, match="never closed"):
        parse_listing(_table("TNRERA/1/BLG/0001/2026 dated 02-01-2026")[:-30])


def test_a_missing_header_raises():
    html = _table("TNRERA/1/BLG/0001/2026 dated 02-01-2026").replace("<th>Form-c</th>", "")
    with pytest.raises(ReraError, match="headers changed"):
        parse_listing(html)


def test_a_repeated_header_raises():
    html = _table("TNRERA/1/BLG/0001/2026 dated 02-01-2026", extra_header="<th>Form-c</th>")
    with pytest.raises(ReraError, match="Repeated: \\['Form-c'\\]"):
        parse_listing(html)


def test_an_extra_header_is_kept_in_fields():
    html = _table("TNRERA/1/BLG/0001/2026 dated 02-01-2026", extra_header="<th>Remarks</th>", width=10)
    html = html.replace("<td></td></tr>", "<td>hello</td></tr>")
    assert parse_listing(html)[0]["fields"]["Remarks"] == "hello"


def test_a_row_of_the_wrong_width_raises():
    with pytest.raises(ReraError, match="8 cells but the header has 9"):
        parse_listing(_table("TNRERA/1/BLG/0001/2026 dated 02-01-2026", width=8))


def test_a_registration_without_a_date_raises():
    with pytest.raises(ReraError, match="no 'number dated dd-mm-yyyy' registration"):
        parse_listing(_table("TNRERA/1/BLG/0001/2026"))


def test_a_repeated_registration_number_raises():
    reg = "TNRERA/1/BLG/0001/2026 dated 02-01-2026"
    with pytest.raises(ReraError, match="appears in rows"):
        parse_listing(_table(reg, reg))


# --- the year form -------------------------------------------------------------------------------


def test_parse_form_reads_token_years_and_default_year():
    assert parse_form(BUILDING) == ("FIXTURE_TOKEN", ["2026", "2025", "2024"], None)
    assert parse_form(BUILDING_2025) == ("FIXTURE_TOKEN", ["2026", "2025", "2024"], "2025")


def test_parse_form_has_no_token_when_the_input_is_missing():
    assert parse_form(_table("x"))[0] is None


# --- robots: the adapter refuses what make_session does not -----------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://rera.tn.gov.in/cms/Other_Details/Building/Approval_Details/2020/1-2020.pdf",
        "https://rera.tn.gov.in/public/storage/upload/abc.PDF",
        "https://rera.tn.gov.in/x/report%2Epdf",
        "https://rera.tn.gov.in/uploads/anything",
        "https://rera.tn.gov.in/uploads/a/b.html",
    ],
)
def test_disallowed_urls_are_refused(url):
    with pytest.raises(ReraError, match="robots.txt disallows"):
        refuse_disallowed_url(url)


@pytest.mark.parametrize("kind", ["building", "layout"])
def test_the_listing_urls_are_allowed(kind):
    refuse_disallowed_url(listing_url(kind))


class FakeSession:
    """Serves one body per method and records every request."""

    def __init__(self, get: bytes = b"", post: bytes = b"", status: int = 200, cookies: bool = True):
        self.get_body, self.post_body, self.status = get, post, status
        self.calls: list[tuple[str, str, dict]] = []
        if cookies:
            self.cookies = object()

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return StdlibResponse(url, self.status, self.get_body, {})

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return StdlibResponse(url, self.status, self.post_body, {})


def _probe(tmp_path, session, sleeps=None):
    sleeps = [] if sleeps is None else sleeps
    return TamilNaduReraProbe(tmp_path, session=session, sleep_fn=sleeps.append), sleeps


def test_a_pdf_url_is_refused_without_a_request(tmp_path):
    session = FakeSession()
    probe, _ = _probe(tmp_path, session)
    for url in (
        "https://rera.tn.gov.in/cms/Current_Status_Project/Building/2020/1-2020.pdf",
        "https://rera.tn.gov.in/public/storage/upload/abc.pdf",
        "https://rera.tn.gov.in/uploads/x",
    ):
        for method in ("GET", "POST"):
            with pytest.raises(ReraError, match="robots.txt disallows"):
                probe._request(method, url)
    assert session.calls == []


def test_a_run_requests_only_the_listing_path(tmp_path):
    session = FakeSession(BUILDING.encode("utf-8"))
    probe, _ = _probe(tmp_path, session)
    probe.run()
    assert [(m, u) for m, u, _ in session.calls] == [
        ("GET", "https://rera.tn.gov.in/registered-building/tn")
    ]


def test_no_row_link_is_ever_requested(tmp_path):
    session = FakeSession(BUILDING.encode("utf-8"), BUILDING_2025.encode("utf-8"))
    probe, _ = _probe(tmp_path, session)
    probe.run(year="2025")
    assert {u for _, u, _ in session.calls} == {"https://rera.tn.gov.in/registered-building/tn"}


# --- probe ---------------------------------------------------------------------------------------------


def test_run_gets_the_current_year_and_writes_rows_and_manifest(tmp_path):
    session = FakeSession(BUILDING.encode("utf-8"))
    probe, _ = _probe(tmp_path, session)
    (record,) = probe.run()

    assert record["status"] == "downloaded"
    assert record["method"] == "GET" and record["form"] == {}
    assert record["row_count"] == 10 and record["http_status"] == 200
    assert record["bytes"] == len(BUILDING.encode("utf-8"))
    assert record["path"].startswith("listings/IN-TN/building-2026-")
    assert (tmp_path / record["path"]).read_bytes() == BUILDING.encode("utf-8")
    rows = [json.loads(line) for line in (tmp_path / "rera_project_rows.jsonl").read_text().splitlines()]
    assert len(rows) == 10
    assert {r["source_sha256"] for r in rows} == {record["sha256"]}
    manifest = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert manifest == [record]


def test_the_current_year_asked_for_by_name_sends_no_post(tmp_path):
    session = FakeSession(BUILDING.encode("utf-8"))
    probe, _ = _probe(tmp_path, session)
    (record,) = probe.run(year="2026")
    assert [m for m, _, _ in session.calls] == ["GET"]
    assert record["status"] == "downloaded" and record["form"] == {}


def test_an_older_year_posts_the_token_from_the_get_page(tmp_path):
    session = FakeSession(BUILDING.encode("utf-8"), BUILDING_2025.encode("utf-8"))
    probe, sleeps = _probe(tmp_path, session)
    (record,) = probe.run(year="2025")

    assert [(m, u) for m, u, _ in session.calls] == [
        ("GET", "https://rera.tn.gov.in/registered-building/tn"),
        ("POST", "https://rera.tn.gov.in/registered-building/tn"),
    ]
    post = session.calls[1][2]
    assert post["data"] == "_token=FIXTURE_TOKEN&year=2025"
    assert post["headers"] == {"Content-Type": "application/x-www-form-urlencoded"}
    assert sleeps == [5.0]
    assert record["method"] == "POST" and record["form"] == {"year": "2025"}
    assert record["row_count"] == 5
    assert record["path"].startswith("listings/IN-TN/building-2025-")
    rows = [json.loads(line) for line in (tmp_path / "rera_project_rows.jsonl").read_text().splitlines()]
    assert rows[0]["registration_no"] == "TN/29/Building/0001/2025"


def test_a_token_missing_from_the_get_page_raises_instead_of_posting(tmp_path):
    no_token = BUILDING.replace('name="_token"', 'name="other"')
    session = FakeSession(no_token.encode("utf-8"), BUILDING_2025.encode("utf-8"))
    probe, _ = _probe(tmp_path, session)
    (record,) = probe.run(year="2025")

    assert record["status"] == "error"
    assert "no _token input" in record["error"]
    assert [m for m, _, _ in session.calls] == ["GET"]
    assert not (tmp_path / "rera_project_rows.jsonl").exists()


def test_a_year_the_form_does_not_offer_is_an_error_without_a_post(tmp_path):
    session = FakeSession(BUILDING.encode("utf-8"))
    probe, _ = _probe(tmp_path, session)
    (record,) = probe.run(year="2019")
    assert record["status"] == "error" and "not '2019'" in record["error"]
    assert [m for m, _, _ in session.calls] == ["GET"]


def test_a_session_without_cookies_cannot_post_the_token(tmp_path):
    session = FakeSession(BUILDING.encode("utf-8"), BUILDING_2025.encode("utf-8"), cookies=False)
    probe, _ = _probe(tmp_path, session)
    (record,) = probe.run(year="2025")
    assert record["status"] == "error" and "commoner-probe[http]" in record["error"]
    assert [m for m, _, _ in session.calls] == ["GET"]


def test_an_http_error_on_the_post_is_reported(tmp_path):
    class Rejecting(FakeSession):
        def post(self, url, **kwargs):
            self.calls.append(("POST", url, kwargs))
            return StdlibResponse(url, 419, b"Page Expired", {})

    probe, _ = _probe(tmp_path, Rejecting(BUILDING.encode("utf-8")))
    (record,) = probe.run(year="2025")
    assert record["status"] == "error" and record["http_status"] == 419
    assert "HTTP 419" in record["error"]


def test_an_http_error_writes_no_rows(tmp_path):
    probe, _ = _probe(tmp_path, FakeSession(b"nope", status=503))
    (record,) = probe.run()
    assert record["status"] == "error" and record["http_status"] == 503
    assert not (tmp_path / "rera_project_rows.jsonl").exists()


def test_a_response_without_the_table_is_an_error_and_writes_no_file(tmp_path):
    probe, _ = _probe(tmp_path, FakeSession(b"<html>Please wait</html>"))
    (record,) = probe.run()
    assert record["status"] == "error" and "example1" in record["error"]
    assert not (tmp_path / "listings").exists()


def test_a_second_kind_waits_before_its_request(tmp_path):
    session = FakeSession(BUILDING.encode("utf-8"))
    probe, sleeps = _probe(tmp_path, session)
    probe.run(["building", "layout"])
    assert sleeps == [5.0]
    assert [u.rsplit("/", 2)[1] for _, u, _ in session.calls] == [
        "registered-building", "registered-layout"
    ]


def test_dry_run_requests_and_writes_nothing(tmp_path):
    session = FakeSession()
    probe, _ = _probe(tmp_path, session)
    (record,) = probe.run(year="2025", dry_run=True)
    assert record["status"] == "dry_run" and record["form"] == {"year": "2025"}
    assert session.calls == [] and list(tmp_path.iterdir()) == []


def test_a_rerun_replaces_rows_by_key(tmp_path):
    probe, _ = _probe(tmp_path, FakeSession(BUILDING.encode("utf-8")))
    probe.run()
    probe.run()
    assert len((tmp_path / "rera_project_rows.jsonl").read_text().splitlines()) == 10


def test_rows_pass_the_validator_and_load_as_typed_records(tmp_path):
    from commoner_probe.validate import validate_corpus

    probe, _ = _probe(tmp_path, FakeSession(BUILDING.encode("utf-8")))
    probe.run()
    assert validate_corpus(tmp_path, log=lambda _m: None)
    rows = list(Corpus(tmp_path).rera_project_rows())
    assert len(rows) == 10
    assert rows[1].cmda_permission_no == "OL-PP/NHRB/0235/2025"
    assert rows[1].gcc_permission_no == "CEBA/WDCN10/00287/2025"
    assert isinstance(rows[0].latitude, float)


# --- CLI ----------------------------------------------------------------------------------------------------------


def test_tamil_nadu_is_a_registered_state():
    assert STATES["TN"] is TamilNaduReraProbe


def test_resolve_kind_ignores_case_and_rejects_unknown():
    assert resolve_kind(" Layout ") == "layout"
    with pytest.raises(ReraError, match="building, layout"):
        resolve_kind("plot")


def test_cli_defaults_to_the_building_list_in_a_dry_run(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["commoner-probe", "rera", "--state", "TN", "--dry-run"])
    main()
    record = json.loads(capsys.readouterr().out)
    assert record["url"].endswith("/registered-building/tn")
    assert record["state"] == "IN-TN" and record["method"] == "GET"


def test_cli_accepts_both_kinds_and_a_year(capsys, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["commoner-probe", "rera", "--state", "in-tn", "--kind", "Layout", "--kind", "building",
         "--year", "2025", "--dry-run"],
    )
    main()
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [r["url"].rsplit("/", 2)[1] for r in records] == ["registered-layout", "registered-building"]
    assert all(r["form"] == {"year": "2025"} for r in records)


def test_cli_rejects_an_unknown_kind(monkeypatch):
    monkeypatch.setattr(
        "sys.argv", ["commoner-probe", "rera", "--state", "TN", "--kind", "plot", "--dry-run"]
    )
    with pytest.raises(SystemExit) as raised:
        main()
    assert "building, layout" in str(raised.value)


@pytest.mark.parametrize("year", ["25", "twenty", "2025a"])
def test_cli_rejects_a_year_that_is_not_four_digits(monkeypatch, year):
    monkeypatch.setattr(
        "sys.argv", ["commoner-probe", "rera", "--state", "TN", "--year", year, "--dry-run"]
    )
    with pytest.raises(SystemExit) as raised:
        main()
    assert "four-digit year" in str(raised.value)


@pytest.mark.parametrize(
    "flag",
    [["--district", "Kodagu"], ["--all-districts"], ["--list-districts"], ["--authority", "gurugram"]],
)
def test_cli_rejects_other_states_flags_for_tamil_nadu(monkeypatch, flag):
    monkeypatch.setattr("sys.argv", ["commoner-probe", "rera", "--state", "TN", "--dry-run", *flag])
    with pytest.raises(SystemExit) as raised:
        main()
    assert "--kind" in str(raised.value)


@pytest.mark.parametrize("state", ["KA", "HR"])
@pytest.mark.parametrize("flag", [["--kind", "layout"], ["--year", "2025"]])
def test_cli_rejects_tamil_nadu_flags_for_other_states(monkeypatch, state, flag):
    monkeypatch.setattr("sys.argv", ["commoner-probe", "rera", "--state", state, "--dry-run", *flag])
    with pytest.raises(SystemExit) as raised:
        main()
    assert "--state TN" in str(raised.value)


def test_cli_runs_a_listing_through_the_fake_session(tmp_path, capsys, monkeypatch):
    session = FakeSession(BUILDING.encode("utf-8"))
    monkeypatch.setattr(TamilNaduReraProbe, "session", property(lambda self: session))
    monkeypatch.setattr("sys.argv", ["commoner-probe", "rera", "--state", "TN", "--out", str(tmp_path)])
    main()
    assert json.loads(capsys.readouterr().out)["row_count"] == 10


def test_cli_exits_non_zero_when_a_request_fails(tmp_path, capsys, monkeypatch):
    session = FakeSession(b"nope", status=500)
    monkeypatch.setattr(TamilNaduReraProbe, "session", property(lambda self: session))
    monkeypatch.setattr("sys.argv", ["commoner-probe", "rera", "--state", "TN", "--out", str(tmp_path)])
    with pytest.raises(SystemExit) as raised:
        main()
    assert raised.value.code == 1
    assert "HTTP 500" in capsys.readouterr().err
