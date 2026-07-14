"""Tests for the Gujarati NeVA Q/A splitter, glyph repair, and district-table
parser.

Fixtures under ``tests/fixtures/neva/`` are real ``pdftotext -layout``
extractions of Gujarat Vidhan Sabha (assembly 15, session 8) question PDFs:

* ``neva_permits.txt``  — clean text layer; liquor-permit tables. The
  Ahmedabad 14,862 / Surat 8,622 figures are the cross-verified oracle
  from the requesting repo's own manual check against the source PDF.
* ``neva_seizure.txt``  — clean text layer; per-district seizure tables
  with city ("શહેર") vs district row variants and money columns.
* ``neva_garbled.txt``  — broken ToUnicode cmap: બ→ફ, પ→઩, લ→઱ plus
  doubled aa-matras. Repairable against the clean metadata subject.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from commoner_probe.neva_text import (
    GUJARAT_DISTRICTS,
    NevaQaExtraction,
    derive_glyph_repair,
    extract_district_rows,
    extract_neva_answers,
    gujarati_digits_to_ascii,
    normalize_gujarati_text,
    repair_text,
    split_qa_neva,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "neva"

PERMITS_SUBJECT = "અમદાવાદ અને સુરત જિલ્લામાં દારૂની પરમીટ બાબત"
SEIZURE_SUBJECT = "ભાવનગર અને પોરબંદર જિલ્લામાં પકડાયેલ નશીલા પદાર્થો"
GARBLED_SUBJECT = "સાબરકાંઠા અને તાપી જિલ્લામાં પકડાયેલ નશીલા પદાર્થો"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_gujarati_digits_to_ascii():
    assert gujarati_digits_to_ascii("૧૪૮૬૨ અને ૮૬૨૨") == "14862 અને 8622"


def test_normalize_collapses_doubled_aa_matra():
    assert normalize_gujarati_text("જિલ્લામાાં") == "જિલ્લામાં"


def test_repair_text_clean_document():
    text, quality, mapping = repair_text(_fixture("neva_permits.txt"), PERMITS_SUBJECT)
    assert quality == "clean"
    assert mapping == {}
    assert PERMITS_SUBJECT in text


def test_repair_text_derives_and_applies_glyph_map():
    text, quality, mapping = repair_text(_fixture("neva_garbled.txt"), GARBLED_SUBJECT)
    assert quality == "repaired"
    assert mapping == {"ફ": "બ", "઩": "પ", "઱": "લ"}
    assert GARBLED_SUBJECT in text


def test_repair_text_low_when_reference_unrecoverable():
    text, quality, mapping = repair_text("સાવ જ અલગ લખાણ\nકંઈક બીજું", GARBLED_SUBJECT)
    assert quality == "low"
    assert mapping == {}


def test_derive_glyph_repair_drops_conflicting_mappings():
    # ક maps to both ગ and ઘ across the alignment — must be dropped.
    mapping = derive_glyph_repair("ગમઘમ", "કમકમ")
    assert "ક" not in mapping


def test_split_qa_neva_separates_columns():
    text, _, _ = repair_text(_fixture("neva_permits.txt"), PERMITS_SUBJECT)
    qa = split_qa_neva(text)
    assert qa is not None
    assert qa.subject == PERMITS_SUBJECT
    assert qa.question_ref == "15/8/3879"
    # Question half keeps the question clauses, answer half keeps the tables.
    assert "પરસ્મટો ધરાવે" in qa.question_text
    assert "14862" in qa.answer_text
    assert "14862" not in qa.question_text
    assert qa.confidence == 0.8


def test_split_qa_neva_appendix_tables_go_to_answer():
    text, _, _ = repair_text(_fixture("neva_seizure.txt"), SEIZURE_SUBJECT)
    qa = split_qa_neva(text)
    assert qa is not None
    assert "પત્રક-1" in qa.answer_text
    assert "53969" in qa.answer_text
    assert "53969" not in qa.question_text


def test_split_qa_neva_returns_none_without_header():
    assert split_qa_neva("કોઈ હેડર નથી\nમાત્ર લખાણ") is None
    assert split_qa_neva("") is None


def test_extract_district_rows_permit_oracle():
    """The cross-verified oracle: Ahmedabad 14,862 / Surat 8,622 permits."""
    text, _, _ = repair_text(_fixture("neva_permits.txt"), PERMITS_SUBJECT)
    rows = extract_district_rows(text)
    first_by_district = {}
    for r in rows:
        first_by_district.setdefault((r.district, r.area), r)
    assert first_by_district[("અમદાવાદ", "")].primary_figure == 14862
    assert first_by_district[("સુરત", "")].primary_figure == 8622


def test_extract_district_rows_city_vs_district_and_money():
    text, _, _ = repair_text(_fixture("neva_seizure.txt"), SEIZURE_SUBJECT)
    rows = extract_district_rows(text)
    seen = [(r.district, r.area, r.figures[0]) for r in rows]
    assert ("ભાવનગર", "શહેર", 53969) in seen
    assert ("ભાવનગર", "", 131728) in seen
    # Money columns parse with commas stripped: ૧,૪૮,૬૭,૨૩૩/- → 14867233.
    city_row = next(r for r in rows if r.area == "શહેર" and r.figures[0] == 53969)
    assert 14867233 in city_row.figures
    # Decimal litres survive: ૧૩૬૧૬.૬ → 13616.6.
    pb_city = next(r for r in rows if r.district == "પોરબંદર" and r.area == "શહેર")
    assert 13616.6 in pb_city.figures


def test_extract_district_rows_ignores_markers_dates_and_statement_refs():
    text, _, _ = repair_text(_fixture("neva_seizure.txt"), SEIZURE_SUBJECT)
    rows = extract_district_rows(text)
    # The question prose "…ભાવનગર અને (૧) પત્રક-૧ મુજબ." must not yield a row.
    assert all(r.figures[0] > 1 for r in rows)


def test_extract_district_rows_on_repaired_garbled_doc():
    text, quality, _ = repair_text(_fixture("neva_garbled.txt"), GARBLED_SUBJECT)
    assert quality == "repaired"
    rows = extract_district_rows(text)
    seen = {(r.district, r.figures[0]) for r in rows}
    # Figures printed in the source PDF: સાબરકાંઠા ૧૨૮૫૮૭…, તાપી ૧૧૨૨૯૯…
    assert ("સાબરકાંઠા", 128587) in seen
    assert ("તાપી", 112299) in seen


def test_extract_district_rows_no_figures_no_rows():
    assert extract_district_rows("અમદાવાદ અને સુરત વિશે પ્રશ્ન") == []


def test_gazetteer_has_33_districts():
    assert len(GUJARAT_DISTRICTS) == 33


def _neva_corpus(tmp_path: Path, fixture: str, subject: str, key: str) -> Path:
    """Assemble a minimal NeVA corpus layout around a fixture text.

    extract_pdf_text falls back through pdftotext/pdfminer and returns ""
    for non-PDFs, so the fixture text is planted via monkeypatching in
    the caller instead; here we just lay out questions.jsonl + a dummy
    pdf path.
    """
    (tmp_path / "pdfs" / "questions").mkdir(parents=True, exist_ok=True)
    pdf = tmp_path / "pdfs" / "questions" / f"{key.replace('|', '_')}.pdf"
    pdf.write_bytes(b"%PDF-1.4 placeholder")
    rec = {
        "key": key,
        "record_type": "question",
        "source": "neva",
        "subject": subject,
        "pdf_path": str(pdf.relative_to(tmp_path)),
    }
    with (tmp_path / "questions.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return pdf


def test_extract_neva_answers_end_to_end(tmp_path, monkeypatch):
    texts = {}
    p1 = _neva_corpus(tmp_path, "neva_permits.txt", PERMITS_SUBJECT, "GJ|q|15|8|3796|14")
    texts[p1] = _fixture("neva_permits.txt")
    p2 = _neva_corpus(tmp_path, "neva_garbled.txt", GARBLED_SUBJECT, "GJ|q|15|8|3796|6")
    texts[p2] = _fixture("neva_garbled.txt")

    from commoner_probe import textparse

    monkeypatch.setattr(textparse, "extract_pdf_text", lambda p: texts[p])
    stats = extract_neva_answers(tmp_path, log_fn=lambda *_: None)

    assert stats.questions_processed == 2
    assert stats.qa_records == 2
    assert stats.quality_counts == {"clean": 1, "repaired": 1}
    assert stats.district_rows > 0

    answers = [json.loads(line) for line in (tmp_path / "answers.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {a["kind"] for a in answers} == {"neva_qa_response"}
    assert {a["quality"] for a in answers} == {"clean", "repaired"}
    rows = [json.loads(line) for line in (tmp_path / "neva_district_rows.jsonl").read_text(encoding="utf-8").splitlines()]
    oracle = [r for r in rows if r["district"] == "અમદાવાદ" and r["primary_figure"] == 14862]
    assert oracle, "permit oracle row missing"


def test_extract_neva_answers_low_quality_rows_carry_label(tmp_path, monkeypatch):
    _neva_corpus(tmp_path, "x", GARBLED_SUBJECT, "GJ|q|15|8|3796|99")
    # A text whose subject line is corrupted beyond the derivable map:
    # the Q/A header is present so the split succeeds, but quality is low.
    # The verbatim-matching district row is still emitted — the per-row
    # gazetteer match is the integrity condition — labelled quality=low.
    hopeless = (
        "99\n"
        "ઋઋઋ ઋઋ ઋઋઋ\n"
        "*15/8/9999 કોઈ સભ્ય (ક્યાંક): માનનીય મંત્રીશ્રી જણાવવા કૃપા કરશે કે.-\n"
        "     પ્રશ્ન                             િવાબ\n"
        " (1) કેટલા છે,               (1)\n"
        "                             અમદાવાદ 12345\n"
    )
    from commoner_probe import textparse

    monkeypatch.setattr(textparse, "extract_pdf_text", lambda p: hopeless)
    stats = extract_neva_answers(tmp_path, log_fn=lambda *_: None)
    assert stats.quality_counts == {"low": 1}
    assert stats.district_rows == 1
    (row,) = [json.loads(line) for line in (tmp_path / "neva_district_rows.jsonl").read_text(encoding="utf-8").splitlines()]
    assert row["district"] == "અમદાવાદ"
    assert row["quality"] == "low"
    assert row["primary_figure"] == 12345


def test_records_validate_against_schemas(tmp_path, monkeypatch):
    jsonschema = pytest.importorskip("jsonschema")
    _neva_corpus(tmp_path, "neva_permits.txt", PERMITS_SUBJECT, "GJ|q|15|8|3796|14")
    from commoner_probe import textparse

    monkeypatch.setattr(textparse, "extract_pdf_text", lambda p: _fixture("neva_permits.txt"))
    extract_neva_answers(tmp_path, log_fn=lambda *_: None)

    schemas_dir = Path(__file__).resolve().parent.parent / "commoner_probe" / "schemas"
    qa_schema = json.loads((schemas_dir / "answers_neva_qa_response.schema.json").read_text(encoding="utf-8"))
    row_schema = json.loads((schemas_dir / "neva_district_row.schema.json").read_text(encoding="utf-8"))
    for line in (tmp_path / "answers.jsonl").read_text(encoding="utf-8").splitlines():
        jsonschema.validate(json.loads(line), qa_schema)
    for line in (tmp_path / "neva_district_rows.jsonl").read_text(encoding="utf-8").splitlines():
        jsonschema.validate(json.loads(line), row_schema)


def test_extract_neva_answers_is_idempotent_on_rerun(tmp_path, monkeypatch):
    _neva_corpus(tmp_path, "neva_permits.txt", PERMITS_SUBJECT, "GJ|q|15|8|3796|14")
    from commoner_probe import textparse

    monkeypatch.setattr(textparse, "extract_pdf_text", lambda p: _fixture("neva_permits.txt"))
    first = extract_neva_answers(tmp_path, log_fn=lambda *_: None)
    second = extract_neva_answers(tmp_path, log_fn=lambda *_: None)
    assert first.qa_records == second.qa_records == 1


def test_district_rows_come_from_answer_half_only(tmp_path, monkeypatch):
    """A district + incidental number in the QUESTION prose must not
    fabricate a table row; the answer column's real row still lands."""
    text = (
        "5\n"
        "અમદાવાદ જિલ્લામાં દારૂની પરમીટ બાબત\n"
        "*15/8/9999 કોઈ સભ્ય (ક્યાંક): માનનીય મંત્રીશ્રી જણાવવા કૃપા કરશે કે.-\n"
        "     પ્રશ્ન                                િવાબ\n"
        " (1) અમદાવાદ 2 વર્ષમાં કેટલી,     (1)\n"
        "                                  અમદાવાદ 14862\n"
    )
    _neva_corpus(tmp_path, "x", "અમદાવાદ જિલ્લામાં દારૂની પરમીટ બાબત", "GJ|q|15|8|3796|5")
    from commoner_probe import textparse

    monkeypatch.setattr(textparse, "extract_pdf_text", lambda p: text)
    stats = extract_neva_answers(tmp_path, log_fn=lambda *_: None)
    rows = [json.loads(line) for line in (tmp_path / "neva_district_rows.jsonl").read_text(encoding="utf-8").splitlines()]
    assert stats.district_rows == len(rows) == 1
    assert rows[0]["primary_figure"] == 14862


def test_to_record_shape():
    qa = NevaQaExtraction(
        question_text="q", answer_text="a", confidence=0.8,
        quality="clean", subject="s", question_ref="15/8/1",
    )
    rec = qa.to_record()
    assert rec["kind"] == "neva_qa_response"
    assert rec["question_subject"] == "s"
    assert rec["question_ref"] == "15/8/1"
