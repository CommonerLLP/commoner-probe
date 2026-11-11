"""Karnataka RERA parser, probe, and `rera` subcommand.

Parser tests read the trimmed capture in `tests/fixtures/rera_karnataka/`
(20 of the 4,460 Bengaluru Urban rows, November 11, 2026). The probe tests use
a fake session. No test touches the network.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from commoner_probe.cli import build_parser, main
from commoner_probe.corpus import Corpus
from commoner_probe.http_client import StdlibResponse
from commoner_probe.rera import STATES
from commoner_probe.rera.common import ReraError, iso_date, upsert_jsonl
from commoner_probe.rera.karnataka import (
    HEADERS,
    LISTING_URL,
    KarnatakaReraProbe,
    listing_form,
    parse_districts,
    parse_listing,
    resolve_district,
)

FIXTURE = Path(__file__).parent / "fixtures" / "rera_karnataka" / "project_view_details_trimmed.html"
HTML = FIXTURE.read_text(encoding="utf-8")


def _row(rows, ack):
    return next(r for r in rows if r["key"] == f"RERA|IN-KA|{ack}")


def _by_sno(rows, sno):
    return next(r for r in rows if r["fields"]["S.No"] == str(sno))


# --- parser ------------------------------------------------------------------


def test_parses_every_data_row_of_the_fixture():
    rows = parse_listing(HTML)
    assert len(rows) == 20
    assert len({r["key"] for r in rows}) == 20
    assert all(set(HEADERS) <= set(r["fields"]) for r in rows)


def test_first_row_has_typed_dates_and_text_fields():
    row = _by_sno(parse_listing(HTML), 1)
    assert row["key"] == "RERA|IN-KA|PR/KN/170729/000003"
    assert row["registration_no"] == "PRM/KA/RERA/1251/446/PR/171010/000003"
    assert row["promoter"] == "United Oxygen Company Private Limited"
    assert row["project_name"] == "Godrej United"
    assert row["district"] == "Bengaluru Urban"
    # APPROVED ON and PROPOSED COMPLETION DATE are dd/mm/yyyy.
    assert row["approved_on"] == "2017-10-10"
    assert row["proposed_completion_date_current"] == "2022-02-28"
    # The registration-time date and the extension ranges are dd-mm-yyyy.
    assert row["proposed_completion_date_at_registration"] == "2021-05-31"
    assert row["covid_extension_start"] == "2021-05-31"
    assert row["covid_extension_end"] == "2022-02-28"
    assert row["fields"]["APPROVED ON"] == "10/10/2017"
    assert row["fields"]["COVID-19 EXTENSION DATE"] == "31-05-2021 - 28-02-2022"


def test_empty_extension_columns_are_null():
    row = _by_sno(parse_listing(HTML), 2)
    for name in ("covid", "section6", "further"):
        assert row[f"{name}_extension_start"] is None
        assert row[f"{name}_extension_end"] is None
    assert row["fields"]["COVID-19 EXTENSION DATE"] is None


def test_a_start_with_no_end_keeps_the_start_and_nulls_the_end():
    rows = parse_listing(HTML)
    row = _by_sno(rows, 337)
    assert row["fields"]["COVID-19 EXTENSION DATE"].endswith(" -")
    assert row["covid_extension_start"] is not None
    assert row["covid_extension_end"] is None
    assert sum(1 for r in rows if r["covid_extension_start"] and not r["covid_extension_end"]) == 4


def test_rows_without_a_registration_or_approval_date_stay_rows():
    row = _by_sno(parse_listing(HTML), 2759)
    assert row["registration_no"] is None
    assert row["approved_on"] is None
    assert row["project_name"] is None
    assert row["key"].startswith("RERA|IN-KA|ACK/KA/RERA/")


def test_status_keeps_the_status_and_the_order_text_apart():
    rows = parse_listing(HTML)
    rejected = _by_sno(rows, 3)
    assert rejected["fields"]["STATUS"] == "REJECTED"
    assert "rejected" in rejected["fields"]["STATUS ORDER TEXT"]
    approved = _by_sno(rows, 1)
    assert approved["fields"]["STATUS"] == "APPROVED"
    assert approved["fields"]["STATUS ORDER TEXT"] is None


def test_link_columns_hold_the_link_not_text():
    rows = parse_listing(HTML)
    first = _by_sno(rows, 1)["fields"]
    assert first["View Project Details"] == "6"
    assert first["Certificate"].startswith("/certificate?CER_NO=")
    assert first["Covid Certificate"].startswith("/covidcertificate?CER_NO=")
    assert first["COMPLAINTS / LITIGATION"] is not None
    assert _by_sno(rows, 3)["fields"]["COMPLAINTS / LITIGATION"] is None
    assert _by_sno(rows, 3)["fields"]["Certificate"] is None


def test_rows_validate_against_the_row_schema():
    jsonschema = pytest.importorskip("jsonschema")
    from commoner_probe import schemas as sc

    validator = jsonschema.Draft202012Validator(sc.load("rera_project_row"))
    for row in parse_listing(HTML):
        assert list(validator.iter_errors(row)) == []


def test_a_missing_header_raises_instead_of_mislabelling():
    broken = HTML.replace("<th>COVID-19 EXTENSION DATE</th>", "<th>COVID EXTENSION</th>")
    with pytest.raises(ReraError, match="COVID-19 EXTENSION DATE"):
        parse_listing(broken)


def test_headers_are_anchored_by_name_not_position():
    # Add a column in front of every row. Position-based parsing would shift
    # every value one column left; name-based parsing doesn't notice.
    shifted = HTML.replace("<th>S.No</th>", "<th>NEW</th><th>S.No</th>")
    shifted = re.sub(r"(<tr>\s*)(<td>)", r"\1<td>x</td>\2", shifted)
    rows = parse_listing(shifted)
    assert len(rows) == 20
    assert _by_sno(rows, 1)["approved_on"] == "2017-10-10"
    assert _by_sno(rows, 1)["fields"]["NEW"] == "x"


def test_a_row_of_the_wrong_width_raises():
    short = re.sub(r"<td>\s*</td>\s*</tr>", "</tr>", HTML, count=1)
    with pytest.raises(ReraError, match="cells"):
        parse_listing(short)


def test_a_page_without_the_table_raises():
    with pytest.raises(ReraError, match="approvedTable"):
        parse_listing("<html><body><h1>Error Page</h1>Requested resource is not available</body></html>")


def test_a_header_commented_out_in_the_source_is_not_a_column():
    assert "<!--  <th>APPLIED ON</th> -->" in HTML
    assert "APPLIED ON" not in parse_listing(HTML)[0]["fields"]


def test_whitespace_runs_of_plain_spaces_are_kept():
    html = HTML.replace("<td>Bengaluru Urban</td>", "<td>Bengaluru  Rural</td>", 1)
    assert parse_listing(html)[0]["district"] == "Bengaluru  Rural"


@pytest.mark.parametrize(
    ("text", "sep", "expected"),
    [
        ("10/10/2017", "/", "2017-10-10"),
        ("31-05-2021", "-", "2021-05-31"),
        ("31-05-2021", "/", None),
        ("31/02/2021", "/", None),
        ("", "/", None),
        (None, "/", None),
    ],
)
def test_iso_date(text, sep, expected):
    assert iso_date(text, sep) == expected


def test_parse_districts_keeps_the_value_attribute_exactly():
    form = (
        "<select class='x' name='district' id=\"d\">"
        "<option value='0'> --Select District-- </option>"
        "<option value='Bengaluru  Rural'> Bengaluru  Rural </option>"
        "<option value='Bengaluru Urban'> Bengaluru Urban </option>"
        "</select><select name=\"subdistrict\"><option value='0'>x</option></select>"
    )
    assert parse_districts(form) == ["Bengaluru  Rural", "Bengaluru Urban"]


def test_resolve_district_matches_despite_case_and_spacing():
    assert resolve_district("bengaluru rural", ["Bengaluru  Rural"]) == "Bengaluru  Rural"
    assert resolve_district("Atlantis", ["Bengaluru  Rural"]) is None


# --- probe -------------------------------------------------------------------


class FakeSession:
    def __init__(self, body: bytes = b"", status: int = 200, form_html: str = ""):
        self.body = body
        self.status = status
        self.form_html = form_html
        self.calls: list[tuple[str, str, dict]] = []

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return StdlibResponse(url, self.status, self.body, {})

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return StdlibResponse(url, 200, self.form_html.encode("utf-8"), {})


def _probe(tmp_path, session, sleeps=None):
    sleeps = [] if sleeps is None else sleeps
    return KarnatakaReraProbe(tmp_path, session=session, sleep_fn=sleeps.append), sleeps


def test_run_posts_the_verified_form_and_writes_rows_and_manifest(tmp_path):
    session = FakeSession(HTML.encode("utf-8"))
    probe, _ = _probe(tmp_path, session)
    records = probe.run()

    assert [c[0] for c in session.calls] == ["POST"], "the default run needs no GET"
    _, url, kwargs = session.calls[0]
    assert url == LISTING_URL
    sent = parse_qs(kwargs["data"], keep_blank_values=True)
    assert sent == {
        "project": [""], "firm": [""], "appNo": [""], "regNo": [""],
        "district": ["Bengaluru Urban"], "subdistrict": ["0"], "btn1": ["Search"],
    }
    assert set(kwargs.get("headers", {})) <= {"Content-Type"}, "no Origin, Referer, or cookie"

    (record,) = records
    assert record["status"] == "downloaded"
    assert record["row_count"] == 20
    assert record["http_status"] == 200
    assert record["bytes"] == len(HTML.encode("utf-8"))
    saved = tmp_path / record["path"]
    assert saved.read_bytes() == HTML.encode("utf-8")
    assert record["path"].startswith("listings/IN-KA/bengaluru-urban-")

    corpus = Corpus(tmp_path)
    rows = list(corpus.rera_project_rows())
    assert len(rows) == 20
    assert {r.source_sha256 for r in rows} == {record["sha256"]}
    (listing,) = list(corpus.manifest_rera_listings())
    assert listing.row_count == 20


def test_run_output_passes_validate(tmp_path):
    pytest.importorskip("jsonschema")
    from commoner_probe.validate import validate_corpus

    probe, _ = _probe(tmp_path, FakeSession(HTML.encode("utf-8")))
    probe.run()
    assert validate_corpus(tmp_path) is True


def test_a_rerun_replaces_rows_instead_of_duplicating(tmp_path):
    probe, _ = _probe(tmp_path, FakeSession(HTML.encode("utf-8")))
    probe.run()
    probe.run()
    lines = (tmp_path / "rera_project_rows.jsonl").read_text().splitlines()
    assert len(lines) == 20
    assert len((tmp_path / "manifest.jsonl").read_text().splitlines()) == 1


def test_an_error_page_gives_an_error_record_and_no_rows(tmp_path):
    page = b"<html><body><h1>Error Page</h1>Requested resource is not available</body></html>"
    probe, _ = _probe(tmp_path, FakeSession(page))
    (record,) = probe.run()
    assert record["status"] == "error"
    assert "approvedTable" in record["error"]
    assert record["path"] is None and record["row_count"] is None
    assert not (tmp_path / "rera_project_rows.jsonl").exists()
    assert not (tmp_path / "listings").exists()
    (stored,) = [json.loads(x) for x in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert stored["status"] == "error"


def test_a_changed_header_gives_an_error_record_and_no_rows(tmp_path):
    broken = HTML.replace("<th>STATUS</th>", "<th>STATE</th>").encode("utf-8")
    probe, _ = _probe(tmp_path, FakeSession(broken))
    (record,) = probe.run()
    assert record["status"] == "error"
    assert "STATUS" in record["error"]
    assert not (tmp_path / "rera_project_rows.jsonl").exists()


def test_an_http_error_gives_an_error_record(tmp_path):
    probe, _ = _probe(tmp_path, FakeSession(b"Requested resource is not available", status=405))
    (record,) = probe.run()
    assert record["status"] == "error"
    assert record["http_status"] == 405
    assert not (tmp_path / "rera_project_rows.jsonl").exists()


def test_the_error_record_validates_against_the_manifest_schema(tmp_path):
    pytest.importorskip("jsonschema")
    from commoner_probe.validate import validate_corpus

    probe, _ = _probe(tmp_path, FakeSession(b"nope", status=500))
    probe.run()
    assert validate_corpus(tmp_path) is True


def test_dry_run_writes_nothing_and_makes_no_request(tmp_path):
    out = tmp_path / "out"
    session = FakeSession(HTML.encode("utf-8"))
    probe, _ = _probe(out, session)
    (record,) = probe.run(["Bengaluru  Rural"], dry_run=True)
    assert session.calls == []
    assert not out.exists()
    assert record["status"] == "dry_run"
    assert record["form"]["district"] == "Bengaluru  Rural"


def test_districts_are_fetched_in_turn_with_a_pause_between(tmp_path):
    probe, sleeps = _probe(tmp_path, FakeSession(HTML.encode("utf-8")))
    records = probe.run(["Kodagu", "Yadgir", "Gadag"])
    assert len(records) == 3
    assert sleeps == [5.0, 5.0]


def test_resolving_a_known_district_needs_no_request(tmp_path):
    session = FakeSession()
    probe, _ = _probe(tmp_path, session)
    assert probe.resolve_districts(["bengaluru rural"]) == ["Bengaluru  Rural"]
    assert session.calls == []


def test_an_unknown_district_reads_the_live_list_then_raises(tmp_path):
    form = "<select name='district'><option value='0'>-</option><option value='Newton'>Newton</option></select>"
    session = FakeSession(form_html=form)
    probe, _ = _probe(tmp_path, session)
    assert probe.resolve_districts(["newton"]) == ["Newton"]
    with pytest.raises(ReraError, match="not a Karnataka district"):
        probe.resolve_districts(["Atlantis"])
    assert {c[1] for c in session.calls} == {"https://rera.karnataka.gov.in/viewAllProjects"}


def test_the_first_post_waits_after_reading_the_district_list(tmp_path):
    form = "<select name='district'><option value='0'>-</option><option value='Kodagu'>K</option></select>"
    probe, sleeps = _probe(tmp_path, FakeSession(HTML.encode("utf-8"), form_html=form))
    assert probe.list_districts() == ["Kodagu"]
    probe.run(["Kodagu"])
    assert sleeps == [5.0]


def test_listing_form_matches_the_portal_form():
    assert listing_form("Kodagu") == {
        "project": "", "firm": "", "appNo": "", "regNo": "",
        "district": "Kodagu", "subdistrict": "0", "btn1": "Search",
    }


def test_upsert_keeps_position_and_replaces_by_key(tmp_path):
    path = tmp_path / "x.jsonl"
    upsert_jsonl(path, [{"key": "a", "v": 1}, {"key": "b", "v": 1}])
    upsert_jsonl(path, [{"key": "a", "v": 2}, {"key": "c", "v": 1}])
    assert [json.loads(x) for x in path.read_text().splitlines()] == [
        {"key": "a", "v": 2}, {"key": "b", "v": 1}, {"key": "c", "v": 1},
    ]


# --- CLI ---------------------------------------------------------------------


def test_karnataka_is_a_registered_state():
    assert STATES["KA"] is KarnatakaReraProbe


def test_cli_defaults_to_bengaluru_urban_in_a_dry_run(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["commoner-probe", "rera", "--state", "KA", "--dry-run"])
    main()
    record = json.loads(capsys.readouterr().out)
    assert record["form"]["district"] == "Bengaluru Urban"
    assert record["status"] == "dry_run"


def test_cli_accepts_the_iso_state_code_and_resolves_the_two_space_district(capsys, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["commoner-probe", "rera", "--state", "in-ka", "--district", "bengaluru rural", "--dry-run"],
    )
    main()
    assert json.loads(capsys.readouterr().out)["form"]["district"] == "Bengaluru  Rural"


def test_cli_rejects_an_unregistered_state_and_lists_the_registered_ones(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["commoner-probe", "rera", "--state", "ZZ", "--dry-run"])
    with pytest.raises(SystemExit) as raised:
        main()
    assert raised.value.code not in (0, None)
    assert "KA" in str(raised.value)


def test_cli_needs_an_out_directory_to_write(monkeypatch):
    monkeypatch.setattr("sys.argv", ["commoner-probe", "rera", "--state", "KA"])
    with pytest.raises(SystemExit) as raised:
        main()
    assert "--out" in str(raised.value)


def test_cli_refuses_a_sleep_below_the_minimum(monkeypatch):
    monkeypatch.setattr(
        "sys.argv", ["commoner-probe", "rera", "--state", "KA", "--out", "x", "--sleep", "1"]
    )
    with pytest.raises(SystemExit) as raised:
        main()
    assert "at least 5" in str(raised.value)


def test_cli_exits_non_zero_when_a_request_fails(tmp_path, capsys, monkeypatch):
    session = FakeSession(b"nope", status=500)
    monkeypatch.setattr(
        KarnatakaReraProbe, "session", property(lambda self: session)
    )
    monkeypatch.setattr("sys.argv", ["commoner-probe", "rera", "--state", "KA", "--out", str(tmp_path)])
    with pytest.raises(SystemExit) as raised:
        main()
    assert raised.value.code == 1
    assert "HTTP 500" in capsys.readouterr().err


def test_cli_lists_districts(capsys, monkeypatch):
    form = "<select name='district'><option value='0'>-</option><option value='Kodagu'>K</option></select>"
    session = FakeSession(form_html=form)
    monkeypatch.setattr(KarnatakaReraProbe, "session", property(lambda self: session))
    monkeypatch.setattr("sys.argv", ["commoner-probe", "rera", "--state", "KA", "--list-districts"])
    main()
    assert capsys.readouterr().out == "Kodagu\n"


def test_rera_is_a_subcommand():
    assert "rera" in build_parser()._subparsers._group_actions[0].choices
