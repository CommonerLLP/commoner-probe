# SPDX-License-Identifier: MIT
"""State RERA (Real Estate Regulatory Authority) project listings.

One module per state portal, plus ``common`` for what they share. ``STATES``
is the registry the ``rera`` subcommand reads: a state is registered here once
its adapter has been verified against the live portal.
"""

from __future__ import annotations

from .haryana import HaryanaReraProbe
from .karnataka import KarnatakaReraProbe

#: Registered states, keyed by the two-letter code the CLI accepts.
STATES: dict[str, type] = {
    "HR": HaryanaReraProbe,
    "KA": KarnatakaReraProbe,
}

__all__ = ["STATES", "HaryanaReraProbe", "KarnatakaReraProbe"]
