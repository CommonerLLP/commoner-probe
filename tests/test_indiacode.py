"""Offline unit tests for commoner_probe.indiacode.

Fixtures reproduce the exact quirks observed on live indiacode.nic.in pages
(verified 2026-07 against the West Bengal Public Libraries Act, 1979,
handle 14547): unclosed ``<tr>`` tags in the subordinate-legislation modal
tables, and a trailing space before the closing quote in ``ViewFileUploaded``
href values. No network — a fake session serves canned HTML.
"""

from __future__ import annotations

import json

from commoner_probe.indiacode import (
    STATE_HANDLES,
    IndiaCodeProbe,
    parse_act_metadata,
    parse_act_pdf_url,
    parse_browse_page,
    parse_subordinate_rows,
)

ACT_DETAIL_HTML = """
<html><body>
<a data-toggle="tooltip" href="/bitstream/123456789/14547/1/1979-39.pdf">PDF</a>
<table class="table itemDisplayTable">
<tr><td class="metadataFieldLabel">Act ID:&nbsp;</td><td class="metadataFieldValue">197939</td></tr>
<tr><td class="metadataFieldLabel">Act Number:&nbsp;</td><td class="metadataFieldValue">39</td></tr>
<tr><td class="metadataFieldLabel">Enactment Date:&nbsp;</td><td class="metadataFieldValue">1980-01-07</td></tr>
<tr><td class="metadataFieldLabel">Act Year:&nbsp;</td><td class="metadataFieldValue">1979</td></tr>
<tr><td class="metadataFieldLabel">Short Title:&nbsp;</td><td id="short_title" class="metadataFieldValue">The West Bengal Public Libraries Act, 1979</td></tr>
<tr><td class="metadataFieldLabel">Department:&nbsp;</td><td class="metadataFieldValue">Law Department</td></tr>
<tr><td class="metadataFieldLabel">Type:&nbsp;</td><td class="metadataFieldValue">STATE</td></tr>
<tr><td class="metadataFieldLabel">Location:&nbsp;</td><td class="metadataFieldValue">West Bengal</td></tr>
</table>
<table id="myTableRules" class="table"><thead><th>Year</th></thead><tbody><tr><td class="modaltd1">10-08-2005</td>
<td  class="modaltd2">The West Bengal Sponsored Public Library Management Rules, 2005</td>
<td class="modaltd2"style="vertical-align: middle;">  </td>
<td  class="modaltd3"style="vertical-align: middle;"><a href="/ViewFileUploaded?path=AC_WB_TEST/rulesindividualfile/&file=32.pdf " ><img/></a></td>
<td class="modaltd3"style="vertical-align: middle;"> </td>
</tbody></table>
<table id="myTableRegulation" class="table"><thead><th>Year</th></thead><tbody></tbody></table>
<table id="myTableNotification" class="table"><thead><th>Year</th></thead><tbody><tr><td class="modaltd1">12-10-1982</td>
<td  class="modaltd2">The West Bengal Public Libraries ( Amendment ) Act, 1982</td>
<td class="modaltd2"style="vertical-align: middle;">  </td>
<td  class="modaltd3"style="vertical-align: middle;"><a href="/ViewFileUploaded?path=AC_WB_TEST/notificationindividualfile/&file=30.pdf " ><img/></a></td>
<td class="modaltd3"style="vertical-align: middle;"> </td>
<tr><td class="modaltd1">09-10-1985</td>
<td  class="modaltd2">The West Bengal Public Libraries ( Amendment ) Act, 1985</td>
<td class="modaltd2"style="vertical-align: middle;">  </td>
<td  class="modaltd3"style="vertical-align: middle;"><a href="/ViewFileUploaded?path=AC_WB_TEST/notificationindividualfile/&file=8.pdf " ><img/></a></td>
<td class="modaltd3"style="vertical-align: middle;"> </td>
</tbody></table>
</body></html>
"""

BROWSE_PAGE_HTML = """
<html><body>
<a href="/handle/123456789/17953?view_type=browse">Act A</a>
<a href="/handle/123456789/17368?view_type=browse">Act B</a>
Showing items 1 to 2 of 2
</body></html>
"""

BROWSE_PAGE_EMPTY_HTML = "<html><body>No items to display.</body></html>"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

class TestParseActMetadata:
    def test_extracts_all_fields(self):
        meta = parse_act_metadata(ACT_DETAIL_HTML)
        assert meta == {
            "act_id": "197939",
            "act_no": "39",
            "enactment_date": "1980-01-07",
            "act_year": "1979",
            "short_title": "The West Bengal Public Libraries Act, 1979",
            "department": "Law Department",
            "act_type": "STATE",
            "location": "West Bengal",
        }

    def test_missing_fields_are_none(self):
        meta = parse_act_metadata("<html></html>")
        assert meta["act_id"] is None
        assert meta["short_title"] is None


class TestParseActPdfUrl:
    def test_finds_bitstream_link(self):
        url = parse_act_pdf_url(ACT_DETAIL_HTML)
        assert url == "https://indiacode.nic.in/bitstream/123456789/14547/1/1979-39.pdf"

    def test_none_when_absent(self):
        assert parse_act_pdf_url("<html></html>") is None


class TestParseSubordinateRows:
    def test_rules_table_one_row(self):
        rows = parse_subordinate_rows(ACT_DETAIL_HTML, "myTableRules")
        assert len(rows) == 1
        r = rows[0]
        assert r["instrument_date"] == "10-08-2005"
        assert r["description"] == "The West Bengal Sponsored Public Library Management Rules, 2005"
        assert r["lang"] == "en"
        assert r["actid"] == "AC_WB_TEST"
        assert r["folder"] == "rulesindividualfile"
        # Regression: trailing space before the closing quote in the source
        # href must not leak into the parsed filename.
        assert r["filename"] == "32.pdf"

    def test_notification_table_six_amendment_rows(self):
        rows = parse_subordinate_rows(ACT_DETAIL_HTML, "myTableNotification")
        assert len(rows) == 2
        assert rows[0]["filename"] == "30.pdf"
        assert rows[1]["filename"] == "8.pdf"
        assert all("Amendment" in r["description"] for r in rows)

    def test_empty_table(self):
        assert parse_subordinate_rows(ACT_DETAIL_HTML, "myTableRegulation") == []

    def test_missing_table_id(self):
        assert parse_subordinate_rows(ACT_DETAIL_HTML, "myTableStatutes") == []


class TestParseBrowsePage:
    def test_extracts_handles_and_pagination(self):
        handles, shown_to, total = parse_browse_page(BROWSE_PAGE_HTML)
        assert handles == ["17953", "17368"]
        assert shown_to == 2
        assert total == 2

    def test_dedupes_handles(self):
        html = BROWSE_PAGE_HTML + '<a href="/handle/123456789/17953?view_type=browse">dup</a>'
        handles, _, _ = parse_browse_page(html)
        assert handles == ["17953", "17368"]

    def test_no_items_halts_pagination(self):
        handles, shown_to, total = parse_browse_page(BROWSE_PAGE_EMPTY_HTML)
        assert handles == []
        assert shown_to == total == 0


# ---------------------------------------------------------------------------
# STATE_HANDLES registry
# ---------------------------------------------------------------------------

class TestStateHandles:
    def test_covers_35_plus_states_and_uts(self):
        assert len(STATE_HANDLES) >= 35

    def test_known_states_present(self):
        assert STATE_HANDLES["West Bengal"] == "2512"
        assert STATE_HANDLES["Gujarat"] == "2455"
        assert STATE_HANDLES["Sikkim"] == "2506"

    def test_handles_are_unique(self):
        values = list(STATE_HANDLES.values())
        assert len(values) == len(set(values))


# ---------------------------------------------------------------------------
# IndiaCodeProbe — fake session, no network
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, text: str = "", content: bytes | None = None, status: int = 200):
        self.text = text
        self.content = content if content is not None else text.encode("utf-8")
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, routes: dict[str, FakeResponse]):
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append(url)
        for needle, resp in self.routes.items():
            if needle in url:
                return resp
        raise AssertionError(f"FakeSession had no route matching: {url}")


def _probe(tmp_path, routes):
    probe = IndiaCodeProbe(tmp_path, sleep=0)
    probe.session = FakeSession(routes)
    return probe


class TestIterActHandles:
    def test_single_page(self, tmp_path):
        probe = _probe(tmp_path, {"browse": FakeResponse(BROWSE_PAGE_HTML)})
        assert list(probe.iter_act_handles("2512")) == ["17953", "17368"]

    def test_empty_state_yields_nothing(self, tmp_path):
        probe = _probe(tmp_path, {"browse": FakeResponse(BROWSE_PAGE_EMPTY_HTML)})
        assert list(probe.iter_act_handles("2512")) == []


class TestProbeAct:
    def test_emits_act_plus_subordinate_records(self, tmp_path):
        probe = _probe(tmp_path, {"handle/123456789/14547": FakeResponse(ACT_DETAIL_HTML)})
        records = probe.probe_act("West Bengal", "2512", "14547")

        assert len(records) == 1 + 1 + 2  # act + 1 rule + 2 notifications
        act_rec = records[0]
        assert act_rec["instrument_type"] == "act"
        assert act_rec["key"] == "INDIACODE|2512|14547|act|en"
        assert act_rec["act_no"] == "39"
        assert act_rec["short_title"] == "The West Bengal Public Libraries Act, 1979"
        assert act_rec["source_url"].endswith("1979-39.pdf")
        assert act_rec["is_amendment"] is False

        rule_rec = next(r for r in records if r["instrument_type"] == "rule")
        assert rule_rec["is_amendment"] is False
        assert rule_rec["filename"] == "32.pdf"

        notif_recs = [r for r in records if r["instrument_type"] == "notification"]
        assert len(notif_recs) == 2
        assert all(r["is_amendment"] is True for r in notif_recs)
        assert notif_recs[0]["key"] == "INDIACODE|2512|14547|notification|en|30.pdf"

    def test_all_records_share_act_metadata(self, tmp_path):
        probe = _probe(tmp_path, {"handle/123456789/14547": FakeResponse(ACT_DETAIL_HTML)})
        records = probe.probe_act("West Bengal", "2512", "14547")
        assert all(r["state"] == "West Bengal" for r in records)
        assert all(r["act_handle"] == "14547" for r in records)
        assert all(r["department"] == "Law Department" for r in records)


class TestProbeStates:
    def test_dry_run_emits_status_only_no_network(self, tmp_path):
        probe = _probe(tmp_path, {})
        records = probe.probe_states(["West Bengal"], dry_run=True)
        assert len(records) == 1
        assert records[0]["status"] == "dry_run"
        assert probe.session.calls == []

    def test_unknown_state(self, tmp_path):
        probe = _probe(tmp_path, {})
        records = probe.probe_states(["Narnia"], dry_run=True)
        assert records[0]["status"] == "unknown_state"
        assert (tmp_path / "manifest.jsonl").exists()

    def test_full_probe_writes_manifest_and_dedupes_on_rerun(self, tmp_path):
        routes = {
            "browse": FakeResponse(BROWSE_PAGE_HTML),
            "handle/123456789/17953": FakeResponse(ACT_DETAIL_HTML),
            "handle/123456789/17368": FakeResponse(ACT_DETAIL_HTML),
        }
        probe = _probe(tmp_path, routes)
        first = probe.probe_states(["West Bengal"], download=False)
        assert len(first) == 2 * 4  # 2 acts x 4 records each

        manifest_lines = (tmp_path / "manifest.jsonl").read_text().splitlines()
        assert len(manifest_lines) == 8

        probe2 = _probe(tmp_path, routes)
        second = probe2.probe_states(["West Bengal"], download=False)
        assert second == []  # everything already seen; no new manifest rows
        assert len(manifest_lines) == len((tmp_path / "manifest.jsonl").read_text().splitlines())

    def test_no_download_pass_then_download_rerun_updates_stale_manifest_rows(self, tmp_path):
        """Regression for a Codex review finding on PR #20: a metadata-only
        (--no-download) pass followed by a downloads-enabled rerun on the same
        corpus must not leave manifest.jsonl rows permanently stuck at
        status="pending" while the files exist on disk."""
        routes = {
            "browse": FakeResponse(BROWSE_PAGE_HTML),
            "handle/123456789/17953": FakeResponse(ACT_DETAIL_HTML),
            "handle/123456789/17368": FakeResponse(ACT_DETAIL_HTML),
            "1979-39.pdf": FakeResponse(content=b"%PDF-act"),
            "rulesindividualfile": FakeResponse(content=b"%PDF-rule"),
            "notificationindividualfile": FakeResponse(content=b"%PDF-notif"),
        }

        probe1 = _probe(tmp_path, routes)
        first = probe1.probe_states(["West Bengal"], download=False)
        assert all(r["status"] == "pending" for r in first if r.get("source_url"))
        assert all("dest" not in r or r["dest"] is None for r in first)

        probe2 = _probe(tmp_path, routes)
        second = probe2.probe_states(["West Bengal"], download=True)
        # The rerun must actually re-emit updated rows for every pending
        # record — not silently skip them just because the key was seen.
        assert len(second) == len(first)
        assert all(r["status"] == "downloaded" for r in second)
        assert all(r["dest"] for r in second)

        # manifest.jsonl now has both the stale pending rows (run 1) and the
        # fresh downloaded rows (run 2) — append-only. A downstream reader
        # must take the LAST row per key as authoritative.
        manifest = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text().splitlines()]
        by_key_last = {}
        for rec in manifest:
            by_key_last[rec["key"]] = rec
        assert all(r["status"] == "downloaded" and r["dest"] for r in by_key_last.values())

        # A third run (still download=True) must not re-download anything —
        # the "downloaded" status is now terminal.
        probe3 = _probe(tmp_path, routes)
        third = probe3.probe_states(["West Bengal"], download=True)
        assert third == []

    def test_max_acts_stops_early(self, tmp_path):
        routes = {
            "browse": FakeResponse(BROWSE_PAGE_HTML),
            "handle/123456789/17953": FakeResponse(ACT_DETAIL_HTML),
            "handle/123456789/17368": FakeResponse(ACT_DETAIL_HTML),
        }
        probe = _probe(tmp_path, routes)
        records = probe.probe_states(["West Bengal"], download=False, max_acts=1)
        assert len(records) == 4  # one act's worth only

    def test_fetch_error_is_recorded_not_raised(self, tmp_path):
        class BoomSession:
            def get(self, url, **kwargs):
                raise RuntimeError("network exploded")

        probe = IndiaCodeProbe(tmp_path, sleep=0)
        probe.session = BoomSession()
        records = probe.probe_states(["West Bengal"])
        assert records[0]["status"] == "fetch_error"
        assert "network exploded" in records[0]["error"]


class TestDownloadInstrument:
    def test_downloads_and_hashes(self, tmp_path):
        probe = _probe(tmp_path, {"ViewFileUploaded": FakeResponse(content=b"%PDF-fake-bytes")})
        record = {
            "state": "West Bengal",
            "act_handle": "14547",
            "instrument_type": "rule",
            "lang": "en",
            "filename": "32.pdf",
            "source_url": "https://indiacode.nic.in/ViewFileUploaded?path=x/rulesindividualfile/&file=32.pdf",
        }
        probe.download_instrument(record)
        assert record["status"] == "downloaded"
        assert record["dest"] == "pdfs/West_Bengal/14547/rule_en_32.pdf"
        assert (tmp_path / record["dest"]).read_bytes() == b"%PDF-fake-bytes"
        assert len(record["sha256"]) == 64

    def test_skips_existing_file(self, tmp_path):
        dest = tmp_path / "pdfs" / "West_Bengal" / "14547" / "rule_en_32.pdf"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"already-here")

        probe = _probe(tmp_path, {})  # no routes — must not hit the network
        record = {
            "state": "West Bengal",
            "act_handle": "14547",
            "instrument_type": "rule",
            "lang": "en",
            "filename": "32.pdf",
            "source_url": "https://indiacode.nic.in/ViewFileUploaded?path=x/rulesindividualfile/&file=32.pdf",
        }
        probe.download_instrument(record)
        assert record["status"] == "skipped_exists"
        assert probe.session.calls == []

    def test_no_source_url_is_noop(self, tmp_path):
        probe = _probe(tmp_path, {})
        record = {"source_url": None}
        result = probe.download_instrument(record)
        assert result is record
        assert probe.session.calls == []


def test_manifest_records_round_trip_through_json(tmp_path):
    routes = {"handle/123456789/14547": FakeResponse(ACT_DETAIL_HTML)}
    probe = _probe(tmp_path, routes)
    probe.probe_states(["West Bengal"], download=False)
    # Nothing was enumerated (no browse route) so this exercises probe_act
    # indirectly via a manual call instead — the real integration is covered
    # by test_full_probe_writes_manifest_and_dedupes_on_rerun above.
    records = probe.probe_act("West Bengal", "2512", "14547")
    for r in records:
        json.dumps(r)  # every field must be JSON-serialisable
