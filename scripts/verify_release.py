#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Verify a released version is actually installable from PyPI.

Usage:  python3 scripts/verify_release.py 0.12.0

Why this exists
---------------
A green release workflow is not proof the package is published, and a single
read of PyPI's top-level JSON is not proof either. On 2026-07-31, twenty seconds
after a successful publish of 0.12.0, `https://pypi.org/pypi/commoner-probe/json`
still reported `0.11.0` with no 0.12.0 files — the release was fine and the index
read was stale. The same trap bit in the other direction at 0.10.0, where a
cached read showed the PREVIOUS version and made a publish look done.

So this does not trust any single index read. The proof is that a fresh
environment can install the exact version from PyPI and import it — which is the
only thing a consumer actually cares about, and which cannot pass while the
files are unserved.

Exits non-zero, loudly, if the version is not installable.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT = "commoner-probe"
IMPORT_NAME = "commoner_probe"
ATTEMPTS = 6
BACKOFF_SEC = 15


def _version_endpoint(version: str) -> dict | None:
    """The version-specific JSON route. 404 until the release is served."""
    url = f"https://pypi.org/pypi/{PROJECT}/{version}/json?cb={time.time()}"
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _install_check(version: str) -> str:
    """Install the exact version from PyPI into a throwaway venv and import it.

    This is the load-bearing check. An index read can be stale in either
    direction; an install cannot succeed unless the artefacts are really served.
    """
    with tempfile.TemporaryDirectory() as tmp:
        venv = Path(tmp) / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, capture_output=True)
        pip = venv / "bin" / "pip"
        python = venv / "bin" / "python"
        subprocess.run(
            [str(pip), "install", "-q", f"{PROJECT}=={version}"],
            check=True,
            capture_output=True,
        )
        result = subprocess.run(
            [str(python), "-c", f"import {IMPORT_NAME}; print({IMPORT_NAME}.__version__)"],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <version>", file=sys.stderr)
        return 2
    version = argv[1]

    payload = None
    for attempt in range(1, ATTEMPTS + 1):
        payload = _version_endpoint(version)
        if payload:
            break
        print(
            f"  {PROJECT} {version} not yet served ({attempt}/{ATTEMPTS}) — "
            f"waiting {BACKOFF_SEC}s. An index read lags a publish; this is not "
            "yet a failure.",
            file=sys.stderr,
        )
        if attempt < ATTEMPTS:
            time.sleep(BACKOFF_SEC)

    if not payload:
        print(
            f"FAIL: {PROJECT} {version} is not on PyPI after "
            f"{ATTEMPTS * BACKOFF_SEC}s. Check the release workflow log for the "
            "'Publish to PyPI' step before assuming a cache issue.",
            file=sys.stderr,
        )
        return 1

    for file_info in payload.get("urls", []):
        print(f"  {file_info['packagetype']:<12} {file_info['filename']}  {file_info['size']} bytes")

    installed = _install_check(version)
    if installed != version:
        print(
            f"FAIL: installed {PROJECT} reports __version__ {installed!r}, expected {version!r}",
            file=sys.stderr,
        )
        return 1

    print(f"OK: {PROJECT} {version} installs from PyPI and reports __version__ {installed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
