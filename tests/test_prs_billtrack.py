"""Tests for the PRS Bill Track surface.

Fixture markup mirrors the live Drupal Views listing (verified 2026-07-25,
964 bills in one page, no pagination). The load-bearing detail it encodes:

    PRS renders class="status-pending" on EVERY row regardless of the real
    status. Live, all 964 rows carry that class while the text spans Passed
    (552), Lapsed (168), Withdrawn (89), Draft, Pending, and more. The class is
    decoration; only the span's text is data.

No network.
"""

from __future__ import annotations

import json

from commoner_probe.prs import PrsProbe, parse_bill_track


def _row(slug: str, title: str, status: str) -> str:
    # Note the class is always status-pending, exactly as the live site emits it.
    return f"""
<div class="views-row">
<div class="views-field views-field-title-field"> <span class="field-content">
<h3 class="cate"><a
href="/billtrack/{slug}">{title}</a>
</h3>
</span> </div>
<div class="views-field views-field-field-bill-status"> <span
class="status-pending">{status}</span>
</div>
</div>
"""


LISTING = "<div id='parliament_view' class='view-content'>" + "".join([
    _row("the-finance-bill-2026", "The Finance Bill, 2026", "Passed"),
    _row("the-forest-conservation-amendment-bill-2023", "The Forest (Conservation) Amendment Bill, 2023", "Lapsed"),
    _row("the-inland-vessels-bill-2021", "The Inland Vessels Bill, 2021", "Withdrawn"),
    _row("the-unnamed-bill-2026", "The Unnamed Bill, 2026", ""),
]) + "</div>"


class FakeResponse:
    def __init__(self, text: str):
        self.text = text
        self.content = text.encode("utf-8")

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self, html: str = LISTING):
        self.html = html
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if url.endswith("/billtrack"):
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


class TestParseBillTrack:
    def test_status_comes_from_text_not_the_class(self):
        """The whole point: every row's class is status-pending."""
        bills = parse_bill_track(LISTING)
        assert [b["status"] for b in bills] == ["Passed", "Lapsed", "Withdrawn", ""]

    def test_title_url_and_slug(self):
        first = parse_bill_track(LISTING)[0]
        assert first["title"] == "The Finance Bill, 2026"
        assert first["url"] == "https://prsindia.org/billtrack/the-finance-bill-2026"
        assert first["slug"] == "the-finance-bill-2026"

    def test_a_bill_with_no_status_is_kept(self):
        """An empty status is missing data, not a missing bill."""
        slugs = [b["slug"] for b in parse_bill_track(LISTING)]
        assert "the-unnamed-bill-2026" in slugs

    def test_empty_page_yields_nothing_rather_than_raising(self):
        assert parse_bill_track("<html><body>no rows here</body></html>") == []


class TestProbeBillTrack:
    def test_writes_one_record_per_bill(self, tmp_path):
        probe = _probe(tmp_path)
        records = probe.probe_billtrack()
        assert len(records) == 4
        assert probe.session.calls == ["https://prsindia.org/billtrack"], "one request, no pagination"
        rows = _manifest(tmp_path)
        assert [r["kind"] for r in rows] == ["prs_bill_track"] * 4
        assert rows[0]["key"] == "PRS_BILL_TRACK|the-finance-bill-2026"
        assert rows[0]["bill_status"] == "Passed"
        assert rows[0]["source"] == "prsindia.org"

    def test_rerun_with_no_change_appends_nothing(self, tmp_path):
        probe = _probe(tmp_path)
        probe.probe_billtrack()
        again = _probe(tmp_path).probe_billtrack()
        assert again == []
        assert len(_manifest(tmp_path)) == 4

    def test_rerun_appends_only_the_bill_that_moved(self, tmp_path):
        """Bill Track is a tracker: Pending -> Passed must be recorded."""
        _probe(tmp_path).probe_billtrack()
        moved = LISTING.replace(
            '<span\nclass="status-pending">Lapsed</span>',
            '<span\nclass="status-pending">Passed</span>',
        )
        records = _probe(tmp_path, FakeSession(moved)).probe_billtrack()
        assert len(records) == 1
        assert records[0]["slug"] == "the-forest-conservation-amendment-bill-2023"
        assert records[0]["bill_status"] == "Passed"
        assert len(_manifest(tmp_path)) == 5, "status change appends, it does not overwrite"

    def test_dry_run_writes_no_manifest(self, tmp_path):
        records = _probe(tmp_path).probe_billtrack(dry_run=True)
        assert len(records) == 4
        assert all(r["status"] == "dry_run" for r in records)
        assert not (tmp_path / "manifest.jsonl").exists()

    def test_max_records_brake(self, tmp_path):
        records = _probe(tmp_path).probe_billtrack(max_records=2)
        assert len(records) == 2
        assert len(_manifest(tmp_path)) == 2


def test_schema_bundled_and_validates(tmp_path):
    import pytest

    pytest.importorskip("jsonschema")
    from commoner_probe import schemas
    from commoner_probe.validate import validate_corpus

    assert "manifest_prs_bill_track" in schemas.list_all()
    assert _probe(tmp_path).probe_billtrack()
    assert validate_corpus(tmp_path, log=lambda _: None)


def test_corpus_streams_prs_bill_track(tmp_path):
    from commoner_probe.corpus import Corpus

    _probe(tmp_path).probe_billtrack()
    rows = list(Corpus(tmp_path).manifest_prs_bill_track())
    assert len(rows) == 4
    assert rows[0].slug == "the-finance-bill-2026"
    assert rows[0].bill_status == "Passed"
