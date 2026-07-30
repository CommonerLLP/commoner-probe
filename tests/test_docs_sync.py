"""Docs/code consistency checks for public-facing contracts.

Tests validate narrow factual invariants to catch drift in version
strings and CLI command names. README-level assertions are deferred
until Phase 9 lands a full README.
"""

from __future__ import annotations

import re
import shlex
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
                "census",
                "committees",
                "extract-answers",
                "extract-debates",
                "state-assembly",
                "state-assembly-probe",
                "mca-csr",
                "dpe-csr",
                "mines-dmft",
                "doe-pay-allowances",
                "ministry-ddg",
                "cag",
                "mospi",
                "courts",
                "render",
                "abhilekh-patal",
                "wayback",
                "attendance",
                "myneta",
                "prs",
                "questions-list",
                "legacy-dspace",
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

    def test_readme_shell_examples_actually_parse(self):
        """A documented command that fails at argparse is a broken interface.

        The eCourts example shipped as `--ecourts-arg --court`, which argparse
        reads as a missing value. Nothing caught it because no test had ever
        run a README command through the parser.
        """
        readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
        parser = build_parser()
        joined = readme.replace("\\\n", " ")
        commands = re.findall(r"^commoner-probe .*$", joined, re.MULTILINE)
        self.assertTrue(commands, "no commoner-probe examples found in README")
        for raw in commands:
            with self.subTest(command=raw):
                argv = shlex.split(raw)[1:]
                try:
                    parser.parse_args(argv)
                except SystemExit as exc:
                    self.fail(f"README example does not parse (exit {exc.code}): {raw}")

    def test_explicit_zero_sleep_is_not_replaced_by_the_default(self):
        """`or` treated an explicit --sleep 0 as absent and restored 2.0s,
        silently adding hours to a thousands-page run."""
        parser = build_parser()
        args = parser.parse_args(["abhilekh-patal", "--out", "x", "--query", "q", "--sleep", "0"])
        self.assertEqual(args.sleep, 0.0)
        self.assertIsNotNone(args.sleep, "None and 0.0 must stay distinguishable")
        default = parser.parse_args(["abhilekh-patal", "--out", "x", "--query", "q"])
        self.assertIsNone(default.sleep, "unset must be None so the default can apply")

    def test_indiacode_legacy_list_states_invocation_still_works(self):
        out = StringIO()
        with patch("sys.argv", ["commoner-probe", "indiacode", "--list-states"]):
            with redirect_stdout(out):
                main()
        self.assertIn("\tWest Bengal", out.getvalue())


if __name__ == "__main__":
    unittest.main()
