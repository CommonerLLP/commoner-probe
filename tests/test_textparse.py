import tempfile
import unittest
from pathlib import Path

from commoner_probe.textparse import (
    clean_htmlish,
    excerpt,
    pdf_path_for,
    read_jsonl,
    text_path_for,
)


class TextParseUtilsTests(unittest.TestCase):
    def test_clean_htmlish_strips_tags(self):
        self.assertEqual(clean_htmlish("<b>Hello</b> world"), "Hello world")

    def test_clean_htmlish_none(self):
        self.assertEqual(clean_htmlish(None), "")

    def test_excerpt_short(self):
        self.assertEqual(excerpt("hello world"), "hello world")

    def test_excerpt_long(self):
        text = "word " * 100
        result = excerpt(text, max_len=20)
        self.assertLessEqual(len(result), 25)
        self.assertTrue(result.endswith("..."))

    def test_read_jsonl_missing_file(self):
        self.assertEqual(read_jsonl(Path("/nonexistent/file.jsonl")), [])

    def test_read_jsonl_roundtrip(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "data.jsonl"
            rows = [{"key": "a"}, {"key": "b"}]
            p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            result = read_jsonl(p)
        self.assertEqual(result, rows)

    def test_text_path_for(self):
        out = Path("/tmp/out")
        rec = {"key": "LS|U|1|2026-01-01"}
        path = text_path_for(out, rec)
        self.assertTrue(str(path).startswith("/tmp/out/text/"))

    def test_pdf_path_for_missing(self):
        self.assertIsNone(pdf_path_for(Path("/tmp"), {"pdf_path": None}))


if __name__ == "__main__":
    unittest.main()
