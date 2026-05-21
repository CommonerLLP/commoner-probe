"""Tests for URL path encoding in BaseCrawler.write_pdf.

sansad.in's committee endpoints embed committee names with literal spaces in
the URL path. Both urllib and requests reject URLs containing unencoded
spaces. The crawler must percent-encode URLs before passing them to the HTTP
client; otherwise downloads silently fail and the manifest gains records
without their corresponding PDFs.
"""

from __future__ import annotations

import unittest

from sansad_crawler.base import _encode_url_path


class EncodeUrlPathTests(unittest.TestCase):

    def test_spaces_in_path_get_percent_encoded(self):
        url = "https://sansad.in/getFile/app/lsscommittee/Rural Development and Panchayati Raj/18_Rural_Development.pdf?source=app"
        encoded = _encode_url_path(url)
        self.assertNotIn(" ", encoded)
        self.assertIn("%20", encoded)
        self.assertIn("Rural%20Development%20and%20Panchayati%20Raj", encoded)

    def test_already_encoded_url_is_idempotent(self):
        url = "https://sansad.in/getFile/app/lsscommittee/Rural%20Development/file.pdf?source=app"
        encoded = _encode_url_path(url)
        # Should not double-encode the % into %25
        self.assertNotIn("%2520", encoded)
        self.assertEqual(encoded, url)

    def test_query_string_preserved(self):
        url = "https://sansad.in/path/file.pdf?source=app&id=42"
        encoded = _encode_url_path(url)
        self.assertIn("?source=app&id=42", encoded)

    def test_simple_url_unchanged(self):
        url = "https://sansad.in/api/q?id=1"
        self.assertEqual(_encode_url_path(url), url)

    def test_path_with_unicode_handled(self):
        # If sansad.in ever returns a path with non-ASCII characters, they
        # should be encoded as UTF-8 percent-escaped bytes.
        url = "https://sansad.in/path/स्थायी_समिति.pdf"
        encoded = _encode_url_path(url)
        self.assertNotIn("स", encoded)
        self.assertTrue(encoded.startswith("https://sansad.in/path/"))


if __name__ == "__main__":
    unittest.main()
