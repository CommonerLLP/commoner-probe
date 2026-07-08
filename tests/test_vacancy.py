"""Tests for typed vacancy-row extraction.

The three fixture texts reproduce the pdftotext -layout output of real
answers fetched live on 2026-07-08: LS US Q5305 25.03.2026 (in-answer
transposed summary), LS US Q5491 25.03.2026 (annexure cadre-wise matrix,
abridged to four cadres + Total), and RS US Q2529 10.08.2023 (evasive
boilerplate, zero numbers). Expected values match the source PDFs.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from commoner_probe.answers import extract_answers, split_qa
from commoner_probe.vacancy import (
    LAYOUT_ANNEXURE_CADRE_MATRIX,
    LAYOUT_EVASIVE,
    LAYOUT_IN_ANSWER_SUMMARY,
    LAYOUT_UNKNOWN,
    extract_vacancy_rows,
    is_vacancy_question,
)

Q5305_TEXT = """\
                           GOVERNMENT OF INDIA
          MINISTRY OF PERSONNEL, PUBLIC GRIEVANCES AND PENSIONS
                 (DEPARTMENT OF PERSONNEL & TRAINING)

                                    LOK SABHA
                             UNSTARRED QUESTION NO.5305
                               (ANSWERED ON 25.03.2026)

                           VACANT POSTS IN CIVIL SERVICES

5305. ADV. ADOOR PRAKASH:

Will the PRIME MINISTER be pleased to state:

(a) whether a large percentage of sanctioned strength of Civil Service posts are lying vacant
presently and if so, the details thereof and the reasons therefor;
(b) whether the Government has taken any assessment of the impact of the shortage of IAS, IPS
and IFS officers in the functioning of these services and if so, the details thereof; and
(c) the details of vacant posts in each service and the measures taken by the Government to fill
these vacancies?

                                            ANSWER

MINISTER OF STATE IN THE MINISTRY OF PERSONNEL, PUBLIC GRIEVANCES
AND PENSIONS AND MINISTER OF STATE IN THE PRIME MINISTER'S OFFICE
                        (DR. JITENDRA SINGH)

(a) to (c): The details of sanctioned strength and officers in position in Indian Administrative
Service (IAS), Indian Police Service (IPS) and Indian Forest Service (IFS) are as under:

                         Service       IAS           IPS       IFS
                         Sanctioned    6877          5099      3193
                         strength
                         Officers   in 5577          4594      2164
                         position

       Recruitment is a continuous process and vacancies in these services are filled on year-
to-year basis considering the administrative requirements across the states.

                                            *****
"""

Q5491_TEXT = """\
                           GOVERNMENT OF INDIA
          MINISTRY OF PERSONNEL, PUBLIC GRIEVANCES AND PENSIONS
                 (DEPARTMENT OF PERSONNEL & TRAINING)

                                    LOK SABHA
                             UNSTARRED QUESTION NO.5491
                               (ANSWERED ON 25.03.2026)

                             SANCTIONED POSTS OF IAS/IPS

#5491. SHRI AMRA RAM:

Will the PRIME MINISTER be pleased to state:

(a) the details of the number of posts of IAS and IPS sanctioned and filled in the country, State-
wise;
(b) the details of the number of posts filled and lying vacant out of the posts reserved for the
persons belonging to the Scheduled Castes, Scheduled Tribes and Other Backward Classes?

                                            ANSWER

MINISTER OF STATE IN THE MINISTRY OF PERSONNEL, PUBLIC GRIEVANCES
AND PENSIONS AND MINISTER OF STATE IN THE PRIME MINISTER'S OFFICE
                        (DR. JITENDRA SINGH)


(a): Relevant information in respect of sanctioned posts and number of officers currently in
position is placed at Annexure-A.

(b): Total number of persons belonging to SC, ST and OBC category appointed to IAS and
IPS as Direct Recruit during the last five years (CSE 2020 to CSE 2024), is at Annexure-B.

                                                *****

                                                                                     Page 1 of 3
                                                                         Annexure A

Details of Total Sanctioned Strength of IAS and IPS officers, in-position and vacant
posts as on 01.01.2025

S. No. Cadre              Total Authorized Strength     No. of officers in position

                             IAS           IPS            IAS              IPS
1.    AGMUT                   542           457            406             427
2.    Andhra Pradesh          239           174            195             140
6.    Gujarat                 313           208            255             203
23.   Uttar Pradesh           652           541            571             510
      Total                  6877          5099           5577             4594

                                                                            Page 2 of 3
                                                                      Annexure B

Total number of persons belonging to SC, ST and OBC category appointed to IAS and
IPS as Direct Recruit during the last five years (CSE 2020 to CSE 2024):

                             IAS                 IPS

                      OBC     SC    ST   OBC     SC    ST

                      245     135   67   255     141   71

                                         *****

                                                                       Page 3 of 3
"""

Q2529_TEXT = """\
                           GOVERNMENT OF INDIA
          MINISTRY OF PERSONNEL, PUBLIC GRIEVANCES AND PENSIONS
                 (DEPARTMENT OF PERSONNEL AND TRAINING)

                                     RAJYA SABHA
                             UNSTARRED QUESTION NO. 2529
                             (TO BE ANSWERED ON 10.08.2023)

                                   SHORTAGE OF STAFF

2529 SHRI RAGHAV CHADHA:

       Will the PRIME MINISTER be pleased to state:

(a)    whether there is an acute shortage of staff in various offices of the Union Government
       due to non-filling of vacant posts;
(b)    if so, the details thereof, State-wise;
(c)    whether Government is taking any initiative to fill all the vacant posts in the said offices
       in a time bound manner?

                                            ANSWER

MINISTER OF STATE IN THE MINISTRY OF PERSONNEL, PUBLIC GRIEVANCES
AND PENSIONS AND MINISTER OF STATE IN THE PRIME MINISTER'S OFFICE
                        (DR. JITENDRA SINGH)

(a) to (c): Occurrence and filling up of vacancies in various Ministries/Departments is a
continuous process. These vacancies arise due to retirement, promotion, resignation, death etc.
Filling up of vacant post is the responsibility of concerned Ministries/Departments. All
Ministries/ Departments of the Central Government are being asked, from time to time, to take
action in a mission mode to fill up vacant posts in a time bound manner.

                                              *****
"""


def _rows_for(text):
    qa = split_qa(text)
    assert qa is not None
    return extract_vacancy_rows(qa.question_text, qa.answer_text)


def test_non_vacancy_question_returns_none():
    assert extract_vacancy_rows(
        "Will the Minister of CULTURE state the status of public libraries?",
        "The scheme is under implementation.",
    ) is None


def test_vacancy_question_gate_matches_hindi_terms():
    assert is_vacancy_question("मंत्रालय में रिक्त पद कितने हैं?")
    assert is_vacancy_question("मंज़ूर पद और कार्यरत कर्मचारी")


def test_in_answer_summary_q5305():
    rows = _rows_for(Q5305_TEXT)
    assert [r.layout for r in rows] == [LAYOUT_IN_ANSWER_SUMMARY] * 3
    by_service = {r.service: r for r in rows}
    assert by_service["IAS"].sanctioned == 6877
    assert by_service["IAS"].in_position == 5577
    assert by_service["IAS"].vacant == 1300
    assert by_service["IPS"].sanctioned == 5099
    assert by_service["IPS"].in_position == 4594
    assert by_service["IPS"].vacant == 505
    assert by_service["IFS"].sanctioned == 3193
    assert by_service["IFS"].in_position == 2164
    assert by_service["IFS"].vacant == 1029
    assert all(not r.vacant_stated for r in rows)
    assert all(r.category == "ALL" for r in rows)


def test_annexure_cadre_matrix_q5491():
    rows = _rows_for(Q5491_TEXT)
    assert all(r.layout == LAYOUT_ANNEXURE_CADRE_MATRIX for r in rows)
    # 5 cadre rows (incl. Total) x 2 services.
    assert len(rows) == 10
    idx = {(r.org_unit, r.service): r for r in rows}
    assert idx[("Gujarat", "IAS")].sanctioned == 313
    assert idx[("Gujarat", "IAS")].in_position == 255
    assert idx[("AGMUT", "IAS")].sanctioned == 542
    assert idx[("AGMUT", "IAS")].in_position == 406
    assert idx[("Uttar Pradesh", "IPS")].sanctioned == 541
    assert idx[("Total", "IAS")].sanctioned == 6877
    # The "as on" date lives in the annexure title, not the main answer.
    assert all(r.date_of_data == "2025-01-01" for r in rows)
    # Annexure-B (direct-recruit counts, no sanctioned/in-position
    # semantics) must not leak typed rows.
    assert not any(r.category in ("SC", "ST", "OBC") for r in rows)


def test_evasive_q2529():
    rows = _rows_for(Q2529_TEXT)
    assert len(rows) == 1
    assert rows[0].layout == LAYOUT_EVASIVE
    assert rows[0].sanctioned is None
    assert rows[0].vacant is None


def test_unknown_when_supply_labels_carry_numbers_but_no_table_parses():
    rows = extract_vacancy_rows(
        "the details of sanctioned and vacant posts in the Ministry?",
        "The sanctioned strength is 4521 and further details are being collected.",
    )
    assert len(rows) == 1
    assert rows[0].layout == LAYOUT_UNKNOWN


def test_evasive_when_only_recruitment_aggregates_are_offered():
    # The Railways pattern: vacancy talk + recruitment aggregates, but the
    # sanctioned/in-position anchor is withheld.
    rows = extract_vacancy_rows(
        "the details of vacant posts in Railways?",
        "During 2014-15 to 2024-25, 5.08 lakh candidates were recruited. "
        "Filling up of vacancies is a continuous process; CEN 01/2024 "
        "notified 18,799 vacancies.",
    )
    assert len(rows) == 1
    assert rows[0].layout == LAYOUT_EVASIVE


def test_group_wise_rows_map_group_letter():
    rows = extract_vacancy_rows(
        "details of sanctioned strength and vacant posts, group-wise?",
        "Group Sanctioned Strength No. of officers in position\n"
        "Group A 100 80\n"
        "Group B 250 200\n",
    )
    assert {(r.group, r.sanctioned, r.in_position) for r in rows} == {
        ("A", 100, 80),
        ("B", 250, 200),
    }


def test_split_qa_prefers_answer_marker_over_to_be_answered_header():
    # RS PDFs carry "(TO BE ANSWERED ON <date>)" in the page header; the
    # question body must stay in the question half when a bare ANSWER
    # marker exists further down (mis-split observed live before the
    # pattern-priority fix).
    qa = split_qa(Q2529_TEXT)
    assert qa.boundary_marker == "ANSWER"
    assert "vacant posts" in qa.question_text
    assert "continuous process" in qa.answer_text


class _FakePdf:
    HEADER = b"%PDF-1.4 fake\n"

    @classmethod
    def write(cls, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = text.encode("utf-8")
        pad = max(0, 1100 - len(body))
        path.write_bytes(cls.HEADER + body + b"\n" + b"%" * pad)

    @classmethod
    def read(cls, path) -> str:
        data = Path(path).read_bytes()
        if data.startswith(cls.HEADER):
            data = data[len(cls.HEADER):]
        return data.rstrip(b"%").rstrip(b"\n").decode("utf-8", errors="replace")


def test_extract_answers_writes_vacancy_rows_jsonl(monkeypatch):
    from commoner_probe import answers as ans_mod
    monkeypatch.setattr(ans_mod, "extract_pdf_text", _FakePdf.read)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        for rel, text in (
            ("pdfs/ls/AU5305.pdf", Q5305_TEXT),
            ("pdfs/rs/AU2529.pdf", Q2529_TEXT),
        ):
            _FakePdf.write(out / rel, text)
        (out / "manifest.jsonl").write_text(
            json.dumps({
                "key": "LS|U|5305|2026-03-25", "run_id": "r1", "kind": "qa",
                "ministry": "PERSONNEL, PUBLIC GRIEVANCES AND PENSIONS",
                "pdf_path": "pdfs/ls/AU5305.pdf",
            }) + "\n" + json.dumps({
                "key": "RS|U|2529|2023-08-10", "run_id": "r1", "kind": "qa",
                "ministry": "PERSONNEL,PUBLIC GRIEVANCES AND PENSIONS",
                "pdf_path": "pdfs/rs/AU2529.pdf",
            }) + "\n",
            encoding="utf-8",
        )
        stats = extract_answers(out, log_fn=lambda *_: None)
        assert stats.qa_records == 2
        assert stats.vacancy_rows == 3
        assert stats.vacancy_evasive == 1
        assert stats.vacancy_unknown == 0
        rows = [
            json.loads(line)
            for line in (out / "vacancy_rows.jsonl").read_text().splitlines()
        ]
        assert len(rows) == 4
        assert all(r["kind"] == "vacancy_row" for r in rows)
        ias = next(r for r in rows if r.get("service") == "IAS")
        assert ias["key"] == "LS|U|5305|2026-03-25"
        assert ias["ministry"] == "PERSONNEL, PUBLIC GRIEVANCES AND PENSIONS"
        assert (ias["sanctioned"], ias["in_position"], ias["vacant"]) == (6877, 5577, 1300)
        evasive = next(r for r in rows if r["layout"] == "evasive")
        assert evasive["key"] == "RS|U|2529|2023-08-10"


def test_extract_answers_removes_stale_vacancy_file(monkeypatch):
    from commoner_probe import answers as ans_mod
    monkeypatch.setattr(ans_mod, "extract_pdf_text", _FakePdf.read)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        _FakePdf.write(out / "pdfs/ls/plain.pdf", (
            "Question about libraries...\n"
            "Reply by SHRI X, MINISTER OF CULTURE:\n"
            "The scheme is under implementation. " * 4
        ))
        (out / "manifest.jsonl").write_text(
            json.dumps({"key": "LS|U|1|2026-01-01", "kind": "qa", "pdf_path": "pdfs/ls/plain.pdf"}) + "\n",
            encoding="utf-8",
        )
        (out / "vacancy_rows.jsonl").write_text("{}\n", encoding="utf-8")
        extract_answers(out, log_fn=lambda *_: None)
        assert not (out / "vacancy_rows.jsonl").exists()


def test_vacancy_rows_validate_against_schema():
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        import pytest
        pytest.skip("jsonschema not installed")
    from commoner_probe import schemas
    schema = schemas.load("vacancy_row")
    for text in (Q5305_TEXT, Q5491_TEXT, Q2529_TEXT):
        for row in _rows_for(text):
            record = {
                "key": "LS|U|1|2026-01-01",
                "run_id": "r1",
                "source_pdf": "pdfs/ls/x.pdf",
                "extracted_at": "2026-07-08T00:00:00+00:00",
                "ministry": "PERSONNEL, PUBLIC GRIEVANCES AND PENSIONS",
                **row.to_record(),
            }
            Draft202012Validator(schema).validate(record)
