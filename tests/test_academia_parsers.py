"""Tests for the migrated institution-specific academia parsers.

Each parser is exercised against a small HTML fixture; we assert the probe-native
dict shape (plain dicts, string post_type/contract_status) and key fields.
"""

from __future__ import annotations

from datetime import datetime

import pytest

FETCHED = datetime(2026, 6, 1)


def test_dispatch_now_resolves_migrated_parsers():
    from commoner_probe.academia.parsers import (
        UNMIGRATED_PARSERS,
        anna_university,
        get_parser,
        iit_kanpur,
        private_university,
    )

    assert get_parser("iit_kanpur") is iit_kanpur.parse
    assert get_parser("anna_university") is anna_university.parse
    assert get_parser("private_university") is private_university.parse
    # No longer falling back to generic:
    assert not {"iit_kanpur", "anna_university", "private_university"} & UNMIGRATED_PARSERS


def test_iit_kanpur_extracts_department_blocks():
    pytest.importorskip("bs4")
    from commoner_probe.academia.parsers import iit_kanpur

    html = (
        "<div>Physics: We seek faculty in condensed matter and astrophysics. "
        "Chemistry: Positions in organic and physical chemistry.</div>"
    )
    ads = iit_kanpur.parse(html, "https://iitk.ac.in/dofa/department-wise", FETCHED)
    depts = {a["department"] for a in ads}
    assert "Physics" in depts and "Chemistry" in depts
    phys = next(a for a in ads if a["department"] == "Physics")
    assert phys["post_type"] == "Faculty"
    assert phys["contract_status"] == "TenureTrack"
    assert phys["institution_id"] == "__placeholder__"
    assert "condensed matter" in phys["raw_text_excerpt"]
    assert phys["pdf_parsed"] is False


def test_anna_university_parses_recruitment_table():
    pytest.importorskip("bs4")
    from commoner_probe.academia.parsers import anna_university

    html = """
    <table>
      <tr><td>1</td><td><a href="/jobs/ap-civil.pdf">Assistant Professor</a></td>
          <td>Civil Engineering</td><td>31.12.2026</td></tr>
    </table>
    """
    ads = anna_university.parse(html, "https://www.annauniv.edu/events.php", FETCHED)
    assert len(ads) == 1
    ad = ads[0]
    assert ad["post_type"] == "Faculty"
    assert ad["department"] == "Civil Engineering"
    assert ad["closing_date"] == "2026-12-31"
    assert ad["original_url"].endswith("ap-civil.pdf")


def test_private_university_block_parsing():
    pytest.importorskip("bs4")
    from commoner_probe.academia.parsers import private_university

    html = """
    <article>
      <h3>Assistant Professor of Economics</h3>
      <a href="/apply">Apply</a>
      Applications due Jan 15, 2026.
    </article>
    """
    ads = private_university.parse(html, "https://shivnadar.edu.in/careers", FETCHED)
    assert ads
    ad = ads[0]
    assert "Professor" in ad["title"]
    assert ad["post_type"] == "Faculty"
    assert ad["closing_date"] == "2026-01-15"
    assert ad["pdf_parsed"] is False


def test_private_university_apu_follows_subpages_via_fetcher():
    pytest.importorskip("bs4")
    from commoner_probe.academia.parsers import private_university

    index_html = """
    <article>
      <a href="https://azimpremjiuniversity.edu.in/jobs/faculty-economics">Faculty Positions in Economics</a>
      Deadline Feb 10, 2026
    </article>
    """
    position_html = (
        "<html><h1>Faculty Positions in Economics</h1>"
        "<meta name='description' content='We invite applications for faculty in Economics.'>"
        "<p>We invite applications from scholars in development economics.</p>"
        "<section><h3>Requirements</h3> PhD in Economics. Open Positions: 2</section></html>"
    ) + ("padding " * 200)

    class FakeFetcher:
        def get_html(self, url):
            return position_html

    ads = private_university.parse(
        index_html, "https://azimpremjiuniversity.edu.in/jobs/role:faculty", FETCHED, FakeFetcher()
    )
    assert ads
    ad = ads[0]
    assert ad["title"].startswith("Faculty Positions in Economics")
    assert ad["number_of_posts"] == 2  # lifted from "Open Positions: 2"
    assert ad["unit_eligibility"] and "PhD" in ad["unit_eligibility"]


def test_iit_indore_associates_title_with_pdf_link():
    pytest.importorskip("bs4")
    from commoner_probe.academia.parsers import get_parser, iit_indore

    assert get_parser("iit_indore") is iit_indore.parse
    html = (
        "<div><p><strong>Advt. No. IITI/Rectt/Faculty/2026/01 — Faculty Positions</strong></p>"
        "<p><a href='/public/storage/recruitments/advt2026.pdf'>Download</a></p></div>"
    )
    ads = iit_indore.parse(html, "https://www.iiti.ac.in/recruitments/faculty-positions", FETCHED)
    assert len(ads) == 1
    ad = ads[0]
    assert ad["post_type"] == "Faculty"
    assert ad["contract_status"] == "Regular"
    assert ad["ad_number"] == "IITI/Rectt/Faculty/2026/01"
    assert ad["original_url"].endswith("advt2026.pdf")


def test_iit_rolling_split_into_units():
    from commoner_probe.academia.pdf_text import split_into_units

    text = (
        "1   Department of Physics      Condensed matter physics      Publications: 3 papers\n"
        "2   Department of Chemistry    Organic chemistry             Publications: 4 papers\n"
    )
    blocks = split_into_units(text)
    assert [b.unit_num for b in blocks] == [1, 2]
    assert blocks[0].unit_name == "Department of Physics"
    assert blocks[1].unit_name == "Department of Chemistry"


def test_iit_rolling_parse_emits_per_unit_ads(monkeypatch):
    pytest.importorskip("bs4")
    from commoner_probe.academia.parsers import get_parser, iit_rolling

    assert get_parser("iit_rolling") is iit_rolling.parse

    body = (
        "Rolling Advertisement No. IITD/AP/2026/1\n"
        "Extent of reservation as follows: SC-15% ST-7.5% OBC(NCL)-27% EWS-10% PwBD-4%.\n"
        "1   Department of Physics      Condensed matter physics\n"
        "2   Department of Chemistry    Organic chemistry\n"
    )
    # extract_text is mocked, so the (non-existent) downloaded path is never read.
    monkeypatch.setattr(iit_rolling, "extract_text", lambda p: body)

    class FakeFetcher:
        def download(self, url):
            from pathlib import Path
            return Path("/tmp/iitd-fake.pdf")

    html = '<a href="/files/AP-1-rolling.pdf">Areas of Specialization AP-1</a>'
    ads = iit_rolling.parse(html, "https://www.iitd.ac.in/jobs-iitd/index.html", FETCHED, FakeFetcher())

    assert ads
    depts = {a["department"] for a in ads}
    assert "Department of Physics" in depts and "Department of Chemistry" in depts
    assert all(a["pdf_parsed"] for a in ads)
    assert all(a["post_type"] == "Faculty" for a in ads)
    assert ads[0]["ad_number"] == "IITD/AP/2026/1"
    assert ads[0]["reservation_note"] and "SC-15%" in ads[0]["reservation_note"]
    assert ads[0]["apply_url"].startswith("https://ecampus.iitd.ac.in")


def test_iit_rolling_returns_empty_without_fetcher():
    from commoner_probe.academia.parsers import iit_rolling

    # No Fetcher (--no-download): PDF-based parser produces nothing, no crash.
    ads = iit_rolling.parse("<a href='AP-1.pdf'>x</a>", "https://www.iitd.ac.in/jobs", FETCHED, None)
    assert ads == []


def test_private_university_apu_degrades_without_fetcher():
    pytest.importorskip("bs4")
    from commoner_probe.academia.parsers import private_university

    index_html = """
    <article><h3>Faculty Positions in Economics</h3>
    <a href="https://azimpremjiuniversity.edu.in/jobs/faculty-economics">View</a></article>
    """
    # No fetcher -> APU branch returns [], falls back to index block parsing (no crash).
    ads = private_university.parse(
        index_html, "https://azimpremjiuniversity.edu.in/jobs/role:faculty", FETCHED, None
    )
    assert isinstance(ads, list)  # graceful, may be empty or index-only


# --------------------------------------------------------------------------- #
# iit_gandhinagar parser (rolling PoP page, department pipe-block)            #
# --------------------------------------------------------------------------- #


def test_get_parser_resolves_iit_gandhinagar():
    from commoner_probe.academia.parsers import UNMIGRATED_PARSERS, get_parser, iit_gandhinagar

    assert get_parser("iit_gandhinagar") is iit_gandhinagar.parse
    assert "iit_gandhinagar" not in UNMIGRATED_PARSERS


def test_iit_gandhinagar_pop_page_explodes_departments():
    pytest.importorskip("bs4")
    from commoner_probe.academia.parsers import iit_gandhinagar

    url = "https://iitgn.ac.in/careers/pop"
    html = "<p>Disciplines Physics | Chemistry | Mathematics</p>"
    ads = iit_gandhinagar.parse(html, url, FETCHED)
    assert len(ads) == 3
    assert all(a["post_type"] == "Faculty" for a in ads)
    assert all(a["contract_status"] == "Unknown" for a in ads)
    assert all(a["apply_url"] == url and a["info_url"] == url for a in ads)
    assert all(a["title"].startswith("Professor of Practice — ") for a in ads)
    assert {a["department"] for a in ads} == {"Physics", "Chemistry", "Mathematics"}


def test_iit_gandhinagar_pop_page_uses_fallback_when_regex_finds_nothing():
    pytest.importorskip("bs4")
    from commoner_probe.academia.parsers import iit_gandhinagar

    url = "https://iitgn.ac.in/careers/pop"
    html = "<p>Professor of Practice positions are open on a rolling basis.</p>"
    ads = iit_gandhinagar.parse(html, url, FETCHED)
    assert len(ads) == len(iit_gandhinagar._POP_DEPTS_FALLBACK)
    assert {a["department"] for a in ads} == set(iit_gandhinagar._POP_DEPTS_FALLBACK)


def test_iit_gandhinagar_non_pop_page_falls_back_to_generic():
    pytest.importorskip("bs4")
    from commoner_probe.academia.parsers import generic, iit_gandhinagar

    url = "https://iitgn.ac.in/careers/faculty"
    html = '<a href="/advt.pdf">Faculty Recruitment Advertisement</a>'
    assert iit_gandhinagar.parse(html, url, FETCHED) == generic.parse(html, url, FETCHED, None)


# --------------------------------------------------------------------------- #
# iit_hyderabad parser (mixed faculty/rolling-project postings)               #
# --------------------------------------------------------------------------- #


def test_get_parser_resolves_iit_hyderabad():
    from commoner_probe.academia.parsers import UNMIGRATED_PARSERS, get_parser, iit_hyderabad

    assert get_parser("iit_hyderabad") is iit_hyderabad.parse
    assert "iit_hyderabad" not in UNMIGRATED_PARSERS


def test_iit_hyderabad_extracts_department_and_post_type():
    pytest.importorskip("bs4")
    from commoner_probe.academia.parsers import iit_hyderabad

    url = "https://www.iith.ac.in/careers/"
    html = (
        '<p><a href="/rec/ap-cse.pdf">'
        "Professor positions in Computer Science and Engineering</a></p>"
    )
    ads = iit_hyderabad.parse(html, url, FETCHED)
    assert len(ads) == 1
    ad = ads[0]
    assert ad["post_type"] == "Faculty"
    assert ad["department"] == "Computer Science and Engineering"
    assert ad["apply_url"] == "https://www.iith.ac.in/rec/ap-cse.pdf"
    assert ad["parse_confidence"] == 0.55
    assert ad["info_url"] == url


def test_iit_hyderabad_skips_result_notifications():
    from commoner_probe.academia.parsers import iit_hyderabad

    url = "https://www.iith.ac.in/careers/"
    html = (
        '<p><a href="/notice.pdf">'
        "Notification of Results — Assistant Professor Recruitment</a></p>"
    )
    ads = iit_hyderabad.parse(html, url, FETCHED)
    assert ads == []


def test_iit_hyderabad_non_pdf_link_has_no_apply_url():
    from commoner_probe.academia.parsers import iit_hyderabad

    url = "https://www.iith.ac.in/careers/"
    html = (
        '<p><a href="/rec/ap-cse.php">'
        "Professor positions in Computer Science and Engineering</a></p>"
    )
    ads = iit_hyderabad.parse(html, url, FETCHED)
    assert len(ads) == 1
    assert ads[0]["apply_url"] is None
    assert ads[0]["info_url"] == url


# --------------------------------------------------------------------------- #
# jnu parser (column-band PDF table)                                          #
# --------------------------------------------------------------------------- #


def _jnu_row(num: int, school: str, cadre: str, cat: str, quals: str) -> str:
    """Place fields at JNU's fixed column bands (school col 6, cadre 36, cat 51, quals 66)."""
    cells = [(f"{num}.", 0), (school, 6), (cadre, 36), (cat, 51), (quals, 66)]
    width = max(col + len(s) for s, col in cells)
    line = [" "] * width
    for s, col in cells:
        line[col:col + len(s)] = list(s)
    return "".join(line)


def test_get_parser_resolves_jnu():
    from commoner_probe.academia.parsers import UNMIGRATED_PARSERS, get_parser, jnu

    assert get_parser("jnu") is jnu.parse
    assert "jnu" not in UNMIGRATED_PARSERS  # ported referenced parser


def test_jnu_parse_posts_from_text():
    from commoner_probe.academia.parsers import jnu

    text = "\n".join([
        "JAWAHARLAL NEHRU UNIVERSITY",
        "Advertisement RC/75/2026",
        "Post   School/Centre   Cadre   Category   Qualifications",
        "", "", "", "",  # gap so the header is outside post 1's column windows
        _jnu_row(1, "School of Arts &", "Professor", "UR", "Ph.D. in History required"),
        _jnu_row(2, "Centre for Studies (CCS)", "Professor", "SC", "Politics; publications expected"),
        "",
    ])
    posts = jnu._parse_posts_from_text(
        text, "https://www.jnu.ac.in/adv/JNU_AdvtNo.pdf", "RC/75/2026",
        "https://www.jnu.ac.in/career", FETCHED,
    )
    assert len(posts) == 2
    p1, p2 = posts
    assert p1["department"] == "School of Arts &"
    assert p1["category_breakdown"] == {"UR": 1}
    assert p1["ad_number"] == "RC/75/2026"
    assert p1["post_type"] == "Faculty"
    assert p1["contract_status"] == "Regular"
    assert p1["number_of_posts"] == 1
    assert p1["pdf_parsed"] is True
    assert p2["department"] == "Centre for Studies (CCS)"
    assert p2["category_breakdown"] == {"SC": 1}
    assert p2["unit_eligibility"] and "publications" in p2["unit_eligibility"]


def test_jnu_listing_fallback_without_fetcher():
    pytest.importorskip("bs4")
    from commoner_probe.academia.parsers import jnu

    html = (
        '<p>Faculty recruitment RC/75/2026 — '
        '<a href="/adv/JNU_AdvtNo_RC_75_2026.pdf">advertisement (PDF)</a></p>'
        '<p><a href="/adv/JNU_shortlist.pdf">Shortlist of candidates</a></p>'
    )
    # No Fetcher (--no-download): no PDF table parse; one listing-level record for
    # the initial notice, and the shortlist (update hint) is skipped.
    ads = jnu.parse(html, "https://www.jnu.ac.in/career", FETCHED, None)
    assert len(ads) == 1
    assert ads[0]["pdf_parsed"] is False
    assert ads[0]["post_type"] == "Faculty"
    assert ads[0]["original_url"].endswith("JNU_AdvtNo_RC_75_2026.pdf")
