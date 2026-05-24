"""Tests for scripts/check_leaks.py — the pre-commit / CI guard
against private-information leaks in tracked files.

These tests exercise the scan() function against synthetic file
content; they don't shell out to git (the git-integration paths
``--staged``, ``--tracked``, ``--diff`` are exercised manually
during pre-commit by the hook itself).
"""

from __future__ import annotations

import importlib.util
import re
import sys
import unittest
from pathlib import Path

# Load the script as a module without requiring it on PYTHONPATH.
_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_leaks.py"
_spec = importlib.util.spec_from_file_location("check_leaks", _SCRIPT)
assert _spec and _spec.loader
check_leaks = importlib.util.module_from_spec(_spec)
sys.modules["check_leaks"] = check_leaks
_spec.loader.exec_module(check_leaks)


def _make_patterns(*pairs: tuple[str, str]) -> list[tuple[re.Pattern[str], str]]:
    return [(re.compile(p, re.IGNORECASE), label) for p, label in pairs]


class _FakeFile:
    """Provide content for a path that scan() will look up via
    get_file_content_lines. We monkey-patch the loader for the test."""
    def __init__(self, path: str, text: str) -> None:
        self.path = path
        self.text = text


class _FakeFs:
    def __init__(self, files: dict[str, str]) -> None:
        self.files = files

    def __call__(self, path: str, *, staged: bool):
        text = self.files.get(path, "")
        for i, line in enumerate(text.splitlines(), start=1):
            yield i, line


class PublicPatternTests(unittest.TestCase):

    def test_internal_doc_cross_reference_caught(self):
        files = {"CHANGELOG.md": "See `notes/ROADMAP.md §IV` for plan.\n"}
        check_leaks.get_file_content_lines = _FakeFs(files)
        leaks = check_leaks.scan(
            ["CHANGELOG.md"],
            _make_patterns(
                (r"\bnotes/(?:ROADMAP|PRODUCT_DESIGN)\b", "internal notes/ doc cross-reference"),
            ),
            staged=False,
        )
        self.assertEqual(len(leaks), 1)
        self.assertIn("notes/ROADMAP", leaks[0].matched_text)
        self.assertEqual(leaks[0].path, "CHANGELOG.md")
        self.assertEqual(leaks[0].line_no, 1)

    def test_adp_internal_corpus_tag_caught(self):
        files = {"docs/usage.md": "Live ADP coverage: 95%\n"}
        check_leaks.get_file_content_lines = _FakeFs(files)
        leaks = check_leaks.scan(
            ["docs/usage.md"],
            _make_patterns((r"\bADP\b", "internal corpus tag")),
            staged=False,
        )
        self.assertEqual(len(leaks), 1)
        self.assertEqual(leaks[0].matched_text, "ADP")

    def test_adp_does_not_match_substring(self):
        # ADP inside a longer word (e.g. ADAPT, ADPS, ADAPTER) must not match.
        files = {"src/x.py": "def adapter(): pass  # ADAPTERS not adp_test\n"}
        check_leaks.get_file_content_lines = _FakeFs(files)
        leaks = check_leaks.scan(
            ["src/x.py"],
            _make_patterns((r"\bADP\b", "internal corpus tag")),
            staged=False,
        )
        self.assertEqual(len(leaks), 0)

    def test_bridging_knowledge_caught(self):
        files = {
            "src/dossier.py": "  # the artefact the analyst reads to make the bridging-knowledge call\n",
        }
        check_leaks.get_file_content_lines = _FakeFs(files)
        leaks = check_leaks.scan(
            ["src/dossier.py"],
            _make_patterns((r"\bbridging[- ]knowledge\b", "internal product vocabulary")),
            staged=False,
        )
        self.assertEqual(len(leaks), 1)

    def test_clean_file_passes(self):
        files = {"src/x.py": "def hello():\n    return 'world'\n"}
        check_leaks.get_file_content_lines = _FakeFs(files)
        leaks = check_leaks.scan(
            ["src/x.py"],
            _make_patterns(
                (r"\bnotes/[A-Z]\w+", "x"),
                (r"\bADP\b", "y"),
                (r"\bbridging[- ]knowledge\b", "z"),
            ),
            staged=False,
        )
        self.assertEqual(leaks, [])


class LocalPatternTests(unittest.TestCase):

    def test_local_pattern_can_match_target_name(self):
        # The local pattern file would contain something like '\bDoeName\b'.
        files = {"CHANGELOG.md": "Validate with DoeName's office.\n"}
        check_leaks.get_file_content_lines = _FakeFs(files)
        leaks = check_leaks.scan(
            ["CHANGELOG.md"],
            _make_patterns((r"\bDoeName\b", "local-pattern")),
            staged=False,
        )
        self.assertEqual(len(leaks), 1)
        self.assertEqual(leaks[0].pattern_label, "local-pattern")


class SkipPathTests(unittest.TestCase):

    def test_notes_dir_is_skipped_at_path_filter_level(self):
        # The actual list_*_files() helpers filter notes/ via SKIP_PATH_RE.
        # Verify the regex matches notes/ paths.
        for p in [
            "notes/INTERNAL_CHANGELOG.md",
            "notes/handoffs/2026-05-09T19-23Z-handoff.md",
            "notes/leak-patterns.txt",
            "data/azad-demo/manifest.jsonl",
            ".git/HEAD",
            "scripts/check_leaks.py",
            "tests/test_check_leaks.py",
            ".venv/bin/python",
        ]:
            self.assertTrue(check_leaks.SKIP_PATH_RE.search(p), p)

    def test_legitimate_paths_not_skipped(self):
        for p in [
            "CHANGELOG.md",
            "README.md",
            "src/dossier.py",
            "tests/test_dossier.py",
            "examples/topics/affirmative_action.json",
        ]:
            self.assertFalse(check_leaks.SKIP_PATH_RE.search(p), p)


class MultiplePatternsTests(unittest.TestCase):

    def test_multiple_leaks_reported(self):
        files = {
            "CHANGELOG.md": (
                "Live ADP coverage: 95%\n"
                "See notes/PRODUCT_DESIGN.md §IV\n"
                "Validate with the analyst office.\n"
            ),
        }
        check_leaks.get_file_content_lines = _FakeFs(files)
        leaks = check_leaks.scan(
            ["CHANGELOG.md"],
            _make_patterns(
                (r"\bADP\b", "tag"),
                (r"\bnotes/PRODUCT_DESIGN\b", "ref"),
                (r"(?:the\s+)?analyst(?:'s)?\s+office", "framing"),
            ),
            staged=False,
        )
        self.assertEqual(len(leaks), 3)
        labels = {item.pattern_label for item in leaks}
        self.assertEqual(labels, {"tag", "ref", "framing"})


if __name__ == "__main__":
    unittest.main()
