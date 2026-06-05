"""Offline unit tests for commoner_probe.neva (StateAssemblyCrawler).

All tests use frozen HTML/data fixtures — no network calls.
"""

from __future__ import annotations

import pytest

from commoner_probe.neva import (
    StateAssemblyCrawler,
    _collect_pdf_hrefs,
    _parse_members_html,
    _parse_table,
    _split_member_cell,
)


# ---------------------------------------------------------------------------
# _split_member_cell
# ---------------------------------------------------------------------------

class TestSplitMemberCell:
    def test_name_and_constituency(self):
        name, const = _split_member_cell("Shri A. Kumar (Ahmedabad East)")
        assert name == "Shri A. Kumar"
        assert const == "Ahmedabad East"

    def test_no_constituency(self):
        name, const = _split_member_cell("Shri B. Patel")
        assert name == "Shri B. Patel"
        assert const == ""

    def test_empty(self):
        name, const = _split_member_cell("")
        assert name == ""
        assert const == ""

    def test_multiple_parens_uses_last(self):
        # Only the outermost trailing (…) should be the constituency
        name, const = _split_member_cell("Dr. C (ABC) Singh (Surat North)")
        assert const == "Surat North"


# ---------------------------------------------------------------------------
# _collect_pdf_hrefs
# ---------------------------------------------------------------------------

class TestCollectPdfHrefs:
    def test_picks_neva_urls(self):
        hrefs = [
            ["https://cms.neva.gov.in/files/q1.pdf", "https://example.com/other"],
            [],
        ]
        result = _collect_pdf_hrefs(hrefs)
        assert "https://cms.neva.gov.in/files/q1.pdf" in result
        assert "https://example.com/other" not in result

    def test_picks_pdf_extension(self):
        hrefs = [["https://other-domain.gov.in/doc.pdf"]]
        assert "https://other-domain.gov.in/doc.pdf" in _collect_pdf_hrefs(hrefs)

    def test_empty(self):
        assert _collect_pdf_hrefs([]) == []


# ---------------------------------------------------------------------------
# _parse_table — frozen HTML snippets
# ---------------------------------------------------------------------------

_QUESTION_TABLE_HTML = """
<table>
  <tr>
    <td>1</td><td>42</td><td>Water Supply</td><td>Is water clean?</td>
    <td>Urban Development</td><td>Written</td><td>Shri Test (Ahmedabad)</td>
  </tr>
</table>
"""

_MEMBERS_HTML = """
<a class="card" href="/Member/Details/101/assembly/15">
  <img src="https://cms.neva.gov.in/photos/101.jpg" />
  <h3>Shri Test MLA</h3>
  <h6>Congress</h6>
  <table>
    <tr><td>મતવિસ્તારનું</td><td>Ahmedabad East</td></tr>
    <tr><td>જન્મ તારીખ</td><td>01/01/1970</td></tr>
  </table>
  <ul>
    <li><i class="fa-phone"></i> 9876543210</li>
    <li><i class="fa-envelope"></i>test[at]example[dot]com</li>
  </ul>
</a>
"""


class TestParseTable:
    def test_extracts_rows(self):
        rows, hrefs = _parse_table(_QUESTION_TABLE_HTML)
        assert len(rows) == 1
        assert rows[0][1] == "42"
        assert rows[0][2] == "Water Supply"

    def test_hrefs_parallel_to_rows(self):
        rows, hrefs = _parse_table(_QUESTION_TABLE_HTML)
        assert len(hrefs) == len(rows)

    def test_empty_html(self):
        rows, hrefs = _parse_table("<table></table>")
        assert rows == []


class TestParseMembersHtml:
    def test_extracts_member(self):
        records = _parse_members_html(_MEMBERS_HTML, "GJ", "gujarat", 15)
        assert len(records) == 1
        r = records[0]
        assert r["member_id"] == 101
        assert r["name"] == "Shri Test MLA"
        assert r["party"] == "Congress"
        assert r["record_type"] == "member"
        assert r["state_code"] == "GJ"
        assert r["portal_code"] == "gujarat"
        assert r["assembly_no"] == 15

    def test_email_deobfuscated(self):
        records = _parse_members_html(_MEMBERS_HTML, "GJ", "gujarat", 15)
        assert records[0]["email"] == "test@example.com"

    def test_probed_at_present(self):
        records = _parse_members_html(_MEMBERS_HTML, "GJ", "gujarat", 15)
        assert "probed_at" in records[0]
        assert records[0]["probed_at"]

    def test_no_members(self):
        records = _parse_members_html("<html></html>", "GJ", "gujarat", 15)
        assert records == []


# ---------------------------------------------------------------------------
# StateAssemblyCrawler — constructor (no network)
# ---------------------------------------------------------------------------

class TestStateAssemblyCrawlerInit:
    def test_init_sets_paths(self, tmp_path):
        crawler = StateAssemblyCrawler(
            portal_code="gujarat",
            state_code="GJ",
            out_dir=tmp_path,
            sleep=0,
        )
        assert crawler.portal_code == "gujarat"
        assert crawler.state_code == "GJ"
        assert crawler.out_dir == tmp_path
        assert crawler.questions_path == tmp_path / "questions.jsonl"
        assert crawler.unlisted_path == tmp_path / "questions_unlisted.jsonl"
        assert crawler.members_path == tmp_path / "members.jsonl"
        assert crawler.papers_path == tmp_path / "papers_laid.jsonl"
