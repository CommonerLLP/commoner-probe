"""Tests for the PRS Report Summaries and Vital Stats surfaces.

Fixture markup mirrors the live Drupal Views listing (verified 2026-07-28:
442 report summaries and 24 vital stats, each in one response, no pager
markup, `?page=1` returning the identical first row).

Two details it deliberately encodes:

    The row's opening tag carries inline style attributes BEFORE the class,
    so the literal `<div class="views-row">` that Bill Track matches finds
    nothing on this template.

    The last row's block runs to the end of the document, so a PDF search
    that is not anchored on the download_pdf container hands the final item
    whatever PDF the page footer carries.

No network.
"""

from __future__ import annotations

import json

import pytest

from commoner_probe.drupal_publication_index import PrsProbe, parse_publications


def _row(slug: str, title: str, pdf: str | None, *, path: str = "policy/report-summaries") -> str:
    download = (
        f'<div style="float:left;" class="read_more pull-right download_pdf">\n'
        f'<a href="/files/policy/policy_committee_reports/{pdf}" title="Edit" data-pjax="0"> Download</a>\n'
        f"</div>\n"
        if pdf
        else ""
    )
    return f"""
<div style="padding-top: 0px;margin-top:15px" class="views-row">
<div class="views-field views-field-nothing">
<span class="field-content">
<div class="other_fields">
<div>
<h3 style="margin-top: 0px;"><a href="/{path}/{slug}">{title}</a>
</h3>
</div>
<div style="float: right" class="more-link download-link">
<div class="read_more pull-right right-icon">
<a href="/{path}/{slug}" class="views-more-link ">Read More <i class="fas fa-chevron-right"></i></a>
</div>
{download}</div>
</div>
</span>
</div>
</div>
"""


LISTING = (
    "<div class='view-content'>"
    + _row("cyber-crimes-and-cyber-security-of-women", "Cyber Crimes and Cyber Security of Women", "SCR_Cybercrime.pdf")
    + _row("research-water-efficient-seeds", "Research for Developing Water Efficient Variety of Seeds", "SCR_Water.pdf")
    + _row("a-summary-with-no-download", "A Summary With No Download", None)
    + "</div>"
    # The page footer carries its own PDF. The final row above has no download,
    # so an unanchored search would attach this one to it.
    + '<footer><a href="/files/annual-report-2025.pdf">Our Annual Report</a></footer>'
)

VITAL_LISTING = (
    "<div class='view-content'>"
    + _row("direct-taxes-in-india", "Direct Taxes in India", "Vital_Stats-Direct_Taxes.pdf", path="policy/vital-stats")
    + "</div>"
)


class FakeResponse:
    def __init__(self, body, *, is_pdf: bool = False):
        self.text = "" if is_pdf else body
        self.content = body if isinstance(body, bytes) else body.encode("utf-8")

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=16384):
        body = self.content or b""
        for i in range(0, len(body), chunk_size):
            yield body[i : i + chunk_size]


class FakeSession:
    def __init__(self, html: str = LISTING, *, pdf_body: bytes = b"%PDF-1.7 fake"):
        self.html = html
        self.pdf_body = pdf_body
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if url.endswith(".pdf"):
            return FakeResponse(self.pdf_body, is_pdf=True)
        if "/policy/" in url:
            return FakeResponse(self.html)
        raise AssertionError(f"unrouted url: {url}")


def _probe(tmp_path, session=None):
    probe = PrsProbe(tmp_path, sleep=0)
    probe.session = session or FakeSession()
    return probe


def _manifest(tmp_path):
    path = tmp_path / "manifest.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class TestParsePublications:
    def test_parses_title_url_and_slug(self):
        first = parse_publications(LISTING)[0]
        assert first["title"] == "Cyber Crimes and Cyber Security of Women"
        assert first["url"] == "https://prsindia.org/policy/report-summaries/cyber-crimes-and-cyber-security-of-women"
        assert first["slug"] == "cyber-crimes-and-cyber-security-of-women"
        assert first["pdf_url"].endswith("/SCR_Cybercrime.pdf")

    def test_an_item_with_no_download_is_kept_with_an_empty_pdf_url(self):
        items = parse_publications(LISTING)
        assert [i["slug"] for i in items][-1] == "a-summary-with-no-download"
        assert items[-1]["pdf_url"] == ""

    def test_the_footer_pdf_is_not_attached_to_the_last_item(self):
        """The last block runs to EOF; an unanchored search would grab this."""
        assert "annual-report-2025" not in (parse_publications(LISTING)[-1]["pdf_url"] or "")

    def test_vital_stats_uses_the_same_template(self):
        items = parse_publications(VITAL_LISTING)
        assert len(items) == 1
        assert items[0]["url"].endswith("/policy/vital-stats/direct-taxes-in-india")

    def test_empty_page_yields_nothing_rather_than_raising(self):
        assert parse_publications("<html><body>no rows here</body></html>") == []


class TestProbePublications:
    def test_writes_one_metadata_record_per_item(self, tmp_path):
        probe = _probe(tmp_path)
        records = probe.probe_publications(surface="report-summaries")
        assert len(records) == 3
        assert probe.session.calls == ["https://prsindia.org/policy/report-summaries"], "one request, no pagination"
        rows = _manifest(tmp_path)
        assert [r["kind"] for r in rows] == ["prs_report_summary"] * 3
        assert rows[0]["key"] == "PRS_REPORT_SUMMARIES|cyber-crimes-and-cyber-security-of-women"
        assert rows[0]["source"] == "prsindia.org"
        assert rows[0]["surface"] == "report-summaries"
        assert all(r["status"] == "metadata_only" for r in rows)

    def test_vital_stats_gets_its_own_kind_and_key(self, tmp_path):
        probe = _probe(tmp_path, FakeSession(VITAL_LISTING))
        records = probe.probe_publications(surface="vital-stats")
        assert records[0]["kind"] == "prs_vital_stats"
        assert records[0]["key"] == "PRS_VITAL_STATS|direct-taxes-in-india"

    def test_download_fetches_pdfs_and_stamps_sha256(self, tmp_path):
        records = _probe(tmp_path).probe_publications(surface="report-summaries", download=True)
        downloaded = [r for r in records if r["status"] == "downloaded"]
        assert len(downloaded) == 2, "the item with no PDF cannot be downloaded"
        assert len(downloaded[0]["pdf_sha256"]) == 64
        assert (tmp_path / downloaded[0]["pdf_path"]).read_bytes().startswith(b"%PDF")

    def test_a_non_pdf_response_is_an_error_not_a_download(self, tmp_path):
        """A WAF interstitial answers 200 with HTML. That is not an acquisition."""
        session = FakeSession(pdf_body=b"<html>Access denied</html>")
        records = _probe(tmp_path, session).probe_publications(surface="report-summaries", download=True)
        errored = [r for r in records if r["status"] == "error"]
        assert len(errored) == 2
        assert "not a PDF" in errored[0]["error"]
        assert errored[0]["pdf_sha256"] is None

    def test_rerun_appends_nothing(self, tmp_path):
        _probe(tmp_path).probe_publications(surface="report-summaries")
        again = _probe(tmp_path).probe_publications(surface="report-summaries")
        assert again == []
        assert len(_manifest(tmp_path)) == 3

    def test_rerun_with_download_upgrades_metadata_only_rows(self, tmp_path):
        _probe(tmp_path).probe_publications(surface="report-summaries")
        records = _probe(tmp_path).probe_publications(surface="report-summaries", download=True)
        assert [r["status"] for r in records].count("downloaded") == 2

    def test_dry_run_writes_no_manifest(self, tmp_path):
        records = _probe(tmp_path).probe_publications(surface="report-summaries", dry_run=True)
        assert len(records) == 3
        assert all(r["status"] == "dry_run" for r in records)
        assert not (tmp_path / "manifest.jsonl").exists()

    def test_max_records_brake(self, tmp_path):
        records = _probe(tmp_path).probe_publications(surface="report-summaries", max_records=2)
        assert len(records) == 2
        assert len(_manifest(tmp_path)) == 2

    def test_an_unknown_surface_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="unknown PRS surface"):
            _probe(tmp_path).probe_publications(surface="bill-track")


def test_schema_bundled_and_validates(tmp_path):
    pytest.importorskip("jsonschema")
    from commoner_probe import schemas
    from commoner_probe.validate import validate_corpus

    assert "manifest_prs_publication" in schemas.list_all()
    assert _probe(tmp_path).probe_publications(surface="report-summaries", download=True)
    assert validate_corpus(tmp_path, log=lambda _: None)


def test_corpus_streams_prs_publications(tmp_path):
    from commoner_probe.corpus import Corpus

    _probe(tmp_path).probe_publications(surface="report-summaries")
    rows = list(Corpus(tmp_path).manifest_prs_publications())
    assert len(rows) == 3
    assert rows[0].slug == "cyber-crimes-and-cyber-security-of-women"
    assert rows[0].surface == "report-summaries"
