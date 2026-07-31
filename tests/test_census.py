"""Tests for the ORGI / Census of India adapter (REQ-0045).

Fixtures mirror the live OGD API, contract measured 2026-07-30:

    /lists returns {total, count, records:[{index_name, title, ...}]} and its
    `filters[title]` match is LOOSE — `town amenities` returns "Digital Payments
    Data: New Town Kolkata" among real hits, so every record must be re-checked
    against the surface's own pattern.

    /resource/<id> returns {total, records:[...]} and paginates on offset.

    Catalogue titles carry two formats: "Primary Census Abstract, 2001 - Delhi"
    and "Primary Census Abstract 2011 - Rajasthan".

No network.
"""

from __future__ import annotations

import json

import pytest

from commoner_probe import census
from commoner_probe.census import CensusApiError, CensusProbe, parse_title

REGISTERED_KEY = "579b464db66ec23bdd0000010000000000000000000000000000000000"


class FakeResponse:
    def __init__(self, payload):
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def raise_for_status(self):
        pass


class FakeSession:
    """Serves scripted payloads and records the params it was handed."""

    def __init__(self, *payloads):
        self._payloads = list(payloads)
        self.calls: list[dict] = []

    def get(self, url, params=None, timeout=None, **kwargs):
        self.calls.append({"url": url, **(params or {})})
        payload = self._payloads[min(len(self.calls) - 1, len(self._payloads) - 1)]
        if isinstance(payload, Exception):
            raise payload
        return FakeResponse(payload)


def _listing(*titles, total=None):
    return {
        "total": total if total is not None else len(titles),
        "records": [{"index_name": f"rid-{i}", "title": t} for i, t in enumerate(titles)],
    }


def _rows(n, total=None, start=0):
    return {
        "total": total if total is not None else n,
        "records": [{"state_code": "08", "village_code": f"{start + i:06d}"} for i in range(n)],
    }


def _probe(tmp_path, *payloads, **kw):
    return CensusProbe(tmp_path, sleep=0, session=FakeSession(*payloads),
                       api_key=kw.pop("api_key", REGISTERED_KEY), **kw)


class TestTitleParsing:
    """All four formats are real, taken from the live catalogue."""

    @pytest.mark.parametrize(
        "title,year,state,district",
        [
            ("Primary Census Abstract, 2001 - Delhi", "2001", "Delhi", None),
            ("Primary Census Abstract 2011 - Rajasthan", "2011", "Rajasthan", None),
            (
                "Village Amenities for Hoshangabad District of Madhya Pradesh, 2011",
                "2011", "Madhya Pradesh", "Hoshangabad",
            ),
            (
                "Complete Town Directory by India/State/District/Sub-District Level, "
                "Census 2011 - GUJARAT",
                "2011", "GUJARAT", None,
            ),
        ],
    )
    def test_the_two_title_formats_both_parse(self, title, year, state, district):
        got = parse_title(title)
        assert got["census_year"] == year
        assert got["state_name"] == state
        assert got["district_name"] == district

    def test_an_unparseable_year_is_null_not_a_guess(self):
        assert parse_title("Village Amenities for Nowhere District")["census_year"] is None


class TestTheKeyNeverReachesAnArtefact:
    """`api-key` rides in the query string, so the request URL is a credential."""

    def test_the_manifest_url_carries_no_key(self, tmp_path):
        probe = _probe(
            tmp_path,
            _listing("Village Amenities for Sagar District of Madhya Pradesh, 2011"),
            _rows(2),
        )
        rec = probe.probe("village-amenities")[0]
        assert "api-key" not in rec["url"]
        assert REGISTERED_KEY not in json.dumps(rec)

    def test_an_api_failure_message_carries_no_key(self, tmp_path):
        probe = _probe(tmp_path, RuntimeError("HTTP 500"))
        with pytest.raises(CensusApiError) as excinfo:
            probe.discover("pca")
        assert REGISTERED_KEY not in str(excinfo.value)
        assert "api-key" not in str(excinfo.value)


class TestSampleKeyGuard:
    """A shared, rate-limited credential must not be used for a corpus pass.

    The guard compares a DIGEST. Every data.gov.in key begins `579b464db66e`,
    registered ones included, so the first cut of this guard used a prefix and
    refused the org's own registered key.
    """

    def test_a_registered_key_is_not_mistaken_for_the_sample(self, tmp_path):
        probe = _probe(
            tmp_path,
            _listing("Primary Census Abstract 2011 - Goa"),
            _rows(1),
            api_key=REGISTERED_KEY,
        )
        assert probe.probe("pca")[0]["status"] == "downloaded"

    def test_the_sample_key_is_refused_for_a_real_run(self, tmp_path, monkeypatch):
        monkeypatch.setattr(census, "_is_sample_key", lambda key: True)
        probe = _probe(tmp_path, _listing("Primary Census Abstract 2011 - Goa"))
        with pytest.raises(CensusApiError, match="sample key"):
            probe.probe("pca")

    def test_the_sample_key_still_allows_a_dry_run(self, tmp_path, monkeypatch):
        monkeypatch.setattr(census, "_is_sample_key", lambda key: True)
        probe = _probe(tmp_path, _listing("Primary Census Abstract 2011 - Goa"))
        assert probe.probe("pca", dry_run=True)[0]["status"] == "dry_run"


class TestKeyResolution:
    def test_the_error_names_where_the_key_lives(self, tmp_path, monkeypatch):
        monkeypatch.delenv(census.KEY_ENV, raising=False)
        monkeypatch.setattr(census.Path, "exists", lambda self: False)
        with pytest.raises(CensusApiError, match=census.KEY_HINT):
            census.resolve_api_key()

    def test_the_environment_is_preferred(self, monkeypatch):
        monkeypatch.setenv(census.KEY_ENV, "from-env")
        assert census.resolve_api_key() == "from-env"


class TestDiscovery:
    def test_loosely_matched_titles_are_rejected(self, tmp_path):
        """`filters[title]=town amenities` really does return Kolkata street lights."""
        probe = _probe(
            tmp_path,
            _listing(
                "Town Amenities for Sagar District of Madhya Pradesh, 2011",
                "Digital Payments Data: New Town Kolkata_2017-19",
                "Street Lights Data:New Town Kolkata_2018-19",
            ),
        )
        found = probe.discover("town-amenities")
        assert [f["district_name"] for f in found] == ["Sagar"]

    def test_the_year_filter_selects_a_vintage(self, tmp_path):
        probe = _probe(
            tmp_path,
            _listing(
                "Primary Census Abstract, 2001 - Delhi",
                "Primary Census Abstract 2011 - Delhi",
            ),
        )
        assert [f["census_year"] for f in probe.discover("pca", year="2011")] == ["2011"]

    def test_an_unknown_surface_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="unknown surface"):
            _probe(tmp_path).probe("district-handbook")


class TestRowsAndFailureModes:
    def test_rows_paginate(self, tmp_path):
        probe = _probe(tmp_path, _rows(2, total=4), _rows(2, total=4, start=2), _rows(0, total=4))
        assert len(list(probe.fetch_rows("rid-0", page=2))) == 4

    def test_a_resource_with_zero_rows_raises_rather_than_writing_nothing(self, tmp_path):
        """Silent success is this repo's signature defect; an empty Census
        resource is a withdrawn dataset, not an empty result."""
        probe = _probe(
            tmp_path,
            _listing("Primary Census Abstract 2011 - Goa"),
            _rows(0, total=0),
        )
        with pytest.raises(CensusApiError, match="zero rows"):
            probe.probe("pca")

    def test_a_non_json_body_is_an_outage_not_an_empty_result(self, tmp_path):
        probe = _probe(tmp_path, "<html>503 Service Unavailable</html>")
        with pytest.raises(CensusApiError, match="NOT an empty result"):
            probe.discover("pca")

    def test_a_dry_run_writes_no_manifest_and_no_rows(self, tmp_path):
        probe = _probe(tmp_path, _listing("Primary Census Abstract 2011 - Goa"))
        recs = probe.probe("pca", dry_run=True)

        assert recs[0]["status"] == "dry_run"
        assert not (tmp_path / "manifest.jsonl").exists()
        assert not (tmp_path / "rows").exists()

    def test_a_downloaded_resource_records_its_rows_and_digest(self, tmp_path):
        probe = _probe(
            tmp_path,
            _listing("Village Amenities for Sagar District of Madhya Pradesh, 2011"),
            _rows(3),
        )
        rec = probe.probe("village-amenities")[0]

        assert rec["rows"] == 3
        assert len(rec["sha256"]) == 64
        assert rec["level"] == "district"
        written = (tmp_path / rec["dest"]).read_text().strip().splitlines()
        assert len(written) == 3
        assert json.loads(written[0])["state_code"] == "08"

    def test_an_already_downloaded_resource_is_not_refetched(self, tmp_path):
        listing = _listing("Village Amenities for Sagar District of Madhya Pradesh, 2011")
        first = _probe(tmp_path, listing, _rows(2))
        assert len(first.probe("village-amenities")) == 1
        again = _probe(tmp_path, listing, _rows(2))
        assert again.probe("village-amenities") == []


def test_the_urban_library_gap_is_recorded_in_code():
    """REQ-0045 asked for ALL libraries. The urban count is not on the OGD API,
    so the constraint has to travel with the module rather than live in a
    session note that the next reader never sees."""
    assert "DCHB" in census.URBAN_LIBRARY_COUNT_UNAVAILABLE
    assert "Never sum" in census.URBAN_LIBRARY_COUNT_UNAVAILABLE


class TestCodexWave91:
    def test_a_row_limited_pull_is_not_marked_complete(self, tmp_path):
        """`--max-rows` is a smoke test. Marking it `downloaded` made
        load_seen() skip the resource forever, so a later unrestricted run left
        the subset in place and the corpus looked complete (Codex, PR #91)."""
        probe = _probe(tmp_path, _listing("Primary Census Abstract 2011 - Goa"), _rows(2, total=99))
        rec = probe.probe("pca", max_rows=2)[0]

        assert rec["status"] == "partial"
        again = _probe(tmp_path, _listing("Primary Census Abstract 2011 - Goa"), _rows(2, total=99))
        assert rec["key"] not in again.load_seen(), "a partial pull must stay refetchable"

    def test_an_unlimited_pull_is_still_terminal(self, tmp_path):
        probe = _probe(tmp_path, _listing("Primary Census Abstract 2011 - Goa"), _rows(2, total=2))
        rec = probe.probe("pca")[0]
        assert rec["status"] == "downloaded"

    def test_the_credential_is_kept_out_of_the_http_cache(self, tmp_path):
        """requests-cache persists the prepared URL, and the OGD contract puts
        the key in it. Suppression is DETECTED, not passed as a kwarg —
        `expire_after=0` reaches requests.Session.request() on a non-caching
        install and raises TypeError, which the first cut of this fix did."""
        used = []

        class CachingSession(FakeSession):
            def cache_disabled(self):
                from contextlib import contextmanager

                @contextmanager
                def _cm():
                    used.append(True)
                    yield

                return _cm()

        probe = CensusProbe(
            tmp_path, sleep=0,
            session=CachingSession(_listing("Primary Census Abstract 2011 - Goa"), _rows(1)),
            api_key=REGISTERED_KEY,
        )
        probe.probe("pca")
        assert used, "caching must be suppressed for credential-bearing requests"

    def test_a_plain_session_without_cache_support_still_works(self, tmp_path):
        """The regression the kwarg approach caused: a non-caching session."""
        probe = _probe(tmp_path, _listing("Primary Census Abstract 2011 - Goa"), _rows(1))
        assert probe.probe("pca")[0]["status"] == "downloaded"


def test_the_new_kinds_are_registered_with_validate():
    """An unregistered kind makes `validate` silently skip the records — it
    reported a deliberately corrupted census manifest as "ok" (Codex, PR #91)."""
    from commoner_probe.validate import _pick_schema_name

    assert _pick_schema_name({"kind": "orgi_census_resource"}) == "manifest_orgi_census"
    assert _pick_schema_name({"kind": "niti_annual_report"}) == "manifest_niti_annual_report"
