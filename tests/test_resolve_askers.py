"""Tests for the BaseProbe.resolve_askers helper.

This is the chokepoint where free-text asker names from API metadata
become stable entity_ids on records. The schema commitment for v0.5.0
is that ``asker_entity_ids`` is **always present** on every QA record,
with ``None`` entries when no resolver is configured or resolution
isn't confident.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from commoner_probe.entities import (
    EntityStore,
    populate_entity_store_from_mp_roster,
)
from commoner_probe.resolver import Resolver


class _Stub:
    """Minimal stub for any object with ``iter_members``."""

    def __init__(self, members):
        self._members = members

    def iter_members(self):
        return list(self._members)


class _FakeMember:
    def __init__(self, name, party, party_name, state, house):
        self.name = name
        self.party = party
        self.party_name = party_name
        self.state = state
        self.house = house


def _stub_crawler(out_dir: Path, resolver=None):
    """Build a minimally-functional BaseProbe for helper tests.

    Bypasses the network-fetching topic + RunLog by providing fake stubs.
    Only the helper under test (``resolve_askers``) is exercised.
    """
    from commoner_probe.base import BaseProbe

    class _FakeTopic:
        name = "fake"
        classifier_config: dict = {}

    probe = BaseProbe.__new__(BaseProbe)
    probe.topic = _FakeTopic()
    probe.out_dir = out_dir
    probe.pdf_dir = out_dir / "pdfs"
    probe.manifest = out_dir / "manifest.jsonl"
    probe.log_path = out_dir / "probe.log"
    probe.sleep = 0.0
    probe.session = None
    probe.topic_path = None
    probe.classifier_mode = "regex"
    from commoner_probe.runlog import RunLog
    probe.runlog = RunLog(out_dir)
    probe.resolver = resolver
    return probe


class ResolveAskersTests(unittest.TestCase):
    def test_no_resolver_returns_parallel_nulls(self):
        with tempfile.TemporaryDirectory() as tmp:
            crawler = _stub_crawler(Path(tmp))
            result = crawler.resolve_askers(["Pralhad Joshi", "Nirmala Sitharaman"])
        self.assertEqual(result, [None, None])

    def test_no_resolver_empty_input_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            crawler = _stub_crawler(Path(tmp))
            self.assertEqual(crawler.resolve_askers([]), [])
            self.assertEqual(crawler.resolve_askers(None), [])

    def test_resolver_resolves_known_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EntityStore(Path(tmp))
            roster = _Stub([
                _FakeMember("Pralhad Joshi", "BJP", "Bharatiya Janata Party", "Karnataka", "Lok Sabha"),
                _FakeMember("Nirmala Sitharaman", "BJP", "Bharatiya Janata Party", "Karnataka", "Rajya Sabha"),
            ])
            populate_entity_store_from_mp_roster(roster, store)
            crawler = _stub_crawler(Path(tmp), resolver=Resolver(store))
            result = crawler.resolve_askers(["Pralhad Joshi", "Nirmala Sitharaman"])
        self.assertEqual(len(result), 2)
        self.assertTrue(all(eid is not None for eid in result))
        self.assertTrue(all(eid.startswith("PERSON_") for eid in result))

    def test_resolver_returns_null_for_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EntityStore(Path(tmp))
            roster = _Stub([
                _FakeMember("Pralhad Joshi", "BJP", "Bharatiya Janata Party", "Karnataka", "Lok Sabha"),
            ])
            populate_entity_store_from_mp_roster(roster, store)
            crawler = _stub_crawler(Path(tmp), resolver=Resolver(store))
            result = crawler.resolve_askers(["Pralhad Joshi", "Some Unknown Person"])
        self.assertIsNotNone(result[0])
        self.assertIsNone(result[1])

    def test_parallel_list_length_preserved_regardless_of_resolution_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            crawler = _stub_crawler(Path(tmp))
            self.assertEqual(len(crawler.resolve_askers(["a", "b", "c"])), 3)


if __name__ == "__main__":
    unittest.main()
