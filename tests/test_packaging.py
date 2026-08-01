"""The published package must not carry internal-only references.

This repo is public and `commoner_probe/` is what gets built into the wheel on
PyPI. On 2026-08-01 a review found `sevent4/.secrets/keys.env` in the shipped
`census.py` — a private directory named in a public artefact, next to code that
walked every parent of `__file__` to `/` looking for it.

One occurrence was found by reading. This scans for the whole class, so the
next one fails a test instead of reaching PyPI.

Two surfaces, because they leak differently:

* ``commoner_probe/`` is built into the wheel — anything here reaches every
  installer.
* ``README.md``, ``CHANGELOG.md`` and tracked ``docs/`` are readable on GitHub
  because this repo is public, and the sdist carries some of them besides.

``docs/_archive/`` and ``notes/`` are gitignored local coordination state and
are deliberately out of scope; they are where internal detail is *supposed* to
live.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO_ROOT / "commoner_probe"

#: Substrings that must not appear in shipped source. The list is calibrated on
#: checked repo visibility, not on a hunch: naming a PUBLIC sibling discloses
#: nothing, so `partial-recall`, `sevent4`, `public-finance` and
#: `commoner-analyse` are deliberately absent and may be cited freely.
FORBIDDEN = (
    # Private or local-only sibling repos. Naming one in a public artefact
    # discloses what the org works on before the org has chosen to say so.
    # Verified 2026-08-01: academiaindia PRIVATE, theright2read PRIVATE,
    # narcotrek and twenty27 have no remote at all.
    "academiaindia",
    "theright2read",
    "narcotrek",
    "twenty27",
    # Credential locations. A library has no business knowing these.
    ".secrets",
    "keys.env",
    # Operator-specific absolute paths. The pre-commit hook covers the repo;
    # this covers the artefact.
    "/Users/",
    "/Volumes/",
)


def _shipped_sources() -> list[Path]:
    return sorted(PACKAGE_ROOT.rglob("*.py"))


def _tracked_public_docs() -> list[Path]:
    """Markdown that git tracks, so GitHub serves it, minus this test's own docs.

    Asks git rather than globbing: `docs/_archive/` is gitignored and its whole
    purpose is to hold the internal detail this test forbids elsewhere. A glob
    would flag it; `git ls-files` correctly does not see it.
    """
    out = subprocess.run(
        ["git", "ls-files", "*.md"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [REPO_ROOT / line for line in out.stdout.split("\n") if line]


def test_the_package_has_sources_to_scan():
    """Guard the guard: an empty glob would make every scan below vacuous."""
    assert len(_shipped_sources()) > 50


def test_there_are_tracked_docs_to_scan():
    """Same guard for the docs sweep — `git ls-files` returning nothing must fail."""
    assert len(_tracked_public_docs()) > 5


def _hits(paths: list[Path], needle: str) -> list[str]:
    found = []
    for path in paths:
        if path.resolve() == Path(__file__).resolve():
            continue  # this file names the markers on purpose
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if needle in line:
                found.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
    return found


@pytest.mark.parametrize("needle", FORBIDDEN)
def test_no_internal_reference_in_shipped_source(needle):
    hits = _hits(_shipped_sources(), needle)
    assert not hits, (
        f"{needle!r} appears in shipped source at {', '.join(hits)}. "
        "This package is published to PyPI from a public repo — internal repo "
        "names, credential locations and operator paths do not belong in it."
    )


@pytest.mark.parametrize("needle", FORBIDDEN)
def test_no_internal_reference_in_tracked_docs(needle):
    hits = _hits(_tracked_public_docs(), needle)
    assert not hits, (
        f"{needle!r} appears in tracked documentation at {', '.join(hits)}. "
        "This repo is public, so tracked markdown is readable by anyone. Put "
        "internal detail in notes/ or docs/_archive/, which are gitignored."
    )
