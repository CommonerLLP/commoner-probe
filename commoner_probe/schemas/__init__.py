# SPDX-License-Identifier: AGPL-3.0-or-later
"""JSON Schema helpers for commoner-probe output streams.

Usage::

    from commoner_probe import schemas

    # List available schema names
    names = schemas.list_all()

    # Load a schema as a dict
    manifest_schema = schemas.load("manifest_committee_report")

Schema names correspond to the ``.schema.json`` files shipped under
``commoner_probe/schemas/``.  Each schema targets JSON Schema Draft 2020-12.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path


def _schemas_dir() -> Path:
    """Return the path to the bundled schemas directory."""
    try:
        # Python 3.9+ — importlib.resources.files()
        ref = resources.files(__name__)
        return Path(str(ref))
    except AttributeError:
        # Python 3.8 fallback (not declared in pyproject, but harmless)
        import importlib.resources as pkg_resources
        return Path(pkg_resources.path(__name__, "__init__.py").__enter__()).parent  # type: ignore[attr-defined]


def list_all() -> list[str]:
    """Return sorted schema names (without the ``.schema.json`` suffix)."""
    return sorted(
        p.name.removesuffix(".schema.json")
        for p in _schemas_dir().iterdir()
        if p.suffix == ".json" and p.name.endswith(".schema.json")
    )


def load(name: str) -> dict:
    """Load a schema by name and return it as a dict.

    ``name`` is the filename without the ``.schema.json`` suffix,
    e.g. ``"manifest_qa"`` or ``"atr_linkage"``.

    Raises :exc:`KeyError` if the schema does not exist.
    """
    path = _schemas_dir() / f"{name}.schema.json"
    if not path.exists():
        available = list_all()
        raise KeyError(
            f"Schema {name!r} not found. Available: {available}"
        )
    return json.loads(path.read_text(encoding="utf-8"))
