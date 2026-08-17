"""Tests for commoner_probe.gridview.

Each test encodes a failure that actually happened while scraping Bihar's
Aangan ICDS MIS, and each one produced a full, plausible table rather than an
error — which is why they are regression tests rather than documentation.

- test_header_is_not_row_zero: a filter widget was taken as the header, turning
  a 15-column report into a 3-column one called All/Sevika/Sahaika.
- test_static_columns_are_flagged: four columns ignored the financial-year
  filter, so a "decade trend" carried the same constant in every year.
- test_duplicate_fold_detected: a month dropdown with 13 options changed
  nothing, inflating every total thirteenfold.
- test_phone_column_is_numeric_but_not_summable: two contact-number columns
  passed a numeric check and summed to 9.6 trillion.
"""

from __future__ import annotations

import pytest

from commoner_probe import gridview

# A GridView shaped like the real thing: caption row, filter widget, then the
# actual header, then data. Only the third row is the header.
PAGE = """
<table>
 <tr><td>District/Project Wise Total Count Of Pending Honorarium</td></tr>
 <tr><td>All</td><td>Sevika</td><td>Sahaika</td></tr>
 <tr><th>Sl. No.</th><th>District</th><th>Project</th><th>Absentee Not Entered</th><th>CDPO Contact No.</th></tr>
 <tr><td>1.</td><td>ARARIA</td><td>Araria</td><td>126</td><td>9431005489</td></tr>
 <tr><td>2.</td><td>ARARIA</td><td>Bhargama</td><td>71</td><td>9431005490</td></tr>
</table>
"""


def test_header_is_not_row_zero():
    header, rows = gridview.parse_grid(PAGE)
    assert header[0] == "Sl. No."
    assert header[3] == "Absentee Not Entered"
    assert len(header) == 5
    assert len(rows) == 2


def test_header_anchor_missing_raises_rather_than_guessing():
    """A wrong header is worse than no parse, because it is silent."""
    with pytest.raises(LookupError):
        gridview.parse_grid(PAGE, header_startswith="zzz")


def test_to_records_snake_cases_keys():
    recs = gridview.to_records(*gridview.parse_grid(PAGE))
    assert recs[0]["district"] == "ARARIA"
    assert recs[0]["absentee_not_entered"] == "126"
    assert recs[0]["cdpo_contact_no"] == "9431005489"


def _series():
    """Two filter values. `pending` moves; `stopped` and the phone do not."""
    return {
        "2017-2018": [{"district": "A", "project": "P1", "pending": "500", "stopped": "7", "phone": "9431005489"},
                      {"district": "A", "project": "P2", "pending": "300", "stopped": "3", "phone": "9431005490"}],
        "2026-2027": [{"district": "A", "project": "P1", "pending": "90", "stopped": "7", "phone": "9431005489"},
                      {"district": "A", "project": "P2", "pending": "40", "stopped": "3", "phone": "9431005490"}],
    }


def test_static_columns_are_flagged():
    resp = gridview.responsive_columns(_series())
    assert resp["pending"] is True
    assert resp["stopped"] is False
    assert resp["phone"] is False


def test_phone_column_is_numeric_but_not_summable():
    """numeric_columns finds candidates; it does not license aggregation."""
    recs = _series()["2017-2018"]
    assert "phone" in gridview.numeric_columns(recs)
    assert gridview.responsive_columns(_series())["phone"] is False


def test_duplicate_fold_detected():
    one = {"district": "A", "project": "P1", "pending": "5"}
    assert gridview.duplicate_fold([one] * 13, ["district", "project"]) == 13
    assert gridview.duplicate_fold([one], ["district", "project"]) == 1


def test_dedupe_collapses_the_fold():
    rows = [{"district": "A", "project": "P1", "m": m} for m in range(13)]
    assert len(gridview.dedupe(rows, ["district", "project"])) == 1


def test_audit_reports_everything_needed_before_summing():
    a = gridview.audit(_series(), ["district", "project"])
    assert a["rows"] == 4
    assert a["distinct"] == 2
    assert a["fold"] == 2          # same two projects under two filter values
    assert "pending" in a["responsive"]
    assert "stopped" in a["static"]
    assert "phone" in a["static"]


# ---- added after a whole endpoint vanished for one month, unnoticed ----

def _gappy():
    """`snp` is missing entirely in one period, present in the others.

    The Poshan Tracker shape: keyServices_v3 returned nothing for 2024-11 in
    0 of 772 districts while neighbouring months were complete.
    """
    return {
        "2024-11": [{"district": "A", "project": "P1", "awc": "10"},
                    {"district": "A", "project": "P2", "awc": "20"}],
        "2024-12": [{"district": "A", "project": "P1", "awc": "10", "snp": "5"},
                    {"district": "A", "project": "P2", "awc": "20", "snp": "7"}],
        "2025-01": [{"district": "A", "project": "P1", "awc": "10", "snp": "6"},
                    {"district": "A", "project": "P2", "awc": "20", "snp": "8"}],
    }


def test_absent_periods_finds_the_vanished_endpoint():
    gaps = gridview.absent_periods(_gappy())
    assert gaps == {"snp": ["2024-11"]}


def test_absent_periods_is_empty_when_every_field_is_everywhere():
    clean = {"a": [{"x": "1"}], "b": [{"x": "2"}]}
    assert gridview.absent_periods(clean) == {}


def test_audit_surfaces_absent_periods():
    """A missing endpoint reads as a legitimate zero and drags any mean down."""
    a = gridview.audit(_gappy(), ["district", "project"])
    assert a["absent_periods"] == {"snp": ["2024-11"]}


def test_a_wholly_absent_field_is_not_the_same_as_a_zero():
    """The failure this guards: .get() returns None, the period counts as 0."""
    s = _gappy()
    naive = {p: sum(int(r.get("snp", 0)) for r in recs) for p, recs in s.items()}
    assert naive["2024-11"] == 0          # looks like a real collapse
    assert "2024-11" in gridview.absent_periods(s)["snp"]   # but it is absence
