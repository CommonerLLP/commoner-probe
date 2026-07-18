"""Tests for the ministry Detailed Demands for Grants (DDG) fetcher.

The "card" listing HTML mirrors the live dea.gov.in "documents/reports"
Bootstrap-card template (verified 2026-07-08): one ``documentRecordTitle``
div per fiscal year followed by a ``viewBtn`` anchor into
``/files/detail_demands_grants_documents/``. The "table" listing HTML
mirrors the live mha.gov.in / doe.gov.in classic Drupal Views table
(verified 2026-07-09); a second table fixture mirrors moef.gov.in's
Hindi-only title cells (verified 2026-07-09). The "list" listing HTML
mirrors the live tribal.nic.in flat anchor list and dst.gov.in's Drupal
Views ``<li>`` rows, including dst.gov.in's older editions that drop the
"Demand"/"Grant" wording entirely (verified 2026-07-09). No network.
"""

from __future__ import annotations

import hashlib
import json

from commoner_probe.ddg import (
    MINISTRY_DDG_PORTALS,
    MinistryDDGPortal,
    MinistryDDGProbe,
    get_portal,
    parse_ddg_listing_list,
    parse_ddg_listing_table,
)

LISTING_HTML = """
<html><body>
<div class="customTable">
  <div class="mt-2 customTablebdr">
    <div class="row">
      <div class="col-lg-8 mb-2">
        <div class="documentRecordTitle">
Detailed Demands for Grants 2026-27</div>
      </div>
      <div class="col-md-3 col-lg-4 d-flex align-items-center">
        <div class="fileSize">12.49 MB</div>
        <div class="viewButton">
          <a href="http://dea.gov.in/files/detail_demands_grants_documents/Final%20DDG%20%282026-27%29%20MoF-1.pdf" class="viewBtn" id="document">View</a>
        </div>
      </div>
    </div>
  </div>
  <div class="mt-2 customTablebdr">
    <div class="row">
      <div class="col-lg-8 mb-2">
        <div class="documentRecordTitle">
Detailed Demand for Grants (2022-23) of Ministry of Finance</div>
      </div>
      <div class="col-md-3 col-lg-4 d-flex align-items-center">
        <div class="fileSize">18.56 MB</div>
        <div class="viewButton">
          <a href="http://dea.gov.in/files/detail_demands_grants_documents/DDG_2022_2023_Scanned_Copy.pdf" class="viewBtn" id="document">View</a>
        </div>
      </div>
    </div>
  </div>
</div>
<a href="http://dea.gov.in/files/circulars_document/unrelated.pdf">Unrelated circular</a>
</body></html>
"""

DEA_PORTAL = get_portal("dea")

PDF_WITH_TEXT = b"%PDF-1.7 born digital " + b"x" * 2000
PDF_SCANNED = b"%PDF-1.5 flattened scan " + b"y" * 2000


class FakeResponse:
    def __init__(self, *, text=None, content=None, status=200, content_type=None):
        self.text = text
        self.content = content
        self.status_code = status
        self.headers = {"Content-Type": content_type} if content_type else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, *, pdf_for_url=None, fail_listing=False):
        self.pdf_for_url = pdf_for_url or {}
        self.fail_listing = fail_listing
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if url == DEA_PORTAL.listing_url:
            if self.fail_listing:
                return FakeResponse(status=503)
            return FakeResponse(text=LISTING_HTML)
        if url in self.pdf_for_url:
            return FakeResponse(content=self.pdf_for_url[url], content_type="application/pdf")
        raise AssertionError(f"unrouted url: {url}")


URL_2627 = "http://dea.gov.in/files/detail_demands_grants_documents/Final%20DDG%20%282026-27%29%20MoF-1.pdf"
URL_2223 = "http://dea.gov.in/files/detail_demands_grants_documents/DDG_2022_2023_Scanned_Copy.pdf"


def _probe(tmp_path, **kw):
    probe = MinistryDDGProbe(tmp_path, portal=DEA_PORTAL, sleep=0)
    probe.session = FakeSession(**kw)
    return probe


def test_get_portal_known_code():
    assert get_portal("dea").ministry_name == "Department of Economic Affairs (Ministry of Finance)"


def test_registry_has_seven_verified_ministries():
    assert {p.ministry_code for p in MINISTRY_DDG_PORTALS} == {
        "dea", "mha", "doe", "dolr", "moefcc", "mopng", "dst",
    }


def test_get_portal_unknown_code_raises():
    import pytest

    with pytest.raises(KeyError):
        get_portal("not-a-real-ministry")


def test_parse_listing_enumerates_years_titles_and_urls(tmp_path):
    probe = _probe(tmp_path)
    docs = probe.discover()
    assert [(d["year"], d["url"]) for d in docs] == [
        ("2026-27", URL_2627),
        ("2022-23", URL_2223),
    ]
    assert docs[0]["title"] == "Detailed Demands for Grants 2026-27"
    # Non-DDG PDFs elsewhere on the page must not be enumerated.
    assert not any("circulars_document" in d["url"] for d in docs)


def test_probe_downloads_with_provenance_and_text_layer(tmp_path, monkeypatch):
    from commoner_probe import ddg as ddg_mod

    monkeypatch.setattr(
        ddg_mod, "extract_pdf_text",
        lambda p: "" if "2022-23" in str(p) else "extracted text " * 50,
    )
    probe = _probe(tmp_path, pdf_for_url={
        URL_2627: PDF_WITH_TEXT,
        URL_2223: PDF_SCANNED,
    })
    records = probe.probe(years=["2026-27", "2022-23"])
    assert [r["status"] for r in records] == ["downloaded", "downloaded"]
    by_year = {r["year"]: r for r in records}
    assert by_year["2026-27"]["text_layer"] is True
    assert by_year["2022-23"]["text_layer"] is False
    assert by_year["2022-23"]["sha256"] == hashlib.sha256(PDF_SCANNED).hexdigest()
    assert by_year["2022-23"]["key"].startswith("MINISTRY_DDG|dea|2022-23|")
    assert by_year["2022-23"]["kind"] == "ministry_ddg_document"
    assert by_year["2022-23"]["ministry_code"] == "dea"
    assert (tmp_path / "dea" / by_year["2022-23"]["filename"]).read_bytes() == PDF_SCANNED
    manifest = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert len(manifest) == 2


def test_probe_skips_existing_file(tmp_path, monkeypatch):
    from commoner_probe import ddg as ddg_mod

    monkeypatch.setattr(ddg_mod, "extract_pdf_text", lambda p: "")
    probe = _probe(tmp_path, pdf_for_url={URL_2223: PDF_SCANNED})
    first = probe.probe(years=["2022-23"])
    second = probe.probe(years=["2022-23"])
    assert first[0]["status"] == "downloaded"
    assert second[0]["status"] == "skipped_exists"
    assert second[0]["sha256"] == first[0]["sha256"]
    assert probe.session.calls.count(URL_2223) == 1


def test_probe_dry_run_lists_without_downloading(tmp_path):
    probe = _probe(tmp_path)
    records = probe.probe(dry_run=True)
    assert [r["year"] for r in records] == ["2026-27", "2022-23"]
    assert all(r["status"] == "dry_run" for r in records)
    assert not (tmp_path / "manifest.jsonl").exists()
    assert probe.session.calls == [DEA_PORTAL.listing_url]


def test_non_pdf_body_is_recorded_as_error_not_written(tmp_path):
    probe = _probe(tmp_path, pdf_for_url={URL_2223: b"<html>WAF interstitial</html>"})
    records = probe.probe(years=["2022-23"])
    assert records[0]["status"] == "error"
    assert "sha256" not in records[0]
    assert not (tmp_path / records[0]["filename"]).exists()


def test_ad_hoc_portal_not_in_registry(tmp_path):
    """A ministry not yet in the seed registry can still be probed directly."""
    portal = MinistryDDGPortal(
        ministry_code="msde",
        ministry_name="Ministry of Skill Development and Entrepreneurship",
        listing_url="https://www.msde.gov.in/documents/reports/detailed-demand-for-grants",
    )
    probe = MinistryDDGProbe(tmp_path, portal=portal, sleep=0)

    class MsdeSession(FakeSession):
        def get(self, url, **kwargs):
            self.calls.append(url)
            if url == portal.listing_url:
                return FakeResponse(text="<html><body>no rows</body></html>")
            raise AssertionError(f"unrouted url: {url}")

    probe.session = MsdeSession()
    records = probe.probe(dry_run=True)
    assert records == []


MHA_LISTING_URL = "https://www.mha.gov.in/en/divisionofmha/finance-division"

TABLE_LISTING_HTML = """
<html><body><table><tbody>
<tr>
  <td class="views-field views-field-counter">1</td>
  <td class="views-field views-field-field-title">Detailed Demands for Grants (Vol-I)-2026-27</td>
  <td class="views-field views-field-id"><a href="/sites/default/files/2026-02/DDGVOL12026-27_11022026.pdf" class="ext">Download (8.56 MB)</a></td>
</tr>
<tr>
  <td class="views-field views-field-counter">2</td>
  <td class="views-field views-field-field-title">Detailed Demands for Grants (Vol-II A)- 2026-27</td>
  <td class="views-field views-field-id"><a href="/sites/default/files/2026-02/DDGVol2A2026-27_11022026.pdf" class="ext">Download (3.1 MB)</a></td>
</tr>
<tr>
  <td class="views-field views-field-counter">3</td>
  <td class="views-field views-field-title">Annual Report 2025-26</td>
  <td class="views-field views-field-id"><a href="/sites/default/files/2025-08/AnnualReport2025-26.pdf" class="ext">Download</a></td>
</tr>
</tbody></table></body></html>
"""


def test_parse_listing_table_matches_title_cell_and_skips_non_ddg_rows():
    docs = parse_ddg_listing_table(TABLE_LISTING_HTML, MHA_LISTING_URL)
    assert [d["year"] for d in docs] == ["2026-27", "2026-27"]
    assert docs[0]["title"] == "Detailed Demands for Grants (Vol-I)-2026-27"
    # The Annual Report row has no "demand"/"grant" title cell match — excluded.
    assert not any("AnnualReport" in d["url"] for d in docs)


def test_multi_volume_per_year_does_not_collide(tmp_path, monkeypatch):
    """mha.gov.in publishes two volumes for the same fiscal year — the
    fixed key must disambiguate them instead of colliding (2026-07-09 bug
    caught while expanding the registry beyond dea.gov.in's one-doc-per-year
    shape)."""
    from commoner_probe import ddg as ddg_mod

    monkeypatch.setattr(ddg_mod, "extract_pdf_text", lambda p: "")
    mha_portal = get_portal("mha")
    probe = MinistryDDGProbe(tmp_path, portal=mha_portal, sleep=0)

    vol1_url = "https://www.mha.gov.in/sites/default/files/2026-02/DDGVOL12026-27_11022026.pdf"
    vol2_url = "https://www.mha.gov.in/sites/default/files/2026-02/DDGVol2A2026-27_11022026.pdf"

    class MhaSession(FakeSession):
        def get(self, url, **kwargs):
            self.calls.append(url)
            if url == mha_portal.listing_url:
                return FakeResponse(text=TABLE_LISTING_HTML)
            if url in (vol1_url, vol2_url):
                return FakeResponse(content=PDF_WITH_TEXT, content_type="application/pdf")
            raise AssertionError(f"unrouted url: {url}")

    probe.session = MhaSession()
    records = probe.probe(years=["2026-27"])
    assert [r["status"] for r in records] == ["downloaded", "downloaded"]
    keys = [r["key"] for r in records]
    assert len(keys) == len(set(keys)), f"colliding keys: {keys}"
    filenames = [r["filename"] for r in records]
    assert len(filenames) == len(set(filenames)), f"colliding filenames: {filenames}"


# moef.gov.in publishes Hindi-only title cells ("विस्तृत मांगें" = "detailed
# demands") in an otherwise identical tr/td Drupal table — the English-only
# _DEMAND_GRANT_RE regex used to miss these entirely (2026-07-09 bug caught
# while expanding the registry; see the bilingual pattern in ddg.py).
HINDI_TABLE_HTML = """
<html><body><table><tbody>
<tr>
  <td>1.</td>
  <td><a href="/uploads/2023/11/DDG-2023-24.pdf">विस्तृत मांगें 2023-2024।</a></td>
</tr>
<tr>
  <td>2.</td>
  <td><a href="/uploads/2022/02/annual-report.pdf">वार्षिक रिपोर्ट 2022-2023</a></td>
</tr>
</tbody></table></body></html>
"""


def test_parse_listing_table_matches_hindi_title_cells():
    docs = parse_ddg_listing_table(HINDI_TABLE_HTML, "https://moef.gov.in/detailed-demand-for-grants")
    assert [d["year"] for d in docs] == ["2023-24"]
    # The unrelated "annual report" row (no demand/grant keyword, Hindi or
    # English) must not be picked up.
    assert not any("annual-report" in d["url"] for d in docs)


TRIBAL_LIST_HTML = """
<html><body>
<span class="far fa-file-pdf"></span><a href="downloads/FINANCE/DDG 2024-25.pdf" target="_blank"> Detailed Demand for Grants 2024-25 (3.15 MB)</a><br>
<span class="far fa-file-pdf"></span><a href="downloads/FINANCE/circular.pdf" target="_blank"> Recruitment Circular 2024 (1.2 MB)</a><br>
</body></html>
"""

# dst.gov.in drops the "Demand"/"Grant" wording for older editions — the
# anchor text is bare "<year> (<size>)". The filename still says "DDG",
# which is the fallback signal parse_ddg_listing_list uses.
DST_LIST_HTML = """
<html><body><ul>
<li class="views-row"><div class="views-field-php"><span class="field-content">
  <a href="https://dst.gov.in/sites/default/files/DDG%202024-25.pdf" target="_BLANK" title="pdf">Detailed Demands For Grants 2024-2025 (179.95 KB)</a>
</span></div></li>
<li class="views-row"><div class="views-field-php"><span class="field-content">
  <a href="https://dst.gov.in/sites/default/files/DDG%202017-18.pdf" target="_BLANK" title="pdf">2017-18 (3.37 MB)</a>
</span></div></li>
</ul></body></html>
"""


def test_parse_listing_list_flat_anchor_shape():
    docs = parse_ddg_listing_list(TRIBAL_LIST_HTML, "https://tribal.nic.in/Finance.aspx")
    assert [d["year"] for d in docs] == ["2024-25"]
    assert not any("circular" in d["url"] for d in docs)


def test_parse_listing_list_falls_back_to_ddg_filename_token():
    docs = parse_ddg_listing_list(DST_LIST_HTML, "https://dst.gov.in/documents/budget")
    assert sorted(d["year"] for d in docs) == ["2017-18", "2024-25"]
    by_year = {d["year"]: d for d in docs}
    # The un-keyworded 2017-18 anchor was caught via the "ddg" filename token.
    assert by_year["2017-18"]["title"] == "2017-18 (3.37 MB)"


def test_probe_dispatches_list_template(tmp_path):
    """MinistryDDGProbe.discover() must route template='list' portals
    through parse_ddg_listing_list, not the card/table parsers."""
    portal = MinistryDDGPortal(
        ministry_code="dst",
        ministry_name="Department of Science and Technology",
        listing_url="https://dst.gov.in/documents/budget",
        template="list",
    )
    probe = MinistryDDGProbe(tmp_path, portal=portal, sleep=0)

    class DstSession(FakeSession):
        def get(self, url, **kwargs):
            self.calls.append(url)
            if url == portal.listing_url:
                return FakeResponse(text=DST_LIST_HTML)
            raise AssertionError(f"unrouted url: {url}")

    probe.session = DstSession()
    docs = probe.discover()
    assert sorted(d["year"] for d in docs) == ["2017-18", "2024-25"]


def test_records_validate_against_schema(tmp_path, monkeypatch):
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        import pytest
        pytest.skip("jsonschema not installed")
    from commoner_probe import ddg as ddg_mod
    from commoner_probe import schemas

    monkeypatch.setattr(ddg_mod, "extract_pdf_text", lambda p: "")
    schema = schemas.load("manifest_ministry_ddg")
    probe = _probe(tmp_path, pdf_for_url={URL_2223: PDF_SCANNED})
    for record in probe.probe(years=["2022-23"]):
        Draft202012Validator(schema).validate(record)


# dolr.gov.in is a WordPress "document-category" listing: the DDG series
# spills onto a page 2 (Codex review, PR #42 — page 1 alone silently dropped
# the older editions). discover() must follow same-listing /page/<N>/ links.
DOLR_LISTING_URL = "https://dolr.gov.in/document-category/detailed-demand-for-grants/"

DOLR_PAGE1_HTML = """
<html><body><table><tbody>
<tr>
  <td>1</td>
  <td>Detailed Demand for Grants 2025-26</td>
  <td><a href="/wp-content/uploads/2025/03/DDG-2025-26.pdf">View</a></td>
</tr>
</tbody></table>
<nav class="pagination">
  <a class="page-numbers" href="https://dolr.gov.in/document-category/detailed-demand-for-grants/page/1/">1</a>
  <a class="page-numbers" href="https://dolr.gov.in/document-category/detailed-demand-for-grants/page/2/">2</a>
  <a class="page-numbers" href="https://dolr.gov.in/document-category/other-category/page/2/">Other section</a>
</nav>
</body></html>
"""

DOLR_PAGE2_HTML = """
<html><body><table><tbody>
<tr>
  <td>2</td>
  <td>Detailed Demand for Grants 2024-25</td>
  <td><a href="/wp-content/uploads/2024/03/DDG-2024-25.pdf">View</a></td>
</tr>
</tbody></table>
</body></html>
"""


def test_discover_follows_wordpress_pagination(tmp_path):
    """Page 2 of a paginated listing must be fetched; the page-1 self-link
    must not be re-requested; a paginated link to a *different* section of
    the site must not be followed."""
    portal = get_portal("dolr")
    probe = MinistryDDGProbe(tmp_path, portal=portal, sleep=0)
    page2_url = DOLR_LISTING_URL + "page/2/"

    class DolrSession(FakeSession):
        def get(self, url, **kwargs):
            self.calls.append(url)
            if url == portal.listing_url:
                return FakeResponse(text=DOLR_PAGE1_HTML)
            if url == page2_url:
                return FakeResponse(text=DOLR_PAGE2_HTML)
            raise AssertionError(f"unrouted url: {url}")

    probe.session = DolrSession()
    docs = probe.discover()
    assert [d["year"] for d in docs] == ["2025-26", "2024-25"]
    assert docs[0]["url"].endswith("/wp-content/uploads/2025/03/DDG-2025-26.pdf")
    assert docs[1]["url"].endswith("/wp-content/uploads/2024/03/DDG-2024-25.pdf")
    assert probe.session.calls == [portal.listing_url, page2_url]


def test_discover_single_page_listing_unchanged(tmp_path):
    """A listing with no /page/<N>/ links makes exactly one request."""
    portal = get_portal("dolr")
    probe = MinistryDDGProbe(tmp_path, portal=portal, sleep=0)

    class SinglePageSession(FakeSession):
        def get(self, url, **kwargs):
            self.calls.append(url)
            if url == portal.listing_url:
                return FakeResponse(text=DOLR_PAGE2_HTML)  # no pagination nav
            raise AssertionError(f"unrouted url: {url}")

    probe.session = SinglePageSession()
    docs = probe.discover()
    assert [d["year"] for d in docs] == ["2024-25"]
    assert probe.session.calls == [portal.listing_url]
