"""Tests for the outsourcing/consultancy signal extractor.

Fixture lines mirror real DRSC Demands-for-Grants report prose — the
NCPCR passage (34 of 36 sanctioned posts vacant; functions with 102
contractual staff) is the reference example the requesting repo proved
on disk.
"""

from __future__ import annotations

import json

from commoner_probe.outsourcing import (
    KIND_HEADCOUNT,
    KIND_MENTION,
    KIND_SPEND,
    KIND_VACANCY,
    extract_outsourcing_signals,
)

NCPCR_TEXT = """\
4.12 The Committee notes with concern that in the National Commission for
Protection of Child Rights, 34 out of the 36 sanctioned posts are lying vacant.
The Commission presently functions with 102 contractual staff engaged through
a manpower agency.
The Committee observes that an expenditure of Rs. 4.56 crore was incurred on
consultancy services during 2025-26.
The Ministry has set up a Project Management Unit for the scheme.
"""


def _by_kind(signals, kind):
    return [s for s in signals if s.kind == kind]


def test_vacancy_pair():
    (v,) = _by_kind(extract_outsourcing_signals(NCPCR_TEXT), KIND_VACANCY)
    assert v.vacant == 34
    assert v.sanctioned == 36
    assert "lying vacant" in v.context


def test_contractual_headcount():
    signals = extract_outsourcing_signals(NCPCR_TEXT)
    heads = _by_kind(signals, KIND_HEADCOUNT)
    assert any(s.value == 102 and s.term.lower().startswith("contractual") for s in heads)


def test_consultancy_spend_normalised_to_inr():
    signals = extract_outsourcing_signals(NCPCR_TEXT)
    spends = _by_kind(signals, KIND_SPEND)
    assert any(s.value == 4.56e7 and s.unit == "inr" for s in spends)


def test_pmu_mention_without_number():
    signals = extract_outsourcing_signals(NCPCR_TEXT)
    mentions = _by_kind(signals, KIND_MENTION)
    assert any("project management unit" in s.term.lower() for s in mentions)


def test_lakh_scale():
    signals = extract_outsourcing_signals(
        "An amount of Rs. 25.5 lakh was paid to consultants during the year."
    )
    (s,) = _by_kind(signals, KIND_SPEND)
    assert s.value == 25.5e5


def test_plain_rupee_amount_no_scale():
    signals = extract_outsourcing_signals(
        "Rs. 12,50,000 was spent on outsourced security services."
    )
    (s,) = _by_kind(signals, KIND_SPEND)
    assert s.value == 1250000.0


def test_year_ranges_are_not_headcounts():
    # "2025-26" near a term must not become a headcount of 2025.
    signals = extract_outsourcing_signals(
        "The consultancy policy was revised in the year under review."
    )
    assert _by_kind(signals, KIND_HEADCOUNT) == []
    assert len(_by_kind(signals, KIND_MENTION)) == 1


def test_no_terms_no_signals():
    assert extract_outsourcing_signals("The Committee examined the Demands for Grants.") == []


def test_years_and_percentages_are_not_headcounts():
    # Real report 377 lines that previously mis-parsed:
    for line, expect_mention_only in [
        ("Rule 24 (5) of NCPCR Rules, 2006 NCPCR engages Consultants on functional basis.", True),
        ("contractual appointments now constitute 49.06% in 2025", True),
        ("The details of number of contractual appointments in the CARA since 2021", True),
    ]:
        signals = extract_outsourcing_signals(line)
        assert _by_kind(signals, KIND_HEADCOUNT) == [], line
        assert _by_kind(signals, KIND_MENTION), line


def test_durations_are_not_headcounts():
    signals = extract_outsourcing_signals(
        "regularize those who are working there for more than 10 years on contractual basis"
    )
    assert _by_kind(signals, KIND_HEADCOUNT) == []
    assert _by_kind(signals, KIND_MENTION)


def test_real_headcount_still_extracts_next_to_year():
    signals = extract_outsourcing_signals(
        "Contractual appointments have increased from 60 in 2021 to 157 in 2025."
    )
    (h,) = _by_kind(signals, KIND_HEADCOUNT)
    assert h.value == 60


def test_young_professionals_headcount():
    signals = extract_outsourcing_signals(
        "NITI Aayog engaged 87 Young Professionals during the period."
    )
    heads = _by_kind(signals, KIND_HEADCOUNT)
    assert any(s.value == 87 for s in heads)


def test_dedup_same_term_same_line():
    signals = extract_outsourcing_signals(
        "outsourced staff and outsourced services totalling 40 persons"
    )
    heads = [s for s in signals if s.kind == KIND_HEADCOUNT]
    assert len(heads) == 1


def test_extract_answers_emits_outsourcing_rows(tmp_path):
    import pytest

    jsonschema = pytest.importorskip("jsonschema")
    from pathlib import Path
    from unittest.mock import patch

    from commoner_probe.answers import extract_answers

    manifest = {
        "key": "RS|EDU|377",
        "kind": "committee_report",
        "report_type": "demands_for_grants",
        "committee": "education",
        "pdf_path": "pdfs/rs/377.pdf",
    }
    (tmp_path / "pdfs" / "rs").mkdir(parents=True)
    (tmp_path / "pdfs" / "rs" / "377.pdf").write_bytes(b"%PDF-1.4 " + b"x" * 2000)
    (tmp_path / "manifest.jsonl").write_text(json.dumps(manifest) + "\n", encoding="utf-8")

    with patch("commoner_probe.answers.extract_pdf_text", return_value=NCPCR_TEXT):
        stats = extract_answers(tmp_path, log_fn=lambda *_: None)

    assert stats.outsourcing_signals > 0
    rows = [json.loads(line) for line in (tmp_path / "outsourcing_rows.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {r["signal"] for r in rows} >= {"vacancy", "headcount", "spend", "mention"}
    assert all(r["committee"] == "education" for r in rows)
    assert all(r["report_type"] == "demands_for_grants" for r in rows)

    schema = json.loads(
        (Path(__file__).resolve().parent.parent / "commoner_probe" / "schemas" / "outsourcing_row.schema.json")
        .read_text(encoding="utf-8")
    )
    for r in rows:
        jsonschema.validate(r, schema)
