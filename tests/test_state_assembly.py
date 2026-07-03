"""Offline unit tests for commoner_probe.neva (StateAssemblyCrawler).

All tests use frozen HTML/data fixtures — no network calls.
"""

from __future__ import annotations

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


# ---------------------------------------------------------------------------
# probe_depth — orchestration only; lower-level methods stubbed
# ---------------------------------------------------------------------------

class TestProbeDepth:
    def _crawler(self, tmp_path):
        crawler = StateAssemblyCrawler(
            portal_code="gujarat", state_code="GJ", out_dir=tmp_path, sleep=0,
        )
        crawler.bootstrap = lambda: None
        return crawler

    def test_finds_latest_nonempty_assembly(self, tmp_path):
        crawler = self._crawler(tmp_path)

        def get_sessions(asm):
            if asm == 15:
                return [{"SessionCode": 7, "SessionName": "Monsoon"}]
            return []

        crawler.get_sessions = get_sessions
        crawler.get_dates = lambda asm, sess: [{"SessionDateId": 99}]
        crawler.fetch_questions_for_date = lambda asm, sess, date_id, seen: [{}, {}]
        crawler.fetch_papers_laid = lambda asm, sess, date_id, seen: [{}]
        crawler.fetch_members = lambda asm: [{}, {}, {}]

        result = crawler.probe_depth(max_assembly=16)
        assert result["latest_assembly"] == 15
        assert result["sessions_found"] == 1
        assert result["latest_session_code"] == 7
        assert result["dates_found"] == 1
        assert result["questions_sample"] == 2
        assert result["papers_sample"] == 1
        assert result["members_count"] == 3
        assert result["reachable"] is True
        assert "probed_at" in result

    def test_no_sessions_at_any_assembly(self, tmp_path):
        crawler = self._crawler(tmp_path)
        crawler.get_sessions = lambda asm: []
        result = crawler.probe_depth(max_assembly=5)
        assert result["latest_assembly"] is None
        assert result["assemblies_scanned"] == 5
        assert result["members_count"] == 0

    def test_tolerates_http_errors_per_step(self, tmp_path):
        crawler = self._crawler(tmp_path)
        crawler.get_sessions = lambda asm: (
            [{"SessionCode": 1, "SessionName": "S"}] if asm == 10 else []
        )
        crawler.get_dates = lambda asm, sess: (_ for _ in ()).throw(RuntimeError("HTTP 500"))
        crawler.fetch_members = lambda asm: (_ for _ in ()).throw(RuntimeError("HTTP 500"))

        result = crawler.probe_depth(max_assembly=10)
        assert result["latest_assembly"] == 10
        assert result["dates_found"] == 0
        assert result["members_count"] == 0


# ---------------------------------------------------------------------------
# neva_portals registry
# ---------------------------------------------------------------------------

class TestNevaPortalsRegistry:
    def test_31_assemblies_6_councils(self):
        from commoner_probe.neva_portals import ASSEMBLIES, COUNCILS

        assert len(ASSEMBLIES) == 31
        assert len(COUNCILS) == 6

    def test_portal_codes_unique(self):
        from commoner_probe.neva_portals import ALL_PORTALS

        codes = [p.portal_code for p in ALL_PORTALS]
        assert len(codes) == len(set(codes))

    def test_council_states_are_bicameral(self):
        from commoner_probe.neva_portals import COUNCILS

        council_states = {p.state_code for p in COUNCILS}
        assert council_states == {"AP", "BR", "KA", "MH", "TG", "UP"}

    def test_get_portal(self):
        from commoner_probe.neva_portals import get_portal

        p = get_portal("gujarat")
        assert p is not None
        assert p.state_code == "GJ"
        assert p.chamber == "assembly"
        assert get_portal("nonexistent") is None

    def test_iter_portals_filters_by_chamber(self):
        from commoner_probe.neva_portals import iter_portals

        assert all(p.chamber == "council" for p in iter_portals(chamber="council"))
        assert len(iter_portals(chamber=None)) == 37
