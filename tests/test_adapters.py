"""Tests for the MPRoster -> EntityStore adapter.

Coverage rationale: this is the bridge between the existing v0.4.0
``MPRoster`` (which fetches from sansad.in APIs) and the v0.5.0
``EntityStore`` (which persists identity). If this adapter drifts,
identity records and corpus records de-sync.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from commoner_probe.entities import (
    EntityStore,
    populate_entity_store_from_mp_roster,
)


@dataclass
class _FakeMember:
    name: str
    party: str
    party_name: str
    state: str | None
    house: str


class _FakeRoster:
    """Duck-typed substitute for MPRoster in tests."""

    def __init__(self, members: list[_FakeMember]):
        self._members = members

    def iter_members(self) -> list[_FakeMember]:
        return list(self._members)


class AdapterTests(unittest.TestCase):
    def _make_roster(self) -> _FakeRoster:
        return _FakeRoster([
            _FakeMember(
                name="Pralhad Joshi",
                party="BJP",
                party_name="Bharatiya Janata Party",
                state="Karnataka",
                house="Lok Sabha",
            ),
            _FakeMember(
                name="Nirmala Sitharaman",
                party="BJP",
                party_name="Bharatiya Janata Party",
                state="Karnataka",
                house="Rajya Sabha",
            ),
        ])

    def test_populate_creates_one_person_one_membership_per_member(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EntityStore(Path(tmp))
            people_added, memberships_added = populate_entity_store_from_mp_roster(
                self._make_roster(), store
            )
        self.assertEqual(people_added, 2)
        self.assertEqual(memberships_added, 2)
        self.assertEqual(len(store.people), 2)
        self.assertEqual(len(store.mp_memberships), 2)

    def test_populate_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EntityStore(Path(tmp))
            populate_entity_store_from_mp_roster(self._make_roster(), store)
            people_added, memberships_added = populate_entity_store_from_mp_roster(
                self._make_roster(), store
            )
        self.assertEqual(people_added, 0)
        self.assertEqual(memberships_added, 0)

    def test_ls_members_get_default_term(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EntityStore(Path(tmp))
            populate_entity_store_from_mp_roster(self._make_roster(), store, ls_term=17)
        ls_memberships = [m for m in store.mp_memberships if m.house == "ls"]
        self.assertEqual(len(ls_memberships), 1)
        self.assertEqual(ls_memberships[0].term, 17)

    def test_rs_members_get_no_term(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EntityStore(Path(tmp))
            populate_entity_store_from_mp_roster(self._make_roster(), store)
        rs_memberships = [m for m in store.mp_memberships if m.house == "rs"]
        self.assertEqual(len(rs_memberships), 1)
        self.assertIsNone(rs_memberships[0].term)

    def test_house_label_normalised_to_short_form(self):
        roster = _FakeRoster([
            _FakeMember(name="X", party="A", party_name="A Party", state=None, house="Lok Sabha"),
            _FakeMember(name="Y", party="B", party_name="B Party", state=None, house="Rajya Sabha"),
            _FakeMember(name="Z", party="C", party_name="C Party", state=None, house="LS"),  # already short
        ])
        with tempfile.TemporaryDirectory() as tmp:
            store = EntityStore(Path(tmp))
            populate_entity_store_from_mp_roster(roster, store)
        houses = sorted(m.house for m in store.mp_memberships)
        self.assertEqual(houses, ["ls", "ls", "rs"])

    def test_persists_through_save_load_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EntityStore(Path(tmp))
            populate_entity_store_from_mp_roster(self._make_roster(), store)
            store.save()
            reloaded = EntityStore(Path(tmp))
            reloaded.load()
        self.assertEqual(len(reloaded.people), 2)
        self.assertEqual(len(reloaded.mp_memberships), 2)


if __name__ == "__main__":
    unittest.main()
