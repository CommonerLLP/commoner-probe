"""Tests for the Abhilekh Patal (National Archives of India) catalogue adapter.

Fixture markup mirrors the live cards (verified 2026-07-28 from
ap-south-1: a `police` query reports 59,414 records across 5,942 pages, ten
per page).

Three live findings the fixtures deliberately encode:

    `?Page.Number=N` on the search URL is IGNORED and silently returns page 0,
    which is why pagination goes through `/Category/Search/PaginationScroll`
    with lower-cased keys and a JSON `partialView` body. A crawler that trusted
    the query parameter would re-record the first ten records forever.

    The WAF answers non-India egress — and every commoner-probe User-Agent —
    with HTTP 202 and a Human Verification page. That must raise, never be
    read as "the archive has nothing".

    The documents are not downloadable, so `status` never leaves
    `metadata_only`.

No network.
"""

from __future__ import annotations

import json

import pytest

from commoner_probe.catalogue_search_api import (
    AbhilekhPatalProbe,
    ChallengeBlocked,
    is_challenge,
    parse_cards,
    parse_totals,
)

CHALLENGE_BODY = (
    '<html><head><title>Human Verification</title></head>'
    '<body>x-amzn-waf-action: challenge</body></html>'
)


def _card(item_id: str, title: str, *, identifier="MF_222400148993", year="1946",
          pages="8", language="English", keywords="NA", extra_label=None) -> str:
    extra = ""
    if extra_label:
        extra = (
            f'<li class="viewgrid pb-3"><span class="pleft">{extra_label[0]}:</span> {extra_label[1]}</li>'
        )
    return f"""
<div class="row pb-3">
  <div class="grid-view-result-div">
    <div class="main-card-view-cont">
      <div class="thumb-right-gap">
        <a href="/Category/ItemDetails/ItemDetails?itemId={item_id}">
          <img src="data:image/jpeg;base64,AAAA" alt="{title}" />
        </a>
      </div>
      <div class="card-view-right-content"><div class="card-body"><div class="card-content-1">
        <figcaption><ul class="fsize p-0">
          <li class="headingview pb-4">
            <a href="/Category/ItemDetails/ItemDetails?itemId={item_id}">
              <span class=""></span> {title}
            </a>
          </li>
          <li class="viewgrid pb-3"><span class="pleft">Identifier:</span> {identifier}</li>
          <li class="viewgrid pb-3"><span class="pleft">Year:</span> {year}</li>
          <li class="viewgrid pb-3"><span class="pleft">No of Pages:</span> {pages}</li>
          <li class="viewgrid pb-3"><span class="pleft">Language:</span> {language}</li>
          {extra}
          <li class="viewgrid pb-4"><span class="pleft">Keywords: </span> {keywords}</li>
        </ul></figcaption>
      </div></div></div>
    </div>
  </div>
</div>
"""


def _search_page(cards: str, *, total=59414, pages=5942) -> str:
    return f"""
<html><body><div id="search_dashboard">
<input type="hidden" id="Number" name="Page.Number" value="0" />
<input type="hidden" id="TotalElements" name="Page.TotalElements" value="{total}" />
<input type="hidden" id="TotalPages" name="Page.TotalPages" value="{pages}" />
<input type="hidden" id="Size" name="Page.Size" value="10" />
</div>{cards}</body></html>
"""


PAGE0 = _search_page(
    _card("1d02301b-3fe4-4e70-a448-4ec5620195aa", "Cooperative Societies 1956 Policy",
          identifier="NAILSF00316528", year="1956", pages="90", keywords="Bombay")
    + _card("63789ab8-f6e3-43a2-a0d4-993520fb5e35", "Measures taken to put a stop to the attacks",
            year="1921", pages="22"),
    total=25, pages=3,
)
PAGE1_CARDS = (
    _card("adcd077d-08ae-47d0-9ee6-a7a8e250dd21", "Grant of financial assistance to evacuee students")
    + _card("040bbe40-ac78-4045-b689-68b25a0d7486", "Regarding the General Provident Fund",
            year="", keywords="NA", extra_label=("Department", "Home"))
)
PAGE2_CARDS = _card("dc306594-1111-2222-3333-444455556666", "Sikar Affairs")


class FakeResponse:
    def __init__(self, body: str, status: int = 200):
        self.text = body
        self.content = body.encode("utf-8")
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Routes the seed search page and the PaginationScroll endpoint."""

    def __init__(self, *, challenge: bool = False):
        self.challenge = challenge
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if self.challenge:
            return FakeResponse(CHALLENGE_BODY, status=202)
        if "PaginationScroll" in url:
            number = int(url.split("number=")[1].split("&")[0])
            cards = {1: PAGE1_CARDS, 2: PAGE2_CARDS}.get(number, "")
            return FakeResponse(json.dumps({"partialView": cards}))
        if "QuerySearch" in url:
            return FakeResponse(PAGE0)
        raise AssertionError(f"unrouted url: {url}")


def _probe(tmp_path, session=None, **kw):
    probe = AbhilekhPatalProbe(tmp_path, sleep=0, **kw)
    probe.session = session or FakeSession()
    return probe


def _manifest(tmp_path):
    path = tmp_path / "manifest.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class TestParsing:
    def test_totals_come_from_the_hidden_fields(self):
        assert parse_totals(PAGE0) == (25, 3)

    def test_missing_totals_are_none_not_zero(self):
        """Zero would read as 'the archive is empty'. It is not the same fact."""
        assert parse_totals("<html></html>") == (None, None)

    def test_card_fields(self):
        first = parse_cards(PAGE0)[0]
        assert first["item_id"] == "1d02301b-3fe4-4e70-a448-4ec5620195aa"
        assert first["title"] == "Cooperative Societies 1956 Policy"
        assert first["identifier"] == "NAILSF00316528"
        assert first["year"] == 1956
        assert first["page_count"] == 90
        assert first["language"] == "English"
        assert first["keywords"] == "Bombay"

    def test_na_keywords_become_none_not_the_string_na(self):
        assert parse_cards(PAGE0)[1]["keywords"] is None

    def test_an_undated_record_is_kept_with_year_none(self):
        """Archival material is frequently undated. That is not a bad row."""
        item = parse_cards(PAGE1_CARDS)[1]
        assert item["year"] is None
        assert item["title"]

    def test_unmapped_labels_are_preserved_in_extra(self):
        assert parse_cards(PAGE1_CARDS)[1]["extra"] == {"department": "Home"}

    def test_the_scroll_fragment_uses_the_same_markup(self):
        assert len(parse_cards(PAGE1_CARDS)) == 2

    def test_empty_html_yields_nothing_rather_than_raising(self):
        assert parse_cards("") == []
        assert parse_cards("<html><body>nothing</body></html>") == []


class TestChallengeDetection:
    def test_202_is_a_challenge(self):
        assert is_challenge(202, b"") is True

    def test_waf_marker_in_a_200_body_is_a_challenge(self):
        assert is_challenge(200, CHALLENGE_BODY) is True

    def test_a_real_page_is_not_a_challenge(self):
        assert is_challenge(200, PAGE0) is False

    def test_probe_raises_rather_than_recording_an_empty_corpus(self, tmp_path):
        probe = _probe(tmp_path, FakeSession(challenge=True))
        with pytest.raises(ChallengeBlocked, match="India-region egress"):
            list(probe.probe(query="police"))
        assert not (tmp_path / "manifest.jsonl").exists()

    def test_the_error_names_the_user_agent_cause_too(self, tmp_path):
        probe = _probe(tmp_path, FakeSession(challenge=True))
        with pytest.raises(ChallengeBlocked, match="User-Agent"):
            list(probe.probe(query="police"))


class TestProbe:
    def test_walks_every_page_and_writes_one_record_each(self, tmp_path):
        probe = _probe(tmp_path)
        records = list(probe.probe(query="police"))
        assert len(records) == 5, "2 on the seed page + 2 + 1 from the scroll endpoint"
        rows = _manifest(tmp_path)
        assert [r["kind"] for r in rows] == ["nai_catalogue_record"] * 5
        assert rows[0]["key"] == "NAI|1d02301b-3fe4-4e70-a448-4ec5620195aa"
        assert rows[0]["search_query"] == "police"
        assert rows[0]["url"].endswith("itemId=1d02301b-3fe4-4e70-a448-4ec5620195aa")

    def test_pagination_uses_the_scroll_endpoint_not_a_page_query_param(self, tmp_path):
        """?Page.Number is ignored by the site; trusting it loops on page 0."""
        probe = _probe(tmp_path)
        list(probe.probe(query="police"))
        scrolls = [c for c in probe.session.calls if "PaginationScroll" in c]
        assert len(scrolls) == 2
        assert "number=1" in scrolls[0] and "number=2" in scrolls[1]
        assert not any("Page.Number" in c for c in probe.session.calls)

    def test_status_is_always_metadata_only(self, tmp_path):
        """The scans sit behind a paid ordering flow. Nothing is downloaded."""
        records = list(_probe(tmp_path).probe(query="police"))
        assert {r["status"] for r in records} == {"metadata_only"}
        assert all("dest" not in r and "sha256" not in r for r in records)

    def test_records_stamp_the_identity_used(self, tmp_path):
        probe = _probe(tmp_path, user_agent="some-explicit-agent/1.0")
        record = next(probe.probe(query="police"))
        assert record["user_agent"] == "some-explicit-agent/1.0"

    def test_the_typed_record_keeps_the_identity_too(self, tmp_path):
        """`_from_dict` filters to declared fields — an undeclared one vanishes.

        The schema marks user_agent required and it is the audit trail for which
        identity was presented to the WAF. A provenance field that survives into
        the manifest but not into the typed API is not provenance.
        """
        from commoner_probe.corpus import Corpus

        probe = _probe(tmp_path, user_agent="some-explicit-agent/1.0")
        list(probe.probe(query="police"))
        rows = list(Corpus(tmp_path).manifest_nai_catalogue())
        assert rows[0].user_agent == "some-explicit-agent/1.0"

    def test_rerun_appends_nothing(self, tmp_path):
        list(_probe(tmp_path).probe(query="police"))
        again = list(_probe(tmp_path).probe(query="police"))
        assert again == []
        assert len(_manifest(tmp_path)) == 5

    def test_max_records_brake(self, tmp_path):
        records = list(_probe(tmp_path).probe(query="police", max_records=3))
        assert len(records) == 3
        assert len(_manifest(tmp_path)) == 3

    def test_max_pages_brake(self, tmp_path):
        records = list(_probe(tmp_path).probe(query="police", max_pages=1))
        assert len(records) == 2, "seed page only"

    def test_dry_run_writes_nothing(self, tmp_path):
        records = list(_probe(tmp_path).probe(query="police", dry_run=True))
        assert len(records) == 5
        assert all(r["status"] == "dry_run" for r in records)
        assert not (tmp_path / "manifest.jsonl").exists()

    def test_markup_change_that_removes_the_totals_is_a_loud_failure(self, tmp_path):
        class NoTotals(FakeSession):
            def get(self, url, **kwargs):
                self.calls.append(url)
                return FakeResponse("<html><body>no hidden fields</body></html>")

        probe = _probe(tmp_path, NoTotals())
        with pytest.raises(RuntimeError, match="pagination contract"):
            list(probe.probe(query="police"))


def test_schema_bundled_and_validates(tmp_path):
    pytest.importorskip("jsonschema")
    from commoner_probe import schemas
    from commoner_probe.validate import validate_corpus

    assert "manifest_abhilekh_patal" in schemas.list_all()
    assert list(_probe(tmp_path).probe(query="police"))
    assert validate_corpus(tmp_path, log=lambda _: None)


def test_corpus_streams_nai_catalogue(tmp_path):
    from commoner_probe.corpus import Corpus

    list(_probe(tmp_path).probe(query="police"))
    rows = list(Corpus(tmp_path).manifest_nai_catalogue())
    assert len(rows) == 5
    assert rows[0].identifier == "NAILSF00316528"
    assert rows[0].status == "metadata_only"
