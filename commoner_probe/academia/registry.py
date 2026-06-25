# SPDX-License-Identifier: MIT
"""Institution registry loader.

Ships ``institutions_registry.json`` (migrated from
academiaindia/docs/data/institutions_registry.json — 79 HEIs). Each entry has
``id``, ``name``, ``short_name``, ``type``, ``state``, ``career_page_url_guess``,
``parser``, etc.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

_REGISTRY_FILE = "institutions_registry.json"


def load_registry(registry_path: str | Path | None = None) -> list[dict]:
    """Load the institution registry. Defaults to the bundled JSON."""
    if registry_path is not None:
        return json.loads(Path(registry_path).read_text(encoding="utf-8"))
    ref = resources.files("commoner_probe.academia").joinpath(_REGISTRY_FILE)
    return json.loads(ref.read_text(encoding="utf-8"))


def select_institutions(registry: list[dict], ids: set[str] | None) -> list[dict]:
    """Return registry entries filtered to ``ids`` (None / empty = all)."""
    if not ids:
        return registry
    return [inst for inst in registry if inst.get("id") in ids]
