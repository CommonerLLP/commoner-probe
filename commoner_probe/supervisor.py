"""Run an acquisition that outlives one process: threads, leases, atomic publish.

Any acquisition that runs for days meets the same three problems. The registry
it walks is too big to hold once per process. The run gets interrupted. And a
half-written output file looks exactly like a finished one.

**The measured case.** A payroll harvest needed 231,038 requests per financial
year — 115,519 centres times two roles — and nine years needed 2,079,342. The
runner loaded the whole 115,519-record registry inside every process. Two live
workers each held about 211 MiB RSS, almost all anonymous. A four-process probe
exceeded the host's memory and the kernel killed a Python process at 190,328
KiB RSS. **The probe recorded zero throttle errors: the portal was not the
constraint, the process model was.**

Five contracts, and the fifth is the one that bites.

1. Load the registry once. Run several stateful sessions as threads in one
   process.
2. Lease each task in SQLite under a unique owner. An interrupted task is
   resumable and a retry produces no duplicate final rows.
3. Stream each unit of work into a ``.part`` file.
4. Publish only after a fenced lease check, then one atomic rename.
5. **Read progress from the ledger and the finalized files alone. An active
   file is never completion evidence.** A status check that lists output files
   reports work as done while it is still being written.

**The supervisor knows nothing about any portal.** A :class:`Fetcher` receives
one task and one paced session and yields records. It owns its field mapping,
and it aborts when the source header changes. It reports its own request,
retry, response-time and throttle counters, because **a worker exit is not a
throttle signal** — a worker exits for many reasons, and backing off on an exit
means backing off against a bug.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

__all__ = [
    "Task", "Pacer", "Fetcher", "TaskStore", "AtomicWriter", "Supervisor",
    "validate_final_outputs", "LeaseLost",
]

STATUSES = ("pending", "running", "complete", "failed")

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


class LeaseLost(RuntimeError):
    """A worker tried to publish a task another owner now holds."""


@dataclass(frozen=True)
class Task:
    """One unit of work, and whatever the adapter needs to perform it.

    ``key`` identifies the task for the whole life of the corpus, so it must be
    derived from the source and never from a position in a list. ``payload`` is
    free-form and belongs to the adapter: the supervisor stores it, hands it
    back, and never reads a field of it.
    """

    key: str
    payload: dict[str, Any] = field(default_factory=dict)


class Pacer:
    """One minimum interval between requests, inside one session.

    Per session, not per process. Three sessions paced at one second each make
    three requests a second, which is the point of running them together.
    """

    def __init__(self, interval_seconds: float) -> None:
        self.interval_seconds = interval_seconds
        self._last_request = 0.0

    def wait(self) -> None:
        delay = self.interval_seconds - (time.monotonic() - self._last_request)
        if delay > 0:
            time.sleep(delay)
        self._last_request = time.monotonic()


class Fetcher(Protocol):
    """One stateful portal session, owned by one worker thread.

    ``records`` yields. It must not build the whole response set first: holding
    one task's output in memory is the failure this package exists to avoid.

    ``counters`` is how throttling reaches the supervisor. Report requests,
    retries, response times and throttle responses from inside the session that
    saw them.
    """

    def records(self, task: Task, pacer: Pacer) -> Iterable[dict[str, Any]]: ...

    def counters(self) -> dict[str, float]: ...


class TaskStore:
    """The SQLite ledger. It is the progress source, and the only one."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'running', 'complete', 'failed')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT,
                    lease_until REAL,
                    rows_written INTEGER NOT NULL DEFAULT 0,
                    output_path TEXT,
                    sha256 TEXT,
                    last_error TEXT,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS tasks_status_idx ON tasks(status, task_key);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        # WAL so a reader checking progress never blocks a writer, and a
        # busy timeout because several worker threads claim at once.
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def seed(self, tasks: Iterable[Task]) -> int:
        """Add tasks that are not already here. Returns how many were added.

        ``INSERT OR IGNORE``, so re-seeding an existing run is safe and a task
        already complete is not reset to pending.
        """
        now = time.time()
        rows = [(t.key, json.dumps(t.payload, ensure_ascii=False, sort_keys=True), now)
                for t in tasks]
        with self._connect() as connection:
            before = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            connection.executemany(
                "INSERT OR IGNORE INTO tasks (task_key, payload, updated_at) "
                "VALUES (?, ?, ?)", rows)
            after = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        return after - before

    def claim(self, owner: str, lease_seconds: float) -> Task | None:
        """Take the next task, or a task whose lease has expired.

        ``BEGIN IMMEDIATE`` takes the write lock before the SELECT, so two
        workers cannot read the same pending row and both claim it.
        """
        now = time.time()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM tasks WHERE status = 'pending' "
                "   OR (status = 'running' AND lease_until < ?) "
                "ORDER BY task_key LIMIT 1", (now,)).fetchone()
            if row is None:
                connection.commit()
                return None
            connection.execute(
                "UPDATE tasks SET status = 'running', attempts = attempts + 1, "
                "lease_owner = ?, lease_until = ?, last_error = NULL, updated_at = ? "
                "WHERE task_key = ?",
                (owner, now + lease_seconds, now, row["task_key"]))
            connection.commit()
            return _task_from_row(row)
        finally:
            connection.close()

    def heartbeat(self, task_key: str, owner: str, lease_seconds: float) -> None:
        """Extend this owner's lease. A task that outlives its lease is
        reclaimed, so a long task must say it is still alive."""
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                "UPDATE tasks SET lease_until = ?, updated_at = ? "
                "WHERE task_key = ? AND status = 'running' AND lease_owner = ?",
                (now + lease_seconds, now, task_key, owner))

    def publish(self, task_key: str, owner: str, rows_written: int,
                part_path: Path, final_path: Path, sha256: str) -> None:
        """Rename the part into place and mark the task complete, or neither.

        The lease is checked inside the write transaction and again in the
        UPDATE's WHERE clause. A worker whose lease expired while it was
        writing must not publish: another worker has been given the same task
        and its file is the one that counts.
        """
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            held = connection.execute(
                "SELECT 1 FROM tasks WHERE task_key = ? AND status = 'running' "
                "AND lease_owner = ?", (task_key, owner)).fetchone()
            if held is None:
                connection.rollback()
                raise LeaseLost(task_key)
            os.replace(part_path, final_path)
            cursor = connection.execute(
                "UPDATE tasks SET status = 'complete', rows_written = ?, "
                "output_path = ?, sha256 = ?, lease_owner = NULL, "
                "lease_until = NULL, last_error = NULL, updated_at = ? "
                "WHERE task_key = ? AND status = 'running' AND lease_owner = ?",
                (rows_written, str(final_path), sha256, time.time(), task_key, owner))
            if cursor.rowcount != 1:
                connection.rollback()
                raise LeaseLost(task_key)
            connection.commit()
        finally:
            connection.close()

    def fail(self, task_key: str, owner: str, error: BaseException,
             max_attempts: int) -> str:
        """Return the task to the queue, or retire it. Returns the new status.

        ``max_attempts`` counts attempts, not retries. At 3 a task is tried
        three times in total and then marked ``failed``.
        """
        with self._connect() as connection:
            row = connection.execute(
                "SELECT attempts FROM tasks WHERE task_key = ?", (task_key,)).fetchone()
            if row is None:
                raise KeyError(task_key)
            status = "failed" if row["attempts"] >= max_attempts else "pending"
            connection.execute(
                "UPDATE tasks SET status = ?, lease_owner = NULL, lease_until = NULL, "
                "last_error = ?, updated_at = ? WHERE task_key = ? AND lease_owner = ?",
                (status, f"{type(error).__name__}: {error}"[:500], time.time(),
                 task_key, owner))
        return status

    def summary(self) -> dict[str, int]:
        """Counts by status, plus the totals. Read from the ledger, never from
        the output directory."""
        result: dict[str, int] = {name: 0 for name in STATUSES}
        with self._connect() as connection:
            for row in connection.execute(
                    "SELECT status, COUNT(*) AS count FROM tasks GROUP BY status"):
                result[row["status"]] = row["count"]
            totals = connection.execute(
                "SELECT COALESCE(SUM(rows_written), 0) AS rows_written, "
                "COALESCE(SUM(attempts), 0) AS attempts FROM tasks").fetchone()
        result["rows_written"] = totals["rows_written"]
        result["attempts"] = totals["attempts"]
        result["tasks"] = sum(result[name] for name in STATUSES)
        return result

    def rows(self, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM tasks"
        params: tuple = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (status,)
        with self._connect() as connection:
            return [dict(row) for row in
                    connection.execute(query + " ORDER BY task_key", params)]


class AtomicWriter:
    """Stream one task into a ``.part`` file. The supervisor publishes it."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def paths(self, task: Task, owner: str) -> tuple[Path, Path]:
        """``(part, final)`` for one task and one owner.

        The owner is in the part name so two workers holding the same task
        after a lease expiry write two different files. Only one of them wins
        the fenced publish, and the loser's bytes were never in the final path.
        """
        name = _safe(task.key) + ".jsonl.gz"
        directory = self.root
        return directory / f"{name}.{_safe(owner)}.part", directory / name

    def write_partial(self, task: Task, owner: str, records: Iterable[dict[str, Any]],
                      heartbeat: Callable[[], None] | None = None,
                      heartbeat_every: int = 100) -> tuple[Path, Path, int, str]:
        part_path, final_path = self.paths(task, owner)
        part_path.parent.mkdir(parents=True, exist_ok=True)
        rows_written = 0
        with gzip.open(part_path, "wt", encoding="utf-8") as stream:
            for record in records:
                stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                rows_written += 1
                if heartbeat and rows_written % heartbeat_every == 0:
                    heartbeat()
        return part_path, final_path, rows_written, _sha256(part_path)

    def sweep_abandoned_parts(self, older_than_seconds: float) -> int:
        """Delete ``.part`` files no live lease can explain. Returns the count.

        Age is the whole test, and it has to be. A part file belonging to
        ANOTHER owner may be a dead worker's leftover or a live worker's
        half-written file, and the two look identical. Deleting by name — which
        the prototype did on every successful publish — can therefore unlink a
        file another thread is writing. Nothing older than twice the lease can
        still be live, so that is what goes.
        """
        cutoff = time.time() - older_than_seconds
        removed = 0
        for stale in self.root.glob("*.part"):
            try:
                if stale.stat().st_mtime < cutoff:
                    stale.unlink()
                    removed += 1
            except OSError:  # pragma: no cover - another worker got there first
                continue
        return removed


class Supervisor:
    """Run bounded stateful workers as threads inside one process."""

    def __init__(self, store: TaskStore, output_root: Path,
                 fetcher_factory: Callable[[str], Fetcher], *,
                 workers: int = 3, pace_seconds: float = 1.0,
                 lease_seconds: float = 300.0, max_attempts: int = 3,
                 log=None) -> None:
        if workers < 1:
            raise ValueError("workers must be positive")
        self.store = store
        self.writer = AtomicWriter(output_root)
        self.fetcher_factory = fetcher_factory
        self.workers = workers
        self.pace_seconds = pace_seconds
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.run_id = uuid.uuid4().hex[:12]
        self._log = log
        #: Summed from every fetcher's own counters. Throttling is reported by
        #: the session that met it, never inferred from a worker exit.
        self.counters: Counter[str] = Counter()
        self._counters_lock = threading.Lock()

    def log(self, msg: str) -> None:
        if self._log:
            self._log(msg)

    def run(self) -> dict[str, int]:
        # Before any worker starts, and only here: at this moment no lease this
        # run owns can exist, so an old part file is unambiguously abandoned.
        removed = self.writer.sweep_abandoned_parts(self.lease_seconds * 2)
        if removed:
            self.log(f"swept {removed} abandoned part file(s)")
        threads = [threading.Thread(target=self._worker,
                                    args=(f"{self.run_id}:worker-{index}",),
                                    daemon=False)
                   for index in range(self.workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return self.store.summary()

    def _worker(self, owner: str) -> None:
        fetcher = self.fetcher_factory(owner)
        pacer = Pacer(self.pace_seconds)
        while True:
            task = self.store.claim(owner, self.lease_seconds)
            if task is None:
                self._collect(fetcher)
                return
            # The path is taken BEFORE the write, because the write is where
            # the failure happens. Reading it off a return value means never
            # reading it on the path that needs it.
            part_path, _ = self.writer.paths(task, owner)
            try:
                part_path, final_path, rows, digest = self.writer.write_partial(
                    task, owner, fetcher.records(task, pacer),
                    heartbeat=lambda: self.store.heartbeat(
                        task.key, owner, self.lease_seconds))
                self.store.publish(task.key, owner, rows, part_path, final_path, digest)
                self.log(f"{owner} published {task.key} rows={rows}")
            except BaseException as error:  # noqa: BLE001
                # The part file goes with the failure. The prototype removed
                # parts only after a SUCCESSFUL publish, so every failed task
                # left one behind and the directory filled with files that
                # look like work in progress forever.
                part_path.unlink(missing_ok=True)
                status = self.store.fail(task.key, owner, error, self.max_attempts)
                self.log(f"{owner} {status} {task.key}: {type(error).__name__}: {error}")
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    self._collect(fetcher)
                    raise

    def _collect(self, fetcher: Fetcher) -> None:
        """Fold one finished worker's counters into the run's.

        A fetcher that reports nothing is not an error. It is a fetcher that
        chose not to count, and the supervisor must not invent numbers for it.
        """
        report = getattr(fetcher, "counters", None)
        if not callable(report):
            return
        try:
            values = report() or {}
        except Exception as exc:  # noqa: BLE001 - counting must not fail a run
            self.log(f"counters unavailable: {type(exc).__name__}: {exc}")
            return
        with self._counters_lock:
            for name, value in values.items():
                self.counters[name] += value


def validate_final_outputs(root: Path, *, id_field: str = "record_id") -> dict[str, int]:
    """Read the finalized files. ``.part`` files are excluded, on purpose.

    An active file is never completion evidence. A check that counts every file
    in the directory reports a task as done while it is still being written,
    which is the failure the atomic publish exists to prevent — and a validator
    that undoes it is worse than none.
    """
    root = Path(root)
    result = {"files": 0, "rows": 0, "corrupt": 0, "duplicate_records": 0}
    seen: set[str] = set()
    for path in sorted(root.rglob("*.jsonl.gz")):
        result["files"] += 1
        try:
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                for line in stream:
                    record = json.loads(line)
                    result["rows"] += 1
                    identifier = record.get(id_field)
                    if identifier is None:
                        continue
                    if identifier in seen:
                        result["duplicate_records"] += 1
                    seen.add(identifier)
        except (OSError, EOFError, json.JSONDecodeError):
            result["corrupt"] += 1
    return result


def _task_from_row(row: sqlite3.Row) -> Task:
    try:
        payload = json.loads(row["payload"])
    except (json.JSONDecodeError, TypeError):
        payload = {}
    return Task(key=row["task_key"], payload=payload if isinstance(payload, dict) else {})


def _safe(value: str) -> str:
    return _SAFE.sub("_", value).strip("_") or "unknown"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
