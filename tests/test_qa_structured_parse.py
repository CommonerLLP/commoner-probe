"""Tests for v0.6.5 structured Q/A parsing.

The full ``question_text`` / ``answer_text`` halves were already extracted
in v0.5.0. v0.6.5 adds structured sub-fields:

- ``question_subject``  — the all-caps topic line (e.g. "ANNUAL INCOME OF SHGS")
- ``question_stem``     — "Will the Minister of X be pleased to state:"
- ``question_body``     — the (a)/(b)/(c)/(d) sub-questions
- ``answer_minister_name`` — name from the "MINISTER OF STATE / IN THE
                              MINISTRY OF / (NAME)" preamble
- ``answer_body``       — answer text with that preamble stripped

These exist to feed the v0.7.0 ``mp-draft`` semantic-search bridge feature
(per ``ROADMAP.md``). The parsers are best-effort: each returns empty
when its anchor is missing. ``to_record()`` omits empty fields rather
than emitting placeholders.
"""

from __future__ import annotations

import unittest

from commoner_probe.answers import (
    QaExtraction,
    _parse_answer_minister_and_body,
    _parse_question_stem_and_body,
    _parse_question_subject,
    split_qa,
)

# A real-shape Q/A text fixture — boilerplate header + subject + asker
# + stem + body, then the answer half with minister preamble + body.
_REAL_QA_TEXT = """\
GOVERNMENT OF INDIA
MINISTRY OF RURAL DEVELOPMENT
DEPARTMENT OF RURAL DEVELOPMENT

LOK SABHA
UNSTARRED QUESTION NO. 1147
ANSWERED ON 13/12/2022

ANNUAL INCOME OF SHGS

1147. SHRI. RAMESH CHANDRA MAJHI:

Will the Minister of RURAL DEVELOPMENT be pleased to state:

(a) the details of the current rural development schemes;
(b) whether the Government is planning to enhance the annual income of SHGs;
(c) whether the rural products from SHGs have huge potential; and
(d) whether the Government has identified ecommerce as an effective tool?

ANSWER

MINISTER OF STATE IN THE MINISTRY OF RURAL DEVELOPMENT
(SADHVI NIRANJAN JYOTI)

(a) The Ministry of Rural Development is implementing DAY-NRLM, MGNREGA and PMGSY.
(b) Yes, the Government is enhancing income through various schemes.
(c) Yes, the rural products from SHGs have substantial market potential.
(d) Yes, e-commerce is being leveraged through SARAS portal.
"""


class ParseQuestionSubjectTests(unittest.TestCase):

    def test_real_corpus_subject_recovered(self):
        s = _parse_question_subject(_REAL_QA_TEXT.split("\nANSWER\n")[0])
        self.assertIn("ANNUAL INCOME OF SHGS", s)

    def test_returns_empty_when_no_answered_on_anchor(self):
        self.assertEqual(
            _parse_question_subject("just some random text without the anchor"),
            "",
        )

    def test_subject_truncated_at_200(self):
        # Defensive: a very long subject section shouldn't blow up the field.
        text = (
            "LOK SABHA\nANSWERED ON 13/12/2022\n\n"
            + ("VERY LONG SUBJECT " * 50)
            + "\n1. SHRI X:\nWill the Minister..."
        )
        s = _parse_question_subject(text)
        self.assertLessEqual(len(s), 200)


class ParseQuestionStemAndBodyTests(unittest.TestCase):

    def test_real_corpus_stem_extracted(self):
        question_half = _REAL_QA_TEXT.split("\nANSWER\n")[0]
        stem, body = _parse_question_stem_and_body(question_half)
        self.assertIn("Will the Minister of RURAL DEVELOPMENT", stem)
        self.assertTrue(stem.endswith(":"))

    def test_real_corpus_body_starts_at_a(self):
        question_half = _REAL_QA_TEXT.split("\nANSWER\n")[0]
        _, body = _parse_question_stem_and_body(question_half)
        self.assertTrue(body.lstrip().startswith("(a)"))
        self.assertIn("(b)", body)
        self.assertIn("(c)", body)
        self.assertIn("(d)", body)

    def test_returns_empty_when_no_stem(self):
        stem, body = _parse_question_stem_and_body("a question with no canonical stem.")
        self.assertEqual(stem, "")
        self.assertEqual(body, "")

    def test_handles_lowercase_will(self):
        # Some PDFs have inconsistent casing.
        text = "lok sabha\nanswered on 1/1/2024\n\nSUBJECT\n1. Shri X:\nwill the Minister of x be pleased to state:\n(a) some thing?"
        stem, body = _parse_question_stem_and_body(text)
        self.assertIn("be pleased to state", stem.lower())
        self.assertIn("(a)", body)


class ParseAnswerMinisterAndBodyTests(unittest.TestCase):

    def test_state_minister_with_name(self):
        ans = (
            "MINISTER OF STATE IN THE MINISTRY OF RURAL DEVELOPMENT\n"
            "(SADHVI NIRANJAN JYOTI)\n\n"
            "(a) The Ministry is implementing schemes."
        )
        name, body = _parse_answer_minister_and_body(ans)
        self.assertEqual(name, "SADHVI NIRANJAN JYOTI")
        self.assertTrue(body.startswith("(a)"))

    def test_cabinet_minister_for_x(self):
        ans = (
            "THE MINISTER OF FINANCE\n"
            "(SHRIMATI NIRMALA SITHARAMAN):\n\n"
            "The data is as follows."
        )
        name, body = _parse_answer_minister_and_body(ans)
        self.assertEqual(name, "SHRIMATI NIRMALA SITHARAMAN")
        self.assertTrue(body.startswith("The data"))

    def test_no_preamble_returns_empty_name(self):
        ans = "Just some text with no minister preamble."
        name, body = _parse_answer_minister_and_body(ans)
        self.assertEqual(name, "")
        # Body should be unchanged when no preamble found.
        self.assertEqual(body, ans)


class SplitQaIntegrationTests(unittest.TestCase):
    """End-to-end: split_qa() returns a QaExtraction with all five
    structured fields populated for a real-shape PDF text."""

    def test_full_extraction_populates_all_fields(self):
        e = split_qa(_REAL_QA_TEXT)
        self.assertIsNotNone(e)
        # Legacy fields still populated
        self.assertTrue(e.question_text)
        self.assertTrue(e.answer_text)
        # New v0.6.5 fields all populated
        self.assertIn("ANNUAL INCOME OF SHGS", e.question_subject)
        self.assertIn("Will the Minister", e.question_stem)
        self.assertIn("(a)", e.question_body)
        self.assertEqual(e.answer_minister_name, "SADHVI NIRANJAN JYOTI")
        self.assertTrue(e.answer_body.startswith("(a)"))

    def test_to_record_emits_only_populated_structured_fields(self):
        # When subject/stem/body parsing fails, to_record() should NOT
        # emit empty-string placeholders — those would lie about the
        # presence of structured data.
        e = QaExtraction(
            question_text="raw q",
            answer_text="raw a",
            confidence=0.5,
            boundary_marker="ANSWER",
            # All structured fields default-empty.
        )
        rec = e.to_record()
        self.assertEqual(rec["question_text"], "raw q")
        self.assertEqual(rec["answer_text"], "raw a")
        self.assertNotIn("question_subject", rec)
        self.assertNotIn("question_stem", rec)
        self.assertNotIn("question_body", rec)
        self.assertNotIn("answer_minister_name", rec)
        self.assertNotIn("answer_body", rec)

    def test_to_record_emits_structured_fields_when_present(self):
        e = split_qa(_REAL_QA_TEXT)
        rec = e.to_record()
        self.assertIn("question_subject", rec)
        self.assertIn("question_stem", rec)
        self.assertIn("question_body", rec)
        self.assertIn("answer_minister_name", rec)
        self.assertIn("answer_body", rec)

    def test_split_qa_legacy_behaviour_unchanged(self):
        # Records that DON'T have a minister preamble or canonical stem
        # should still produce a usable extraction with empty structured
        # fields, not crash and not return None.
        text = (
            "Some short question text\n\n"
            "ANSWER\n\n"
            "Some short answer text without a minister preamble."
        )
        e = split_qa(text)
        self.assertIsNotNone(e)
        self.assertEqual(e.question_text, "Some short question text")
        self.assertTrue(e.answer_text.startswith("Some short answer"))
        self.assertEqual(e.question_subject, "")
        self.assertEqual(e.question_stem, "")
        self.assertEqual(e.answer_minister_name, "")


if __name__ == "__main__":
    unittest.main()
