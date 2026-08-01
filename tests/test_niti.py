"""Tests for the NITI Aayog Annual Report adapter (residual).

The fixture is the real listing's markup, reduced. Every filename below is
verbatim from https://www.niti.gov.in/index.php/publication/annual-report as it
stood on 2026-07-30 — the awkward ones are the point:

    Annual Report of NITI Aayog 2025-26 (English).pdf   space before the paren
    Annual Report of NITI Aayog 2025-26(Hindi).pdf      no space
    Annual Report 2024-25 Hindi_V3 LOWRES.pdf           no parens at all
    Annual-Report-2022-2023-English_06022023...pdf      four-digit second year

No network.
"""

from __future__ import annotations

import json

import pytest

from commoner_probe.niti import NitiAnnualReportProbe, parse_listing

BASE = "/sites/default/files"

LISTING_HTML = f"""
<a href="{BASE}/2026-05/Annual%20Report%20of%20NITI%20Aayog%202025-26%28Hindi%29.pdf">hi</a>
<a href="{BASE}/2026-05/Annual%20Report%20of%20NITI%20Aayog%202025-26%28Hindi%29.pdf">hi again</a>
<a href="{BASE}/2026-05/Annual%20Report%20of%20NITI%20Aayog%202025-26%20%28English%29.pdf">en</a>
<a href="{BASE}/2026-05/Annual%20Report%20of%20NITI%20Aayog%202025-26%20%28English%29.pdf">en again</a>
<a href="{BASE}/2025-02/Annual%20Report%202024-25%20Hindi_V3%20LOWRES.pdf">hi 24</a>
<a href="{BASE}/2025-02/Annual%20Report%202024-25%20English_FINAL_LOW%20RES_0.pdf">en 24</a>
<a href="{BASE}/2023-02/Annual-Report-2022-2023-English_06022023_compressed.pdf">en 22</a>
<a href="{BASE}/2020-02/brochure-no-year-here.pdf">no year</a>
"""


class FakeResponse:
    def __init__(self, text="", content=b""):
        self.text = text
        self.content = content

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self, listing=LISTING_HTML, pdf=b"%PDF-1.7 fake body"):
        self.listing = listing
        self.pdf = pdf
        self.calls: list[str] = []

    def get(self, url, timeout=None, **kwargs):
        self.calls.append(url)
        if url.endswith(".pdf"):
            return FakeResponse(content=self.pdf)
        return FakeResponse(text=self.listing)


def _probe(tmp_path, **kw):
    return NitiAnnualReportProbe(tmp_path, sleep=0, session=FakeSession(**kw))


class TestListingTraps:
    """Each of these produced a wrong manifest before it was fixed."""

    def test_duplicate_links_are_deduped(self):
        """The listing renders every document twice."""
        reports = parse_listing(LISTING_HTML)
        urls = [r["url"] for r in reports]
        assert len(urls) == len(set(urls))

    def test_the_upload_directory_is_not_read_as_a_fiscal_year(self):
        """Files live under /sites/default/files/2025-02/, which matches a
        fiscal-year pattern and invents 2020-02 and 2023-02."""
        years = {r["year"] for r in parse_listing(LISTING_HTML)}
        assert years == {"2025-26", "2024-25", "2022-23"}
        assert not {y for y in years if y.endswith("-02")}

    def test_a_four_digit_second_year_is_normalised(self):
        """`Annual-Report-2022-2023-...` must not become 2022-20."""
        got = {r["year"] for r in parse_listing(LISTING_HTML) if "2022" in r["filename"]}
        assert got == {"2022-23"}

    def test_a_file_with_no_year_is_skipped_not_guessed(self):
        names = {r["filename"] for r in parse_listing(LISTING_HTML)}
        assert not any("brochure" in n for n in names)

    @pytest.mark.parametrize(
        "fragment,language",
        [
            ("2025-26 (English)", "english"),
            ("2025-26(Hindi)", "hindi"),
            ("2024-25 Hindi_V3", "hindi"),
            ("2022-2023-English_06022023", "english"),
        ],
    )
    def test_all_three_language_markings_are_read(self, fragment, language):
        """`\\bhindi\\b` does NOT match `Hindi_V3` — `_` is a word character —
        and a parenthesis-anchored match misses it too. Both mislabelled a Hindi
        report as English."""
        match = [r for r in parse_listing(LISTING_HTML) if fragment.replace("%20", " ") in r["filename"]]
        assert match, f"fixture lost the {fragment!r} case"
        assert match[0]["language"] == language


class TestEnglishOnly:
    def test_hindi_editions_are_not_acquired(self, tmp_path):
        """Nobody asked for Hindi. Detection exists so 'English only' is true
        rather than assumed — the Hindi_V3 file would otherwise pass as English."""
        records = _probe(tmp_path).probe()

        assert {r["language"] for r in records} == {"english"}
        assert {r["report_year"] for r in records} == {"2025-26", "2024-25", "2022-23"}

    def test_there_is_no_language_option_to_get_wrong(self, tmp_path):
        with pytest.raises(TypeError):
            _probe(tmp_path).probe(language="hindi")


class TestAcquisition:
    def test_a_downloaded_report_records_its_digest_and_size(self, tmp_path):
        rec = [r for r in _probe(tmp_path).probe() if r["report_year"] == "2025-26"][0]

        assert rec["status"] == "downloaded"
        assert rec["bytes"] == len(b"%PDF-1.7 fake body")
        assert len(rec["sha256"]) == 64
        assert (tmp_path / rec["dest"]).read_bytes().startswith(b"%PDF")

    def test_a_non_pdf_body_raises_rather_than_being_stored(self, tmp_path):
        """The listing's own 404 page is 41 KB of HTML, so a size check passes
        on it. The magic bytes are what actually distinguish them."""
        probe = NitiAnnualReportProbe(
            tmp_path, sleep=0, session=FakeSession(pdf=b"<!DOCTYPE html><html>404")
        )
        with pytest.raises(ValueError, match="did not return a PDF"):
            probe.probe()

    def test_an_empty_listing_raises_rather_than_reporting_nothing_found(self, tmp_path):
        probe = NitiAnnualReportProbe(tmp_path, sleep=0, session=FakeSession(listing="<html></html>"))
        with pytest.raises(ValueError, match="no Annual Report PDFs"):
            probe.probe()

    def test_a_dry_run_writes_nothing(self, tmp_path):
        records = _probe(tmp_path).probe(dry_run=True)

        assert {r["status"] for r in records} == {"dry_run"}
        assert not (tmp_path / "manifest.jsonl").exists()
        assert list(tmp_path.glob("*.pdf")) == []

    def test_a_year_filter_selects(self, tmp_path):
        records = _probe(tmp_path).probe(years=["2024-25"])
        assert [r["report_year"] for r in records] == ["2024-25"]

    def test_an_already_downloaded_report_is_not_refetched(self, tmp_path):
        assert len(_probe(tmp_path).probe()) == 3
        assert _probe(tmp_path).probe() == []

    def test_records_validate_and_round_trip_through_the_typed_api(self, tmp_path):
        import jsonschema

        from commoner_probe.corpus import Corpus

        _probe(tmp_path).probe()
        schema = json.loads(
            (
                __import__("pathlib").Path("commoner_probe/schemas/manifest_niti_annual_report.schema.json")
            ).read_text()
        )
        rows = [
            json.loads(line)
            for line in (tmp_path / "manifest.jsonl").read_text().splitlines()
            if line.strip()
        ]
        for row in rows:
            jsonschema.validate(row, schema)

        typed = list(Corpus(tmp_path).manifest_niti_annual_report())
        assert len(typed) == len(rows)
        assert typed[0].language == "english"
