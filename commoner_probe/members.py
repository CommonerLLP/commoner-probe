# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .http_client import make_session

if TYPE_CHECKING:
    from .http_client import StdlibSession


@dataclass(frozen=True)
class MemberInfo:
    name: str
    party: str
    party_name: str
    state: str | None = None
    house: str | None = None


class MPRoster:
    """Fetches and matches members from Sansad rosters (LS/RS)."""

    def __init__(self, session: StdlibSession | None = None):
        self.session = session or make_session()
        self._roster: dict[str, MemberInfo] = {}
        self._normalized_map: dict[str, str] = {}

    def load_ls(self) -> None:
        """Fetch and index Lok Sabha members."""
        url = "https://sansad.in/api_ls/member/member-list"
        r = self.session.get(url, timeout=30)
        r.raise_for_status()
        for row in r.json():
            name = (row.get("mpName") or "").strip()
            # LS API returns 'party' (slug) and 'partyName'
            party = (row.get("party") or "").strip()
            party_full = (row.get("partyName") or "").strip()
            info = MemberInfo(
                name=name,
                party=party,
                party_name=party_full,
                house="Lok Sabha",
            )
            self._add_to_roster(info)

    def load_rs(self) -> None:
        """Fetch and index Rajya Sabha members."""
        url = "https://sansad.in/api_rs/member/member-list"
        r = self.session.get(url, timeout=30)
        r.raise_for_status()
        for row in r.json():
            name = (row.get("mpName") or "").strip()
            # RS API returns 'partyCode' (slug) and 'partyName'
            party = (row.get("partyCode") or "").strip()
            party_full = (row.get("partyName") or "").strip()
            info = MemberInfo(
                name=name,
                party=party,
                party_name=party_full,
                house="Rajya Sabha",
            )
            self._add_to_roster(info)

    def _add_to_roster(self, info: MemberInfo) -> None:
        # Use full name as primary key
        self._roster[info.name] = info
        # Also index by normalized name for fuzzy matching
        norm = self.normalize_name(info.name)
        if norm:
            self._normalized_map[norm] = info.name

    @staticmethod
    def normalize_name(name: str) -> str:
        """Strip titles and punctuation for robust matching."""
        if not name:
            return ""
        s = name
        # Handle "Surname, Name" -> "Name Surname"
        if "," in s:
            parts = [p.strip() for p in s.split(",")]
            if len(parts) == 2:
                s = f"{parts[1]} {parts[0]}"
        # Remove common titles/honorifics (anywhere in string)
        s = re.sub(
            r"\b(Shri|Smt|Dr|Prof|Babu|Ven'ble|Kumari|Sushri|Sardar)\b\.?\s*",
            "",
            s,
            flags=re.I,
        )
        # Lowercase, strip dots, and strip non-alphanumeric (except space)
        s = s.lower().replace(".", " ")
        s = re.sub(r"[^a-z0-9\s]", "", s)
        # Sort words for a canonical word-order-independent form
        words = sorted([w for w in s.split() if w])
        return " ".join(words)

    def iter_members(self) -> "list[MemberInfo]":
        """Public iterator over members. Used by entity-store adapters."""
        return list(self._roster.values())

    def lookup(self, name: str) -> MemberInfo | None:
        """Find a member by exact or normalized name."""
        if not name:
            return None
        # Try exact first
        if name in self._roster:
            return self._roster[name]
        # Try normalized
        norm = self.normalize_name(name)
        orig_name = self._normalized_map.get(norm)
        if orig_name:
            return self._roster[orig_name]
        return None


def fetch_committee_members(house: str, code: int, ls_no: int = 18) -> list[dict]:
    """Fetch raw committee composition for a given house and code."""
    session = make_session()
    if house.lower() == "ls":
        url = f"https://sansad.in/api_ls/committee/committeeMembers?committeeCode={code}&lsNo={ls_no}"
        r = session.get(url, timeout=30)
        if r.status_code == 200:
            return r.json()
    return []
