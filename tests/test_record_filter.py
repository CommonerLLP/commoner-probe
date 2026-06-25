"""Tests for TopicProfile.record_filter_fn — the record-level acquisition filter.

``filter_fn`` only sees ``(title, query)`` at acquisition, so callers that must
match on fields built later (e.g. ``answer_text``) used to keep every acquired
row and drop the non-matches downstream. That made ``max_records`` cap acquired
rows rather than kept rows, and left the per-bucket ``no_match``/``kept``
counters reporting acquisitions instead of what was actually kept.

``record_filter_fn(record) -> bool`` runs after the full record is built but
before it is downloaded/enriched/appended/counted, so the cap and the counters
stay aligned with the rows that survive the filter.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from commoner_probe.sansad import SansadProbe
from commoner_probe.topics import TopicProfile


def _rs_row(qno: str, ans_text: str) -> dict:
    return {
        "qslno": qno,
        "ses_no": 261,
        "qtitle": f"Question {qno}",
        "ans_date": "02.01.2026",
        "qtype": "Unstarred",
        "qno": qno,
        "min_name": "Culture",
        "name": "MP One",
        "qn_text": "Question text",
        "ans_text": ans_text,
        "files": "",
        "hindifiles": "",
        "status": "Answered",
    }


def _make_rs_probe(out_dir: Path, rows: list[dict], record_filter_fn):
    topic = TopicProfile(
        name="contract",
        description="",
        search_groups={"g": ["q"]},
        lok_sabha_ministries=["Culture"],
        rajya_sabha_ministry_likes=["Culture"],
        classifier_config={"mode": "contract"},
        record_filter_fn=record_filter_fn,
    )
    probe = SansadProbe(topic, out_dir, sleep=0)
    probe.rs_search_session = lambda ses_no, ministry_like: rows  # stub HTTP
    probe._enrich_askers = lambda rec: None  # stub roster (network) for kept rows
    buckets: list[dict] = []
    probe.runlog.record_bucket = lambda **kw: buckets.append(kw)
    return probe, buckets


def _manifest(out_dir: Path) -> list[dict]:
    path = out_dir / "manifest.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# Keep only rows whose answer_text mentions "library" — a field filter_fn
# cannot see at acquisition time.
def _keeps_library(rec: dict) -> bool:
    return "library" in (rec.get("answer_text") or "").lower()


class RecordFilterRSTests(unittest.TestCase):
    def test_record_filter_drops_nonmatching_rows_from_manifest_seen_and_count(self):
        rows = [
            _rs_row("1", "About roads."),                 # dropped
            _rs_row("2", "The National Library expands."),  # kept
            _rs_row("3", "About bridges."),               # dropped
            _rs_row("4", "Public library funding."),      # kept
        ]
        with TemporaryDirectory() as td:
            out = Path(td)
            probe, buckets = _make_rs_probe(out, rows, _keeps_library)
            seen: set[str] = set()
            added = probe.probe_rs(
                seen, sessions=[261], from_date=None, to_date=None,
                qtype_filter=None, limit=None, max_buckets=None,
                max_records=None, download=False,
            )

            self.assertEqual(added, 2)
            written = _manifest(out)
            self.assertEqual([r["qno"] for r in written], ["2", "4"])
            # Dropped rows must not pollute the seen set.
            self.assertEqual(len(seen), 2)
            # Per-bucket counters reflect kept vs filtered, not acquisitions.
            self.assertEqual(buckets[-1]["no_match"], 2)
            self.assertEqual(buckets[-1]["kept"], 2)

    def test_max_records_caps_kept_rows_not_acquired_rows(self):
        # First acquired row is a non-match; max_records=1 must still yield the
        # first *matching* row, not stop after acquiring the non-match.
        rows = [
            _rs_row("1", "About roads."),                 # dropped
            _rs_row("2", "The National Library expands."),  # kept (the one)
            _rs_row("3", "Public library funding."),      # would be kept, but capped
        ]
        with TemporaryDirectory() as td:
            out = Path(td)
            probe, _ = _make_rs_probe(out, rows, _keeps_library)
            seen: set[str] = set()
            added = probe.probe_rs(
                seen, sessions=[261], from_date=None, to_date=None,
                qtype_filter=None, limit=None, max_buckets=None,
                max_records=1, download=False,
            )

            self.assertEqual(added, 1)
            written = _manifest(out)
            self.assertEqual([r["qno"] for r in written], ["2"])

    def test_no_record_filter_keeps_everything(self):
        rows = [_rs_row("1", "anything"), _rs_row("2", "anything else")]
        with TemporaryDirectory() as td:
            out = Path(td)
            probe, buckets = _make_rs_probe(out, rows, None)
            seen: set[str] = set()
            added = probe.probe_rs(
                seen, sessions=[261], from_date=None, to_date=None,
                qtype_filter=None, limit=None, max_buckets=None,
                max_records=None, download=False,
            )
            self.assertEqual(added, 2)
            self.assertEqual(buckets[-1]["no_match"], 0)
            self.assertEqual(buckets[-1]["kept"], 2)


class RecordFilterLSTests(unittest.TestCase):
    def _ls_item(self, uuid: str, title: str) -> dict:
        return {
            "uuid": uuid,
            "handle": f"123/{uuid}",
            "metadata": {
                "dc.title": [{"value": title}],
                "dc.date.issued": [{"value": "2026-01-01"}],
                "dc.identifier.questiontype": [{"value": "Unstarred"}],
                "dc.identifier.questionnumber": [{"value": uuid}],
            },
        }

    def test_record_filter_applies_to_ls(self):
        items = [
            self._ls_item("1", "Road construction update"),   # dropped
            self._ls_item("2", "National Library mission"),    # kept
        ]
        topic = TopicProfile(
            name="contract", description="", search_groups={"g": ["q"]},
            lok_sabha_ministries=["Culture"], rajya_sabha_ministry_likes=["Culture"],
            record_filter_fn=lambda rec: "library" in (rec.get("title") or "").lower(),
        )
        with TemporaryDirectory() as td:
            out = Path(td)
            probe = SansadProbe(topic, out, sleep=0)
            probe.ls_search_all = lambda query, ministry, limit: iter(items)
            probe._enrich_askers = lambda rec: None
            seen: set[str] = set()
            added = probe.probe_ls(
                seen, from_date=None, to_date=None, qtype_filter=None,
                limit=None, max_buckets=None, max_records=None, download=False,
            )
            self.assertEqual(added, 1)
            written = _manifest(out)
            self.assertEqual([r["title"] for r in written], ["National Library mission"])


if __name__ == "__main__":
    unittest.main()
