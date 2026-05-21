from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TopicProfile:
    name: str
    description: str
    search_groups: dict[str, list[str]]
    lok_sabha_ministries: list[str]
    rajya_sabha_ministry_likes: list[str]

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
    )
