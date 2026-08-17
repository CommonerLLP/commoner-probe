"""Checkpoint discipline for a run measured in hours.

A four-hour extraction was destroyed by a watchdog kill, because the extractor
held everything in memory and wrote once at the end. These tests pin the kill
path, so it is tested before the long run rather than assumed.

The signal path is exercised by calling the handler directly. No test sends a
real signal, and no test sleeps.
"""

from __future__ import annotations

import json
import signal

import pytest

from commoner_probe.checkpoint import (
    CheckpointCorrupt,
    CheckpointedRun,
    checkpointed_run,
    load_checkpoint,
)


class TestLoadStates:
    """The five states a checkpoint file can be in on restart."""

    def test_no_file_is_a_clean_start_and_not_an_error(self, tmp_path):
        state = load_checkpoint(tmp_path / "cp.jsonl")
        assert state.done == []
        assert state.torn is False

    def test_an_empty_file_is_a_clean_start(self, tmp_path):
        path = tmp_path / "cp.jsonl"
        path.write_text("", encoding="utf-8")
        state = load_checkpoint(path)
        assert state.done == []
        assert state.torn is False

    def test_one_good_line_resumes_one_unit(self, tmp_path):
        path = tmp_path / "cp.jsonl"
        path.write_text('{"unit": "a"}\n', encoding="utf-8")
        assert load_checkpoint(path).done == ["a"]

    def test_good_lines_then_a_torn_line_drop_the_torn_one_and_say_so(self, tmp_path):
        """Skipping a torn line while leaving its partial output in place
        duplicated a document when none of it survived. The caller must be told
        the file was truncated, not handed a quietly shorter list."""
        path = tmp_path / "cp.jsonl"
        path.write_text('{"unit": "a"}\n{"unit": "b"}\n{"unit": "c', encoding="utf-8")
        state = load_checkpoint(path)
        assert state.done == ["a", "b"]
        assert state.torn is True

    def test_a_file_holding_only_a_torn_line_resumes_nothing_and_says_so(self, tmp_path):
        path = tmp_path / "cp.jsonl"
        path.write_text('{"unit": "a', encoding="utf-8")
        state = load_checkpoint(path)
        assert state.done == []
        assert state.torn is True

    def test_damage_in_the_middle_raises_rather_than_resuming_a_short_list(self, tmp_path):
        """A terminated unparseable line is corruption, not truncation. Reading
        past it would resume from a list that silently lost a unit."""
        path = tmp_path / "cp.jsonl"
        path.write_text('{"unit": "a"}\nnot json\n{"unit": "c"}\n', encoding="utf-8")
        with pytest.raises(CheckpointCorrupt) as excinfo:
            load_checkpoint(path)
        assert "line 2" in str(excinfo.value)

    def test_a_repeated_unit_is_recorded_once_in_order(self, tmp_path):
        path = tmp_path / "cp.jsonl"
        path.write_text('{"unit": "a"}\n{"unit": "b"}\n{"unit": "a"}\n', encoding="utf-8")
        assert load_checkpoint(path).done == ["a", "b"]


class TestFlush:
    def test_it_flushes_on_the_interval(self, tmp_path):
        path = tmp_path / "cp.jsonl"
        with checkpointed_run(path, interval=0.0) as run:
            run.mark("a")
            assert load_checkpoint(path).done == ["a"]

    def test_a_long_interval_still_flushes_at_exit(self, tmp_path):
        path = tmp_path / "cp.jsonl"
        with checkpointed_run(path, interval=1e9) as run:
            run.mark("a")
            assert not path.exists()
        assert load_checkpoint(path).done == ["a"]

    def test_it_flushes_when_the_body_raises(self, tmp_path):
        """The kill this exists for arrives mid-run. Work already finished must
        survive the exception that ends the run."""
        path = tmp_path / "cp.jsonl"
        with pytest.raises(ZeroDivisionError):
            with checkpointed_run(path, interval=1e9) as run:
                run.mark("a")
                raise ZeroDivisionError
        assert load_checkpoint(path).done == ["a"]

    def test_it_writes_no_partial_line_because_it_replaces_the_whole_file(self, tmp_path):
        path = tmp_path / "cp.jsonl"
        with checkpointed_run(path, interval=0.0) as run:
            run.mark("a")
            run.mark("b")
        text = path.read_text(encoding="utf-8")
        assert text.endswith("\n")
        assert [json.loads(line)["unit"] for line in text.splitlines()] == ["a", "b"]

    def test_it_leaves_no_temp_file_behind(self, tmp_path):
        path = tmp_path / "cp.jsonl"
        with checkpointed_run(path, interval=0.0) as run:
            run.mark("a")
        assert [p.name for p in tmp_path.iterdir()] == ["cp.jsonl"]

    def test_it_creates_the_parent_directory(self, tmp_path):
        path = tmp_path / "deep" / "cp.jsonl"
        with checkpointed_run(path, interval=0.0) as run:
            run.mark("a")
        assert load_checkpoint(path).done == ["a"]


class TestResume:
    def test_a_resumed_run_does_not_redo_finished_units(self, tmp_path):
        path = tmp_path / "cp.jsonl"
        with checkpointed_run(path, interval=0.0) as run:
            run.mark("a")
            run.mark("b")
        fetched = []
        with checkpointed_run(path, interval=0.0) as run:
            for unit in ["a", "b", "c"]:
                if unit in run.done:
                    continue
                fetched.append(unit)
                run.mark(unit)
        assert fetched == ["c"]
        assert load_checkpoint(path).done == ["a", "b", "c"]

    def test_a_resumed_run_reports_the_torn_line_so_partial_output_is_discarded(self, tmp_path):
        path = tmp_path / "cp.jsonl"
        path.write_text('{"unit": "a"}\n{"unit": "b', encoding="utf-8")
        with checkpointed_run(path, interval=1e9) as run:
            assert run.torn is True
            assert run.resumed == 1

    def test_rewriting_after_a_torn_resume_leaves_no_torn_line(self, tmp_path):
        path = tmp_path / "cp.jsonl"
        path.write_text('{"unit": "a"}\n{"unit": "b', encoding="utf-8")
        with checkpointed_run(path, interval=0.0) as run:
            run.mark("b")
        state = load_checkpoint(path)
        assert state.done == ["a", "b"]
        assert state.torn is False


class TestSignals:
    def test_it_installs_no_handler_at_import_time(self):
        """A library that changes a process's signal disposition on import is a
        trap for every other caller."""
        for signum in (signal.SIGTERM, signal.SIGINT):
            owner = getattr(signal.getsignal(signum), "__self__", None)
            assert not isinstance(owner, CheckpointedRun)

    def test_it_installs_a_handler_inside_the_context_manager(self, tmp_path):
        with checkpointed_run(tmp_path / "cp.jsonl") as run:
            assert run.signals_installed is True
            assert signal.getsignal(signal.SIGINT) == run.on_signal
            assert signal.getsignal(signal.SIGTERM) == run.on_signal

    def test_it_restores_the_previous_handlers_on_exit(self, tmp_path):
        term = signal.getsignal(signal.SIGTERM)
        interrupt = signal.getsignal(signal.SIGINT)
        with checkpointed_run(tmp_path / "cp.jsonl"):
            pass
        assert signal.getsignal(signal.SIGTERM) is term
        assert signal.getsignal(signal.SIGINT) is interrupt

    def test_it_restores_the_previous_handlers_when_the_body_raises(self, tmp_path):
        term = signal.getsignal(signal.SIGTERM)
        with pytest.raises(ZeroDivisionError):
            with checkpointed_run(tmp_path / "cp.jsonl"):
                raise ZeroDivisionError
        assert signal.getsignal(signal.SIGTERM) is term

    def test_sigterm_flushes_and_then_exits(self, tmp_path):
        """The watchdog kill. Four hours of extraction died here."""
        path = tmp_path / "cp.jsonl"
        with checkpointed_run(path, interval=1e9) as run:
            run.mark("a")
            with pytest.raises(SystemExit) as excinfo:
                run.on_signal(signal.SIGTERM, None)
            assert excinfo.value.code == 128 + int(signal.SIGTERM)
            assert load_checkpoint(path).done == ["a"]

    def test_sigint_flushes_and_raises_keyboardinterrupt(self, tmp_path):
        path = tmp_path / "cp.jsonl"
        with checkpointed_run(path, interval=1e9) as run:
            run.mark("a")
            with pytest.raises(KeyboardInterrupt):
                run.on_signal(signal.SIGINT, None)
            assert load_checkpoint(path).done == ["a"]

    def test_a_caller_s_own_handler_still_runs_after_the_flush(self, tmp_path):
        path = tmp_path / "cp.jsonl"
        called = []
        previous = signal.signal(signal.SIGTERM, lambda *_: called.append("theirs"))
        try:
            with checkpointed_run(path, interval=1e9) as run:
                run.mark("a")
                run.on_signal(signal.SIGTERM, None)
            assert called == ["theirs"]
            assert load_checkpoint(path).done == ["a"]
        finally:
            signal.signal(signal.SIGTERM, previous)

    def test_it_reports_when_no_handler_could_be_installed(self, tmp_path):
        """Off the main thread `signal.signal` refuses, so a kill loses up to
        one interval. The run says so rather than implying it is protected."""
        lines = []
        with checkpointed_run(
            tmp_path / "cp.jsonl", log=lines.append, install_signals=False
        ) as run:
            assert run.signals_installed is False
        assert any("no signal handler" in line for line in lines)


class TestKeyCollisions:
    def test_a_string_and_an_integer_unit_are_different_units(self, tmp_path):
        """`"1"` and `1` both keyed to "1", so marking either reported the other
        as done and its work was skipped in silence."""
        from commoner_probe.checkpoint import checkpointed_run

        path = tmp_path / "cp.jsonl"
        with checkpointed_run(path, interval=0.0) as run:
            run.mark(1)
            assert 1 in run.done
            assert "1" not in run.done, "an int and a string are not one unit"

    def test_a_serialised_object_and_its_string_are_different_units(self, tmp_path):
        from commoner_probe.checkpoint import checkpointed_run

        path = tmp_path / "cp.jsonl"
        with checkpointed_run(path, interval=0.0) as run:
            run.mark({"a": 1})
            assert {"a": 1} in run.done
            assert '{"a": 1}' not in run.done

    def test_the_keys_survive_a_resume(self, tmp_path):
        from commoner_probe.checkpoint import checkpointed_run

        path = tmp_path / "cp.jsonl"
        with checkpointed_run(path, interval=0.0) as run:
            run.mark(1)
            run.mark("1")
            run.mark(("a", 2))
        with checkpointed_run(path, interval=0.0) as again:
            assert again.resumed == 3
            assert 1 in again.done
            assert "1" in again.done
            assert ("a", 2) in again.done
