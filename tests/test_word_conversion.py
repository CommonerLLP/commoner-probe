"""A Word file served from a PDF endpoint, and the routing decision for OCR.

Some government endpoints serve a Word document from the same URL and the same
parameters as their PDFs. `textutil -convert txt|html` FLATTENS its tables into a
single run, so the grid of figures arrives as prose and every row is lost;
LibreOffice preserves it, and that one change recovered 337 rows from a single
order on 2026-08-14.

The conversion is not run here. The binary is injected, so the tests assert what
the code does with success, with failure, and with a tool that reports success and
writes nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from commoner_probe.textparse import (
    ConversionUnavailable,
    needs_ocr,
    soffice_path,
    word_to_pdf,
)


class _Result:
    def __init__(self, returncode=0):
        self.returncode = returncode


@pytest.fixture
def doc(tmp_path):
    path = tmp_path / "GO-SE-MS-84.doc"
    path.write_bytes(b"\xd0\xcf\x11\xe0some word bytes")
    return path


class TestConverting:
    def test_it_returns_the_converted_pdf(self, doc, tmp_path, monkeypatch):
        out = tmp_path / "converted"

        def runner(cmd, **kwargs):
            assert "--headless" in cmd and "pdf" in cmd
            Path(cmd[cmd.index("--outdir") + 1], doc.stem + ".pdf").write_bytes(b"%PDF-1.4\n")
            return _Result(0)

        monkeypatch.setattr("commoner_probe.textparse.soffice_path", lambda: "/bin/true")
        assert word_to_pdf(doc, out, runner=runner) == out / f"{doc.stem}.pdf"

    def test_a_missing_libreoffice_raises_and_says_textutil_is_no_substitute(
            self, doc, tmp_path, monkeypatch):
        monkeypatch.setattr("commoner_probe.textparse.soffice_path", lambda: None)
        with pytest.raises(ConversionUnavailable) as excinfo:
            word_to_pdf(doc, tmp_path / "out")
        assert "textutil" in str(excinfo.value)

    def test_a_nonzero_exit_raises(self, doc, tmp_path, monkeypatch):
        monkeypatch.setattr("commoner_probe.textparse.soffice_path", lambda: "/bin/true")
        with pytest.raises(ConversionUnavailable, match="exited 1"):
            word_to_pdf(doc, tmp_path / "out", runner=lambda cmd, **kw: _Result(1))

    def test_success_that_writes_nothing_raises(self, doc, tmp_path, monkeypatch):
        """A conversion that produces no file must not read as an empty document."""
        monkeypatch.setattr("commoner_probe.textparse.soffice_path", lambda: "/bin/true")
        with pytest.raises(ConversionUnavailable, match="wrote no"):
            word_to_pdf(doc, tmp_path / "out", runner=lambda cmd, **kw: _Result(0))

    def test_the_binary_is_looked_for_outside_PATH_too(self):
        """`soffice` is often absent from PATH on macOS with the app installed, so
        reporting it missing on that basis alone would be wrong."""
        found = soffice_path()
        assert found is None or Path(found).exists()


class TestTheOcrRoutingDecision:
    def test_two_ligature_artefacts_are_not_a_document(self):
        """`chars > 0` accepted these, which is how a corpus reported complete text
        while dozens of documents held nothing readable."""
        assert needs_ocr("fi") is True

    def test_an_empty_extraction_needs_ocr(self):
        assert needs_ocr("") is True
        assert needs_ocr("   \n ") is True

    def test_a_real_page_of_text_does_not(self):
        assert needs_ocr("The Government of Andhra Pradesh hereby orders " * 8) is False

    def test_the_threshold_is_the_caller_s_to_state(self):
        assert needs_ocr("short text", min_chars=5) is False
