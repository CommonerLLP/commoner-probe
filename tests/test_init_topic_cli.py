from __future__ import annotations

import json
from pathlib import Path

import pytest

from commoner_probe.cli import build_parser
from commoner_probe.example_topics import list_example_topics
from commoner_probe.topics import load_topic


def _run_cli(args: list[str]) -> None:
    parser = build_parser()
    parsed = parser.parse_args(args)
    parsed.func(parsed)


def test_lists_expected_bundled_topics():
    assert set(list_example_topics()) == {
        "affirmative_action",
        "home_affairs_starred",
        "libraries",
        "mines_dmft_pmkkky",
        "narcotics_substance",
    }


def test_init_topic_writes_bundled_profile(tmp_path: Path):
    out = tmp_path / "my_topic.json"
    _run_cli(["init-topic", "--name", "libraries", "--out", str(out)])

    assert out.exists()
    topic = load_topic(out)
    assert topic.name == "libraries"


def test_init_topic_unknown_name_fails(tmp_path: Path):
    out = tmp_path / "bad_topic.json"
    with pytest.raises(SystemExit) as excinfo:
        _run_cli(["init-topic", "--name", "not_a_topic", "--out", str(out)])
    assert "unknown built-in topic" in str(excinfo.value)


def test_init_topic_refuses_overwrite_without_force(tmp_path: Path):
    out = tmp_path / "existing.json"
    out.write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        _run_cli(["init-topic", "--name", "libraries", "--out", str(out)])
    assert "output already exists" in str(excinfo.value)


def test_init_topic_overwrites_with_force(tmp_path: Path):
    out = tmp_path / "existing.json"
    out.write_text("{}", encoding="utf-8")

    _run_cli(["init-topic", "--name", "affirmative_action", "--out", str(out), "--force"])

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["name"] == "affirmative_action"
