"""Tests for the OCR fallback on acquisition-time PDF text extraction.

Why this lives in commoner-probe and not the retrieval engine: this runs at
acquisition, on a PDF this repo just downloaded, to produce the text a
downstream analysis repo consumes. `textparse.extract_pdf_text` is already a
fallback chain (pdftotext -> pdfminer) used by nine modules; OCR is the next
rung, not a new capability layer.

The measurement that motivates it (2026-07-28, Gujarat NeVA corpus): the
`low`-quality documents are NOT scans. All 30 sampled carry a Gujarati Unicode
text layer, but the font subset's ToUnicode cmap is partially shifted, so a
minority of codepoints are wrong and no doc-wide substitution can repair them
(a substring-repair prototype recovered 1 of 110). The glyphs *render*
correctly, so rasterizing gives a pristine image and OCR reads what a reader
reads. Head to head on 30 low documents, similarity of the title line to the
portal subject: OCR 0.993 median vs 0.942 for the text layer, better on 28.

No tesseract or poppler is invoked here; the toolchain is injected.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from commoner_probe.textparse import OcrUnavailable, ocr_pdf_text


class FakeRun:
    """Records the argv it was handed and replays a scripted result."""

    def __init__(self, *, stdout=b"", returncode=0, make_png=True, raises=None):
        self.stdout = stdout
        self.returncode = returncode
        self.make_png = make_png
        self.raises = raises
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if self.raises is not None:
            raise self.raises
        if argv[0] == "pdftoppm":
            if self.make_png:
                # argv[-1] is the output prefix; poppler appends -N.png
                Path(f"{argv[-1]}-1.png").write_bytes(b"\x89PNG fake")
            return type("R", (), {"returncode": 0, "stdout": b"", "stderr": b""})()
        return type("R", (), {"returncode": self.returncode, "stdout": self.stdout, "stderr": b""})()


def test_returns_the_ocr_text(tmp_path):
    pdf = tmp_path / "q.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    run = FakeRun(stdout="રાજયમાં ભૂ-રાસાયણિક સંશોધન બાબત\n".encode())

    assert ocr_pdf_text(pdf, page=1, runner=run) == "રાજયમાં ભૂ-રાસાયણિક સંશોધન બાબત\n"


def test_renders_then_reads_in_that_order(tmp_path):
    pdf = tmp_path / "q.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    run = FakeRun(stdout=b"text")

    ocr_pdf_text(pdf, page=1, runner=run)

    assert [c[0] for c in run.calls] == ["pdftoppm", "tesseract"]


def test_language_and_dpi_reach_the_toolchain(tmp_path):
    """Gujarati OCR with the default English model is the failure mode that
    made an existing one-off in a sibling repo useless for this corpus."""
    pdf = tmp_path / "q.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    run = FakeRun(stdout=b"text")

    ocr_pdf_text(pdf, page=3, dpi=400, lang="guj", runner=run)

    render, read = run.calls
    assert "-r" in render and render[render.index("-r") + 1] == "400"
    assert render[render.index("-f") + 1] == "3"
    assert render[render.index("-l") + 1] == "3"
    assert "-l" in read and read[read.index("-l") + 1] == "guj"


def test_interword_spaces_are_preserved(tmp_path):
    """Column geometry is what the NeVA Q/A splitter cuts on.

    Without this flag tesseract collapses the gap and the two-column header
    extracts as `પ્રશ્ન જવાબ` — measured max run of spaces 0, and the splitter
    finds no boundary. With it the gap survives at 35 spaces. Better characters
    in an unparseable layout are worth nothing.
    """
    pdf = tmp_path / "q.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    run = FakeRun(stdout=b"text")

    ocr_pdf_text(pdf, page=1, runner=run)

    read = run.calls[1]
    assert "preserve_interword_spaces=1" in read
    assert read[read.index("preserve_interword_spaces=1") - 1] == "-c"


def test_a_page_that_renders_to_nothing_returns_empty_not_a_crash(tmp_path):
    pdf = tmp_path / "q.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    run = FakeRun(stdout=b"unused", make_png=False)

    assert ocr_pdf_text(pdf, page=1, runner=run) == ""


def test_a_missing_toolchain_raises_and_names_the_tool(tmp_path):
    """Silent empty text here would read as 'the document had no words',
    which is the silent-success failure this repo keeps having to fix."""
    pdf = tmp_path / "q.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    run = FakeRun(raises=FileNotFoundError("pdftoppm"))

    with pytest.raises(OcrUnavailable, match="pdftoppm"):
        ocr_pdf_text(pdf, page=1, runner=run)


def test_scratch_files_do_not_survive(tmp_path):
    pdf = tmp_path / "q.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    run = FakeRun(stdout=b"text")

    ocr_pdf_text(pdf, page=1, runner=run)

    assert list(tmp_path.glob("*.png")) == []


def test_scratch_files_do_not_survive_a_failure(tmp_path):
    pdf = tmp_path / "q.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    class DiesAfterRender(FakeRun):
        def __call__(self, argv, **kwargs):
            self.calls.append(list(argv))
            if argv[0] == "pdftoppm":
                Path(f"{argv[-1]}-1.png").write_bytes(b"\x89PNG fake")
                return type("R", (), {"returncode": 0, "stdout": b"", "stderr": b""})()
            raise FileNotFoundError("tesseract")

    with pytest.raises(OcrUnavailable):
        ocr_pdf_text(pdf, page=1, runner=DiesAfterRender())

    assert list(tmp_path.glob("*.png")) == []


class TestNevaOcrWiring:
    """`extract_neva_answers(ocr=True)` — the acceptance gate is the point.

    OCR text is accepted ONLY where it recovers the portal subject that the
    embedded text layer could not. An OCR pass that also fails proves nothing
    and must not overwrite the record with different unverified text.
    """

    SUBJECT = "રાજયમાં ભૂ-રાસાયણિક સંશોધન બાબત"
    # A minimal NeVA body: the two-column header plus one item on each side.
    BODY = "પ્રશ્ન                જવાબ\n (1) પ્રશ્ન લખાણ            (1) હા,\n"

    def _corpus(self, tmp_path: Path) -> Path:
        import json

        (tmp_path / "q.pdf").write_bytes(b"%PDF-1.4 fake")
        (tmp_path / "questions.jsonl").write_text(
            json.dumps({"key": "GJ|1", "pdf_path": "q.pdf", "subject": self.SUBJECT}) + "\n",
            encoding="utf-8",
        )
        return tmp_path

    def _run(self, monkeypatch, tmp_path, *, layer_text, ocr_text):
        from commoner_probe import neva_text as mod

        monkeypatch.setattr("commoner_probe.textparse.extract_pdf_text", lambda p: layer_text)
        monkeypatch.setattr(
            "commoner_probe.textparse.ocr_pdf_text",
            lambda p, **kw: ocr_text,
        )
        monkeypatch.setattr("commoner_probe.textparse.ocr_toolchain_missing", lambda: [])
        return mod.extract_neva_answers(self._corpus(tmp_path), log_fn=lambda *_: None, ocr=True)

    def _records(self, tmp_path):
        import json

        return [json.loads(x) for x in (tmp_path / "answers.jsonl").read_text().splitlines() if x.strip()]

    def test_ocr_recovers_a_low_document(self, monkeypatch, tmp_path):
        stats = self._run(
            monkeypatch, tmp_path,
            layer_text="રાજયમાાં ભૂ-રાિાયસ્ણક િંશોધન બાબત\n" + self.BODY,   # cmap-shifted
            ocr_text=self.SUBJECT + "\n" + self.BODY,                        # clean render
        )
        assert stats.ocr_recovered == 1
        assert stats.ocr_attempted_unrecovered == 0
        assert stats.quality_counts.get("ocr") == 1

        rec = self._records(tmp_path)[0]
        assert rec["quality"] == "ocr"
        assert rec["text_source"] == "ocr"

    def test_ocr_that_also_fails_leaves_the_record_alone(self, monkeypatch, tmp_path):
        """The honest outcome: still `low`, still the text layer, nothing invented."""
        stats = self._run(
            monkeypatch, tmp_path,
            layer_text="રાજયમાાં ભૂ-રાિાયસ્ણક િંશોધન બાબત\n" + self.BODY,
            ocr_text="ગગગ ગગગ ગગગ ગગગ\n" + self.BODY,                        # OCR garbage
        )
        assert stats.ocr_recovered == 0
        assert stats.ocr_attempted_unrecovered == 1
        assert stats.quality_counts.get("low") == 1

        rec = self._records(tmp_path)[0]
        assert rec["quality"] == "low"
        assert rec["text_source"] == "text_layer"

    def test_a_clean_document_is_never_ocrd(self, monkeypatch, tmp_path):
        """OCR costs a second a page; a document that already passes skips it."""
        calls = []

        from commoner_probe import neva_text as mod

        monkeypatch.setattr(
            "commoner_probe.textparse.extract_pdf_text",
            lambda p: self.SUBJECT + "\n" + self.BODY,
        )
        monkeypatch.setattr("commoner_probe.textparse.ocr_toolchain_missing", lambda: [])
        monkeypatch.setattr(
            "commoner_probe.textparse.ocr_pdf_text",
            lambda p, **kw: calls.append(p) or "",
        )
        stats = mod.extract_neva_answers(
            self._corpus(tmp_path), log_fn=lambda *_: None, ocr=True
        )

        assert calls == []
        assert stats.quality_counts.get("clean") == 1
        assert self._records(tmp_path)[0]["text_source"] == "text_layer"

    def test_missing_toolchain_fails_up_front_not_per_document(self, monkeypatch, tmp_path):
        from commoner_probe import neva_text as mod
        from commoner_probe.textparse import OcrUnavailable

        monkeypatch.setattr("commoner_probe.textparse.ocr_toolchain_missing", lambda: ["tesseract"])
        with pytest.raises(OcrUnavailable, match="tesseract"):
            mod.extract_neva_answers(self._corpus(tmp_path), log_fn=lambda *_: None, ocr=True)

    def test_ocr_is_off_by_default(self, monkeypatch, tmp_path):
        from commoner_probe import neva_text as mod

        monkeypatch.setattr(
            "commoner_probe.textparse.extract_pdf_text",
            lambda p: "રાજયમાાં ભૂ-રાિાયસ્ણક િંશોધન બાબત\n" + self.BODY,
        )
        monkeypatch.setattr(
            "commoner_probe.textparse.ocr_pdf_text",
            lambda p, **kw: pytest.fail("OCR must not run without --ocr"),
        )
        stats = mod.extract_neva_answers(self._corpus(tmp_path), log_fn=lambda *_: None)
        assert stats.quality_counts.get("low") == 1


def test_a_nonzero_rasterizer_exit_raises(tmp_path):
    """A malformed PDF or out-of-range page exits nonzero and writes no PNG.

    Returning "" there makes a tool failure indistinguishable from a blank
    page, and the NeVA caller then records neither an error nor an attempt.
    """
    pdf = tmp_path / "q.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    class RenderFails(FakeRun):
        def __call__(self, argv, **kwargs):
            self.calls.append(list(argv))
            return type("R", (), {"returncode": 1, "stdout": b"", "stderr": b""})()

    with pytest.raises(OcrUnavailable, match="pdftoppm exited 1"):
        ocr_pdf_text(pdf, page=99, runner=RenderFails())


def test_ocr_quality_and_text_source_survive_schema_and_typed_api():
    """A successful OCR run must produce output `validate` accepts and the
    typed API preserves. Both were broken on merge (Codex, PR #77)."""
    jsonschema = pytest.importorskip("jsonschema")
    from commoner_probe import schemas
    from commoner_probe.records import AnswerNevaQaResponse, NevaDistrictRowRecord

    row = {
        "key": "GJ|1", "kind": "neva_district_row", "source_pdf": "q.pdf",
        "extracted_at": "2026-07-28T00:00:00", "district": "સુરત",
        "figures": [12.0], "primary_figure": 12.0, "raw_line": "સુરત 12",
        "quality": "ocr", "text_source": "ocr", "extractor": "neva-gu-v1",
    }
    jsonschema.validate(row, schemas.load("neva_district_row"))
    assert NevaDistrictRowRecord.from_dict(row).text_source == "ocr"

    qa = {
        "key": "GJ|1", "kind": "neva_qa_response", "source_pdf": "q.pdf",
        "extracted_at": "2026-07-28T00:00:00", "question_text": "પ્રશ્ન",
        "answer_text": "જવાબ", "confidence": 1.0, "quality": "ocr",
        "text_source": "ocr", "extractor": "neva-gu-v1",
    }
    jsonschema.validate(qa, schemas.load("answers_neva_qa_response"))
    assert AnswerNevaQaResponse.from_dict(qa).text_source == "ocr"
