# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class TopicProfile:
    name: str
    description: str
    search_groups: dict[str, list[str]]
    lok_sabha_ministries: list[str]
    rajya_sabha_ministry_likes: list[str]
    # Classifier config loaded from the topic JSON (if present). Stored here
    # so it travels with the corpus via _runs.jsonl for auditability — the
    # apparatus that produced the data is inseparable from the data itself.
    classifier_config: dict[str, Any] | None = None
    # Injected by compose (Layer 1) at startup via dataclasses.replace().
    # Called as filter_fn(title, query) -> bool before each record is kept.
    # None means keep everything — pure acquisition mode.
    filter_fn: Callable[[str, str], bool] | None = field(
        default=None, compare=False, hash=False, repr=False
    )

    def searches(self, max_buckets: int | None = None) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        for group, queries in self.search_groups.items():
            pairs.extend((group, query) for query in queries)
        return pairs[:max_buckets] if max_buckets is not None else pairs


def load_topic(path: str | Path) -> TopicProfile:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return TopicProfile(
        name=raw["name"],
        description=raw.get("description", ""),
        search_groups={k: list(v) for k, v in raw.get("search_groups", {}).items()},
        lok_sabha_ministries=list(raw.get("lok_sabha_ministries", [])),
        rajya_sabha_ministry_likes=list(raw.get("rajya_sabha_ministry_likes", [])),
        classifier_config=raw.get("classifier_config"),
    )
