"""Run-level outcome and exit code for Sansad crawls.

zero-hour's `census-2026` holds 1,964 runs across 786 member directories;
1,285 of them recorded `added: 0` and exited 0. Some of those were quiet
members and some were crawls that reached nothing — the artefact could not
tell them apart, so a member percentile computed from it was wrong and had
to be retracted.

These tests pin the difference: a run whose every bucket errored is
`failed` and exits non-zero; a run that legitimately found nothing stays
`complete` and exits 0.

No network.
"""

from __future__ import annotations

import json

import pytest

from commoner_probe.cli import _exit_on_failed_runs
from commoner_probe.sansad import SansadProbe


def _probe(out, *, raises: bool):
    probe = SansadProbe(None, out, sleep=0, member_name="Nobody At All")

    def search(ses_no, ministry_like, member_name=None, mp_code=None):
        if raises:
            raise ConnectionError("SSRF guard rejected https://rsdoc.nic.in/...")
        return []

    probe.rs_search_session = search
    return probe


def _crawl(probe):
    return probe.probe_rs(
        set(),
        sessions=[266, 267],
        from_date=None,
        to_date=None,
        qtype_filter=None,
        limit=None,
        max_buckets=None,
        max_records=None,
        download=False,
    )


def test_every_bucket_failing_marks_the_run_failed(tmp_path):
    probe = _probe(tmp_path, raises=True)
    added = _crawl(probe)

    assert added == 0
    assert probe.runlog.statuses == ["failed"]

    rec = json.loads((tmp_path / "_runs.jsonl").read_text().splitlines()[0])
    assert rec["status"] == "failed"
    assert rec["added"] == 0
    assert len(rec["errors"]) == 2


def test_a_genuinely_empty_crawl_stays_complete(tmp_path):
    """The regression that matters: a quiet member must not read as broken."""
    probe = _probe(tmp_path, raises=False)
    added = _crawl(probe)

    assert added == 0
    assert probe.runlog.statuses == ["complete"]

    rec = json.loads((tmp_path / "_runs.jsonl").read_text().splitlines()[0])
    assert rec["status"] == "complete"
    assert rec["errors"] == []


def test_failed_run_exits_non_zero(tmp_path):
    probe = _probe(tmp_path, raises=True)
    _crawl(probe)
    with pytest.raises(SystemExit, match="failed every bucket"):
        _exit_on_failed_runs(probe)


def test_complete_run_does_not_exit(tmp_path):
    probe = _probe(tmp_path, raises=False)
    _crawl(probe)
    _exit_on_failed_runs(probe)  # must not raise


def test_max_records_is_recorded_in_scope(tmp_path):
    """The census's 25-row ceiling was its own `--max-records 25`, and it
    was legible in `_runs.jsonl` all along. Pin that it stays legible."""
    probe = _probe(tmp_path, raises=False)
    probe.probe_rs(
        set(),
        sessions=[267],
        from_date=None,
        to_date=None,
        qtype_filter=None,
        limit=None,
        max_buckets=None,
        max_records=25,
        download=False,
    )
    rec = json.loads((tmp_path / "_runs.jsonl").read_text().splitlines()[0])
    assert rec["scope"]["max_records"] == 25
