"""Tests for the `commoner-probe validate` subcommand.

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

def test_unknown_kind_fails_instead_of_being_skipped():
    """An unregistered kind is an unvalidated record, and must not pass.

    This inverts the previous contract. Skipping meant a probe could emit a
    new kind, or rename one, and `commoner-probe validate` would print
    "N records — ok" and exit 0 having validated nothing. That shipped at
    least twice; the second fix was commit dc06d85, "one more unvalidated
    manifest kind". Adding one more `if` each time treats the instance, not
    the mechanism.
    """
    rec = {"key": "X|Y|Z", "kind": "future_kind", "house": "Upper House"}
    with tempfile.TemporaryDirectory() as tmp:
        m = Path(tmp) / "manifest.jsonl"
        m.write_text(json.dumps(rec) + "\n", encoding="utf-8")
        ok, lines = _run_validate(Path(tmp))
    assert not ok, "an unregistered kind must fail, not pass silently"
    joined = "\n".join(lines)
    assert "future_kind" in joined, f"the failure must name the kind:\n{joined}"
    assert "line 1" in joined, f"the failure must name the line:\n{joined}"


def test_the_count_reports_records_validated_not_lines_read():
    """`N records — ok` counted non-blank LINES, so it could not detect this.

    A file of records that were all skipped reported the same number as a file
    of records that were all checked.
    """
    known = {
        "key": "K|1",
        "kind": "wayback_capture",
        "url": "https://example.gov.in/x",
        "timestamp": "20240101000000",
        "status": "captured",
        "fetched_at": "2026-01-01T00:00:00Z",
    }
    with tempfile.TemporaryDirectory() as tmp:
        m = Path(tmp) / "manifest.jsonl"
        m.write_text(
            json.dumps(known) + "\n" + json.dumps({"key": "K|2", "kind": "future_kind"}) + "\n",
            encoding="utf-8",
        )
        ok, lines = _run_validate(Path(tmp))
    joined = "\n".join(lines)
    assert not ok
    assert "1 of 2" in joined, f"the count must distinguish validated from read:\n{joined}"


def test_a_non_string_kind_is_reported_not_raised():
    """`kind` comes off disk. A list or object made the schema lookup raise
    TypeError: unhashable type, killing the whole run at that line."""
    with tempfile.TemporaryDirectory() as tmp:
        m = Path(tmp) / "manifest.jsonl"
        m.write_text(
            "\n".join(json.dumps({"key": "K|1", "kind": k}) for k in ([], {}, None, 7))
            + "\n",
            encoding="utf-8",
        )
        ok, lines = _run_validate(Path(tmp))
    joined = "\n".join(lines)
    assert not ok
    assert "0 of 4" in joined, f"every record is unvalidatable:\n{joined}"


def test_the_read_count_survives_truncation():
    """`read` stopped counting at the error limit, so a 40-line file reported
    "0 of 3" — a partial denominator that reads like a small file."""
    bad = json.dumps({"key": "K|1", "kind": "future_kind"})
    with tempfile.TemporaryDirectory() as tmp:
        m = Path(tmp) / "manifest.jsonl"
        m.write_text("\n".join([bad] * 40) + "\n", encoding="utf-8")
        ok, lines = _run_validate(Path(tmp), max_errors=3)
    joined = "\n".join(lines)
    assert not ok
    assert "truncated" in joined
    assert "of 40" in joined, f"the denominator must be the file, not the prefix:\n{joined}"


def test_every_manifest_schema_is_reachable():
    """The kind -> schema map must cover every manifest schema that ships.

    Derived from the schemas rather than hand-written, so a new schema cannot
    be forgotten. This asserts the derivation actually found them all.
    """
    from commoner_probe import schemas as sc
    from commoner_probe.validate import manifest_schema_by_kind

    shipped = {n for n in sc.list_all() if n.startswith("manifest_")}
    reachable = set(manifest_schema_by_kind().values())
    assert shipped == reachable, (
        f"unreachable manifest schemas: {sorted(shipped - reachable)}; "
        f"mapped to non-existent: {sorted(reachable - shipped)}"
    )


# ---------------------------------------------------------------------------
# CLI entrypoint (via parser) exits 0 on smoke corpus
# ---------------------------------------------------------------------------

def test_cli_smoke_exits_zero(monkeypatch):
    from commoner_probe.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["validate", "--out", str(SMOKE)])
    # Should not raise SystemExit(1)
    args.func(args)
