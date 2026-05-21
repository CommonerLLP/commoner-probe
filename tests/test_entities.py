"""Tests for the entity store: identity, schema stability, persistence.

Coverage rationale: this module is the load-bearing piece for v0.5.0's
entity-id-on-records contract. If ``entity_id`` generation drifts
between runs, every record-to-entity link breaks. If round-trip
persistence loses data, the corpus splits from its identity layer.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sansad_crawler.entities import (
    EntityStore,
    MpMembership,
    Person,
    make_entity_id,
    normalize_name,
    slugify,
)


class NormalizeNameTests(unittest.TestCase):
    def test_strips_honorifics(self):
        self.assertEqual(normalize_name("Shri P.V. Joshi"), "joshi p v")
        self.assertEqual(normalize_name("Dr. Manmohan Singh"), "manmohan singh")
        self.assertEqual(normalize_name("Smt. Sushma Swaraj"), "sushma swaraj")

    def test_handles_comma_reversal(self):
        self.assertEqual(normalize_name("Joshi, P.V."), "joshi p v")
        self.assertEqual(normalize_name("Chaudhary, Shri P.P."), "chaudhary p p")

    def test_word_order_independent(self):
        self.assertEqual(
            normalize_name("Pralhad Joshi"),
            normalize_name("Joshi Pralhad"),
        )

    def test_empty_returns_empty(self):
        self.assertEqual(normalize_name(""), "")
        self.assertEqual(normalize_name(None), "")

    def test_does_not_strip_partial_honorific_inside_word(self):
        # "Dravid" should not lose "Dr"; honorific match is word-bounded.
        self.assertIn("dravid", normalize_name("Rahul Dravid"))


class SlugifyTests(unittest.TestCase):
    def test_preserves_word_order(self):
        # Slug retains original order (unlike normalize_name).
        self.assertEqual(slugify("Pralhad Joshi"), "pralhad_joshi")

    def test_strips_honorifics(self):
        self.assertEqual(slugify("Shri P.V. Joshi"), "p_v_joshi")


class EntityIdTests(unittest.TestCase):
    def test_deterministic_for_same_inputs(self):
        a = make_entity_id("Pralhad Joshi", "ls", "BJP")
        b = make_entity_id("Pralhad Joshi", "ls", "BJP")
        self.assertEqual(a, b)

    def test_different_context_produces_different_id(self):
        a = make_entity_id("Pralhad Joshi", "ls", "BJP")
        b = make_entity_id("Pralhad Joshi", "rs", "BJP")
        self.assertNotEqual(a, b)

    def test_format(self):
        eid = make_entity_id("Pralhad Joshi", "ls", "BJP")
        self.assertTrue(eid.startswith("PERSON_"))
        # 8 hex + underscore + slug
        self.assertRegex(eid, r"^PERSON_[0-9a-f]{8}_pralhad_joshi$")

    def test_case_insensitive_seed(self):
        # Canonical forms differing only in case should produce the same id.
        a = make_entity_id("Pralhad Joshi", "ls", "BJP")
        b = make_entity_id("PRALHAD JOSHI", "LS", "bjp")
        self.assertEqual(a, b)


class EntityStoreRoundTripTests(unittest.TestCase):
    def test_save_and_reload_yields_equivalent_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EntityStore(Path(tmp))
            eid = make_entity_id("Pralhad Joshi", "ls", "BJP")
            store.add_person(Person(entity_id=eid, canonical_name="Pralhad Joshi"))
            store.add_mp_membership(
                MpMembership(
                    entity_id=eid, house="ls", term=18, party="BJP",
                    party_name="Bharatiya Janata Party", state="Karnataka",
                )
            )
            store.save()

            reloaded = EntityStore(Path(tmp))
            reloaded.load()
            self.assertEqual(len(reloaded.people), 1)
            self.assertEqual(reloaded.people[eid].canonical_name, "Pralhad Joshi")
            self.assertEqual(len(reloaded.mp_memberships), 1)
            self.assertEqual(reloaded.mp_memberships[0].state, "Karnataka")

    def test_save_writes_readme_with_reserved_namespace(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EntityStore(Path(tmp))
            store.save()
            readme = (Path(tmp) / "entities" / "README.md").read_text()
            self.assertIn("entity_id", readme)
            self.assertIn("eci_nominations.jsonl", readme)
            self.assertIn("bureaucratic_external.jsonl", readme)
            self.assertIn("Reserved", readme)

    def test_add_person_is_idempotent_and_merges_alt_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EntityStore(Path(tmp))
            eid = make_entity_id("Pralhad Joshi", "ls", "BJP")
            store.add_person(Person(entity_id=eid, canonical_name="Pralhad Joshi"))
            store.add_person(Person(
                entity_id=eid, canonical_name="Pralhad Joshi",
                alt_names=["Joshi, P.V.", "Pralhad Venkatesh Joshi"],
            ))
            self.assertEqual(len(store.people), 1)
            person = store.people[eid]
            self.assertIn("Joshi, P.V.", person.alt_names)
            self.assertIn("Pralhad Venkatesh Joshi", person.alt_names)

    def test_dedup_on_membership(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EntityStore(Path(tmp))
            eid = make_entity_id("Pralhad Joshi", "ls", "BJP")
            m = MpMembership(entity_id=eid, house="ls", term=18, party="BJP")
            store.add_mp_membership(m)
            store.add_mp_membership(m)
            self.assertEqual(len(store.mp_memberships), 1)


class FindByNameTests(unittest.TestCase):
    def test_finds_by_canonical_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EntityStore(Path(tmp))
            eid = make_entity_id("Pralhad Joshi", "ls", "BJP")
            store.add_person(Person(entity_id=eid, canonical_name="Pralhad Joshi"))
            self.assertEqual(len(store.find_by_name("Pralhad Joshi")), 1)
            self.assertEqual(len(store.find_by_name("pralhad joshi")), 1)

    def test_finds_by_alt_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EntityStore(Path(tmp))
            eid = make_entity_id("Pralhad Joshi", "ls", "BJP")
            store.add_person(Person(
                entity_id=eid, canonical_name="Pralhad Joshi",
                alt_names=["Joshi, P.V."],
            ))
            self.assertEqual(len(store.find_by_name("Joshi, P.V.")), 1)
            self.assertEqual(len(store.find_by_name("joshi p v")), 1)

    def test_finds_by_honorific_stripped(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EntityStore(Path(tmp))
            eid = make_entity_id("Pralhad Joshi", "ls", "BJP")
            store.add_person(Person(entity_id=eid, canonical_name="Pralhad Joshi"))
            self.assertEqual(len(store.find_by_name("Shri Pralhad Joshi")), 1)
            self.assertEqual(len(store.find_by_name("Dr. Pralhad Joshi")), 1)

    def test_returns_multiple_when_same_name_two_entities(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EntityStore(Path(tmp))
            a = make_entity_id("Pralhad Joshi", "ls", "BJP")
            b = make_entity_id("Pralhad Joshi", "rs", "BJP")
            store.add_person(Person(entity_id=a, canonical_name="Pralhad Joshi"))
            store.add_person(Person(entity_id=b, canonical_name="Pralhad Joshi"))
            results = store.find_by_name("Pralhad Joshi")
            self.assertEqual(len(results), 2)

    def test_empty_input_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EntityStore(Path(tmp))
            self.assertEqual(store.find_by_name(""), [])
            self.assertEqual(store.find_by_name(None), [])


class JsonlStabilityTests(unittest.TestCase):
    def test_save_uses_sort_keys_for_byte_stability(self):
        """The fixture-friendly diff test: same inputs produce byte-identical output."""
        def write(tmp: Path) -> bytes:
            store = EntityStore(tmp)
            eid = make_entity_id("Pralhad Joshi", "ls", "BJP")
            person = Person(
                entity_id=eid, canonical_name="Pralhad Joshi",
                first_seen_at="2026-05-08T00:00:00",
                last_updated_at="2026-05-08T00:00:00",
            )
            store.add_person(person)
            store.save()
            return (tmp / "entities" / "people.jsonl").read_bytes()

        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            self.assertEqual(write(Path(tmp1)), write(Path(tmp2)))

    def test_people_jsonl_is_one_record_per_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EntityStore(Path(tmp))
            for nm in ["A", "B", "C"]:
                eid = make_entity_id(nm, "ls", "X")
                store.add_person(Person(entity_id=eid, canonical_name=nm))
            store.save()
            lines = (Path(tmp) / "entities" / "people.jsonl").read_text().splitlines()
            self.assertEqual(len(lines), 3)
            for line in lines:
                json.loads(line)  # parses


if __name__ == "__main__":
    unittest.main()
