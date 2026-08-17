# SPDX-License-Identifier: MIT
"""Make a multi-hour acquisition survive a kill, and resume without duplicating.

This is a POSIX process-lifecycle helper, not a portal driver. It exists because
the process, not the source, is what ends a long crawl. A watchdog sends SIGTERM
at a deadline, an operator sends SIGINT, a laptop sleeps, a CI job hits its wall
clock. Any run measured in hours meets one of those before it meets a bad row.

THE INCIDENT, and it destroyed four hours of work
=================================================
An extractor against `apsac.ap.gov.in/geoserver` held every feature in memory and
wrote once at the end. A deadline kill arrived at hour four. The extractor never
created the output file, so the run left **nothing at all**. There was no partial
extract, no resume point, and no record of which tiles the sweep already covered.
The failure was silent in the worst way. The process exited 143, and the directory
looked like a run that never started.

Therefore the kill path is TESTED before the long run. `tests/test_checkpoint.py`
calls :meth:`CheckpointedRun.on_signal` directly. An untested kill path is an
assumption, and this is the assumption that cost the four hours.

A TORN LAST LINE IS NOT A SHORTER CHECKPOINT
============================================
This repo mishandled a checkpoint truncated mid-line twice. Both times the reader
skipped the torn line and said nothing. Both times the partial output for that
torn line stayed on disk. The next run re-fetched the unit and appended it, so one
document existed twice while the checkpoint claimed a clean resume. **None of the
torn unit's work is trustworthy, so the caller has to hear about it.**
:attr:`Checkpoint.torn` and
:attr:`CheckpointedRun.torn` report it, and the caller discards that unit's
partial output before continuing.

The five states, all of which :func:`load_checkpoint` handles explicitly:

===========================  =====================================
no file                      clean start
empty file                   clean start
one good line                resume one unit
good lines then a torn line  resume the good ones, ``torn=True``
only a torn line             resume nothing, ``torn=True``
===========================  =====================================

A sixth state is NOT tolerated. An unparseable line that ends in a newline is
damage in the middle of the file, not truncation of its end, and reading past it
would resume from a list that silently lost a unit. That raises
:class:`CheckpointCorrupt`.

WHY THE WHOLE FILE IS REPLACED, NOT APPENDED
============================================
Appending one line per unit is cheaper and is how the torn line gets written in
the first place: a kill between the write and the newline leaves half a record.
Each flush writes a temp file in the same directory and calls ``os.replace``,
which is atomic within a filesystem. A reader therefore sees the previous
checkpoint or the new one, never a mixture.

SIGNAL DISPOSITION IS PROCESS-WIDE STATE
========================================
This module installs NO handler at import time. A library that changes a
process's signal disposition on import is a trap for every other caller in that
process. :func:`checkpointed_run` installs handlers on entry and restores the
previous ones on exit, including when the body raises. After flushing, the
previous handler runs, so a caller's own SIGTERM policy still applies.
"""

from __future__ import annotations

import json
import os
import signal
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "Checkpoint",
    "CheckpointCorrupt",
    "CheckpointedRun",
    "checkpointed_run",
    "load_checkpoint",
]


class CheckpointCorrupt(RuntimeError):
    """A checkpoint line is damaged somewhere other than the end of the file."""


@dataclass(frozen=True)
class Checkpoint:
    """What a checkpoint file on disk says, and how much of it is trustworthy."""

    #: The finished units as the file holds them, in their native JSON types. A
    #: string unit reads back a string, an integer an integer.
    done: list = field(default_factory=list)
    torn: bool = False

    @property
    def report(self) -> str:
        if self.torn:
            return (
                f"resumed {len(self.done)} finished unit(s); the last line was TORN and "
                "was discarded — discard that unit's partial output before continuing"
            )
        return f"resumed {len(self.done)} finished unit(s)"


def load_checkpoint(path: Path | str) -> Checkpoint:
    """Read a checkpoint file, and say plainly what part of it is trustworthy.

    A missing or empty file is a clean start, not an error. A torn last line is
    dropped and reported. A damaged line anywhere earlier raises, because
    continuing past it resumes from a list that silently lost a unit.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Checkpoint()
    if not text:
        return Checkpoint()

    lines = text.split("\n")
    torn = lines[-1] != ""
    complete = lines[:-1]

    done: dict[str, object] = {}
    for number, line in enumerate(complete, start=1):
        if not line.strip():
            continue
        try:
            unit = json.loads(line)["unit"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise CheckpointCorrupt(
                f"{path}: line {number} is damaged and it is not the last line, so this "
                f"is corruption rather than a truncated write ({exc}); resuming from it "
                "would drop a finished unit silently"
            ) from exc
        # Keyed the same way `mark` keys it, while the VALUE stays the unit the
        # file holds. `str(unit)` disagreed with the write path for every
        # non-string unit, so a resumed run held keys no membership test matched.
        done[unit_key(unit)] = unit
    return Checkpoint(done=list(done.values()), torn=torn)


def unit_key(unit: object) -> str:
    """The checkpoint key for one unit of work.

    One function for writing and for membership. `mark` stringified the unit while
    the documented test was ``unit in run.done``, so a run over integer, tuple or
    Path units resumed, reported "resumed N finished unit(s)", and refetched every
    one of them — a clean-resume claim printed beside duplicated output.

    Every unit is JSON-encoded, INCLUDING a string, so the type survives the key.
    Returning a string unchanged collided it with the number of the same name: the
    string ``"1"`` and the integer ``1`` both keyed to ``1``, and marking either
    reported the other as finished and skipped its work in silence.
    """
    return json.dumps(unit, sort_keys=True, default=str)


class _DoneKeys:
    """Finished units, keyed by :func:`unit_key`, in the order they finished.

    Iteration yields the UNITS, so the checkpoint file keeps them in their native
    JSON types and stays readable. Membership keys them, so a string and the number
    of the same name are two units.
    """

    def __init__(self, units=()) -> None:
        self._units: dict[str, object] = {unit_key(u): u for u in units}

    def add(self, unit: object) -> None:
        self._units[unit_key(unit)] = unit

    def __contains__(self, unit: object) -> bool:
        return unit_key(unit) in self._units

    def __iter__(self):
        return iter(self._units.values())

    def __len__(self) -> int:
        return len(self._units)


class CheckpointedRun:
    """The resume state of one long acquisition, flushed atomically.

    Use it through :func:`checkpointed_run`, which owns the signal handlers.
    """

    def __init__(
        self,
        path: Path,
        *,
        interval: float = 60.0,
        log: Callable[[str], None] | None = None,
    ) -> None:
        state = load_checkpoint(path)
        self.path = Path(path)
        self.interval = interval
        self.torn = state.torn
        self.resumed = len(state.done)
        self.signals_installed = False
        #: Finished unit keys, in the order they finished. Membership is what
        #: callers want: ``if unit in run.done: continue``, and it holds for a unit
        #: of any type, because :meth:`mark` and this lookup use one key function.
        self.done: _DoneKeys = _DoneKeys(state.done)
        self._log = log
        self._dirty = False
        self._flushed_at = time.monotonic()
        self._previous: dict[int, object] = {}
        if log and (state.torn or state.done):
            log(state.report)

    def mark(self, unit: object) -> None:
        """Record one finished unit, and flush if the interval has elapsed."""
        self.done.add(unit)
        self._dirty = True
        if time.monotonic() - self._flushed_at >= self.interval:
            self.flush()

    def flush(self) -> None:
        """Write every finished unit to a temp file, then replace the checkpoint.

        A no-op when nothing changed, so a resumed run that finishes no new unit
        does not rewrite the file it just read. A TORN file is the exception: it
        must be rewritten even by a run that marks nothing, or the tear persists
        across every later run and each one re-discards output that is long gone.
        """
        if not self._dirty and not self.torn:
            return
        self.torn = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = "".join(json.dumps({"unit": unit}) + "\n" for unit in self.done)
        handle, tmp_name = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=self.path.name + ".", suffix=".tmp"
        )
        tmp = Path(tmp_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as f:
                f.write(body)
                f.flush()
                os.fsync(f.fileno())
            # mkstemp creates the file 0600, and the replaced checkpoint would
            # inherit that, so a second reader of a shared output directory
            # loses access to a file it could read before the run.
            os.chmod(tmp, 0o644)
            os.replace(tmp, self.path)
        finally:
            tmp.unlink(missing_ok=True)
        self._dirty = False
        self._flushed_at = time.monotonic()

    def on_signal(self, signum: int, frame: object) -> None:
        """Flush, then hand the signal back to whoever held it before.

        Public because the kill path is tested by calling it, not by sending a
        real signal. A test that sends SIGTERM to its own runner cannot assert
        anything afterwards.

        The previous handler runs after the flush, so a caller's own policy still
        applies. Where the previous disposition is the default, SIGINT raises
        ``KeyboardInterrupt`` and any other signal raises ``SystemExit`` with the
        shell's ``128 + signum``.
        """
        self.flush()
        if self._log:
            self._log(
                f"signal {signum}: flushed {len(self.done)} finished unit(s) to {self.path}"
            )
        previous = self._previous.get(int(signum), signal.SIG_DFL)
        if previous is signal.SIG_IGN:
            return
        if callable(previous):
            previous(signum, frame)
            return
        if signum == signal.SIGINT:
            raise KeyboardInterrupt
        raise SystemExit(128 + int(signum))

    def _install(self) -> None:
        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                self._previous[int(signum)] = signal.signal(signum, self.on_signal)
            except (ValueError, OSError) as exc:
                self._restore()
                if self._log:
                    self._log(
                        f"no signal handler installed ({exc}); a kill loses up to "
                        f"{self.interval:g}s of work"
                    )
                return
        self.signals_installed = True

    def _restore(self) -> None:
        for signum, previous in self._previous.items():
            signal.signal(signum, previous)  # type: ignore[arg-type]
        self._previous.clear()
        self.signals_installed = False


@contextmanager
def checkpointed_run(
    path: Path | str,
    *,
    interval: float = 60.0,
    log: Callable[[str], None] | None = None,
    install_signals: bool = True,
) -> Iterator[CheckpointedRun]:
    """Run a long acquisition with a resume point that survives a kill.

    ::

        with checkpointed_run(out / "_checkpoint.jsonl", interval=60) as run:
            if run.torn:
                discard_partial_output()
            for tile in tiles:
                if tile.key in run.done:
                    continue
                sweep(tile)
                run.mark(tile.key)

    ``install_signals=False`` is for a caller that owns its own SIGTERM policy,
    or for a run driven off the main thread where ``signal.signal`` refuses.
    Interval flushes and the exit flush still happen; only the kill path is lost,
    and :attr:`CheckpointedRun.signals_installed` says so.
    """
    run = CheckpointedRun(Path(path), interval=interval, log=log)
    if install_signals:
        run._install()
    elif log:
        log(
            f"no signal handler installed (install_signals=False); a kill loses up to "
            f"{interval:g}s of work"
        )
    try:
        yield run
    finally:
        run._restore()
        run.flush()
