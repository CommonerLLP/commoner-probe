"""Tests for commoner_probe.corpus.Corpus.

Covers:
- Streaming iteration counts on the smoke fixture.
- from_dict tolerates extra unknown keys.
- join_qa and join_atr_chain produce correct counts on hand-crafted fixtures.
- to_dataframe raises ImportError with a helpful message when pandas absent.
- entities() returns an EntityStore (empty if no entities/ dir).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "examples" / "corpora" / "committees-smoke"


# ---------------------------------------------------------------------------
# Streaming iteration on the smoke fixture
# ---------------------------------------------------------------------------

def test_manifest_committee_reports_count():
    from commoner_probe import Corpus
    c = Corpus(SMOKE)
    records = list(c.manifest_committee_reports())
    assert len(records) == 4, f"expected 4 committee report records, got {len(records)}"


def test_manifest_qa_on_smoke_is_empty():
    """The smoke fixture has no Q/A records."""
    from commoner_probe import Corpus
    c = Corpus(SMOKE)
    records = list(c.manifest_qa())
    assert records == []


def test_answers_qa_on_smoke_is_empty():
    """The smoke fixture has no answers.jsonl."""
    from commoner_probe import Corpus
    c = Corpus(SMOKE)
    assert list(c.answers_qa()) == []


def test_runs_on_smoke_is_empty():
    """The smoke fixture has no _runs.jsonl."""
    from commoner_probe import Corpus
    c = Corpus(SMOKE)
    assert list(c.runs()) == []


# ---------------------------------------------------------------------------
# from_dict tolerates unknown keys
# ---------------------------------------------------------------------------

def test_from_dict_tolerates_unknown_keys():
    from commoner_probe import ManifestCommitteeReportRecord
    d = {
        "key": "LS|finance|35|18",
        "kind": "committee_report",
        "house": "Lok Sabha",
        "report_type": "demands_for_grants",
        "presented_via": "both_houses",
        "committee_slug": "finance",
        "committee_name": "Finance",
        "title": "Test",
        "date": "2026-03-17",
        "source": "sansad.in/api_ls/committee",
        "future_field_v999": "should be silently dropped",
        "another_new_field": 42,
    }
    r = ManifestCommitteeReportRecord.from_dict(d)
    assert r.key == "LS|finance|35|18"
    assert not hasattr(r, "future_field_v999")


# ---------------------------------------------------------------------------
# join_qa on a hand-crafted fixture
# ---------------------------------------------------------------------------

def _write_lines(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def test_join_qa_correct_counts():
    from commoner_probe import Corpus

    manifest_recs = [
        {
            "key": "LS|S|1|2024-01-15",
            "kind": "qa",
            "house": "Lok Sabha",
            "title": "Q1",
            "date": "2024-01-15",
            "qtype": "STARRED",
            "qno": "1",
            "ministry": "EDUCATION",
            "askers": ["MP A"],
            "source": "elibrary.sansad.in",
        },
        {
            "key": "LS|U|2|2024-01-15",
            "kind": "qa",
            "house": "Lok Sabha",
            "title": "Q2",
            "date": "2024-01-15",
            "qtype": "UNSTARRED",
            "qno": "2",
            "ministry": "EDUCATION",
            "askers": ["MP B"],
            "source": "elibrary.sansad.in",
        },
    ]
    answers_recs = [
        {
            "key": "LS|S|1|2024-01-15",
            "kind": "qa_response",
            "question_text": "What is the status?",
            "answer_text": "The minister states...",
            "confidence": 0.85,
            "extractor": "answers_regex_v1",
            "boundary_marker": "REPLY",
            "source_pdf": "pdfs/ls/q1.pdf",
            "extracted_at": "2024-01-15T10:00:00Z",
            "language_classified": ["en"],
            "source_report_type": None,
        }
    ]

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        _write_lines(p / "manifest.jsonl", manifest_recs)
        _write_lines(p / "answers.jsonl", answers_recs)

        c = Corpus(p)
        pairs = list(c.join_qa())

    assert len(pairs) == 2
    # First record has an answer
    paired = {pair.manifest.key: pair for pair in pairs}
    assert len(paired["LS|S|1|2024-01-15"].answers) == 1
    assert paired["LS|S|1|2024-01-15"].answers[0].question_text == "What is the status?"
    # Second record has no answer
    assert paired["LS|U|2|2024-01-15"].answers == []


# ---------------------------------------------------------------------------
# join_atr_chain on a hand-crafted fixture
# ---------------------------------------------------------------------------

def test_join_atr_chain_correct_linkage():
    from commoner_probe import AtrChain, Corpus

    manifest_recs = [
        # Original report
        {
            "key": "LS|finance|24|18",
            "kind": "committee_report",
            "house": "Lok Sabha",
            "report_type": "demands_for_grants",
            "presented_via": "both_houses",
            "committee_slug": "finance",
            "committee_name": "Finance",
            "report_no": 24,
            "title": "24th Report on DFG",
            "date": "2022-03-10",
            "source": "sansad.in/api_ls/committee",
        },
        # ATR responding to it
        {
            "key": "LS|finance|35|18",
            "kind": "committee_report",
            "house": "Lok Sabha",
            "report_type": "action_taken",
            "presented_via": "both_houses",
            "committee_slug": "finance",
            "committee_name": "Finance",
            "report_no": 35,
            "title": "Action Taken on 24th Report",
            "date": "2026-03-17",
            "source": "sansad.in/api_ls/committee",
        },
    ]
    atr_linkage_recs = [
        {
            "atr_key": "LS|finance|35|18",
            "atr_no": 35,
            "house": "Lok Sabha",
            "committee_slug": "finance",
            "atr_title": "Action Taken Report on the 24th Report",
            "references_report_no": 24,
            "references_report_key": "LS|finance|24|18",
            "extracted_at": "2026-03-17T12:00:00",
            "extractor": "atr_linkage_v1",
        }
    ]

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        _write_lines(p / "manifest.jsonl", manifest_recs)
        _write_lines(p / "atr_linkage.jsonl", atr_linkage_recs)

        c = Corpus(p)
        chains = list(c.join_atr_chain())

    assert len(chains) == 1
    chain = chains[0]
    assert isinstance(chain, AtrChain)
    assert chain.atr.key == "LS|finance|35|18"
    assert chain.linkage is not None
    assert chain.linkage.references_report_key == "LS|finance|24|18"
    assert chain.original is not None
    assert chain.original.key == "LS|finance|24|18"
    assert chain.original.report_type == "demands_for_grants"


# ---------------------------------------------------------------------------
# to_dataframe raises ImportError when pandas absent
# ---------------------------------------------------------------------------

def test_to_dataframe_raises_without_pandas(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "pandas":
            raise ImportError("No module named 'pandas'")
        return real_import(name, *args, **kwargs)

    from commoner_probe import Corpus
    with monkeypatch.context() as m:
        m.setattr(builtins, "__import__", mock_import)
        c = Corpus(SMOKE)
        with pytest.raises(ImportError, match="pandas"):
            c.to_dataframe("manifest_committee_reports")


# ---------------------------------------------------------------------------
# entities() returns an EntityStore
# ---------------------------------------------------------------------------

def test_entities_returns_empty_store_when_no_dir():
    from commoner_probe import Corpus
    with tempfile.TemporaryDirectory() as tmp:
        c = Corpus(Path(tmp))
        store = c.entities()
    assert store is not None
    assert store.people == {}


# ---------------------------------------------------------------------------
# Corpus repr
# ---------------------------------------------------------------------------

def test_corpus_repr():
    from commoner_probe import Corpus
    c = Corpus("/some/path")
    assert "/some/path" in repr(c)
