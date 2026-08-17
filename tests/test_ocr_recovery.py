"""OCR recovery reports what it recovered, and refuses a worse result.

`extract_pdf_text(ocr=True)` returns a string. A caller cannot tell from it
whether OCR ran, whether it helped, or whether the text it got back is the
document at all. Three failure modes hide in that string:

* `chars > 0` counts a scanned page's two ligature artefacts as success. One
  corpus reported 100% answer text while 76 answers held nothing readable.
* OCR output shorter than the text layer overwrites a partial extraction with
  a worse one.
* A rasterise failure returns "" and reads as "the page had no words".

So recovery returns a typed result carrying the before and after character
counts, an acceptance flag and a reason. No OCR toolchain runs here — the OCR
step is injected.
"""

from __future__ import annotations

import pytest

from commoner_probe.answers import looks_like_answer
from commoner_probe.textparse import OcrUnavailable, recover_with_ocr

LETTERHEAD = "GOVERNMENT OF INDIA\nLOK SABHA\nUNSTARRED QUESTION NO. 2549\nANSWERED ON 23.07.2026\n"
ANSWER_BODY = LETTERHEAD + ("The Ministry has sanctioned library grants. " * 12)


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "AU2549.pdf"
    path.write_bytes(b"%PDF-1.4\n")
    return path


class TestTheResultSaysWhatHappened:
    def test_an_accepted_recovery_carries_the_text_and_both_counts(self, pdf):
        result = recover_with_ocr(pdf, "  \n", ocr=lambda: ANSWER_BODY,
                                  accept=looks_like_answer)
        assert result.accepted is True
        assert result.before == 0
        assert result.after == len(ANSWER_BODY.strip())
        assert result.gain == result.after
        assert result.text == ANSWER_BODY

    def test_a_usable_text_layer_is_never_re_ocred(self, pdf):
        calls = []

        def _ocr():
            calls.append(1)
            return ANSWER_BODY

        result = recover_with_ocr(pdf, ANSWER_BODY, ocr=_ocr, accept=looks_like_answer)
        assert result.accepted is False
        assert "already usable" in result.reason
        assert calls == [], "OCR costs orders of magnitude more; do not pay it for nothing"

    def test_ocr_no_better_than_the_text_layer_is_refused(self, pdf):
        """Overwriting a partial extraction with a worse one is a silent
        regression, so the shorter result is reported and dropped."""
        # Long enough to be worth keeping, and missing the letterhead, so it is
        # a partial extraction rather than a usable one.
        existing = "sanctioned library grants " * 20
        result = recover_with_ocr(pdf, existing, ocr=lambda: "ANSWER\nLOK SABHA\n" + "x" * 10,
                                  accept=looks_like_answer)
        assert result.accepted is False
        assert result.after < result.before
        assert "no better" in result.reason
        assert result.text == ""

    def test_ocr_that_does_not_look_like_the_document_is_refused(self, pdf):
        result = recover_with_ocr(pdf, "", ocr=lambda: "page 1 of 4" * 40,
                                  accept=looks_like_answer)
        assert result.accepted is False
        assert result.after > 0, "the count is still reported — the attempt happened"
        assert result.text == ""

    def test_empty_ocr_is_reported_as_an_attempt_that_produced_nothing(self, pdf):
        result = recover_with_ocr(pdf, "", ocr=lambda: "   ")
        assert result.accepted is False
        assert result.after == 0
        assert "produced nothing" in result.reason

    def test_a_missing_toolchain_reaches_the_caller(self, pdf):
        """An OCR rung that swallows a tool failure is the silent success this
        module refuses. The exception is the report."""
        def _ocr():
            raise OcrUnavailable("tesseract: not found")

        with pytest.raises(OcrUnavailable):
            recover_with_ocr(pdf, "", ocr=_ocr)

    def test_the_default_acceptance_test_is_length_not_emptiness(self, pdf):
        """Without a caller's predicate, two stray characters must not pass."""
        assert recover_with_ocr(pdf, "", ocr=lambda: "fi").accepted is False
        assert recover_with_ocr(pdf, "", ocr=lambda: "x" * 400).accepted is True


class TestLooksLikeAnAnswer:
    def test_a_plain_reply_passes(self):
        assert looks_like_answer(ANSWER_BODY) is True

    def test_two_stray_characters_fail(self):
        assert looks_like_answer("fi") is False

    def test_answered_on_is_matched(self):
        """`\\bANSWER\\b` cannot match ANSWERED — the boundary fails on the E —
        and that is the commonest heading of all."""
        text = "GOVERNMENT OF INDIA\nANSWERED ON 23.07.2026\n" + "body " * 50
        assert looks_like_answer(text) is True

    def test_a_cyrillic_substituted_letterhead_passes(self):
        """Tesseract substitutes Cyrillic В and Н for the Latin letters they are
        drawn identically to."""
        text = "GОVERNMENT ОF INDIA\nANSWER\n" + "body " * 50
        assert looks_like_answer(text) is True

    def test_a_devanagari_reply_passes(self):
        text = "भारत सरकार\nउत्तर\n" + "उत्तर का विवरण " * 30
        assert looks_like_answer(text) is True

    def test_an_annexure_page_without_a_letterhead_fails(self):
        """A stray annexure passes a length check and is not the answer.
        Treating it as one silently truncates the record."""
        assert looks_like_answer("Statement referred to in reply " + "row " * 80) is False
