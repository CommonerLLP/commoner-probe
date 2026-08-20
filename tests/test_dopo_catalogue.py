"""Tests for the DOPO edition catalogue and the ligature-tolerant search.

No network. The catalogue is pinned data and the pattern is pure text, so both
are checkable offline; the live figures they encode are cited in the module.
"""

from __future__ import annotations

import re

from commoner_probe.dopo_catalogue import (
    DOPO_EDITIONS,
    RECOVERY_HOST_PREFIX,
    recovery_urls,
    term_pattern,
)


def test_the_catalogue_covers_the_published_years_once_each():
    years = [e.year for e in DOPO_EDITIONS]
    assert years == sorted(years), "the catalogue reads as a time series"
    assert len(years) == len(set(years)) == 13
    assert years[0] == 2010 and years[-1] == 2022


def test_eleven_editions_are_archived_and_two_are_not():
    """The index answered for each of the thirteen on 2026-08-20. 2021 has no
    capture and the 2022 upload captures only as a 302."""
    archived = [e for e in DOPO_EDITIONS if e.archived]
    missing = [e for e in DOPO_EDITIONS if not e.archived]

    assert len(archived) == 11
    assert [e.year for e in missing] == [2021, 2022]
    assert all(e.note for e in missing), "an absence must say what is known"


def test_two_filenames_break_the_pattern():
    """Generating `dopo<year>.pdf` misses 2014 and 2017 — the two editions the
    first consumer of this data actually used."""
    off_pattern = [e for e in DOPO_EDITIONS
                   if e.archived and not e.path.endswith(f"dopo{e.year}.pdf")]

    assert {e.year for e in off_pattern} == {2014, 2017}
    assert off_pattern[0].path.endswith("dopoFile2014.pdf")
    assert off_pattern[1].path.endswith("databook2017.pdf")


def test_recovery_urls_defaults_to_what_the_archive_can_serve():
    recoverable = recovery_urls()
    everything = recovery_urls(archived_only=False)

    assert len(recoverable) == 11
    assert len(everything) == 13
    assert all(u.startswith("https://bprd.nic.in/") for u in everything)
    assert not any("DoPO 2021" in u for u in recoverable)


def test_the_recovery_prefix_is_scoped_to_a_path():
    """Bare `--host bprd.nic.in` walks the whole capture history of a large
    government domain and did not finish in seven minutes."""
    assert RECOVERY_HOST_PREFIX == "bprd.nic.in/uploads/dopo"
    assert "/" in RECOVERY_HOST_PREFIX, "a bare host is the unusable form"


# ── the ligature ──────────────────────────────────────────────────────────


def test_a_term_matches_both_spellings():
    """2016's fonts drop the `ti` ligature: its tables say `Sanc oned`. Counted
    case-insensitively over the first 90 pages, dopo2011 held 162 `Sanctioned`
    and 0 `Sanc oned`; dopo2016 held 20 and 172."""
    pattern = term_pattern("Sanctioned")

    assert pattern.search("(i) Sanctioned Police Strength")
    assert pattern.search("(i) Sanc oned Police Strength")


def test_the_pattern_covers_every_term_the_tables_use():
    for term, broken in (("Particulars", "Par culars"),
                         ("Promoting", "Promo ng"),
                         ("Organisations", "Organisa ons"),
                         ("Actual", "Actual")):
        pattern = term_pattern(term)
        assert pattern.search(term), term
        assert pattern.search(broken), broken


def test_a_term_with_no_ligature_is_left_alone():
    """`Actual` has no `ti`, so its pattern must be the plain term and must not
    acquire a stray alternation."""
    assert term_pattern("Actual").pattern == "Actual"


def test_the_pattern_does_not_match_an_unrelated_word():
    pattern = term_pattern("Sanctioned")

    assert not pattern.search("Sanctions")
    assert not pattern.search("Sanc  oned"), "two spaces is not the ligature"


def test_the_term_is_escaped_before_it_becomes_a_pattern():
    """A term is data. One containing regex punctuation must match literally."""
    pattern = term_pattern("Table 3.1.1 (a)")

    assert pattern.search("TABLE 3.1.1 (A)")
    assert not pattern.search("Table 3X1Y1 (a)")


def test_case_folding_can_be_turned_off():
    assert term_pattern("Sanctioned", flags=0).search("Sanc oned")
    assert not term_pattern("Sanctioned", flags=0).search("SANC ONED")


def test_a_term_that_is_only_the_ligature_still_builds():
    assert term_pattern("ti").search("ti")
    assert term_pattern("ti").search(" ")


def test_every_edition_url_is_absolute():
    for edition in DOPO_EDITIONS:
        assert re.match(r"^https://bprd\.nic\.in/uploads/", edition.url), edition
