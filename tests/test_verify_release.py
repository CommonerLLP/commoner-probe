"""The release verifier must not cry wolf while PyPI propagates.

It retried the index READ six times and then made exactly one install attempt.
But pip reads the *simple* index, which lags the version-JSON endpoint the
retry loop watches: for both 0.12.1 and 0.13.0 the script reported FAIL and the
identical command passed 45 seconds later. A check that fails when nothing is
wrong trains its operator to re-run it rather than read it.

No network and no venv here — pip and the index are injected.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("verify_release", ROOT / "scripts" / "verify_release.py")
vr = importlib.util.module_from_spec(_spec)
sys.modules["verify_release"] = vr
_spec.loader.exec_module(vr)


NOT_YET_SERVED = (
    "ERROR: Could not find a version that satisfies the requirement "
    "commoner-probe==9.9.9 (from versions: 0.13.0)\n"
    "ERROR: No matching distribution found for commoner-probe==9.9.9"
)


def _pip_failure(stderr: str) -> subprocess.CalledProcessError:
    return subprocess.CalledProcessError(1, ["pip", "install"], output="", stderr=stderr)


def test_an_index_that_has_not_caught_up_is_retried(monkeypatch):
    attempts = []

    def flaky(version):
        attempts.append(version)
        if len(attempts) < 3:
            raise vr.IndexLag(NOT_YET_SERVED)
        return version

    monkeypatch.setattr(vr, "_install_check", flaky)
    monkeypatch.setattr(vr, "_version_endpoint", lambda v: {"urls": []})
    monkeypatch.setattr(vr.time, "sleep", lambda _s: None)
    assert vr.main(["verify_release.py", "9.9.9"]) == 0
    assert len(attempts) == 3


def test_a_real_install_failure_is_not_retried(monkeypatch):
    attempts = []

    def broken(version):
        attempts.append(version)
        raise RuntimeError("commoner_probe is not importable in a clean environment")

    monkeypatch.setattr(vr, "_install_check", broken)
    monkeypatch.setattr(vr, "_version_endpoint", lambda v: {"urls": []})
    monkeypatch.setattr(vr.time, "sleep", lambda _s: None)
    assert vr.main(["verify_release.py", "9.9.9"]) == 1
    assert len(attempts) == 1, "a broken package must fail at once, not after minutes"


def test_pip_not_finding_the_version_is_classified_as_index_lag(monkeypatch):
    """The classification is the whole fix: everything else must stay fatal."""
    assert vr._is_index_lag(NOT_YET_SERVED)
    assert not vr._is_index_lag("ERROR: Failed building wheel for commoner-probe")
    assert not vr._is_index_lag("")


def test_install_raises_index_lag_for_a_missing_version(monkeypatch, tmp_path):
    def fake_run(argv, **kwargs):
        if argv[1:3] == ["-m", "venv"]:
            (Path(argv[3]) / "bin").mkdir(parents=True, exist_ok=True)
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        raise _pip_failure(NOT_YET_SERVED)

    monkeypatch.setattr(vr.subprocess, "run", fake_run)
    with pytest.raises(vr.IndexLag):
        vr._install_check("9.9.9")
