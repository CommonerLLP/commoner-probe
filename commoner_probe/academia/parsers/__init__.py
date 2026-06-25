# SPDX-License-Identifier: MIT
"""Institution parser registry.

Each parser exposes ``parse(html, url, fetched_at, pdf=None) -> list[dict]``.
The registry maps a registry ``parser`` name to its callable; ``get_parser``
falls back to ``generic`` for any institution whose specialised parser has not
been migrated yet (so unported institutions still produce records, degraded
rather than absent).

Explicit dict (not dynamic ``importlib``) so packaging/imports are predictable.

Migrated from academiaindia: every parser referenced by the current registry —
``generic``, ``iim_recruit``, ``iit_kanpur``, ``anna_university``,
``private_university``, ``iit_indore``, ``iit_rolling``, ``jnu``. The only origin
parsers not ported are ``iit_delhi`` and ``samarth_curec``, which no registry row
references (dead in the origin); they resolve to ``generic`` if ever named.
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
    jnu,
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
    "jnu": jnu.parse,
}

#: Origin parser names not migrated to the probe; they fall back to ``generic``.
#: Both are unreferenced by the current registry (dead in the origin), tracked so
#: callers/tests can see the coverage gap.
UNMIGRATED_PARSERS = frozenset({
    "iit_delhi", "samarth_curec",
})


def get_parser(name: str | None) -> Callable:
    """Return the parse callable for ``name``, falling back to ``generic``."""
    return PARSERS.get(name or "generic", PARSERS["generic"])
