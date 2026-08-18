# SPDX-License-Identifier: MIT
"""Report the three versions that are supposed to agree, and say when they do not.

``importlib.metadata`` serves the version recorded when the package was INSTALLED,
not the one in the source tree in front of you. Three numbers therefore exist at
once, and each is read by something different:

* the **source** version in ``pyproject.toml``, which is what a release will carry;
* the **installed** version in the environment's metadata, which is what
  ``__version__``, the outbound User-Agent and every run log report;
* the **declared pin** in a consumer's requirements file, which is what a
  reviewer reads to learn which version that repo runs.

They drift silently, and each drift misleads in its own direction. This repo's own
version test failed for an unknown period reading 0.14.7 against a source of
0.14.6 — a shared venv reports whichever worktree was installed last. And one
consumer ran 0.13.0 against a declared pin of 0.14.3, so a reviewer reading the pin
would have credited it with a parser fix it was not running.

Nothing here is a guess. A number this module cannot read is reported as unknown,
never as agreement.
"""

from __future__ import annotations

import re
from pathlib import Path

__all__ = ["VersionReport", "declared_pins", "installed_version", "source_version",
           "version_report"]

#: Every form the org's consumers actually use, measured against the seven live
#: pin files on 2026-08-17. The requirement does not have to start the line,
#: because a `pyproject.toml` writes it quoted inside a dependency list, and
#: extras are optional, because four of the seven carry them
#: (`commoner-probe[http,pdf]==0.14.3`). The first reader required both and found
#: one pin where three existed.
#: It must still open a requirement token. `my-commoner-probe==9.9.9` is a
#: different package, and `description = "built for commoner-probe==9.9.9"` is
#: prose; each was read as this package's pin, and `doctor` then failed a
#: consumer that never depended on it.
_EXTRAS = r"(?:\[[^\]]*\])?"
_NAME = rf"commoner[-_]probe{_EXTRAS}"
#: A requirement token opens a line, or opens a quoted element of an array.
#: Prose names the package mid-sentence, and TOML names it on both sides of a
#: scalar assignment — `name = "commoner-probe"` and the console-script key —
#: so an opening quote alone is not enough. Only `[` and `,` open a dependency
#: list.
_TOKEN = r"(?:^|[\[,]\s*[\"'])\s*"
#: The git form carries the name inside a URL, so a `/` opens it too.
_URL_TOKEN = r"(?:^|[\[,]\s*[\"']|/)\s*"
_PIN_PATTERNS = (
    re.compile(rf"{_TOKEN}{_NAME}\s*==\s*([0-9][^\s;#,\"']*)", re.I | re.MULTILINE),
    re.compile(rf"{_URL_TOKEN}commoner-probe(?:\.git)?{_EXTRAS}@v?([0-9][^\s;#\"']*)",
               re.I | re.MULTILINE),
)

#: The package named as a requirement with no exact version. The org requires an
#: exact pin, so this is a finding rather than an absence — reporting nothing
#: filed it beside the files that never mention the package at all. Unanchored,
#: like the pin patterns, because the compact TOML form
#: `dependencies = ["commoner-probe>=0.14"]` is valid and a start-of-line test
#: could not reach it: a violated pin policy then exited successfully.
#:
#: Three shapes, and no fourth. A range operator follows the name, or the name
#: is the whole line, or the name is a whole quoted element of a list. Accepting
#: any closing quote made `description = "built on commoner-probe"` a dependency
#: and `commoner-probe = "commoner_probe.cli:main"` a dependency, and neither
#: prose nor an entry-point key declares one.
_UNPINNED_PATTERNS = (
    re.compile(rf"{_TOKEN}{_NAME}\s*(?:[<>~!]=|[<>@])", re.I | re.MULTILINE),
    re.compile(rf"^\s*{_NAME}\s*(?:;|$)", re.I | re.MULTILINE),
    re.compile(rf"[\[,]\s*[\"']\s*{_NAME}\s*(?:;[^\"']*)?[\"']", re.I | re.MULTILINE),
)

#: A `pyproject.toml` holds arrays that are not dependency lists — `keywords`,
#: `classifiers`, `packages` — and a requirement-shaped string in one of them
#: declares nothing. Only these keys carry requirements: `dependencies` and
#: `requires` by name, and every key of an optional-dependency or
#: dependency-group table.
_DEP_KEYS = frozenset({"dependencies", "requires", "requires-dist"})
_DEP_TABLES = ("optional-dependencies", "dependency-groups")
_TABLE_HEADER = re.compile(r"^\s*\[([^\]]+)\]\s*$", re.MULTILINE)
_ARRAY_KEY = re.compile(r"^\s*([A-Za-z_][\w.-]*)\s*=\s*\[", re.MULTILINE)


def _dependency_arrays(text: str) -> list[str]:
    """Every dependency array in a TOML text, brackets included.

    Bracket counting rather than a TOML parser: `tomllib` arrived in 3.11 and
    this package supports 3.10, and an extras marker (`commoner-probe[http]`)
    nests one balanced pair inside a string.
    """
    tables = [(m.start(), m.group(1).strip()) for m in _TABLE_HEADER.finditer(text)]
    out: list[str] = []
    for match in _ARRAY_KEY.finditer(text):
        table = ""
        for start, name in tables:
            if start < match.start():
                table = name
            else:
                break
        declares = (match.group(1) in _DEP_KEYS
                    or any(table.endswith(t) for t in _DEP_TABLES))
        if not declares:
            continue
        depth, i, quote = 0, match.end() - 1, ""
        while i < len(text):
            char = text[i]
            # A bracket inside a string is data, not structure. A marker may
            # legally hold one — `"other; platform_version == \']\'"` — and
            # counting it ended the array early, so every later dependency fell
            # outside the search and an unpinned one was reported as absent.
            if quote:
                if char == quote:
                    quote = ""
            elif char in "\"'":
                quote = char
            elif char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0:
                    out.append(text[match.end() - 1:i + 1])
                    break
            i += 1
    return out


_TOML_STRING = re.compile(r"\"([^\"]*)\"|'([^']*)'")


def _declarations(text: str, *, toml: bool) -> list[str]:
    """The requirement declarations in a file, one string each.

    A requirements file declares one per line. A `pyproject.toml` declares one
    per quoted element of a dependency array, and the rest of the file is
    metadata that must not be read as a requirement.

    One declaration at a time is what keeps the two pattern sets apart: a git
    pin holds both an unpinned shape and an exact one, so classifying a whole
    file at once cannot tell "pinned here, unpinned there" from "both in the
    same requirement".
    """
    if not toml:
        return text.splitlines()
    out: list[str] = []
    for array in _dependency_arrays(text):
        for double, single in _TOML_STRING.findall(array):
            out.append(double or single)
    return out


def _uncommented(text: str) -> str:
    """The text with comment tails removed.

    `search()` returns the FIRST occurrence, so a commented old pin above an
    active one won: `doctor` reported a mismatch that did not exist and exited 1.
    A `#` counts as a comment when it opens a line or follows whitespace, which
    is how both requirements files and TOML write one.
    """
    return re.sub(r"(?m)(?:^|(?<=\s))#.*$", "", text)


class VersionReport:
    """The three versions, and whether they agree.

    ``agrees`` is False only where two numbers are both KNOWN and differ. An
    unknown number cannot disagree, and reporting it as a mismatch would send a
    reader to fix a file that is not the problem.
    """

    def __init__(self, source: str | None, installed: str | None,
                 pins: dict[str, str] | None = None) -> None:
        self.source = source
        self.installed = installed
        self.pins = dict(pins or {})

    @property
    def mismatches(self) -> list[str]:
        out: list[str] = []
        if self.source and self.installed and self.source != self.installed:
            out.append(
                f"the source tree says {self.source} and the installed metadata says "
                f"{self.installed}. Every run log, the outbound User-Agent and "
                "__version__ report the INSTALLED number. Run `pip install -e .` in "
                "this tree; one venv shared across worktrees reports whichever was "
                "installed last.")
        for where, pin in self.pins.items():
            if pin == "unpinned":
                out.append(
                    f"{where} names this package with no exact version. The org pins "
                    "with == or @vX.Y.Z, because a range moves under the consumer "
                    "without anyone deciding to move it.")
                continue
            if self.installed and pin != self.installed:
                out.append(
                    f"{where} pins {pin} and the environment runs {self.installed}. A "
                    "reviewer reading the pin would credit this environment with "
                    "whatever changed between them.")
        return out

    @property
    def agrees(self) -> bool:
        return not self.mismatches

    @property
    def report(self) -> str:
        lines = [
            f"source (pyproject.toml):   {self.source or 'unknown'}",
            f"installed (metadata):      {self.installed or 'unknown'}",
        ]
        for where, pin in sorted(self.pins.items()):
            lines.append(f"declared pin ({where}): {pin}")
        if self.source is None or self.installed is None:
            lines.append(
                "UNKNOWN: a version could not be read, so nothing is established "
                "about whether they agree.")
        lines.extend(f"MISMATCH: {m}" for m in self.mismatches)
        if self.agrees and self.source and self.installed:
            lines.append("the source and the installed metadata agree.")
        return "\n".join(lines)


def source_version(pyproject: Path | str) -> str | None:
    """The version in a ``pyproject.toml``, or None when it cannot be read.

    Read with a regex rather than a TOML parser on purpose: ``tomllib`` arrived in
    Python 3.11 and this package supports 3.10, so a parser import would make the
    check unavailable on the oldest version it claims to run on.
    """
    try:
        text = Path(pyproject).read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else None


def installed_version(package: str = "commoner-probe") -> str | None:
    """The version the environment's metadata records, or None when absent."""
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as dist_version

    try:
        return dist_version(package)
    except PackageNotFoundError:
        return None


def declared_pins(*paths: Path | str) -> dict[str, str]:
    """Pins on this package found in the named requirement files.

    A file that does not exist is skipped rather than reported as unpinned. "No
    pin found" and "no file" are different facts, and only the caller knows which
    files should exist.
    """
    found: dict[str, str] = {}
    for path in paths:
        p = Path(path)
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        text = _uncommented(text)
        haystacks = _declarations(text, toml=p.suffix == ".toml")
        # Every declaration is classified, not just the first. One file can name
        # the package twice — an exact pin in `dependencies` and a range in an
        # optional group — and stopping at the first pin reported a compliant
        # file; if that pin matched the environment, `doctor` exited 0 over a
        # file that breaks the policy this check exists to enforce.
        #
        # Classification is per declaration, because the two pattern sets
        # overlap inside one requirement. A git pin reads as `commoner-probe @
        # git+...@v0.15.0`: the `@` opens an unpinned URL requirement and the
        # tag closes an exact one, in the same string. A file-wide "unpinned
        # wins" rule therefore called every git tag pin unpinned.
        pin: str | None = None
        unpinned = False
        for declaration in haystacks:
            match = next(
                (m for m in (pat.search(declaration) for pat in _PIN_PATTERNS) if m), None)
            if match:
                pin = pin if pin is not None else match.group(1)
            elif any(pat.search(declaration) for pat in _UNPINNED_PATTERNS):
                unpinned = True
        if unpinned:
            found[str(p)] = "unpinned"
        elif pin is not None:
            found[str(p)] = pin
    return found


def version_report(*, pyproject: Path | str = "pyproject.toml",
                   requirements: tuple[Path | str, ...] = ()) -> VersionReport:
    """Read all three versions and compare them."""
    return VersionReport(
        source=source_version(pyproject),
        installed=installed_version(),
        pins=declared_pins(*requirements),
    )
