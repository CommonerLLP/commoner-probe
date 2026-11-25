from pathlib import Path

import pytest

from commoner_probe.aangan_profile import BIHAR_ICDS_AWC_PROFILE, parse_profile

FIXTURE = Path(__file__).parent / "fixtures" / "aangan" / "awc_profile_sample.html"


@pytest.fixture
def page():
    return FIXTURE.read_text(encoding="utf-8")


def test_parses_the_detail_row(page):
    detail = parse_profile(page)["detail"]
    assert len(detail) == 1
    assert detail[0]["awc_type"] == "AWC"
    assert detail[0]["active"] == "Yes"
    assert detail[0]["sevika_mobile"] == "9000000000"


def test_parses_each_inspection_with_counts_and_position(page):
    rows = parse_profile(page)["inspections"]
    assert [r["inspector"] for r in rows] == ["cdpo", "ls"]
    cdpo, ls = rows
    assert (cdpo["children_enrolled"], cdpo["children_present"]) == (40, 21)
    assert (ls["children_enrolled"], ls["children_present"]) == (40, 37)
    assert cdpo["centre_open"] == "Yes" and cdpo["food_cooked"] == "Yes"
    assert (cdpo["lat"], cdpo["lon"]) == (25.0000001, 85.0000001)


def test_timestamps_stay_as_printed(page):
    """Day-first and month-first both parse; the parser must not choose."""
    cdpo = parse_profile(page)["inspections"][0]
    assert cdpo["inspected_at_mobile"] == "07/10/2024 12:34 PM"


def test_an_inspector_with_no_record_gives_no_rows(page):
    assert not [r for r in parse_profile(page)["inspections"] if r["inspector"] == "dpo"]


def test_a_missing_table_raises_rather_than_returning_nothing(page):
    with pytest.raises(LookupError):
        parse_profile(page.replace("ctl00_MainContent_GVLS", "renamed"))


def test_a_moved_column_raises_rather_than_shifting_values(page):
    broken = page.replace("नामांकित", "XXXX")
    with pytest.raises(LookupError, match="children_enrolled"):
        parse_profile(broken)


def test_preset_uses_this_pages_control_names():
    assert BIHAR_ICDS_AWC_PROFILE["controls"]["district"].endswith("ddlDst")
    assert BIHAR_ICDS_AWC_PROFILE["levels"][0] == "fy"


def test_a_nested_photo_table_neither_truncates_nor_adds_cells():
    from commoner_probe.aangan_profile import _table
    page = ('<table id="T"><tr><th>a</th><th>b</th></tr>'
            '<tr><td>1</td><td><table><tr><td>photo</td></tr></table></td></tr>'
            '<tr><td>2</td><td>x</td></tr></table><p>after</p>')
    seg = _table(page, "T")
    assert seg.count("<tr") == 3 and "photo" not in seg and "after" not in seg
    assert seg.endswith("</table>")
