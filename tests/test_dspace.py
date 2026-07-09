"""Tests for the legacy-DSpace (XMLUI/JSPUI) probe.

Fixtures are trimmed real HTML fragments (aladigitallibrary.in, DSpace 6.3,
handle 123456789/2263 and /546) verified live 2026-07-08.
"""

from __future__ import annotations

import json

from commoner_probe.dspace import LegacyDSpaceProbe, parse_browse_page, parse_item_metadata

BROWSE_PAGE_1 = """
<table>
<tr><th id="t1">Issue Date</th><th id="t2">Title</th></tr>
<tr><td headers="t1"><em>24-01</em></td><td headers="t2"><a href="/handle/123456789/2263">Economic&#x20;Survey&#x20;Assam&#x20;2023-24</a></td></tr>
<tr><td headers="t1"><em>197</em></td><td headers="t2"><a href="/handle/123456789/546">The&#x20;Indian&#x20;Electricity&#x20;(Assam&#x20;Amendment)&#x20;Act,&#x20;1973</a></td></tr>
</table>
results 1 to 2 of 2922
"""

BROWSE_PAGE_2_EMPTY = """
<table></table>
"""

ITEM_2263_HTML = """
<li><a href="/">Assam Legislative Assembly Digital Library</a></li>
<li><a href="/handle/123456789/2169">26. Economic Survey of Assam</a></li>
<li><a href="/handle/123456789/2262">Economic Survey Assam 2023-24</a></li>
<table class="table itemDisplayTable">
<tr><td class="metadataFieldLabel dc_title">Title:&nbsp;</td><td class="metadataFieldValue dc_title">Economic&#x20;Survey&#x20;Assam&#x20;2023-24</td></tr>
<tr><td class="metadataFieldLabel dc_date">Issue Date:&nbsp;</td><td class="metadataFieldValue dc_date">Jan-&nbsp;&nbsp;24</td></tr>
<tr><td class="metadataFieldLabel dc_publisher">Publisher:&nbsp;</td><td class="metadataFieldValue dc_publisher">Transformation&#x20;&amp;&#x20;Development&#x20;Department</td></tr>
</table>
<a href="/bitstream/123456789/2263/1/ecosurvey_2023-24.pdf">ecosurvey_2023-24.pdf</a>
"""

ITEM_546_HTML = """
<li><a href="/">Assam Legislative Assembly Digital Library</a></li>
<li><a href="/handle/123456789/1900">19. Government of Assam Acts</a></li>
<li><a href="/handle/123456789/546">The Indian Electricity (Assam Amendment) Act, 1973</a></li>
<table class="table itemDisplayTable">
<tr><td class="metadataFieldLabel dc_title">Title:&nbsp;</td><td class="metadataFieldValue dc_title">The&#x20;Indian&#x20;Electricity&#x20;(Assam&#x20;Amendment)&#x20;Act,&#x20;1973</td></tr>
<tr><td class="metadataFieldLabel dc_date">Issue Date:&nbsp;</td><td class="metadataFieldValue dc_date">197</td></tr>
</table>
<a href="/bitstream/123456789/546/1/act.pdf">act.pdf</a>
"""

PDF_BODY = b"%PDF-1.4 fake ala transcript body that is over one thousand bytes " + b"x" * 1100


class FakeResponse:
    def __init__(self, *, text=None, content=None):
        self.text = text
        self.content = content

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self):
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if "/browse?type=dateissued&order=ASC&rpp=100&offset=0" in url:
            return FakeResponse(text=BROWSE_PAGE_1)
        if "/browse?type=dateissued" in url and "offset=100" in url:
            return FakeResponse(text=BROWSE_PAGE_2_EMPTY)
        if url.endswith("/handle/123456789/2263"):
            return FakeResponse(text=ITEM_2263_HTML)
        if url.endswith("/handle/123456789/546"):
            return FakeResponse(text=ITEM_546_HTML)
        if "bitstream/123456789/2263" in url:
            return FakeResponse(content=PDF_BODY)
        raise AssertionError(f"unrouted url: {url}")


def _probe(tmp_path, **kw):
    probe = LegacyDSpaceProbe(
        tmp_path, base_url="https://aladigitallibrary.in", portal_name="assam-ala", sleep=0, **kw
    )
    probe.session = FakeSession()
    return probe


def test_parse_browse_page_extracts_handles_titles_and_pagination():
    handles, shown_to, total = parse_browse_page(BROWSE_PAGE_1, "123456789")
    assert handles == {
        "2263": "Economic Survey Assam 2023-24",
        "546": "The Indian Electricity (Assam Amendment) Act, 1973",
    }
    assert shown_to == 2
    assert total == 2922


def test_parse_item_metadata_extracts_fields():
    fields = parse_item_metadata(ITEM_2263_HTML)
    assert fields["Title"] == "Economic Survey Assam 2023-24"
    assert fields["Issue Date"] == "Jan-  24"
    assert fields["Publisher"] == "Transformation & Development Department"


def test_probe_records_metadata_only_by_default(tmp_path):
    probe = _probe(tmp_path)
    records = probe.probe(max_records=2)

    assert len(records) == 2
    rec = next(r for r in records if r["handle_id"] == "2263")
    assert rec["key"] == "DSPACE|assam-ala|2263"
    assert rec["title"] == "Economic Survey Assam 2023-24"
    assert rec["issue_date_raw"] == "Jan-  24"
    assert rec["collection"] == "26. Economic Survey of Assam"
    assert rec["bitstream_paths"] == ["/bitstream/123456789/2263/1/ecosurvey_2023-24.pdf"]
    assert rec["status"] == "metadata_only"
    assert rec["downloads"] == []
    assert not any("bitstream" in u for u in probe.session.calls)

    manifest = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert manifest == records


def test_dry_run_lists_handles_without_fetching_item_pages(tmp_path):
    probe = _probe(tmp_path)
    records = probe.probe(max_records=2, dry_run=True)
    assert {r["handle_id"] for r in records} == {"2263", "546"}
    assert all(r["status"] == "dry_run" for r in records)
    assert not any("/handle/123456789/2263" == u.rsplit("aladigitallibrary.in", 1)[-1] for u in probe.session.calls)


def test_download_writes_pdf_and_sha256(tmp_path):
    import hashlib

    probe = _probe(tmp_path)
    records = probe.probe(max_records=1, download=True)
    rec = records[0]
    assert rec["status"] == "downloaded"
    assert len(rec["downloads"]) == 1
    dl = rec["downloads"][0]
    assert dl["sha256"] == hashlib.sha256(PDF_BODY).hexdigest()
    assert (tmp_path / dl["dest"]).read_bytes() == PDF_BODY


def test_metadata_only_then_download_rerun_fetches_bitstream(tmp_path):
    """Regression: metadata_only must not be treated as terminal when a
    later run enables --download (the 2026-07-03 indiacode.py resume-
    staleness lesson applies here too)."""
    probe1 = _probe(tmp_path)
    first = probe1.probe(max_records=1)
    assert first[0]["status"] == "metadata_only"

    probe2 = _probe(tmp_path)
    second = probe2.probe(max_records=1, download=True)
    assert len(second) == 1
    assert second[0]["handle_id"] == "2263"
    assert second[0]["status"] == "downloaded"

    manifest_lines = (tmp_path / "manifest.jsonl").read_text().splitlines()
    assert len(manifest_lines) == 2  # both rows kept — append-only, latest wins


def test_dedup_on_rerun_without_download(tmp_path):
    _probe(tmp_path).probe(max_records=2)
    assert _probe(tmp_path).probe(max_records=2) == []


def test_schema_bundled_and_validates(tmp_path):
    import pytest

    pytest.importorskip("jsonschema")
    from commoner_probe import schemas
    from commoner_probe.validate import validate_corpus

    assert "manifest_legacy_dspace" in schemas.list_all()
    record = {
        "key": "DSPACE|assam-ala|2263",
        "kind": "legacy_dspace_item",
        "record_type": "legacy_dspace_item",
        "source": "https://aladigitallibrary.in",
        "portal_name": "assam-ala",
        "handle_id": "2263",
        "handle_prefix": "123456789",
        "title": "Economic Survey Assam 2023-24",
        "issue_date_raw": "Jan-  24",
        "bitstream_paths": ["/bitstream/123456789/2263/1/ecosurvey_2023-24.pdf"],
        "downloads": [],
        "status": "metadata_only",
        "probed_at": "2026-07-08T18:00:00Z",
    }
    (tmp_path / "manifest.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    assert validate_corpus(tmp_path, log=lambda _: None)


def test_corpus_streams_legacy_dspace(tmp_path):
    from commoner_probe import Corpus

    record = {
        "key": "DSPACE|assam-ala|2263",
        "kind": "legacy_dspace_item",
        "record_type": "legacy_dspace_item",
        "source": "https://aladigitallibrary.in",
        "portal_name": "assam-ala",
        "handle_id": "2263",
        "handle_prefix": "123456789",
        "title": "Economic Survey Assam 2023-24",
        "status": "metadata_only",
        "probed_at": "2026-07-08T18:00:00Z",
    }
    (tmp_path / "manifest.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    records = list(Corpus(tmp_path).manifest_legacy_dspace())
    assert len(records) == 1
    assert records[0].title == "Economic Survey Assam 2023-24"
