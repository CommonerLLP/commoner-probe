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
from types import SimpleNamespace
from unittest.mock import patch

from commoner_probe import __version__
from commoner_probe.cli import build_parser, main

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")


class VersionSyncTests(unittest.TestCase):
    def test_pyproject_version_matches_package_version(self):
        """`__version__` reads the INSTALLED metadata, not this checkout.

        So a mismatch means the environment is stale, never that the code is
        wrong, and the message has to say so: `pip install -e .` after a bump,
        and one venv shared across worktrees reports whichever tree was
        installed last.
        """
        match = re.search(r'^version = "([^"]+)"$', PYPROJECT, re.MULTILINE)
        self.assertIsNotNone(match)
        import commoner_probe
        self.assertEqual(
            __version__, match.group(1),
            f"installed metadata says {__version__}, {REPO_ROOT}/pyproject.toml says "
            f"{match.group(1)}. The package imported from "
            f"{Path(commoner_probe.__file__).parent}. Run `pip install -e .` in this "
            "tree; a venv shared across worktrees reports the last one installed.")


    def test_cli_reports_the_installed_version(self):
        """`--version` must exist and agree with the package.

        A bug report against a published CLI is unusable without it, and a
        `--version` that drifts from `__version__` is worse than none.
        """
        parser = build_parser()
        with self.assertRaises(SystemExit) as raised:
            with redirect_stdout(StringIO()) as out:
                parser.parse_args(["--version"])
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(out.getvalue().strip(), f"commoner-probe {__version__}")


class CliCommandSyncTests(unittest.TestCase):
    def test_cli_exposes_expected_subcommands(self):
        parser = build_parser()
        subcommands = set(parser._subparsers._group_actions[0].choices.keys())  # type: ignore[attr-defined]
        self.assertEqual(
            subcommands,
            {
                "sansad",
                "census",
                "nada",
                "dchb-town",
                "niti-annual-report",
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
                "doctor",
                "wayback-recover",
                "shrug",
                "go-register",
                "attendance",
                "myneta",
                "prs",
                "questions-list",
                "legacy-dspace",
                "budget",
                "academic-jobs",
                "debates",
                "bills",
                "udise-docs",
                "indiacode",
                "atr-linkage",
                "evidence",
                "stats",
                "validate",
                "init-topic",
            },
        )

    def test_readme_subcommand_count_comes_from_the_parser(self):
        """The README states a count. It has been written from memory twice and
        been wrong twice. Ask the parser."""
        parser = build_parser()
        actual = len(parser._subparsers._group_actions[0].choices)  # type: ignore[attr-defined]
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        claimed = re.search(r"(\d+)\s+subcommands", readme)
        self.assertIsNotNone(claimed, "README no longer states a subcommand count")
        self.assertEqual(int(claimed.group(1)), actual)

    def test_readme_doc_links_are_packaged(self):
        """A README link to a repo doc dangles in the sdist unless MANIFEST.in
        ships that doc. The README is packaged; its targets must be too."""
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        included = {line.split(None, 1)[1].strip() for line in manifest.splitlines()
                    if line.startswith("include ")}
        linked = {m for m in re.findall(r"\]\((docs/[A-Za-z0-9_./-]+\.md)\)", readme)}
        self.assertTrue(linked, "README links to no docs — the guard would be vacuous")
        self.assertEqual(linked - included, set())

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
        root = Path(__file__).resolve().parents[1]
        # Both files: the command reference moved to docs/CLI.md, and reading
        # only the README silently dropped this from 55 examples to 12.
        docs = "\n".join((root / name).read_text(encoding="utf-8")
                         for name in ("README.md", "docs/CLI.md"))
        parser = build_parser()
        joined = docs.replace("\\\n", " ")
        commands = re.findall(r"^commoner-probe .*$", joined, re.MULTILINE)
        self.assertGreater(len(commands), 40, "documented examples went missing")
        for raw in commands:
            with self.subTest(command=raw):
                argv = shlex.split(raw)[1:]
                try:
                    parser.parse_args(argv)
                except SystemExit as exc:
                    self.fail(f"documented example does not parse (exit {exc.code}): {raw}")

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


class CliErrorHandlingTests(unittest.TestCase):
    """A domain error reached the user as a traceback, discarding the message
    the exception carries and printing local paths."""

    def _run(self, argv, exc):
        from commoner_probe import cli as cli_mod

        err = StringIO()
        with patch("sys.argv", ["commoner-probe", *argv]), \
             patch.object(cli_mod, "build_parser") as bp, \
             patch("sys.stderr", err):
            def _raise(_args):
                raise exc

            bp.return_value.parse_args.return_value = SimpleNamespace(
                func=_raise, traceback=False
            )
            with self.assertRaises(SystemExit) as raised:
                cli_mod.main()
        return raised.exception.code, err.getvalue()

    def test_a_domain_error_is_a_message_and_an_exit_code(self):
        from commoner_probe.ogd_resource_api import CensusApiError

        code, err = self._run(["census"], CensusApiError("refusing the shared sample key"))
        self.assertEqual(code, 1)
        self.assertIn("refusing the shared sample key", err)
        self.assertNotIn("Traceback", err)

    def test_ctrl_c_is_not_a_crash(self):
        code, err = self._run(["sansad"], KeyboardInterrupt())
        self.assertEqual(code, 130)
        self.assertNotIn("Traceback", err)

    def test_an_unexpected_error_still_reports_its_type(self):
        code, err = self._run(["sansad"], ValueError("bad range"))
        self.assertEqual(code, 1)
        self.assertIn("bad range", err)

    def test_traceback_flag_opts_back_in(self):
        from commoner_probe import cli as cli_mod
        from commoner_probe.ogd_resource_api import CensusApiError

        with patch("sys.argv", ["commoner-probe", "census", "--traceback"]), \
             patch.object(cli_mod, "build_parser") as bp:
            def _raise(_args):
                raise CensusApiError("boom")

            bp.return_value.parse_args.return_value = SimpleNamespace(
                func=_raise, traceback=True
            )
            with self.assertRaises(CensusApiError):
                cli_mod.main()
