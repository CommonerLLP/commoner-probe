"""Tests for Phase 1 structured PDF extraction.

Covers the three extractor shapes (Q/A, ATR, DFG) plus the corpus
dispatcher. The real PDFs at ``test_v4_rs/pdfs/rs/education_*.pdf``
are gitignored, so the integration test is conditional on their
presence (skips if absent).

Synthetic fixtures pin behaviour against the canonical text patterns
each extractor expects.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from commoner_probe.answers import (
    EXTRACTOR_VERSION,
    extract_answers,
    split_atr,
    split_dfg,
    split_qa,
)

# -------------------------------------------------------------------------
# Q/A
# -------------------------------------------------------------------------


class SplitQaTests(unittest.TestCase):
    def test_splits_on_reply_by_minister(self):
        text = (
            "QUESTION ASKED BY SHRI X\n"
            "Will the Minister of Education be pleased to state:\n"
            "(a) the number of vacancies in Central Universities;\n"
            "(b) steps taken to fill them.\n"
            "Reply by SMT. Y, MINISTER OF EDUCATION:\n"
            "(a) The total number of vacancies as on 31.03.2026 is 8500.\n"
            "(b) Recruitment is being done in mission mode.\n"
        )
        qa = split_qa(text)
        self.assertIsNotNone(qa)
        self.assertIn("vacancies", qa.question_text)
        self.assertIn("8500", qa.answer_text)
        self.assertGreater(qa.confidence, 0.5)

    def test_splits_on_bare_answer_header(self):
        text = (
            "Question text body...\nMore text.\nANSWER\n"
            "(a) The total is 1000.\n(b) Done.\n"
        )
        qa = split_qa(text)
        self.assertIsNotNone(qa)
        self.assertIn("Question text", qa.question_text)
        self.assertIn("1000", qa.answer_text)

    def test_returns_none_when_no_boundary_marker(self):
        text = "Some random text without any reply marker."
        self.assertIsNone(split_qa(text))

    def test_returns_none_for_empty_input(self):
        self.assertIsNone(split_qa(""))
        self.assertIsNone(split_qa(None))


# -------------------------------------------------------------------------
# ATR
# -------------------------------------------------------------------------


class SplitAtrTests(unittest.TestCase):
    def test_extracts_recommendation_and_response_pair(self):
        text = (
            "Recommendation No. 1\n"
            "The Committee recommends that the Government should fill all "
            "vacant faculty posts in Central Universities on a war footing.\n"
            "Reply of the Government\n"
            "The matter is under active consideration. Steps are being taken.\n"
        )
        items = split_atr(text)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.recommendation_no, 1)
        self.assertIn("vacant faculty", item.recommendation_text)
        self.assertIn("under active consideration", item.response_text)
        self.assertGreaterEqual(item.confidence, 0.8)

    def test_extracts_multiple_recommendations(self):
        text = (
            "Recommendation No. 1\nRec text 1.\nReply of the Government\nReply 1.\n"
            "Recommendation No. 2\nRec text 2.\nReply of the Government\nReply 2.\n"
            "Recommendation No. 3\nRec text 3.\nAction Taken by the Government\nReply 3.\n"
        )
        items = split_atr(text)
        self.assertEqual(len(items), 3)
        nos = [i.recommendation_no for i in items]
        self.assertEqual(nos, [1, 2, 3])

    def test_handles_observation_recommendation_prefix(self):
        text = (
            "Observation/Recommendation No. 5\n"
            "The Committee notes that the budget is inadequate.\n"
            "Reply of the Government\n"
            "Noted.\n"
        )
        items = split_atr(text)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].recommendation_no, 5)

    def test_lowers_confidence_when_no_reply_boundary(self):
        # Recommendation marker present but no "Reply of the Government" — body
        # becomes the recommendation, response is empty, confidence drops.
        text = (
            "Recommendation No. 1\n"
            "The Committee recommends X.\n"
            "Recommendation No. 2\n"
            "The Committee recommends Y.\n"
        )
        items = split_atr(text)
        self.assertEqual(len(items), 2)
        for item in items:
            self.assertLess(item.confidence, 0.6)
            self.assertEqual(item.response_text, "")

    def test_returns_empty_when_no_recommendation_markers(self):
        self.assertEqual(split_atr("This is some random text."), [])


# -------------------------------------------------------------------------
# DFG
# -------------------------------------------------------------------------


class SplitDfgTests(unittest.TestCase):
    def test_extracts_numbered_recommendations_after_section_header(self):
        text = (
            "Body of report...\n"
            "OBSERVATIONS/RECOMMENDATIONS OF THE COMMITTEE\n\n"
            "1.     The Committee observes that allocation is inadequate. "
            "The Committee recommends substantial enhancement.\n"
            "                                    (Para 2.11)\n\n"
            "2.     The Committee notes the reduction in flagship schemes funding. "
            "The Ministry should examine SNA fund flow restrictions.\n"
            "                                    (Para 2.15)\n\n"
            "3.     The Committee observed significant increase in Mission Saksham allocation.\n"
            "                                    (Para 3.5)\n"
        )
        items = split_dfg(text)
        self.assertEqual(len(items), 3)
        nos = [i.recommendation_no for i in items]
        self.assertEqual(nos, [1, 2, 3])
        # (Para X.Y) cross-references stripped by _clean.
        for item in items:
            self.assertNotIn("(Para", item.recommendation_text)

    def test_uses_last_occurrence_of_section_header(self):
        # First occurrence is in TOC, second is the actual section.
        text = (
            "Table of Contents\n5. OBSERVATIONS/RECOMMENDATIONS OF THE COMMITTEE  56-67\n"
            "Body\n"
            "OBSERVATIONS/RECOMMENDATIONS OF THE COMMITTEE\n\n"
            "1.     First recommendation here.\n"
            "2.     Second recommendation here.\n"
        )
        items = split_dfg(text)
        # TOC entry "5. OBSERVATIONS/..." would be misread if we used the first
        # occurrence; we use the last so we get the real recommendations.
        nos = [i.recommendation_no for i in items]
        self.assertEqual(nos, [1, 2])

    def test_stops_at_annexure_boundary(self):
        text = (
            "OBSERVATIONS/RECOMMENDATIONS OF THE COMMITTEE\n\n"
            "1.     First recommendation.\n"
            "2.     Second recommendation.\n"
            "ANNEXURE I\n"
            "3.     This is annexure content not a recommendation.\n"
        )
        items = split_dfg(text)
        nos = [i.recommendation_no for i in items]
        self.assertEqual(nos, [1, 2])

    def test_returns_empty_when_no_section_header(self):
        self.assertEqual(split_dfg("No header in this text."), [])

    def test_returns_empty_for_empty_input(self):
        self.assertEqual(split_dfg(""), [])


# -------------------------------------------------------------------------
# Corpus dispatcher
# -------------------------------------------------------------------------


def _write_manifest(out: Path, records: list[dict]) -> None:
    (out / "manifest.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


def _make_text_pdf(path: Path, text: str) -> None:
    """Write a 'PDF' that's actually a text file we can shim around. The
    extractor calls pdftotext; we monkey-patch ``extract_pdf_text`` in the
    test instead of generating real PDFs. Padded past the 1000-byte size
    guard in ``_pdf_for_record`` (real PDFs would always be larger).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    body = text.encode("utf-8")
    pad_needed = max(0, 1100 - len(body))  # comfortably > 1000 byte threshold
    path.write_bytes(b"%PDF-1.4 fake\n" + body + b"\n" + b"%" * pad_needed)


class ExtractAnswersTests(unittest.TestCase):
    def setUp(self):
        # Monkey-patch extract_pdf_text to read the file's bytes directly so
        # we don't need a real PDF binary to test the dispatcher.
        from commoner_probe import answers as ans_mod
        self._orig = ans_mod.extract_pdf_text
        def fake_extract(p):
            try:
                data = Path(p).read_bytes()
            except OSError:
                return ""
            # Strip the fake PDF header and any trailing pad characters.
            if data.startswith(b"%PDF-1.4 fake\n"):
                data = data[len(b"%PDF-1.4 fake\n"):]
            # Pad bytes are runs of '%' at the tail; strip them.
            data = data.rstrip(b"%").rstrip(b"\n")
            return data.decode("utf-8", errors="replace")
        ans_mod.extract_pdf_text = fake_extract

    def tearDown(self):
        from commoner_probe import answers as ans_mod
        ans_mod.extract_pdf_text = self._orig

    def test_dispatches_dfg_records_to_split_dfg(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            pdf_rel = "pdfs/rs/test_dfg.pdf"
            pdf_abs = out / pdf_rel
            _make_text_pdf(pdf_abs, (
                "Body...\nOBSERVATIONS/RECOMMENDATIONS OF THE COMMITTEE\n\n"
                "1.     First. " + "x" * 100 + "\n"
                "2.     Second. " + "y" * 100 + "\n"
            ))
            _write_manifest(out, [{
                "key": "RS|finance|10",
                "kind": "committee_report",
                "report_type": "original",
                "pdf_path": pdf_rel,
            }])
            stats = extract_answers(out, log_fn=lambda *_: None)
            self.assertEqual(stats.dfg_records, 2)
            self.assertEqual(stats.atr_records, 0)
            self.assertEqual(stats.qa_records, 0)
            rows = (out / "answers.jsonl").read_text().splitlines()
            self.assertEqual(len(rows), 2)
            first = json.loads(rows[0])
            self.assertEqual(first["kind"], "dfg_recommendation")
            self.assertEqual(first["key"], "RS|finance|10")
            self.assertEqual(first["extractor"], EXTRACTOR_VERSION)

    def test_dispatches_atr_records_to_split_atr(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            pdf_rel = "pdfs/rs/test_atr.pdf"
            pdf_abs = out / pdf_rel
            _make_text_pdf(pdf_abs, (
                "Recommendation No. 1\nRec body 1.\nReply of the Government\nReply 1.\n"
                "Recommendation No. 2\nRec body 2.\nReply of the Government\nReply 2.\n"
            ))
            _write_manifest(out, [{
                "key": "RS|finance|11",
                "kind": "committee_report",
                "report_type": "action_taken",
                "pdf_path": pdf_rel,
            }])
            stats = extract_answers(out, log_fn=lambda *_: None)
            self.assertEqual(stats.atr_records, 2)
            self.assertEqual(stats.dfg_records, 0)

    def test_dispatches_qa_records_to_split_qa(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            pdf_rel = "pdfs/ls/test_qa.pdf"
            pdf_abs = out / pdf_rel
            _make_text_pdf(pdf_abs, (
                "Question text...\n"
                "Reply by SHRI X, MINISTER OF EDUCATION:\n"
                "Answer text here, long enough to clear the confidence threshold. " * 4
            ))
            _write_manifest(out, [{
                "key": "LS|U|178|2026-03-17",
                "kind": "qa",
                "pdf_path": pdf_rel,
            }])
            stats = extract_answers(out, log_fn=lambda *_: None)
            self.assertEqual(stats.qa_records, 1)
            rows = (out / "answers.jsonl").read_text().splitlines()
            self.assertEqual(len(rows), 1)
            rec = json.loads(rows[0])
            self.assertEqual(rec["kind"], "qa_response")
            self.assertIn("Answer text", rec["answer_text"])

    def test_skips_records_without_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            _write_manifest(out, [{
                "key": "RS|finance|99", "kind": "committee_report",
                "report_type": "original",
                # pdf_path missing entirely
            }])
            stats = extract_answers(out, log_fn=lambda *_: None)
            self.assertEqual(stats.skipped_no_pdf, 1)
            self.assertEqual(stats.dfg_records, 0)


# -------------------------------------------------------------------------
# Optional integration: real PDFs from test_v4_rs (skipped if absent)
# -------------------------------------------------------------------------


_REAL_DFG_PDF = Path(__file__).resolve().parents[1] / "test_v4_rs" / "pdfs" / "rs" / "education_377.pdf"


@unittest.skipUnless(_REAL_DFG_PDF.exists(), f"real DFG PDF not available at {_REAL_DFG_PDF}")
class RealPdfIntegrationTests(unittest.TestCase):
    def test_dfg_extraction_on_real_education_377(self):
        from commoner_probe.textparse import extract_pdf_text
        text = extract_pdf_text(_REAL_DFG_PDF)
        self.assertTrue(text)
        items = split_dfg(text)
        # Real DFG reports have many numbered recommendations (typically 20+).
        # Pin only that we got > 5 to avoid brittleness against PDF revisions.
        self.assertGreater(len(items), 5)
        # Recommendations should be sequentially numbered starting at 1.
        nos = [i.recommendation_no for i in items]
        self.assertEqual(nos[0], 1)
        # Each recommendation text should be non-trivial.
        for item in items[:3]:
            self.assertGreater(len(item.recommendation_text), 80)


if __name__ == "__main__":
    unittest.main()
