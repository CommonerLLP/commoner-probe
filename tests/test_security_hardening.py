"""Regression tests for security hardening (base.py + runlog.py only).

Discourse/LLM endpoint tests live in the analysis repo.
Findings covered: H2 (runlog redaction), M1 (PDF dest path traversal).
"""

from __future__ import annotations

import re
import unittest

from commoner_probe.base import safe_filename_segment
from commoner_probe.runlog import _is_secret_key, _redact


# --------------------------------------------------------------------------- #
# H2 — redact key list (substring-based)                                       #
# --------------------------------------------------------------------------- #


class RedactKeyTests(unittest.TestCase):

    def test_api_key_redacted(self):
        self.assertTrue(_is_secret_key("api_key"))

    def test_apikey_camel_redacted(self):
        self.assertTrue(_is_secret_key("apiKey"))

    def test_OPENAI_API_KEY_redacted(self):
        self.assertTrue(_is_secret_key("OPENAI_API_KEY"))

    def test_secret_redacted(self):
        self.assertTrue(_is_secret_key("secret"))

    def test_access_token_redacted(self):
        self.assertTrue(_is_secret_key("access_token"))

    def test_password_redacted(self):
        self.assertTrue(_is_secret_key("password"))

    def test_innocent_keys_not_redacted(self):
        for k in ("model", "endpoint", "temperature", "channel", "topic"):
            self.assertFalse(_is_secret_key(k), f"{k!r} should not be flagged")

    def test_redact_walks_nested_dicts(self):
        obj = {
            "model": "qwen2.5",
            "config": {
                "api_key": "sk-real-secret",
                "client_secret": "shh",
                "endpoint": "http://localhost:11434/v1",
            },
        }
        out = _redact(obj)
        self.assertEqual(out["model"], "qwen2.5")
        self.assertEqual(out["config"]["api_key"], "<redacted>")
        self.assertEqual(out["config"]["client_secret"], "<redacted>")
        self.assertEqual(out["config"]["endpoint"], "http://localhost:11434/v1")

    def test_redact_walks_lists_of_dicts(self):
        obj = {"members": [{"api_key": "x"}, {"name": "y"}]}
        out = _redact(obj)
        self.assertEqual(out["members"][0]["api_key"], "<redacted>")
        self.assertEqual(out["members"][1]["name"], "y")


# --------------------------------------------------------------------------- #
# M1 — safe filename segment                                                   #
# --------------------------------------------------------------------------- #


class SafeFilenameSegmentTests(unittest.TestCase):

    SAFE_RE = re.compile(r"^[A-Za-z0-9._-]+$")

    def test_simple_alphanumeric_passes_through(self):
        self.assertEqual(safe_filename_segment("finance_18_42"), "finance_18_42")

    def test_result_is_always_in_safe_charset(self):
        for inp in (
            "../../etc/passwd",
            "/etc/shadow",
            "name with spaces",
            "name|`rm -rf /`",
            "name;injection&attack",
        ):
            seg = safe_filename_segment(inp)
            self.assertRegex(seg, self.SAFE_RE)

    def test_path_separators_neutralized(self):
        for inp in ("../../etc/passwd", "/etc/shadow", "a/b/c"):
            seg = safe_filename_segment(inp)
            self.assertNotIn("/", seg)

    def test_no_parent_directory_traversal(self):
        for inp in ("..", ".", "...", "../.."):
            seg = safe_filename_segment(inp)
            self.assertNotIn(seg, {".", ".."})

    def test_none_returns_unknown(self):
        self.assertEqual(safe_filename_segment(None), "unknown")

    def test_empty_string_returns_unknown(self):
        self.assertEqual(safe_filename_segment(""), "unknown")

    def test_int_input_stringified(self):
        self.assertEqual(safe_filename_segment(42), "42")


if __name__ == "__main__":
    unittest.main()
