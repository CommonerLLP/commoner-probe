"""Tests for the ORGI / Census of India adapter.

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

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from commoner_probe import ogd_resource_api
from commoner_probe.ogd_resource_api import CensusApiError, CensusProbe, parse_title

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

    def test_is_sample_key_compares_a_digest_not_a_prefix(self, monkeypatch):
        """Exercise the predicate itself, which every other test stubs out.

        The two tests below monkeypatch `_is_sample_key` to a constant, so
        they prove the CALLER refuses a sample key without ever proving the
        predicate can recognise one. That is the shape of check this package
        keeps having to fix.

        This pins the mechanism: the exact key hashes True, a different key
        hashes False, and — the reason the digest exists at all — a key that
        merely shares the `579b464db66e` prefix every data.gov.in key starts
        with is NOT treated as the sample. A prefix test here would refuse the
        org's registered credential, which is how the first cut of this module
        failed.

        NOT pinned, because pinning it needs the network: whether the shipped
        SAMPLE_KEY_SHA256 is the digest of the key data.gov.in publishes. It
        was verified out of band on 2026-08-04 — see the note on the constant
        — and an assertion here would pass whatever the constant held.
        """
        some_key = "579b464db66eDEADBEEF" + "0" * 38
        monkeypatch.setattr(
            ogd_resource_api,
            "SAMPLE_KEY_SHA256",
            hashlib.sha256(some_key.encode("utf-8")).hexdigest(),
        )
        assert ogd_resource_api._is_sample_key(some_key) is True
        assert ogd_resource_api._is_sample_key("579b464db66eSOMETHINGELSE" + "0" * 33) is False
        assert ogd_resource_api._is_sample_key("579b464db66e") is False

    def test_the_sample_key_is_refused_for_a_real_run(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ogd_resource_api, "_is_sample_key", lambda key: True)
        probe = _probe(tmp_path, _listing("Primary Census Abstract 2011 - Goa"))
        with pytest.raises(CensusApiError, match="sample key"):
            probe.probe("pca")

    def test_the_sample_key_still_allows_a_dry_run(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ogd_resource_api, "_is_sample_key", lambda key: True)
        probe = _probe(tmp_path, _listing("Primary Census Abstract 2011 - Goa"))
        assert probe.probe("pca", dry_run=True)[0]["status"] == "dry_run"


class TestKeyResolution:
    def test_the_error_says_how_to_get_and_supply_a_key(self, monkeypatch):
        monkeypatch.delenv(ogd_resource_api.KEY_ENV, raising=False)
        monkeypatch.delenv(ogd_resource_api.KEY_FILE_ENV, raising=False)
        with pytest.raises(CensusApiError) as exc:
            ogd_resource_api.resolve_api_key()
        message = str(exc.value)
        assert ogd_resource_api.KEY_ENV in message
        assert ogd_resource_api.KEY_FILE_ENV in message
        assert "data.gov.in/apis" in message

    def test_the_environment_is_preferred(self, monkeypatch):
        monkeypatch.setenv(ogd_resource_api.KEY_ENV, "from-env")
        assert ogd_resource_api.resolve_api_key() == "from-env"

    def test_no_credential_is_read_from_outside_the_package(self, monkeypatch):
        """A key file planted in an ancestor directory must NOT be read.

        `resolve_api_key` used to walk every parent of `__file__` looking for a
        fixed relative path. Installed into site-packages that walk covers
        site-packages, python3.x, /usr/lib, /usr and / — the package read and
        parsed arbitrary files outside its own tree while hunting a credential.
        This plants exactly such a file and requires it to be ignored.
        """
        monkeypatch.delenv(ogd_resource_api.KEY_ENV, raising=False)
        monkeypatch.delenv(ogd_resource_api.KEY_FILE_ENV, raising=False)
        planted = Path(ogd_resource_api.__file__).resolve().parent.parent / "sevent4" / ".secrets"
        planted.mkdir(parents=True, exist_ok=True)
        (planted / "keys.env").write_text(f"{ogd_resource_api.KEY_ENV}=leaked-by-ancestor-walk\n", encoding="utf-8")
        try:
            with pytest.raises(CensusApiError):
                ogd_resource_api.resolve_api_key()
        finally:
            shutil.rmtree(planted.parent, ignore_errors=True)

    def test_a_key_file_is_read_only_when_the_operator_names_one(self, tmp_path, monkeypatch):
        monkeypatch.delenv(ogd_resource_api.KEY_ENV, raising=False)
        keyfile = tmp_path / "keys.env"
        keyfile.write_text(f'{ogd_resource_api.KEY_ENV}="abc123"\n', encoding="utf-8")
        monkeypatch.setenv(ogd_resource_api.KEY_FILE_ENV, str(keyfile))
        assert ogd_resource_api.resolve_api_key() == "abc123"

    def test_a_named_key_file_that_is_absent_does_not_crash(self, tmp_path, monkeypatch):
        monkeypatch.delenv(ogd_resource_api.KEY_ENV, raising=False)
        monkeypatch.setenv(ogd_resource_api.KEY_FILE_ENV, str(tmp_path / "nope.env"))
        with pytest.raises(CensusApiError):
            ogd_resource_api.resolve_api_key()

    def test_explicit_beats_everything(self, monkeypatch):
        monkeypatch.setenv(ogd_resource_api.KEY_ENV, "from-env")
        assert ogd_resource_api.resolve_api_key("explicit") == "explicit"


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
    """The request asked for ALL libraries. The urban count is not on the OGD API,
    so the constraint has to travel with the module rather than live in a
    session note that the next reader never sees."""
    assert "DCHB" in ogd_resource_api.URBAN_LIBRARY_COUNT_UNAVAILABLE
    assert "Never sum" in ogd_resource_api.URBAN_LIBRARY_COUNT_UNAVAILABLE


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


def test_every_manifest_kind_this_package_emits_is_registered_with_validate():
    """The generalised form of the census finding.

    An unregistered kind makes `validate` abstain and print "ok". This walks the
    kinds actually emitted into manifest.jsonl and asserts each resolves to a
    schema, so the next adapter cannot ship the same hole. Kinds validated by
    their own per-file path (answers.jsonl, vacancy_rows.jsonl, ...) are covered
    elsewhere and are not manifest kinds.
    """
    import re
    from pathlib import Path

    from commoner_probe.validate import _pick_schema_name

    manifest_kinds = set()
    for src in Path("commoner_probe").rglob("*.py"):
        text = src.read_text(encoding="utf-8")
        if "append_manifest" not in text and "manifest.jsonl" not in text:
            continue
        for m in re.finditer(r'"kind":\s*"([a-z0-9_]+)"', text):
            manifest_kinds.add(m.group(1))
        # A kind held in a module constant escaped the literal search above, so
        # `wayback_recovery` shipped unregistered and this guard passed. A check
        # that only sees one spelling of the thing it guards is not a guard.
        for m in re.finditer(r'^MANIFEST_KIND\s*=\s*"([a-z0-9_]+)"', text, re.MULTILINE):
            manifest_kinds.add(m.group(1))

    # Kinds that live in their own artefact file, not in manifest.jsonl.
    per_file = {
        "qa_response", "atr_response", "dfg_recommendation", "neva_qa_response",
        "neva_district_row", "outsourcing_signal", "question_list_row", "vacancy_row",
        "dchb_town_amenity",
    }
    unregistered = sorted(
        k for k in manifest_kinds - per_file if _pick_schema_name({"kind": k}) is None
    )
    assert not unregistered, f"validate will silently skip these manifest kinds: {unregistered}"
