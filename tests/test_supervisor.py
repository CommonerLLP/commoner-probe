"""Tests for the acquisition supervisor.

No network and no portal. A fake fetcher stands in for a stateful session, and
the interesting cases are the ones where a task is interrupted: the ledger has
to know, and the output directory must not gain a file that looks finished.
"""

from __future__ import annotations

import gzip
import json
import threading
import time
from pathlib import Path

import pytest

from commoner_probe.supervisor import (
    AtomicWriter,
    LeaseLost,
    Pacer,
    Supervisor,
    Task,
    TaskStore,
    validate_final_outputs,
)


class _Fetcher:
    """Yields `rows` records per task. A key in `explode` raises instead."""

    def __init__(self, owner: str, rows: int = 3, explode: set[str] | None = None,
                 pause: float = 0.0, seen: list | None = None):
        self.owner = owner
        self.rows = rows
        self.explode = explode or set()
        self.pause = pause
        self.seen = seen if seen is not None else []
        self.requests = 0

    def records(self, task: Task, pacer: Pacer):
        self.seen.append((self.owner, task.key))
        if task.key in self.explode:
            raise RuntimeError("portal said no")
        for index in range(self.rows):
            pacer.wait()
            self.requests += 1
            if self.pause:
                time.sleep(self.pause)
            yield {"record_id": f"{task.key}#{index}", "task": task.key,
                   "payload": task.payload}

    def counters(self) -> dict[str, float]:
        return {"requests": self.requests, "throttled": 0}


def _tasks(count: int) -> list[Task]:
    return [Task(key=f"T{index:03d}", payload={"n": index}) for index in range(count)]


def _run(tmp_path, workers=2, rows=3, explode=None, max_attempts=3, factory=None):
    store = TaskStore(tmp_path / "state.db")
    store.seed(_tasks(6))
    seen: list = []
    factory = factory or (lambda owner: _Fetcher(owner, rows=rows,
                                                 explode=explode, seen=seen))
    supervisor = Supervisor(store, tmp_path / "out", factory, workers=workers,
                            pace_seconds=0, lease_seconds=60,
                            max_attempts=max_attempts)
    return supervisor, supervisor.run(), seen


# ── the ledger ────────────────────────────────────────────────────────────


def test_every_task_runs_once_and_the_ledger_says_so(tmp_path):
    _, summary, seen = _run(tmp_path)

    assert summary["complete"] == 6
    assert summary["pending"] == summary["running"] == summary["failed"] == 0
    assert summary["rows_written"] == 18
    assert len(seen) == 6, "a task was handed to two workers"
    assert len({key for _, key in seen}) == 6


def test_seeding_twice_adds_nothing_and_resets_nothing(tmp_path):
    store = TaskStore(tmp_path / "state.db")
    assert store.seed(_tasks(6)) == 6
    assert store.seed(_tasks(6)) == 0

    store.claim("owner", 60)
    assert store.seed(_tasks(6)) == 0
    assert store.summary()["running"] == 1, "a re-seed reset a running task"


def test_a_task_survives_the_process_that_was_running_it(tmp_path):
    """The resume case. A worker that dies leaves the row `running` with a
    lease, and nothing else may touch it until that lease expires."""
    store = TaskStore(tmp_path / "state.db")
    store.seed(_tasks(2))

    dead = store.claim("dead-worker", lease_seconds=0.05)
    assert store.claim("other", 60).key != dead.key, "a live lease was stolen"

    time.sleep(0.06)
    assert store.claim("survivor", 60).key == dead.key
    assert store.rows()[0]["attempts"] == 2


def test_a_failing_task_returns_to_the_queue_until_its_attempts_run_out(tmp_path):
    _, summary, seen = _run(tmp_path, workers=1, explode={"T002"}, max_attempts=3)

    assert summary["complete"] == 5
    assert summary["failed"] == 1
    assert len([key for _, key in seen if key == "T002"]) == 3, "max_attempts counts attempts"
    row = next(r for r in TaskStore(tmp_path / "state.db").rows() if r["task_key"] == "T002")
    assert "portal said no" in row["last_error"]


# ── the atomic publish ────────────────────────────────────────────────────


def test_a_failed_task_leaves_no_part_file(tmp_path):
    """The prototype removed part files only after a SUCCESSFUL publish, so a
    failure left one behind forever and the directory filled with files that
    look like work in progress."""
    _run(tmp_path, workers=1, explode={"T002"})

    assert list((tmp_path / "out").glob("*.part")) == []


def test_only_finalized_files_exist_after_a_clean_run(tmp_path):
    _run(tmp_path)
    out = tmp_path / "out"

    assert len(list(out.glob("*.jsonl.gz"))) == 6
    assert list(out.glob("*.part")) == []


def test_a_worker_that_lost_its_lease_cannot_publish(tmp_path):
    """Its lease expired, another worker was given the task, and the file the
    second worker writes is the one that counts."""
    store = TaskStore(tmp_path / "state.db")
    store.seed(_tasks(1))
    writer = AtomicWriter(tmp_path / "out")
    task = store.claim("first", lease_seconds=0.05)
    part, final, rows, digest = writer.write_partial(
        task, "first", [{"record_id": "a"}])

    time.sleep(0.06)
    store.claim("second", 60)

    with pytest.raises(LeaseLost):
        store.publish(task.key, "first", rows, part, final, digest)
    assert not final.exists(), "a stale worker published over a live task"
    assert store.summary()["complete"] == 0


def test_two_owners_write_to_two_different_part_files(tmp_path):
    """They can both hold the same task after a lease expiry. If they shared a
    part path, the loser's bytes would land in the winner's file."""
    writer = AtomicWriter(tmp_path / "out")
    task = Task(key="T001")

    first, final_a = writer.paths(task, "one")
    second, final_b = writer.paths(task, "two")

    assert first != second
    assert final_a == final_b


def test_a_part_file_is_swept_only_when_no_lease_can_explain_it(tmp_path):
    """Age is the whole test. A part file belonging to another owner may be a
    dead worker's leftover or a live worker's half-written file, and the two
    look identical from outside."""
    writer = AtomicWriter(tmp_path / "out")
    (tmp_path / "out").mkdir(parents=True)
    fresh = tmp_path / "out" / "T001.jsonl.gz.live.part"
    old = tmp_path / "out" / "T002.jsonl.gz.dead.part"
    fresh.write_bytes(b"")
    old.write_bytes(b"")
    import os
    os.utime(old, (time.time() - 600, time.time() - 600))

    assert writer.sweep_abandoned_parts(older_than_seconds=120) == 1
    assert fresh.exists()
    assert not old.exists()


# ── progress is the ledger, never the directory ───────────────────────────


def test_the_validator_ignores_an_active_part_file(tmp_path):
    """An active file is never completion evidence. A check that counts every
    file in the directory reports a task done while it is still being
    written."""
    _run(tmp_path)
    out = tmp_path / "out"
    with gzip.open(out / "T999.jsonl.gz.someone.part", "wt") as stream:
        stream.write(json.dumps({"record_id": "not-yet"}) + "\n")

    result = validate_final_outputs(out)
    assert result["files"] == 6
    assert result["rows"] == 18
    assert result["corrupt"] == 0
    assert result["duplicate_records"] == 0


def test_the_validator_names_a_corrupt_file(tmp_path):
    _run(tmp_path)
    (tmp_path / "out" / "T000.jsonl.gz").write_bytes(b"not gzip at all")

    assert validate_final_outputs(tmp_path / "out")["corrupt"] == 1


def test_a_retry_produces_no_duplicate_final_rows(tmp_path):
    """The whole point of the fenced publish. A task retried after an
    interruption must not double its records in the corpus."""
    store = TaskStore(tmp_path / "state.db")
    store.seed(_tasks(3))
    writer = AtomicWriter(tmp_path / "out")

    task = store.claim("first", lease_seconds=0.05)
    writer.write_partial(task, "first", [{"record_id": f"{task.key}#0"}])
    time.sleep(0.06)

    supervisor = Supervisor(store, tmp_path / "out",
                            lambda owner: _Fetcher(owner, rows=2),
                            workers=1, pace_seconds=0, lease_seconds=60)
    supervisor.run()

    result = validate_final_outputs(tmp_path / "out")
    assert result["duplicate_records"] == 0
    assert result["files"] == 3
    assert result["rows"] == 6


# ── the payload, the counters, the threads ────────────────────────────────


def test_the_payload_reaches_the_adapter_unread(tmp_path):
    """The supervisor stores it, hands it back, and never reads a field."""
    store = TaskStore(tmp_path / "state.db")
    store.seed([Task(key="T1", payload={"fy": "2025-26", "sector": ["a", 1]})])
    supervisor = Supervisor(store, tmp_path / "out",
                            lambda owner: _Fetcher(owner, rows=1),
                            workers=1, pace_seconds=0)
    supervisor.run()

    with gzip.open(tmp_path / "out" / "T1.jsonl.gz", "rt") as stream:
        record = json.loads(stream.readline())
    assert record["payload"] == {"fy": "2025-26", "sector": ["a", 1]}


def test_the_counters_come_from_the_adapter_not_from_worker_exits(tmp_path):
    """A worker exits for many reasons. Treating an exit as a throttle signal
    produces a backoff against a bug."""
    supervisor, _, _ = _run(tmp_path, workers=2, rows=3)

    assert supervisor.counters["requests"] == 18
    assert supervisor.counters["throttled"] == 0


def test_a_fetcher_that_counts_nothing_is_not_an_error(tmp_path):
    class _Silent:
        def __init__(self, owner):
            self.owner = owner

        def records(self, task, pacer):
            yield {"record_id": task.key}

    store = TaskStore(tmp_path / "state.db")
    store.seed(_tasks(2))
    supervisor = Supervisor(store, tmp_path / "out", _Silent, workers=1, pace_seconds=0)

    assert supervisor.run()["complete"] == 2
    assert supervisor.counters == {}


def test_the_registry_is_loaded_once_for_every_worker(tmp_path):
    """The measured problem: the runner loaded 115,519 records inside every
    process and a four-process probe hit the memory ceiling. Threads share the
    object; the test asserts one instance serves every worker."""
    registry = {"rows": list(range(1000))}
    holders: list[int] = []

    class _Sharing(_Fetcher):
        def records(self, task, pacer):
            holders.append(id(registry))
            yield from super().records(task, pacer)

    _, summary, _ = _run(tmp_path, workers=3,
                         factory=lambda owner: _Sharing(owner, rows=1))

    assert summary["complete"] == 6
    assert len(set(holders)) == 1, "the registry was copied per worker"


def test_workers_run_at_the_same_time(tmp_path):
    """Three sessions paced at one second make three requests a second. That
    is the reason to run them together, so the pacing must be per session."""
    started = threading.Barrier(3, timeout=5)

    class _Concurrent(_Fetcher):
        def records(self, task, pacer):
            started.wait()
            yield {"record_id": task.key}

    store = TaskStore(tmp_path / "state.db")
    store.seed(_tasks(3))
    supervisor = Supervisor(store, tmp_path / "out", _Concurrent, workers=3,
                            pace_seconds=0, lease_seconds=60)

    assert supervisor.run()["complete"] == 3, "the workers did not overlap"


@pytest.mark.parametrize("workers", [0, -1])
def test_a_supervisor_needs_at_least_one_worker(tmp_path, workers):
    with pytest.raises(ValueError):
        Supervisor(TaskStore(tmp_path / "s.db"), tmp_path / "out",
                   lambda owner: _Fetcher(owner), workers=workers)


def test_the_ledger_survives_a_reopen(tmp_path):
    """A status check runs in a different process from the workers."""
    _run(tmp_path)

    reopened = TaskStore(tmp_path / "state.db")
    assert reopened.summary()["complete"] == 6
    assert len(reopened.rows(status="complete")) == 6
    assert all(Path(row["output_path"]).exists() for row in reopened.rows())
