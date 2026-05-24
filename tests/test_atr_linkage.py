"""Tests for the ATR → original-report linkage extractor."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from commoner_probe.atr_linkage import (
    EXTRACTOR_VERSION,
    _compute_referenced_key,
    _extract_referenced_report_no,
    _words_to_int,
    extract_atr_linkages,
)


class ExtractReferencedReportNoTests(unittest.TestCase):

    def test_canonical_atr_title(self):
        title = (
            "Report on Action taken by the Government on the Observations/"
            "Recommendations contained in the 24th Report of the Standing "
            "Committee on Finance"
        )
        self.assertEqual(_extract_referenced_report_no(title), 24)

    def test_with_ordinal_suffix_st(self):
        self.assertEqual(
            _extract_referenced_report_no("Action Taken on the 1st Report"), 1,
        )

    def test_with_ordinal_suffix_rd(self):
        self.assertEqual(
            _extract_referenced_report_no("Action Taken on the 3rd Report"), 3,
        )

    def test_no_ordinal_suffix(self):
        # Not all titles have the ordinal — bare "5 Report" should match.
        self.assertEqual(
            _extract_referenced_report_no("Action Taken on the 5 Report"), 5,
        )

    def test_report_no_form(self):
        self.assertEqual(
            _extract_referenced_report_no("Action Taken Report on Report No. 47"), 47,
        )

    def test_no_match_returns_none(self):
        self.assertIsNone(_extract_referenced_report_no("Action Taken Report"))
        self.assertIsNone(_extract_referenced_report_no(""))
        self.assertIsNone(_extract_referenced_report_no(None))

    def test_anchored_match_beats_atr_own_number(self):
        # Real RS Education committee title format: the ATR's OWN number
        # (374) appears earlier in the title than the referenced one
        # (366). Without the "contained in the" anchor, the regex would
        # return 374 (the ATR's number) — wrong. The anchor must win.
        title = (
            "374th Report on Action Taken by the Government on the "
            "Recommendations/Observations contained in the Three Hundred "
            "And Sixty Sixth Report on Demands for Grants 2025-26 of the "
            "Ministry of Youth Affairs"
        )
        self.assertEqual(_extract_referenced_report_no(title), 366)

    def test_words_form_at_anchor(self):
        title = (
            "Action Taken on the Observations contained in the Three "
            "Hundred and Twenty Second Report"
        )
        self.assertEqual(_extract_referenced_report_no(title), 322)

    def test_digit_form_at_anchor(self):
        title = "Action Taken on the Observations contained in the 24th Report"
        self.assertEqual(_extract_referenced_report_no(title), 24)


class WordsToIntTests(unittest.TestCase):

    def test_units(self):
        self.assertEqual(_words_to_int("five"), 5)
        self.assertEqual(_words_to_int("nineteen"), 19)

    def test_tens(self):
        self.assertEqual(_words_to_int("twenty four"), 24)
        self.assertEqual(_words_to_int("ninety nine"), 99)

    def test_hundreds(self):
        self.assertEqual(_words_to_int("one hundred"), 100)
        self.assertEqual(_words_to_int("three hundred and forty seven"), 347)

    def test_ordinals(self):
        # The committee numbering style is ordinal — "Sixty Sixth", not "Sixty Six".
        self.assertEqual(_words_to_int("sixty sixth"), 66)
        self.assertEqual(_words_to_int("third"), 3)
        self.assertEqual(_words_to_int("twentieth"), 20)
        self.assertEqual(
            _words_to_int("three hundred and sixty sixth"), 366,
        )

    def test_capitalisation_irrelevant(self):
        self.assertEqual(_words_to_int("Three Hundred And Sixty Sixth"), 366)

    def test_unrecognised_returns_none(self):
        self.assertIsNone(_words_to_int(""))
        self.assertIsNone(_words_to_int("foo bar baz"))
        self.assertIsNone(_words_to_int(None))


class ComputeReferencedKeyTests(unittest.TestCase):

    def test_ls_key_with_loksabha_no(self):
        rec = {
            "house": "Lok Sabha",
            "committee_slug": "finance",
            "loksabha_no": 18,
        }
        self.assertEqual(_compute_referenced_key(rec, 24), "LS|finance|24|18")

    def test_rs_key_no_loksabha_no(self):
        rec = {
            "house": "Rajya Sabha",
            "committee_slug": "education",
        }
        self.assertEqual(_compute_referenced_key(rec, 322), "RS|education|322")

    def test_ls_without_loksabha_returns_none(self):
        rec = {"house": "Lok Sabha", "committee_slug": "finance"}
        self.assertIsNone(_compute_referenced_key(rec, 1))

    def test_no_committee_slug_returns_none(self):
        rec = {"house": "Lok Sabha", "loksabha_no": 18}
        self.assertIsNone(_compute_referenced_key(rec, 1))


class ExtractAtrLinkagesIntegrationTests(unittest.TestCase):

    def _write_manifest(self, out: Path, rows: list[dict]) -> None:
        (out / "manifest.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n",
            encoding="utf-8",
        )

    def test_extracts_linkage_from_ls_atr(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self._write_manifest(out, [
                {
                    "key": "LS|finance|33|18",
                    "kind": "committee_report",
                    "house": "Lok Sabha",
                    "committee_slug": "finance",
                    "loksabha_no": 18,
                    "report_no": 33,
                    "report_type": "action_taken",
                    "title": (
                        "Report on Action taken by the Government on the "
                        "Observations/Recommendations contained in the 24th "
                        "Report of the Standing Committee on Finance"
                    ),
                },
            ])
            stats = extract_atr_linkages(out, log_fn=lambda *_: None)
            self.assertEqual(stats.atr_records_seen, 1)
            self.assertEqual(stats.linkages_extracted, 1)
            rows = json.loads((out / "atr_linkage.jsonl").read_text())
            self.assertEqual(rows["atr_key"], "LS|finance|33|18")
            self.assertEqual(rows["references_report_no"], 24)
            self.assertEqual(rows["references_report_key"], "LS|finance|24|18")
            self.assertEqual(rows["extractor"], EXTRACTOR_VERSION)

    def test_skips_non_atr_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self._write_manifest(out, [
                {
                    "key": "LS|finance|34|18",
                    "kind": "committee_report",
                    "report_type": "demands_for_grants",
                    "title": "Report on Demands for Grants",
                },
                {
                    "key": "LS|finance|33|18",
                    "kind": "committee_report",
                    "report_type": "action_taken",
                    "house": "Lok Sabha",
                    "committee_slug": "finance",
                    "loksabha_no": 18,
                    "title": "Action Taken on the 24th Report",
                },
            ])
            stats = extract_atr_linkages(out, log_fn=lambda *_: None)
            self.assertEqual(stats.atr_records_seen, 1)  # the DFG one was skipped
            self.assertEqual(stats.linkages_extracted, 1)

    def test_titles_without_match_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self._write_manifest(out, [
                {
                    "key": "LS|finance|33|18",
                    "kind": "committee_report",
                    "report_type": "action_taken",
                    "house": "Lok Sabha",
                    "committee_slug": "finance",
                    "loksabha_no": 18,
                    "title": "Action Taken Report",  # no number
                },
            ])
            stats = extract_atr_linkages(out, log_fn=lambda *_: None)
            self.assertEqual(stats.atr_records_seen, 1)
            self.assertEqual(stats.linkages_extracted, 0)
            self.assertEqual(stats.titles_without_match, 1)

    def test_returns_empty_when_manifest_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            stats = extract_atr_linkages(Path(tmp), log_fn=lambda *_: None)
            self.assertEqual(stats.atr_records_seen, 0)
            self.assertFalse((Path(tmp) / "atr_linkage.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
