"""A download that dies part-way must not be mistaken for a finished one.

`write_pdf` streamed straight to its final path and resumed on
`exists() and st_size > 1000`. So a Ctrl-C, a dropped connection or a full
disk inside the chunk loop left a truncated PDF that every later run accepted
as complete — and `answers.extract_answers` then pulled partial text out of
it, which is indistinguishable from a genuinely short ministry answer.

Five other writers in this package already write `.tmp` and rename
(answers, atr_linkage, dchb_town, nada, questions_list). The shared base class
that the sansad and committee crawlers use did not, nor did sansad's own
override of it.

No network: the session is a stub whose chunk stream raises where a real one
would be cut off.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from commoner_probe.base import BaseProbe
from commoner_probe.parliament_qa_api import SansadProbe
from commoner_probe.topics import TopicProfile

GOOD_PDF = b"%PDF-1.4\n" + b"x" * 4000


class _Response:
    """Yields `chunks`, then raises if `fail_after` chunks have gone out."""

    status_code = 200

    def __init__(self, body: bytes, fail_after: int | None = None) -> None:
        self._body = body
        self._fail_after = fail_after

    def raise_for_status(self) -> None:
        pass

    def iter_content(self, chunk_size: int = 16384):
        emitted = 0
        for i in range(0, len(self._body), 1024):
            if self._fail_after is not None and emitted >= self._fail_after:
                raise ConnectionError("connection reset by peer")
            yield self._body[i : i + 1024]
            emitted += 1


class _Session:
    def __init__(self, response: _Response) -> None:
        self._response = response
        self.calls = 0
        self.kwargs: dict = {}

    def get(self, url, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        return self._response


def _probe(out_dir: Path, response: _Response) -> BaseProbe:
    probe = BaseProbe(TopicProfile(name="t", description="", search_groups={}, lok_sabha_ministries=[], rajya_sabha_ministry_likes=[]), out_dir)
    probe.session = _Session(response)
    return probe


def _sansad_probe(out_dir: Path, response: _Response) -> SansadProbe:
    probe = SansadProbe(TopicProfile(name="t", description="", search_groups={}, lok_sabha_ministries=[], rajya_sabha_ministry_likes=[]), out_dir)
    probe.session = _Session(response)
    return probe


class AtomicWriteTests(unittest.TestCase):
    def test_an_interrupted_download_leaves_no_file(self):
        with TemporaryDirectory() as tmp:
            dest = Path(tmp) / "pdfs" / "doc.pdf"
            probe = _probe(Path(tmp), _Response(GOOD_PDF, fail_after=2))
            self.assertFalse(probe.write_pdf("https://example.gov.in/a.pdf", dest, {}))
            self.assertFalse(
                dest.exists(),
                "a partial download must not be left at the final path — the "
                "next run would accept it as complete",
            )

    def test_no_tmp_file_is_left_behind(self):
        with TemporaryDirectory() as tmp:
            dest = Path(tmp) / "pdfs" / "doc.pdf"
            probe = _probe(Path(tmp), _Response(GOOD_PDF, fail_after=2))
            probe.write_pdf("https://example.gov.in/a.pdf", dest, {})
            leftovers = list(dest.parent.glob("*"))
            self.assertEqual(leftovers, [], f"temp files left behind: {leftovers}")

    def test_a_retry_after_an_interruption_refetches(self):
        """The point of the whole fix: the second attempt must do the work."""
        with TemporaryDirectory() as tmp:
            dest = Path(tmp) / "pdfs" / "doc.pdf"
            _probe(Path(tmp), _Response(GOOD_PDF, fail_after=2)).write_pdf(
                "https://example.gov.in/a.pdf", dest, {}
            )
            good = _probe(Path(tmp), _Response(GOOD_PDF))
            self.assertTrue(good.write_pdf("https://example.gov.in/a.pdf", dest, {}))
            self.assertEqual(good.session.calls, 1, "the retry must actually fetch")
            self.assertEqual(dest.read_bytes(), GOOD_PDF)

    def test_a_complete_download_lands_whole(self):
        with TemporaryDirectory() as tmp:
            dest = Path(tmp) / "pdfs" / "doc.pdf"
            probe = _probe(Path(tmp), _Response(GOOD_PDF))
            self.assertTrue(probe.write_pdf("https://example.gov.in/a.pdf", dest, {}))
            self.assertEqual(dest.read_bytes(), GOOD_PDF)

    def test_an_existing_complete_file_is_not_refetched(self):
        with TemporaryDirectory() as tmp:
            dest = Path(tmp) / "pdfs" / "doc.pdf"
            dest.parent.mkdir(parents=True)
            dest.write_bytes(GOOD_PDF)
            probe = _probe(Path(tmp), _Response(GOOD_PDF))
            self.assertTrue(probe.write_pdf("https://example.gov.in/a.pdf", dest, {}))
            self.assertEqual(probe.session.calls, 0)


class ConcurrentWriterTests(unittest.TestCase):
    """Two runs sharing an output directory must not share a temp path.

    Both wrote `doc.pdf.tmp`. The slower writer's `os.replace` then published
    a file the faster one was still writing into, and its own `unlink` could
    delete the other's work in flight.
    """

    def test_two_probes_do_not_collide_on_one_temp_path(self):
        seen = []

        class _Watching(_Response):
            def iter_content(self, chunk_size=16384):
                seen.extend(p.name for p in dest.parent.glob("*.tmp*"))
                yield from _Response.iter_content(self, chunk_size)

        with TemporaryDirectory() as tmp:
            dest = Path(tmp) / "pdfs" / "doc.pdf"
            dest.parent.mkdir(parents=True)
            (dest.parent / "doc.pdf.tmp").write_bytes(b"another writer's work")
            probe = _probe(Path(tmp), _Watching(GOOD_PDF))
            self.assertTrue(probe.write_pdf("https://example.gov.in/a.pdf", dest, {}))
            self.assertEqual(dest.read_bytes(), GOOD_PDF)
            self.assertEqual(
                (dest.parent / "doc.pdf.tmp").read_bytes(),
                b"another writer's work",
                "the other writer's temp file was overwritten",
            )
            self.assertTrue(seen, "the watcher never ran")
            self.assertTrue(
                [name for name in seen if name != "doc.pdf.tmp"],
                f"the writer used the shared temp path: {seen}",
            )

    def test_sansad_override_also_uses_a_private_temp_path(self):
        with TemporaryDirectory() as tmp:
            dest = Path(tmp) / "pdfs" / "doc.pdf"
            dest.parent.mkdir(parents=True)
            (dest.parent / "doc.pdf.tmp").write_bytes(b"another writer's work")
            probe = _sansad_probe(Path(tmp), _Response(GOOD_PDF))
            self.assertTrue(probe.write_pdf("https://example.gov.in/a.pdf", dest, {}))
            self.assertEqual(
                (dest.parent / "doc.pdf.tmp").read_bytes(), b"another writer's work"
            )


class SansadOverrideTests(unittest.TestCase):
    """SansadProbe overrides write_pdf, so it needs the same guarantee.

    This is the crawler the repo is built around; fixing only the base class
    would leave its main surface with the defect.
    """

    def test_an_interrupted_download_leaves_no_file(self):
        """This override propagates the error rather than returning False.

        That difference from the base class is left alone — swallowing it here
        would be a behaviour change beyond this fix. What must hold either way
        is that nothing partial survives on disk.
        """
        with TemporaryDirectory() as tmp:
            dest = Path(tmp) / "pdfs" / "doc.pdf"
            probe = _sansad_probe(Path(tmp), _Response(GOOD_PDF, fail_after=2))
            with self.assertRaises(ConnectionError):
                probe.write_pdf("https://example.gov.in/a.pdf", dest, {})
            self.assertFalse(dest.exists())
            self.assertEqual(list(dest.parent.glob("*")), [])

    def test_a_complete_download_lands_whole(self):
        with TemporaryDirectory() as tmp:
            dest = Path(tmp) / "pdfs" / "doc.pdf"
            probe = _sansad_probe(Path(tmp), _Response(GOOD_PDF))
            self.assertTrue(probe.write_pdf("https://example.gov.in/a.pdf", dest, {}))
            self.assertEqual(dest.read_bytes(), GOOD_PDF)


if __name__ == "__main__":
    unittest.main()


class StreamingRequestTests(unittest.TestCase):
    """The ceiling only bounds memory if the body is not already in memory.

    `iter_capped` counts chunks as they arrive, but requests buffers the whole
    response unless the GET asked for `stream=True` — so the cap fired after
    the allocation it exists to prevent. Same shape as the zip-bomb fix: the
    exception must arrive before the bytes, not after.
    """

    def test_base_probe_asks_for_a_streamed_body(self):
        with TemporaryDirectory() as tmp:
            probe = _probe(Path(tmp), _Response(GOOD_PDF))
            probe.write_pdf("https://example.gov.in/a.pdf", Path(tmp) / "pdfs" / "d.pdf", {})
            self.assertTrue(probe.session.kwargs.get("stream"))

    def test_academia_download_asks_for_a_streamed_body(self):
        from commoner_probe.academia import pdf_text

        with TemporaryDirectory() as tmp:
            session = _Session(_Response(GOOD_PDF))
            path = pdf_text.download_pdf(session, "https://example.gov.in/a.pdf", Path(tmp))
            self.assertIsNotNone(path)
            self.assertEqual(path.read_bytes(), GOOD_PDF)
            self.assertTrue(session.kwargs.get("stream"))

    def test_academia_download_caps_a_requests_response(self):
        """`.content` is always present on a requests response, so the capped
        reader was never reached on the path that actually ships."""
        from commoner_probe import http_client as hc
        from commoner_probe.academia import pdf_text

        class _Oversized(_Response):
            content = b"%PDF-" + b"x" * 4000

        with TemporaryDirectory() as tmp:
            session = _Session(_Oversized(GOOD_PDF))
            original = hc.MAX_RESPONSE_BYTES
            hc.MAX_RESPONSE_BYTES = 64
            try:
                self.assertIsNone(
                    pdf_text.download_pdf(session, "https://example.gov.in/a.pdf", Path(tmp))
                )
            finally:
                hc.MAX_RESPONSE_BYTES = original
