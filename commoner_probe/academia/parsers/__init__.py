# SPDX-License-Identifier: MIT
"""Institution parser registry.

Each parser exposes ``parse(html, url, fetched_at, pdf=None) -> list[dict]``.
The registry maps a registry ``parser`` name to its callable; ``get_parser``
falls back to ``generic`` for any institution whose specialised parser has not
been migrated yet (so unported institutions still produce records, degraded
rather than absent).

Explicit dict (not dynamic ``importlib``) so packaging/imports are predictable.

Migrated from academiaindia: ``generic``, ``iim_recruit``. Remaining origin
parsers (``iit_rolling``, ``iit_kanpur``, ``iit_indore``, ``iit_delhi``,
``jnu``, ``anna_university``, ``private_university``, ``samarth_curec``) are
follow-on ports — until then they resolve to ``generic``.
"""

from __future__ import annotations

from typing import Callable

from . import (
    anna_university,
    generic,
    iim_recruit,
    iit_indore,
    iit_kanpur,
    iit_rolling,
    private_university,
)

PARSERS: dict[str, Callable] = {
    "generic": generic.parse,
    "iim_recruit": iim_recruit.parse,
    "iit_kanpur": iit_kanpur.parse,
    "anna_university": anna_university.parse,
    "private_university": private_university.parse,
    "iit_indore": iit_indore.parse,
    "iit_rolling": iit_rolling.parse,
}

#: Registry parser names that exist in the origin but are not yet migrated; they
#: fall back to ``generic``. Tracked so callers/tests can see the coverage gap.
#: (iit_delhi / jnu / samarth_curec are unreferenced by the current registry.)
UNMIGRATED_PARSERS = frozenset({
    "iit_delhi", "jnu", "samarth_curec",
})


def get_parser(name: str | None) -> Callable:
    """Return the parse callable for ``name``, falling back to ``generic``."""
    return PARSERS.get(name or "generic", PARSERS["generic"])
