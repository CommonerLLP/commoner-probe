"""Tests for the `sansad-crawl validate` subcommand.

Covers:
- Smoke-fixture corpus exits 0.
- A corrupted record (null where string required) causes exit 1 with a
  pointer to the offending line.
- An empty / missing corpus directory is handled gracefully.
- Unknown kind records are skipped (no false positives).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "examples" / "corpora" / "committees-smoke"

try:
    import jsonschema  # noqa: F401
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

pytestmark = pytest.mark.skipif(
    not HAS_JSONSCHEMA,
    reason="jsonschema not installed — pip install commoner-probe[dev]",
)


def _run_validate(out_dir: Path, max_errors: int = 10) -> tuple[bool, list[str]]:
    """Call validate_corpus() directly and capture log output."""
    from commoner_probe.validate import validate_corpus
    lines: list[str] = []
    ok = validate_corpus(out_dir, log=lines.append, max_errors=max_errors)
    return ok, lines


# ---------------------------------------------------------------------------
# Smoke fixture should validate cleanly
# ---------------------------------------------------------------------------

def test_smoke_fixture_validates():
    ok, lines = _run_validate(SMOKE)
    assert ok, "Expected smoke corpus to pass; got:\n" + "\n".join(lines)
    assert any("ok" in line for line in lines)


# ---------------------------------------------------------------------------
# A corrupted record causes failure with a clear pointer
# ---------------------------------------------------------------------------

def test_corrupted_record_fails():
    """Setting report_type to an invalid enum value should trigger an error."""
    good_lines = (SMOKE / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    # Corrupt the first record: set report_type to a value not in the enum
    first = json.loads(good_lines[0])
    first["report_type"] = "completely_invalid_type"

    with tempfile.TemporaryDirectory() as tmp:
        m = Path(tmp) / "manifest.jsonl"
        with m.open("w", encoding="utf-8") as f:
            f.write(json.dumps(first) + "\n")
            for line in good_lines[1:]:
                f.write(line + "\n")
        ok, lines = _run_validate(Path(tmp))

    assert not ok, "Expected validation to fail on corrupted record"
    # Should mention line 1 and the field
    full_output = "\n".join(lines)
    assert "line 1" in full_output
    assert "report_type" in full_output or "completely_invalid_type" in full_output


# ---------------------------------------------------------------------------
# Missing corpus directory is handled gracefully (not a crash)
# ---------------------------------------------------------------------------

def test_empty_directory_passes():
    with tempfile.TemporaryDirectory() as tmp:
        ok, lines = _run_validate(Path(tmp))
    assert ok
    assert any("manifest.jsonl not found" in ln for ln in lines)


# ---------------------------------------------------------------------------
# Records with unknown kind are skipped (no false positives)
# ---------------------------------------------------------------------------

def test_unknown_kind_skipped():
    rec = {"key": "X|Y|Z", "kind": "future_kind", "house": "Upper House"}
    with tempfile.TemporaryDirectory() as tmp:
        m = Path(tmp) / "manifest.jsonl"
        m.write_text(json.dumps(rec) + "\n", encoding="utf-8")
        ok, lines = _run_validate(Path(tmp))
    assert ok, "Unknown kind should be silently skipped"


# ---------------------------------------------------------------------------
# CLI entrypoint (via parser) exits 0 on smoke corpus
# ---------------------------------------------------------------------------

def test_cli_smoke_exits_zero(monkeypatch):
    from commoner_probe.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["validate", "--out", str(SMOKE)])
    # Should not raise SystemExit(1)
    args.func(args)
