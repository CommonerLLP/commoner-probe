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
