"""Tests for the four-way committee report_type classifier.

Pre-v0.6.3 the classifier was binary (`action_taken` vs `original`),
which lumped Demands for Grants reports, Bill examinations, and Subject
(own-initiative) reports into one bucket. The pipeline downstream then
called every record from those reports `dfg_recommendation` regardless
of true source. v0.6.3 fixes this:

* `_report_type()` now returns one of:
  `action_taken` | `demands_for_grants` | `bill` | `subject` | `other`.
* Each output record in `answers.jsonl` carries `source_report_type`
  so consumers can filter cleanly.

Title fixtures are taken from real sansad.in committee report titles
across a representative span of Departmentally Related Standing
Committees in both houses.
"""

from __future__ import annotations

import unittest

from commoner_probe.committees import (
    REPORT_TYPE_ACTION_TAKEN,
    REPORT_TYPE_BILL,
    REPORT_TYPE_DFG,
    REPORT_TYPE_OTHER,
    REPORT_TYPE_SUBJECT,
    REPORT_TYPES_KNOWN,
    _report_type,
)


class ActionTakenReportTests(unittest.TestCase):

    def test_canonical_atr_title(self):
        title = (
            "Action Taken by the Government on the Observations/Recommendations "
            "contained in the 24th Report of the Standing Committee on Finance"
        )
        self.assertEqual(_report_type(title), REPORT_TYPE_ACTION_TAKEN)

    def test_atr_with_hyphen(self):
        # "action-taken" with a hyphen also occurs.
        self.assertEqual(
            _report_type("Action-Taken Report on the 17th Report"),
            REPORT_TYPE_ACTION_TAKEN,
        )

    def test_atr_takes_priority_over_dfg_in_title(self):
        # A common pattern: an ATR on a previous DFG report carries both
        # phrases. ATR must win.
        title = (
            "Action Taken by the Government on the Recommendations contained "
            "in the Report on Demands for Grants (2025-26) of the Ministry of X"
        )
        self.assertEqual(_report_type(title), REPORT_TYPE_ACTION_TAKEN)


class DemandsForGrantsTests(unittest.TestCase):

    def test_dfg_canonical(self):
        title = "Report on 'Demands for Grants (2026-27)' of the Ministry of Finance"
        self.assertEqual(_report_type(title), REPORT_TYPE_DFG)

    def test_dfg_with_curly_quotes(self):
        # Sansad.in titles regularly come with smart quotes from Word.
        title = "Report on ‘Demands for Grants (2026-27)’ of the Ministry of Planning"
        self.assertEqual(_report_type(title), REPORT_TYPE_DFG)

    def test_dfg_with_extra_apostrophes(self):
        # Real corpus title: "''''Demands for Grants (2026-27)' ..."
        title = "Report on ''''Demands for Grants (2026-27)' of the Ministry of Finance"
        self.assertEqual(_report_type(title), REPORT_TYPE_DFG)

    def test_dfg_singular_grant(self):
        # The regex tolerates "Demand for Grant" singular even though
        # the canonical phrasing is plural — defensive.
        self.assertEqual(
            _report_type("Demand for Grant of the Ministry of X"),
            REPORT_TYPE_DFG,
        )


class BillReportTests(unittest.TestCase):

    def test_named_bill_with_year(self):
        self.assertEqual(
            _report_type("The Insolvency and Bankruptcy Code (Amendment) Bill, 2025"),
            REPORT_TYPE_BILL,
        )

    def test_examination_of_bill(self):
        self.assertEqual(
            _report_type("Examination of the Mediation Bill, 2023"),
            REPORT_TYPE_BILL,
        )

    def test_provisions_of_bill(self):
        self.assertEqual(
            _report_type("Provisions of the Digital Personal Data Protection Bill"),
            REPORT_TYPE_BILL,
        )

    def test_billion_does_not_match(self):
        # "billion" must NOT be classified as a Bill report.
        self.assertNotEqual(
            _report_type("India's billion-rupee financial inclusion roadmap"),
            REPORT_TYPE_BILL,
        )

    def test_billboard_does_not_match(self):
        self.assertNotEqual(
            _report_type("Outdoor advertising and billboard regulation"),
            REPORT_TYPE_BILL,
        )


class SubjectReportTests(unittest.TestCase):

    def test_review_of_working(self):
        self.assertEqual(
            _report_type("Review of working of Insolvency and Bankruptcy Code and Emerging Issues"),
            REPORT_TYPE_SUBJECT,
        )

    def test_performance_review(self):
        self.assertEqual(
            _report_type("Performance Review of National Statistical Commission"),
            REPORT_TYPE_SUBJECT,
        )

    def test_roadmap(self):
        self.assertEqual(
            _report_type("Roadmap for Indian economic growth in light of global circumstances"),
            REPORT_TYPE_SUBJECT,
        )

    def test_evolving_role(self):
        self.assertEqual(
            _report_type(
                "Evolving Role of Competition Commission of India in the Economy, "
                "particularly the Digital Sector"
            ),
            REPORT_TYPE_SUBJECT,
        )

    def test_functioning_of(self):
        self.assertEqual(
            _report_type("Functioning of the Reserve Bank of India"),
            REPORT_TYPE_SUBJECT,
        )

    def test_implementation_of(self):
        self.assertEqual(
            _report_type("Implementation of the National Education Policy 2020"),
            REPORT_TYPE_SUBJECT,
        )

    def test_status_report(self):
        self.assertEqual(
            _report_type("Status of Aspirational Districts Programme outcomes"),
            REPORT_TYPE_SUBJECT,
        )


class FallbackTests(unittest.TestCase):

    def test_empty_title_returns_other(self):
        self.assertEqual(_report_type(""), REPORT_TYPE_OTHER)
        self.assertEqual(_report_type(None), REPORT_TYPE_OTHER)

    def test_unrecognised_title_returns_other(self):
        # If the title is genuinely opaque ("Twenty-First Report") and
        # doesn't match any known pattern, return ``other`` so downstream
        # filters can flag it for hand review rather than silently
        # absorb it into one of the typed buckets.
        self.assertEqual(
            _report_type("Twenty-First Report"),
            REPORT_TYPE_OTHER,
        )


class TaxonomySanityTests(unittest.TestCase):

    def test_known_set_has_five_members(self):
        # action_taken, demands_for_grants, bill, subject, other.
        self.assertEqual(len(REPORT_TYPES_KNOWN), 5)

    def test_all_constants_are_in_known_set(self):
        for c in (
            REPORT_TYPE_ACTION_TAKEN,
            REPORT_TYPE_DFG,
            REPORT_TYPE_BILL,
            REPORT_TYPE_SUBJECT,
            REPORT_TYPE_OTHER,
        ):
            self.assertIn(c, REPORT_TYPES_KNOWN)

    def test_priority_order_atr_beats_dfg_beats_bill_beats_subject(self):
        """Priority documented in the docstring: ATR > DFG > Bill > Subject.
        Pin the order against future regression."""
        # ATR + DFG → ATR
        self.assertEqual(
            _report_type("Action Taken on the Demands for Grants Report"),
            REPORT_TYPE_ACTION_TAKEN,
        )
        # DFG + Subject hint → DFG
        self.assertEqual(
            _report_type("Demands for Grants — Review of Ministry of X"),
            REPORT_TYPE_DFG,
        )
        # Bill + Subject hint → Bill
        self.assertEqual(
            _report_type("Examination of the Working Bill, 2025"),
            REPORT_TYPE_BILL,
        )


if __name__ == "__main__":
    unittest.main()
