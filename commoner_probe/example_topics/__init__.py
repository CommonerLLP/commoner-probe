# SPDX-License-Identifier: AGPL-3.0-or-later
"""Built-in topic profile JSON files shipped with the package."""

from __future__ import annotations

from importlib import resources


def list_example_topics() -> list[str]:
    root = resources.files(__package__)
    return sorted(
        p.name[:-5]
        for p in root.iterdir()
        if p.name.endswith(".json")
    )


def load_example_topic_text(name: str) -> str:
    resource_name = f"{name}.json"
    try:
        return resources.files(__package__).joinpath(resource_name).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise KeyError(name) from exc
