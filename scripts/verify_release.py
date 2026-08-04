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
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


class IndexLag(RuntimeError):
    """pip could not see the version yet. Retryable, unlike a broken package.

    pip reads the *simple* index; the retry loop below watches the version-JSON
    endpoint. The two do not update together, so the JSON route can answer 200
    while `pip install` still reports "no matching distribution". Both 0.12.1
    and 0.13.0 reported FAIL here and passed unchanged 45 seconds later.
    """


#: Substrings pip prints when the requested version is not on the index it
#: reads. Anything else — a build failure, a dependency conflict, an import
#: error — is a real failure and must not be retried for minutes.
INDEX_LAG_MARKERS = (
    "No matching distribution found",
    "Could not find a version that satisfies the requirement",
)


def _is_index_lag(stderr: str) -> bool:
    return any(marker in (stderr or "") for marker in INDEX_LAG_MARKERS)


PROJECT = "commoner-probe"
IMPORT_NAME = "commoner_probe"
ATTEMPTS = 6
BACKOFF_SEC = 15


def _version_endpoint(version: str) -> dict | None:
    """The version-specific JSON route. 404 until the release is served.

    Returns None for "not there yet" AND for a transient failure — a 503 or a
    dropped connection during a publish window is not evidence the release is
    missing, and raising on it would turn a retryable blip into a failed
    verification (Codex, PR #102).
    """
    url = f"https://pypi.org/pypi/{PROJECT}/{version}/json?cb={time.time()}"
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            print(f"  transient HTTP {exc.code} from the index — retrying", file=sys.stderr)
        return None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"  transient error from the index ({exc}) — retrying", file=sys.stderr)
        return None


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

        # Ignore the user's pip config and cache. Otherwise a wheel already in
        # the local cache, or a configured alternate index or find-links,
        # satisfies the install without PyPI serving anything (Codex, PR #102).
        env = {
            **os.environ,
            "PIP_CONFIG_FILE": os.devnull,
            "PIP_NO_CACHE_DIR": "1",
            "PIP_INDEX_URL": "https://pypi.org/simple/",
        }
        for key in ("PIP_FIND_LINKS", "PIP_EXTRA_INDEX_URL", "PIP_TARGET"):
            env.pop(key, None)
        try:
            subprocess.run(
                [str(pip), "install", "--no-cache-dir", "--index-url", "https://pypi.org/simple/",
                 "-q", f"{PROJECT}=={version}"],
                check=True,
                capture_output=True,
                env=env,
            )
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            if _is_index_lag(stderr):
                raise IndexLag(stderr.strip().splitlines()[-1] if stderr.strip() else "") from None
            raise

        # cwd=tmp, NOT the repo. `python -c` puts the working directory on
        # sys.path, so running this from the checkout imported the local source
        # tree instead of the installed wheel — a venv with nothing installed
        # still reported the right version (Codex, PR #102). The path assertion
        # below makes that failure impossible to reintroduce silently.
        try:
            result = subprocess.run(
                [str(python), "-c",
                 f"import {IMPORT_NAME}; print({IMPORT_NAME}.__version__); print({IMPORT_NAME}.__file__)"],
                check=True,
                capture_output=True,
                text=True,
                cwd=tmp,
                env=env,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"{IMPORT_NAME} is not importable in a clean environment after "
                f"installing {PROJECT}=={version}: {exc.stderr.strip().splitlines()[-1:]}"
            ) from None
        reported, imported_from = result.stdout.strip().splitlines()[:2]
        # Both sides resolved: on macOS /var/folders is a symlink to
        # /private/var/folders, so tempfile and __file__ disagree on spelling
        # for the very same directory.
        resolved_import = Path(imported_from).resolve()
        resolved_venv = venv.resolve()
        if resolved_venv not in resolved_import.parents:
            raise RuntimeError(
                f"imported {IMPORT_NAME} from {resolved_import}, which is not inside the "
                f"throwaway venv {resolved_venv} — this check would be verifying a local "
                "source tree rather than the published artefact"
            )
        return reported


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

    installed = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            installed = _install_check(version)
            break
        except IndexLag as exc:
            # The load-bearing check gets the same patience as the index read.
            # Reporting FAIL here while the simple index catches up is what
            # taught the operator to re-run rather than read.
            print(
                f"  pip cannot see {version} yet ({attempt}/{ATTEMPTS}): {exc} — "
                f"waiting {BACKOFF_SEC}s. The simple index lags the JSON route.",
                file=sys.stderr,
            )
            if attempt < ATTEMPTS:
                time.sleep(BACKOFF_SEC)
        except (RuntimeError, subprocess.CalledProcessError) as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            return 1
    if installed is None:
        print(
            f"FAIL: pip could not install {PROJECT}=={version} after "
            f"{ATTEMPTS * BACKOFF_SEC}s of index propagation.",
            file=sys.stderr,
        )
        return 1
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
