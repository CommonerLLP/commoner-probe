"""Haryana RERA parser, probe, and `rera --state HR` subcommand.

Parser tests read the trimmed captures in `tests/fixtures/rera_haryana/` (12 of
the 1,086 Gurugram rows and 5 of the 1,132 Panchkula rows, November 11, 2026).
The probe tests use a fake session. No test touches the network.
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
from commoner_probe.rera.haryana import (
    AUTHORITIES,
    HEADERS,
    HaryanaReraProbe,
    listing_url,
    parse_listing,
    resolve_authority,
)

FIXTURES = Path(__file__).parent / "fixtures" / "rera_haryana"
GURUGRAM = (FIXTURES / "registered_projects_2_trimmed.html").read_text(encoding="utf-8")
PANCHKULA = (FIXTURES / "registered_projects_1_trimmed.html").read_text(encoding="utf-8")


def _by_no(rows, registration_no):
    return next(r for r in rows if r["registration_no"] == registration_no)


def _table(*certificates: str, extra_header: str = "", width: int | None = None) -> str:
    """A minimal listing: one row per certificate cell, every other cell empty."""
    head = "".join(f"<th>{h}</th>" for h in HEADERS) + extra_header
    rows = ""
    for i, cert in enumerate(certificates, start=1):
        cells = [str(i), cert] + ["x"] * (len(HEADERS) - 2)
        cells = cells[:width] if width else cells
        rows += "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
    return (
        '<table id="compliant_hearing"><thead><tr>' + head + "</tr></thead>"
        "<tbody>" + rows + "</tbody></table>"
    )


# --- parser ------------------------------------------------------------------


def test_parses_every_data_row_of_both_fixtures():
    gurugram, panchkula = parse_listing(GURUGRAM), parse_listing(PANCHKULA)
    assert (len(gurugram), len(panchkula)) == (12, 5)
    for rows in (gurugram, panchkula):
        assert len({r["key"] for r in rows}) == len(rows)
        assert all(set(HEADERS) <= set(r["fields"]) for r in rows)


def test_first_row_has_typed_fields_and_text_fields():
    row = _by_no(parse_listing(GURUGRAM), "GGM/415/147/2020/31")
    assert row["key"] == "RERA|IN-HR|GGM/415/147/2020/31"
    assert row["source"] == "rera-haryana"
    assert row["state"] == "IN-HR"
    assert row["promoter"] == "COUNTRYWIDE PROMOTERS PRIVATE LIMITED"
    assert row["project_name"] == "102 EDEN ESTATE"
    assert row["district"] == "GURUGRAM"
    assert row["approved_on"] == "2020-10-09"
    assert row["registration_valid_until"] == "2024-04-30"
    assert row["lapsed"] is False
    assert row["listing_url"] == "https://haryanarera.gov.in/admincontrol/registered_projects/2"
    fields = row["fields"]
    assert fields["Registration Certificate Number"] == "GGM/415/147/2020/31 DATED 09.10.2020"
    assert fields["Project ID"] == "RERA-GRG-741-2020"
    assert fields["Registration Up-to"] == "30/04/2024"
    assert fields["Registered With"] == "HRERA"
    assert fields["Project Location"] == "SECTOR 102 & 102 A GURUGRAM"


def test_a_lapsed_project_carries_lapsed_true_and_keeps_the_portal_text():
    rows = parse_listing(GURUGRAM)
    row = _by_no(rows, "GGM/486/218/2021/54")
    assert row["lapsed"] is True
    assert row["registration_no"] == "GGM/486/218/2021/54"
    assert row["approved_on"] == "2021-09-21"
    assert row["fields"]["Registration Certificate Number"].endswith("Lapsed Project")
    assert sum(r["lapsed"] for r in rows) == 5
    assert all(isinstance(r["lapsed"], bool) for r in rows)


@pytest.mark.parametrize(
    ("cell", "number", "approved_on", "lapsed"),
    [
        ("GGM/415/147/2020/31 DATED 09.10.2020", "GGM/415/147/2020/31", "2020-10-09", False),
        ("GGM/359/91/2019/53 DATED-30.09.2019", "GGM/359/91/2019/53", "2019-09-30", False),
        ("57(A) OF 2017 DATED 17.8.2017", "57(A) OF 2017", "2017-08-17", False),
        ("GGM/441/173/2021/08(A) DATED 22.02.2021", "GGM/441/173/2021/08(A)", "2021-02-22", False),
        ("341 OF 2017  DATED 27.10.2017", "341 OF 2017", "2017-10-27", False),
        ("38 OF 2017 DATED 10.08.2017<!-- <span><br><img src=x></span> -->", "38 OF 2017",
         "2017-08-10", False),
        (
            "GGM/832/564/2024/59 DATED 03.06.2024 (SUPERSEDES GGM/661/393/2023/05 "
            "DATED 12.01.2023) <a><span><br>Lapsed Project</span></a>",
            "GGM/832/564/2024/59", "2024-06-03", True,
        ),
        ("GGM/543/275/2022/18 DATED 14.03.2022(SUPERSEDED BY GGM/692 DATED 02.02.2023)",
         "GGM/543/275/2022/18", "2022-03-14", False),
        ("384 OF 2017 (SUPERSEDED BY 06 OF 2018)", "384 OF 2017", None, False),
        ("HRERA-PKL-AMB-812-2025", "HRERA-PKL-AMB-812-2025", None, False),
        ("HRERA-PKL-AMB-67-2018<a><span><br>Lapsed Project</span></a>",
         "HRERA-PKL-AMB-67-2018", None, True),
        ("15 OF 2018 DATED 31.02.2018", "15 OF 2018", None, False),
    ],
)
def test_the_certificate_cell_splits_into_number_date_and_lapsed(cell, number, approved_on, lapsed):
    (row,) = parse_listing(_table(cell))
    assert row["registration_no"] == number
    assert row["key"] == f"RERA|IN-HR|{number}"
    assert row["approved_on"] == approved_on
    assert row["lapsed"] is lapsed


def test_the_epoch_placeholder_is_not_a_registration_date():
    rows = parse_listing(GURUGRAM)
    row = _by_no(rows, "384 OF 2017")
    assert row["fields"]["Registration Up-to"] == "01/01/1970"
    assert row["registration_valid_until"] is None
    assert row["approved_on"] is None


def test_a_superseded_certificate_keeps_its_own_date():
    row = _by_no(parse_listing(GURUGRAM), "GGM/602/334/2022/77")
    assert row["approved_on"] == "2022-08-17"
    assert "SUPERSEDED BY GGM/790/522/2024/17" in row["fields"]["Registration Certificate Number"]


def test_a_commented_out_image_is_not_part_of_the_cell():
    row = _by_no(parse_listing(GURUGRAM), "38 OF 2017")
    assert row["fields"]["Registration Certificate Number"] == "38 OF 2017 DATED 10.08.2017"


def test_project_id_is_na_for_some_rows_so_the_key_does_not_use_it():
    row = _by_no(parse_listing(GURUGRAM), "119 OF 2017")
    assert row["fields"]["Project ID"] == "NA"
    assert row["fields"]["Project ID URL"] is None
    assert row["key"] == "RERA|IN-HR|119 OF 2017"


def test_the_quarterly_progress_link_is_recorded_from_the_popup_call():
    row = _by_no(parse_listing(GURUGRAM), "GGM/415/147/2020/31")
    assert row["fields"]["View Quarterly Progress"] == (
        "https://haryanarera.gov.in/assistancecontrol/print_quarterly_schedules/"
        "Mg==/NzQx/MjAyMA=="
    )
    assert row["fields"]["Monitoring Orders"].startswith(
        "https://haryanarera.gov.in/assistancecontrol/view_monitoring_cases/"
    )


def test_a_link_column_without_a_link_keeps_the_cell_text():
    rows = parse_listing(GURUGRAM)
    assert _by_no(rows, "15 OF 2018")["fields"]["View Quarterly Progress"] == "QPR not Uploaded"
    assert _by_no(rows, "GGM/1078/810/2026/50")["fields"]["View Quarterly Progress"] == "NA"
    assert _by_no(rows, "15 OF 2018")["fields"]["Monitoring Orders"] is not None
    assert _by_no(rows, "GGM/940/672/2025/43")["fields"]["Monitoring Orders"] == "--"
    assert _by_no(rows, "119 OF 2017")["fields"]["Details of Project(Form A-H)"] == "N/A"


def test_link_columns_hold_the_link_not_the_label():
    fields = _by_no(parse_listing(GURUGRAM), "GGM/415/147/2020/31")["fields"]
    assert fields["Details of Project(Form A-H)"] == (
        "https://haryanarera.gov.in/view_project/project_preview_open/1444"
    )
    assert fields["View Certificate"] == (
        "https://haryanarera.gov.in/view_project/view_certificate/ODQ0"
    )
    assert fields["Project ID URL"] == (
        "https://haryanarera.gov.in/view_project/searchprojectDetail/1444"
    )


def test_corrigendum_ids_come_from_the_page_script_calls():
    fields = _by_no(parse_listing(GURUGRAM), "GGM/415/147/2020/31")["fields"]
    assert fields["Corrigendum ID"] == "808"
    assert fields["OC/CC/PCC ID"] == "808"
    assert fields["View OC/CC/PCC"] == "View OC/CC/PCC"
    none = _by_no(parse_listing(GURUGRAM), "GGM/940/672/2025/43")["fields"]
    assert none["Corrigendum ID"] is None
    assert none["View OC/CC/PCC"] is None


def test_a_location_wrapped_mid_word_is_rejoined():
    row = _by_no(parse_listing(PANCHKULA), "211 OF 2017")
    assert row["fields"]["Project Location"] == "KANWALA. SECTOR 27, AMBALA, HARYANA"


def test_runs_of_plain_spaces_stay_as_published():
    row = _by_no(parse_listing(PANCHKULA), "211 OF 2017")
    assert row["project_name"] == "AFFORDABLE   PLOTTED  COLONY  ON 11.85 ACRES"


def test_the_panchkula_listing_covers_other_districts_and_undated_certificates():
    rows = parse_listing(PANCHKULA, url=listing_url("panchkula"))
    assert {r["fields"]["Project District"] for r in rows} >= {"AMBALA", "FARIDABAD"}
    undated = _by_no(rows, "HRERA-PKL-AMB-943-2026")
    assert undated["approved_on"] is None
    assert undated["registration_valid_until"] == "2030-09-17"
    assert undated["listing_url"].endswith("/registered_projects/1")
    assert undated["fields"]["View Quarterly Progress"] == "NA"


def test_rows_validate_against_the_row_schema():
    pytest.importorskip("jsonschema")
    from jsonschema import Draft202012Validator

    from commoner_probe import schemas as sc

    validator = Draft202012Validator(sc.load("rera_project_row"))
    for html in (GURUGRAM, PANCHKULA):
        for row in parse_listing(html):
            assert list(validator.iter_errors(row)) == [], row["key"]


def test_a_missing_header_raises_instead_of_mislabelling():
    html = GURUGRAM.replace(">Builder</th>", ">Developer</th>")
    with pytest.raises(ReraError, match="Builder"):
        parse_listing(html)


def test_headers_are_anchored_by_name_not_position():
    swapped = (
        GURUGRAM.replace(">Builder</th>", ">__tmp__</th>")
        .replace(">Project Name</th>", ">Builder</th>")
        .replace(">__tmp__</th>", ">Project Name</th>")
    )
    row = _by_no(parse_listing(swapped), "GGM/415/147/2020/31")
    assert row["promoter"] == "102 EDEN ESTATE"


def test_a_repeated_header_raises():
    with pytest.raises(ReraError, match="Repeated"):
        parse_listing(GURUGRAM.replace("<th>Project ID</th>", "<th>Project ID</th><th>Project ID</th>", 1))


def test_a_row_of_the_wrong_width_raises():
    with pytest.raises(ReraError, match="cells"):
        parse_listing(_table("A", width=10))


def test_a_page_without_the_table_raises():
    with pytest.raises(ReraError, match="compliant_hearing"):
        parse_listing("<html><body>Error</body></html>")


def test_a_truncated_table_raises():
    with pytest.raises(ReraError, match="truncated"):
        parse_listing(_table("A").replace("</table>", ""))


def test_a_row_without_a_certificate_number_raises():
    with pytest.raises(ReraError, match="no registration certificate number"):
        parse_listing(_table(""))


def test_a_repeated_certificate_number_raises_instead_of_dropping_a_row():
    with pytest.raises(ReraError, match="appears in rows"):
        parse_listing(_table("12 OF 2017 DATED 01.01.2018", "12 OF 2017"))


def test_a_header_commented_out_in_the_source_is_not_a_column():
    assert "<!--                <th align=\"center\">View Proceedings</th>" in GURUGRAM
    assert all("View Proceedings" not in r["fields"] for r in parse_listing(GURUGRAM))


def test_a_header_beyond_the_expected_ones_stays_in_fields():
    (row,) = parse_listing(_table("12 OF 2017", extra_header="<th>New column</th>").replace(
        "</tr></tbody>", "<td>new</td></tr></tbody>"
    ))
    assert row["fields"]["New column"] == "new"


def test_resolve_authority_ignores_case_and_rejects_unknown_names():
    assert resolve_authority(" Gurugram ") == "gurugram"
    assert resolve_authority("PANCHKULA") == "panchkula"
    with pytest.raises(ReraError, match="gurugram, panchkula"):
        resolve_authority("faridabad")


def test_listing_urls_match_the_verified_paths():
    assert AUTHORITIES == {"gurugram": 2, "panchkula": 1}
    assert listing_url("gurugram") == "https://haryanarera.gov.in/admincontrol/registered_projects/2"
    assert listing_url("panchkula") == "https://haryanarera.gov.in/admincontrol/registered_projects/1"


# --- probe -------------------------------------------------------------------


class FakeSession:
    def __init__(self, bodies: dict[str, bytes] | bytes = b"", status: int = 200):
        self.bodies = bodies
        self.status = status
        self.calls: list[tuple[str, str, dict]] = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        body = self.bodies if isinstance(self.bodies, bytes) else self.bodies[url]
        return StdlibResponse(url, self.status, body, {})


def _probe(tmp_path, session, sleeps=None):
    sleeps = [] if sleeps is None else sleeps
    return HaryanaReraProbe(tmp_path, session=session, sleep_fn=sleeps.append), sleeps


def test_run_gets_the_gurugram_listing_and_writes_rows_and_manifest(tmp_path):
    session = FakeSession(GURUGRAM.encode("utf-8"))
    probe, _ = _probe(tmp_path, session)
    records = probe.run()

    assert [(c[0], c[1]) for c in session.calls] == [
        ("GET", "https://haryanarera.gov.in/admincontrol/registered_projects/2")
    ], "one request, and no row link is followed"
    assert set(session.calls[0][2].get("headers", {})) <= set(), "no Origin, Referer, or cookie"

    (record,) = records
    assert record["status"] == "downloaded"
    assert record["method"] == "GET"
    assert record["row_count"] == 12
    assert record["http_status"] == 200
    assert record["path"].startswith("listings/IN-HR/gurugram-")
    assert (tmp_path / record["path"]).read_bytes() == GURUGRAM.encode("utf-8")

    corpus = Corpus(tmp_path)
    rows = list(corpus.rera_project_rows())
    assert len(rows) == 12
    assert {r.source_sha256 for r in rows} == {record["sha256"]}
    assert sum(bool(r.lapsed) for r in rows) == 5
    assert next(r for r in rows if r.registration_no == "GGM/415/147/2020/31").lapsed is False
    (listing,) = list(corpus.manifest_rera_listings())
    assert listing.row_count == 12


def test_run_never_requests_a_quarterly_progress_link(tmp_path):
    session = FakeSession(GURUGRAM.encode("utf-8"))
    probe, _ = _probe(tmp_path, session)
    probe.run()
    assert all("print_quarterly_schedules" not in c[1] for c in session.calls)
    stored = (tmp_path / "rera_project_rows.jsonl").read_text(encoding="utf-8")
    assert "print_quarterly_schedules/Mg==/NzQx/MjAyMA==" in stored


def test_run_output_passes_validate(tmp_path):
    pytest.importorskip("jsonschema")
    from commoner_probe.validate import validate_corpus

    probe, _ = _probe(tmp_path, FakeSession(GURUGRAM.encode("utf-8")))
    probe.run()
    assert validate_corpus(tmp_path) is True


def test_two_authorities_are_fetched_in_turn_with_a_pause_between(tmp_path):
    session = FakeSession({
        listing_url("gurugram"): GURUGRAM.encode("utf-8"),
        listing_url("panchkula"): PANCHKULA.encode("utf-8"),
    })
    probe, sleeps = _probe(tmp_path, session)
    records = probe.run(["gurugram", "panchkula"])
    assert [r["row_count"] for r in records] == [12, 5]
    assert sleeps == [5.0]
    assert len(list(Corpus(tmp_path).rera_project_rows())) == 17


def test_a_rerun_replaces_rows_instead_of_duplicating(tmp_path):
    probe, _ = _probe(tmp_path, FakeSession(GURUGRAM.encode("utf-8")))
    probe.run()
    probe.run()
    assert len(list(Corpus(tmp_path).rera_project_rows())) == 12
    assert len(list(Corpus(tmp_path).manifest_rera_listings())) == 1, "same key on the same day"


def test_an_error_page_gives_an_error_record_and_no_rows(tmp_path):
    probe, _ = _probe(tmp_path, FakeSession(b"<html>Error</html>"))
    (record,) = probe.run()
    assert record["status"] == "error"
    assert "compliant_hearing" in record["error"]
    assert record["path"] is None and record["row_count"] is None
    assert not (tmp_path / "rera_project_rows.jsonl").exists()
    assert not (tmp_path / "listings").exists()


def test_a_changed_header_gives_an_error_record_and_no_rows(tmp_path):
    html = GURUGRAM.replace(">Builder</th>", ">Developer</th>")
    probe, _ = _probe(tmp_path, FakeSession(html.encode("utf-8")))
    (record,) = probe.run()
    assert record["status"] == "error"
    assert "headers changed" in record["error"]
    assert not (tmp_path / "rera_project_rows.jsonl").exists()


def test_an_http_error_gives_an_error_record(tmp_path):
    probe, _ = _probe(tmp_path, FakeSession(b"nope", status=503))
    (record,) = probe.run()
    assert record["status"] == "error"
    assert record["http_status"] == 503
    assert "HTTP 503" in record["error"]


def test_the_error_record_validates_against_the_manifest_schema(tmp_path):
    pytest.importorskip("jsonschema")
    from jsonschema import Draft202012Validator

    from commoner_probe import schemas as sc

    probe, _ = _probe(tmp_path, FakeSession(b"nope", status=503))
    (record,) = probe.run()
    assert list(Draft202012Validator(sc.load("manifest_rera_listing")).iter_errors(record)) == []


def test_dry_run_writes_nothing_and_makes_no_request(tmp_path):
    session = FakeSession(GURUGRAM.encode("utf-8"))
    probe, _ = _probe(tmp_path, session)
    (record,) = probe.run(["panchkula"], dry_run=True)
    assert record["status"] == "dry_run"
    assert record["url"].endswith("/registered_projects/1")
    assert session.calls == []
    assert list(tmp_path.iterdir()) == []


# --- CLI ---------------------------------------------------------------------


def test_haryana_is_a_registered_state():
    assert set(STATES) == {"HR", "KA"}


def test_cli_defaults_to_gurugram_in_a_dry_run(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["commoner-probe", "rera", "--state", "HR", "--dry-run"])
    main()
    record = json.loads(capsys.readouterr().out)
    assert record["url"].endswith("/registered_projects/2")
    assert record["state"] == "IN-HR"


def test_cli_accepts_the_iso_state_code_and_both_authorities(capsys, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["commoner-probe", "rera", "--state", "in-hr", "--authority", "Panchkula",
         "--authority", "gurugram", "--dry-run"],
    )
    main()
    urls = [json.loads(line)["url"] for line in capsys.readouterr().out.splitlines()]
    assert [u.rsplit("/", 1)[1] for u in urls] == ["1", "2"]


def test_cli_rejects_an_unknown_authority(monkeypatch):
    monkeypatch.setattr(
        "sys.argv", ["commoner-probe", "rera", "--state", "HR", "--authority", "rohtak", "--dry-run"]
    )
    with pytest.raises(SystemExit) as raised:
        main()
    assert "gurugram, panchkula" in str(raised.value)


@pytest.mark.parametrize("flag", [["--district", "Kodagu"], ["--all-districts"], ["--list-districts"]])
def test_cli_rejects_karnataka_flags_for_haryana(monkeypatch, flag):
    monkeypatch.setattr("sys.argv", ["commoner-probe", "rera", "--state", "HR", "--dry-run", *flag])
    with pytest.raises(SystemExit) as raised:
        main()
    assert "--authority" in str(raised.value)


def test_cli_rejects_authority_for_karnataka(monkeypatch):
    monkeypatch.setattr(
        "sys.argv", ["commoner-probe", "rera", "--state", "KA", "--authority", "gurugram", "--dry-run"]
    )
    with pytest.raises(SystemExit) as raised:
        main()
    assert "--state HR" in str(raised.value)


def test_cli_refuses_a_sleep_below_the_minimum(monkeypatch):
    monkeypatch.setattr(
        "sys.argv", ["commoner-probe", "rera", "--state", "HR", "--out", "x", "--sleep", "1"]
    )
    with pytest.raises(SystemExit) as raised:
        main()
    assert "at least 5" in str(raised.value)


def test_cli_writes_the_listing_and_exits_non_zero_when_a_request_fails(tmp_path, capsys, monkeypatch):
    session = FakeSession(b"nope", status=500)
    monkeypatch.setattr(HaryanaReraProbe, "session", property(lambda self: session))
    monkeypatch.setattr("sys.argv", ["commoner-probe", "rera", "--state", "HR", "--out", str(tmp_path)])
    with pytest.raises(SystemExit) as raised:
        main()
    assert raised.value.code == 1
    assert "HTTP 500" in capsys.readouterr().err


def test_cli_runs_a_listing_through_the_fake_session(tmp_path, capsys, monkeypatch):
    session = FakeSession(GURUGRAM.encode("utf-8"))
    monkeypatch.setattr(HaryanaReraProbe, "session", property(lambda self: session))
    monkeypatch.setattr("sys.argv", ["commoner-probe", "rera", "--state", "HR", "--out", str(tmp_path)])
    main()
    assert json.loads(capsys.readouterr().out)["row_count"] == 12
