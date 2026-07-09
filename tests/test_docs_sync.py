"""Docs/code consistency checks for public-facing contracts.

Tests validate narrow factual invariants to catch drift in version
strings and CLI command names. README-level assertions are deferred
until Phase 9 lands a full README.
"""

from __future__ import annotations

import re
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from commoner_probe import __version__
from commoner_probe.cli import build_parser, main

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")


class VersionSyncTests(unittest.TestCase):
    def test_pyproject_version_matches_package_version(self):
        match = re.search(r'^version = "([^"]+)"$', PYPROJECT, re.MULTILINE)
        self.assertIsNotNone(match)
        self.assertEqual(__version__, match.group(1))


class CliCommandSyncTests(unittest.TestCase):
    def test_cli_exposes_expected_subcommands(self):
        parser = build_parser()
        subcommands = set(parser._subparsers._group_actions[0].choices.keys())  # type: ignore[attr-defined]
        self.assertEqual(
            subcommands,
            {
                "sansad",
                "committees",
                "extract-answers",
                "extract-debates",
                "state-assembly",
                "state-assembly-probe",
                "mca-csr",
                "dpe-csr",
                "mines-dmft",
                "doe-pay-allowances",
                "attendance",
                "myneta",
                "budget",
                "academic-jobs",
                "debates",
                "bills",
                "indiacode",
                "atr-linkage",
                "evidence",
                "stats",
                "validate",
                "init-topic",
            },
        )

    def test_sansad_has_no_classifier_flag(self):
        parser = build_parser()
        crawl = parser._subparsers._group_actions[0].choices["sansad"]  # type: ignore[attr-defined]
        option_strings = {
            opt
            for action in crawl._actions
            for opt in action.option_strings
        }
        self.assertNotIn("--classifier", option_strings)

    def test_committees_has_no_composition_flag(self):
        parser = build_parser()
        cc = parser._subparsers._group_actions[0].choices["committees"]  # type: ignore[attr-defined]
        option_strings = {
            opt
            for action in cc._actions
            for opt in action.option_strings
        }
        self.assertNotIn("--crawl-composition", option_strings)

    def test_indiacode_legacy_list_states_invocation_still_works(self):
        out = StringIO()
        with patch("sys.argv", ["commoner-probe", "indiacode", "--list-states"]):
            with redirect_stdout(out):
                main()
        self.assertIn("\tWest Bengal", out.getvalue())


if __name__ == "__main__":
    unittest.main()
