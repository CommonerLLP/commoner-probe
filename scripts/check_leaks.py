#!/usr/bin/env python3
"""Pre-commit / CI guard: refuse to land tracked-file content that leaks
private context.

Two pattern sources:

1. **Public patterns** — hardcoded below. Uncontroversial structural
   tells of internal documentation that should never appear in a public
   tracked file: cross-references to gitignored ``notes/`` documents,
   internal corpus tags that have already been exposed historically
   (``ADP``), and internal product-vocabulary terms that read as a tell.

2. **Local patterns** — loaded from ``notes/leak-patterns.txt`` if the
   file exists. One regex per line; ``#`` comments and blank lines are
   ignored. Use this for your own target-MP names, internal corpus
   tags you don't want exposed, etc. The file lives under ``notes/``
   which is gitignored — so the *list itself* never leaves the laptop.

Modes
-----

* ``--staged``   scan only the staged diff (default for pre-commit).
* ``--tracked``  scan all currently-tracked files (default for CI).
* ``--diff REF`` scan the diff against ``REF`` (e.g. ``origin/main``).

Exit code 0 = clean. Nonzero = at least one leak found; offending
matches printed to stderr with file:line:pattern.

Skipped files (always): the script itself, anything under ``notes/``
(gitignored anyway, but extra-safe), anything under ``data/``, the
``.git`` tree.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent

# Public patterns — committed to the repo. Keep this list uncontroversial:
# only patterns that are themselves not target-revealing.
PUBLIC_PATTERNS: list[tuple[str, str]] = [
    # Internal-doc cross-references. Public consumers can't follow
    # these, and the structure of the path (§IV.5, etc.) leaks the
    # internal document organisation.
    (r"\bnotes/(?:ROADMAP|PRODUCT_DESIGN|TECHDEBT|PRINCIPLES|INTERNAL_CHANGELOG|state-of-brain|handoffs)\b",
     "internal notes/ doc cross-reference"),
    # Internal corpus tag that has already been exposed historically.
    # Future commits should never re-introduce it.
    (r"\bADP\b", "internal corpus tag"),
    # Internal product-design vocabulary that reads as a tell when it
    # appears in public source / docs / tests / commits.
    (r"\bbridging[- ]knowledge\b", "internal product vocabulary"),
    (r"\bdeflection[- ]grammar\b", "internal product vocabulary"),
    (r"\bopposition\s+MP\b", "research-narrative framing"),
    (r"\b(?:the\s+)?analyst(?:'s)?\s+(?:office|reads|applies)", "research-narrative framing"),
]

# Local patterns file path (gitignored).
LOCAL_PATTERNS_PATH = REPO_ROOT / "notes" / "leak-patterns.txt"

# Files / paths never scanned: this script itself, gitignored areas,
# and binary fixtures.
SKIP_PATH_RE = re.compile(
    r"(?:^|/)("
    r"\.git/"
    r"|\.venv/"
    r"|notes/"           # gitignored — extra-safe to skip
    r"|data/"            # gitignored
    r"|build/"
    r"|.*\.egg-info/"
    r"|__pycache__/"
    r"|.*\.pyc$"
    r"|.*\.png$|.*\.jpg$|.*\.jpeg$|.*\.gif$|.*\.pdf$"
    r"|scripts/check_leaks\.py$"
    r"|tests/test_check_leaks\.py$"  # tests legitimately contain pattern fixtures
    r")"
)


@dataclass
class Leak:
    path: str
    line_no: int
    pattern_label: str
    matched_text: str

    def format(self) -> str:
        return (
            f"{self.path}:{self.line_no}: [{self.pattern_label}] "
            f"{self.matched_text!r}"
        )


def load_patterns() -> list[tuple[re.Pattern[str], str]]:
    compiled: list[tuple[re.Pattern[str], str]] = []
    for raw, label in PUBLIC_PATTERNS:
        compiled.append((re.compile(raw, re.IGNORECASE), label))
    if LOCAL_PATTERNS_PATH.exists():
        for line in LOCAL_PATTERNS_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                compiled.append((re.compile(line, re.IGNORECASE), "local-pattern"))
            except re.error as exc:
                sys.stderr.write(
                    f"check_leaks: ignoring invalid local pattern {line!r}: {exc}\n"
                )
    return compiled


def list_staged_files() -> list[str]:
    out = subprocess.check_output(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        cwd=REPO_ROOT, text=True,
    )
    return [p for p in out.splitlines() if p and not SKIP_PATH_RE.search(p)]


def list_tracked_files() -> list[str]:
    out = subprocess.check_output(
        ["git", "ls-files"], cwd=REPO_ROOT, text=True,
    )
    return [p for p in out.splitlines() if p and not SKIP_PATH_RE.search(p)]


def list_diff_files(ref: str) -> list[str]:
    out = subprocess.check_output(
        ["git", "diff", "--name-only", "--diff-filter=ACMR", f"{ref}...HEAD"],
        cwd=REPO_ROOT, text=True,
    )
    return [p for p in out.splitlines() if p and not SKIP_PATH_RE.search(p)]


def get_file_content_lines(path: str, *, staged: bool) -> Iterable[tuple[int, str]]:
    """Yield (line_no, line) for the file content. Uses git's view of
    the staged content when ``staged=True`` so pre-commit catches the
    *about-to-land* version, not the worktree."""
    abs_path = REPO_ROOT / path
    if staged:
        try:
            content = subprocess.check_output(
                ["git", "show", f":{path}"], cwd=REPO_ROOT,
            )
        except subprocess.CalledProcessError:
            return
    else:
        try:
            content = abs_path.read_bytes()
        except (FileNotFoundError, IsADirectoryError):
            return
    try:
        text = content.decode("utf-8", errors="replace")
    except Exception:
        return
    for i, line in enumerate(text.splitlines(), start=1):
        yield i, line


def scan(
    files: Iterable[str],
    patterns: list[tuple[re.Pattern[str], str]],
    *,
    staged: bool,
) -> list[Leak]:
    leaks: list[Leak] = []
    for path in files:
        for line_no, line in get_file_content_lines(path, staged=staged):
            for pat, label in patterns:
                m = pat.search(line)
                if m:
                    leaks.append(Leak(
                        path=path,
                        line_no=line_no,
                        pattern_label=label,
                        matched_text=m.group(0),
                    ))
    return leaks


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--staged", action="store_true",
                   help="Scan staged diff only (default for pre-commit).")
    g.add_argument("--tracked", action="store_true",
                   help="Scan all currently-tracked files (default for CI).")
    g.add_argument("--diff", metavar="REF",
                   help="Scan files changed against REF (e.g. origin/main).")
    args = p.parse_args(argv)

    if args.staged:
        files = list_staged_files()
        staged = True
    elif args.diff:
        files = list_diff_files(args.diff)
        staged = False
    else:
        files = list_tracked_files()
        staged = False

    patterns = load_patterns()
    if not patterns:
        sys.stderr.write("check_leaks: no patterns loaded; nothing to do.\n")
        return 0

    leaks = scan(files, patterns, staged=staged)
    if not leaks:
        return 0

    sys.stderr.write(f"check_leaks: found {len(leaks)} leak(s):\n")
    for leak in leaks:
        sys.stderr.write(f"  {leak.format()}\n")
    sys.stderr.write(
        "\nThese patterns are forbidden in tracked files. "
        "Move private context to notes/ (gitignored) and use generic "
        "phrasings in public-tracked files.\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
