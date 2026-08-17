# SPDX-License-Identifier: MIT
"""Registry of NeVA (National e-Vidhan Application) state portals.

Bakes the ``portal_code -> state_code / state_name / chamber`` mapping into
the package so a house can be crawled or probed without hand-passing
``--portal``/``--state``.

Portal subdomains and state codes verified live 2026-07 (HTTP 200 on
``https://{portal}.neva.gov.in/`` for every entry; state names cross-checked
against each portal's homepage ``<title>``, e.g. "Welcome to Bihar Vidhan
Sabha ..." for ``bla``). Councils are the six states with a bicameral
legislature (Legislative Council in addition to the Legislative Assembly):
Andhra Pradesh, Bihar, Karnataka, Maharashtra, Telangana, Uttar Pradesh.

Coverage note (from the filing issue): NeVA's own status is ~28 of 36 Houses
signed on with ~20 fully digital — so portal *reachability* here does not
imply data *depth*. Use ``state-assembly-probe`` to find out which onboarded
houses actually expose Q&A/papers/members data.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NevaPortal:
    portal_code: str
    state_code: str
    state_name: str
    chamber: str  # "assembly" | "council"


ASSEMBLIES: tuple[NevaPortal, ...] = (
    NevaPortal("apa", "AP", "Andhra Pradesh", "assembly"),
    NevaPortal("arla", "AR", "Arunachal Pradesh", "assembly"),
    NevaPortal("asla", "AS", "Assam", "assembly"),
    NevaPortal("bla", "BR", "Bihar", "assembly"),
    NevaPortal("chg", "CG", "Chhattisgarh", "assembly"),
    NevaPortal("dla", "DL", "Delhi", "assembly"),
    NevaPortal("goa", "GA", "Goa", "assembly"),
    NevaPortal("gujarat", "GJ", "Gujarat", "assembly"),
    NevaPortal("hpvs", "HP", "Himachal Pradesh", "assembly"),
    NevaPortal("hrla", "HR", "Haryana", "assembly"),
    NevaPortal("jhla", "JH", "Jharkhand", "assembly"),
    NevaPortal("jkla", "JK", "Jammu and Kashmir", "assembly"),
    NevaPortal("kerala", "KL", "Kerala", "assembly"),
    NevaPortal("kla", "KA", "Karnataka", "assembly"),
    NevaPortal("manipur", "MN", "Manipur", "assembly"),
    NevaPortal("mgla", "ML", "Meghalaya", "assembly"),
    NevaPortal("mhla", "MH", "Maharashtra", "assembly"),
    NevaPortal("mizo", "MZ", "Mizoram", "assembly"),
    NevaPortal("mpla", "MP", "Madhya Pradesh", "assembly"),
    NevaPortal("nagaland", "NL", "Nagaland", "assembly"),
    NevaPortal("odisha", "OD", "Odisha", "assembly"),
    NevaPortal("puddu", "PY", "Puducherry", "assembly"),
    NevaPortal("pvs", "PB", "Punjab", "assembly"),
    NevaPortal("raj", "RJ", "Rajasthan", "assembly"),
    NevaPortal("sikkim", "SK", "Sikkim", "assembly"),
    NevaPortal("tnla", "TN", "Tamil Nadu", "assembly"),
    NevaPortal("tripura", "TR", "Tripura", "assembly"),
    NevaPortal("tsla", "TG", "Telangana", "assembly"),
    NevaPortal("upvs", "UP", "Uttar Pradesh", "assembly"),
    NevaPortal("utkh", "UK", "Uttarakhand", "assembly"),
    NevaPortal("wbla", "WB", "West Bengal", "assembly"),
)

COUNCILS: tuple[NevaPortal, ...] = (
    NevaPortal("apc", "AP", "Andhra Pradesh", "council"),
    NevaPortal("blc", "BR", "Bihar", "council"),
    NevaPortal("klc", "KA", "Karnataka", "council"),
    NevaPortal("mhlc", "MH", "Maharashtra", "council"),
    NevaPortal("tslc", "TG", "Telangana", "council"),
    NevaPortal("uplc", "UP", "Uttar Pradesh", "council"),
)

ALL_PORTALS: tuple[NevaPortal, ...] = ASSEMBLIES + COUNCILS

_BY_CODE: dict[str, NevaPortal] = {p.portal_code: p for p in ALL_PORTALS}


def get_portal(portal_code: str) -> NevaPortal | None:
    return _BY_CODE.get(portal_code)


def iter_portals(*, chamber: str | None = None) -> tuple[NevaPortal, ...]:
    """All registered portals, optionally filtered to ``"assembly"`` or ``"council"``."""
    if chamber is None:
        return ALL_PORTALS
    return tuple(p for p in ALL_PORTALS if p.chamber == chamber)
