"""Frozen smoke fixture: parser regression test, no network.

Drives ``CommitteeCrawler`` against the raw API payloads checked into
``examples/corpora/committees-smoke/raw/`` and asserts the resulting
manifest matches the canonical ``manifest.jsonl`` byte-for-byte (after
stripping volatile fields).

Two failure modes this distinguishes:

* **Parser drift.** Tests fail; live crawls may or may not fail. Fix
  the parser.
* **Upstream API change.** Live crawls fail; this fixture still passes.
  Refresh ``raw/*.json`` per ``examples/corpora/committees-smoke/README.md``.

Setting ``SANSAD_REGENERATE_FIXTURE=1`` rewrites the canonical
``manifest.jsonl`` from current parser output. Use only after
inspecting the live diff.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from sansad_crawler.committees import CommitteeCrawler
from sansad_crawler.topics import load_topic

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples" / "corpora" / "committees-smoke"
RAW = FIXTURE / "raw"
TOPIC = ROOT / "examples" / "topics" / "libraries.json"

# Fields that vary between runs even with identical input. Strip before
# comparing so the fixture stays byte-stable.
VOLATILE_FIELDS = frozenset({"run_id", "crawled_at", "elapsed_ms"})


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.status_code = 200

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        return None


class _FakeSession:
    def __init__(self, routes: dict[str, dict]) -> None:
        self.routes = routes

    def get(self, url: str, **_kwargs) -> _FakeResponse:
        for needle, payload in self.routes.items():
            if needle in url:
                return _FakeResponse(payload)
        raise AssertionError(f"FakeSession had no route matching: {url}")


def _scrub(record: dict) -> dict:
    return {k: v for k, v in record.items() if k not in VOLATILE_FIELDS}


def _run_against_fixture() -> list[dict]:
    """Drive the crawler against the frozen payloads and return scrubbed records."""
    ls_payload = json.loads((RAW / "ls_finance_p1.json").read_text(encoding="utf-8"))
    rs_payload = json.loads((RAW / "rs_health_p1.json").read_text(encoding="utf-8"))
    routes = {"api_ls/committee": ls_payload, "api_rs/committee": rs_payload}
    topic = load_topic(TOPIC)
    records: list[dict] = []
    for slug, fn_name in [("finance", "crawl_ls"), ("health", "crawl_rs")]:
        with tempfile.TemporaryDirectory() as tmp:
            crawler = CommitteeCrawler(
                topic,
                Path(tmp),
                sleep=0,
                lok_sabha_no=18,
                topic_path=TOPIC,
            )
            crawler.session = _FakeSession(routes)
            getattr(crawler, fn_name)(
                set(),
                committees=[slug],
                from_date=None,
                to_date=None,
                max_records=None,
                download=False,
            )
            for line in (Path(tmp) / "manifest.jsonl").read_text(encoding="utf-8").splitlines():
                records.append(_scrub(json.loads(line)))
    return records


class SmokeFixtureTests(unittest.TestCase):
    def test_canonical_manifest_matches_current_parser(self):
        produced = _run_against_fixture()

        if os.environ.get("SANSAD_REGENERATE_FIXTURE"):
            with (FIXTURE / "manifest.jsonl").open("w", encoding="utf-8") as f:
                for rec in produced:
                    f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
            self.skipTest("regenerated fixture; rerun without SANSAD_REGENERATE_FIXTURE=1")

        canonical = [
            json.loads(line)
            for line in (FIXTURE / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        # Compare as canonicalised JSON strings — dict ordering insensitive.
        produced_norm = [json.dumps(r, sort_keys=True) for r in produced]
        canonical_norm = [json.dumps(r, sort_keys=True) for r in canonical]
        self.assertEqual(produced_norm, canonical_norm)

    def test_fixture_records_carry_form_as_data_fields(self):
        records = _run_against_fixture()
        self.assertTrue(records, "fixture produced zero records")
        for r in records:
            self.assertIn("presented_via", r)
            self.assertIn("report_type", r)
            self.assertEqual(r["language_classified"], ["en"])
            self.assertEqual(r["kind"], "committee_report")


if __name__ == "__main__":
    unittest.main()
