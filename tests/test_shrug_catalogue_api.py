# SPDX-License-Identifier: MIT
"""Offline tests for the SHRUG / Devdatalab catalogue client.

No network. The live grammar these fixtures copy was measured on 2026-08-14
against www.devdatalab.org: the catalogue endpoint returned 52 rows, each
download field held an HTML anchor, and a ranged GET reported 79,180,800 bytes
for a file whose HEAD answered 403.
"""
from __future__ import annotations

import json

import pytest

from commoner_probe import shrug_catalogue_api as shrug


class _Resp:
    def __init__(self, body: bytes = b"", status: int = 200, headers: dict | None = None):
        self._body = body
        self.status_code = status
        self.headers = {} if headers is None else headers
        self.text = body.decode("utf-8", errors="replace")

    @property
    def content(self) -> bytes:
        return self._body

    def iter_content(self, chunk_size: int = 16384):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i : i + chunk_size]

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _NoHeaderResp(_Resp):
    """The stdlib fallback response, which exposes no headers at all."""

    def __init__(self, body: bytes = b"", status: int = 200):
        super().__init__(body, status)
        del self.headers


class _Session:
    """A session that answers from a table of URL prefixes."""

    def __init__(self, answers: dict):
        self.answers = answers
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        for prefix, resp in self.answers.items():
            if url.startswith(prefix):
                return resp(kwargs) if callable(resp) else resp
        raise AssertionError(f"unexpected URL {url}")


def _row(module, label, url, filetype="dta", secondary=""):
    anchor = f'<a href="{url}" class="btn">Download</a>' if url else ""
    return {
        "module_short_label": f"<b>{module}</b>",
        "table_short_label": label,
        "primary_filetype": filetype,
        "citation": "Asher et al. (2021)",
        "primary_download": anchor,
        "secondary_download": secondary,
    }


S3 = "https://shrug-assets-ddl.s3.amazonaws.com/static/"
VD11 = S3 + "pc11_vd.zip?X-Amz-Signature=deadbeef&X-Amz-Expires=3600"
VD01 = S3 + "pc01_vd.zip?X-Amz-Signature=cafe"
VD91 = S3 + "pc91_vd.zip?X-Amz-Signature=f00d"


def _catalogue_body(rows):
    return json.dumps(rows).encode("utf-8")


def _full_rows():
    return [
        _row("Census", "1991 Population Census Village Directory", VD91),
        _row("Census", "2001 Population Census Village Directory", VD01),
        _row("Census", "2011 Population Census Village Directory", VD11),
        _row("Census", "1991 Population Census Town Directory", S3 + "pc91_td.zip?X-Amz-Signature=1"),
        _row("Census", "2001 Population Census Town Directory", S3 + "pc01_td.zip?X-Amz-Signature=2"),
        _row("Census", "2011 Population Census Town Directory", S3 + "pc11_td.zip?X-Amz-Signature=3"),
        _row("Keys", "Shrug Location Names and Additional Keys", S3 + "keys.zip?X-Amz-Signature=4"),
        _row("Caste", "SECC Rural", S3 + "secc_rural.zip?X-Amz-Signature=5"),
        _row("Caste", "SECC Urban", S3 + "secc_urban.zip?X-Amz-Signature=6"),
    ]


def _catalogue_session(rows=None):
    body = _catalogue_body(_full_rows() if rows is None else rows)
    return _Session({shrug.CATALOGUE_URL: _Resp(body)})


# ------------------------------------------------------------------ catalogue

def test_catalogue_reads_the_href_and_not_the_cell_text():
    """The download fields hold HTML anchors, not bare URLs.

    Taking the cell text gives the word "Download". Scraping the rendered
    page gives nothing at all.
    """
    tables = shrug.catalogue(session=_catalogue_session(), min_rows=1)
    assert tables["2011 Population Census Village Directory"].url == VD11


def test_catalogue_asks_for_json_and_sends_the_referer():
    """The page renders an empty DataTable and fills it from this endpoint."""
    session = _catalogue_session()
    shrug.catalogue(session=session, min_rows=1)
    url, kwargs = session.calls[0]
    assert url == shrug.CATALOGUE_URL
    assert kwargs["headers"]["Accept"] == "application/json"
    assert kwargs["headers"]["Referer"] == shrug.DOWNLOAD_PAGE_URL


def test_catalogue_strips_html_from_the_module_label():
    tables = shrug.catalogue(session=_catalogue_session(), min_rows=1)
    assert tables["SECC Rural"].module_label == "Caste"


def test_catalogue_refuses_an_empty_array():
    """Zero rows means the endpoint shape changed, not that SHRUG is empty."""
    with pytest.raises(shrug.ShrugCatalogueError, match="no rows"):
        shrug.catalogue(session=_catalogue_session(rows=[]), min_rows=1)


def test_catalogue_names_the_waf_challenge_on_an_empty_bodied_2xx():
    """An empty 202 is the AWS WAF grammar, and it reads as success.

    Harvard Dataverse answers every scripted API request with HTTP 202,
    zero bytes and `x-amzn-waf-action: challenge`. Passing that body to
    json.loads throws a JSONDecodeError that sends the reader to doubt the DOI.
    """
    session = _Session({shrug.CATALOGUE_URL: _Resp(b"", status=202)})
    with pytest.raises(shrug.ShrugCatalogueError, match="challenge"):
        shrug.catalogue(session=session, min_rows=1)


def test_catalogue_reports_a_non_json_body_rather_than_zero_tables():
    session = _Session({shrug.CATALOGUE_URL: _Resp(b"<html>maintenance</html>")})
    with pytest.raises(shrug.ShrugCatalogueError, match="did not return JSON"):
        shrug.catalogue(session=session, min_rows=1)


def test_catalogue_refuses_two_rows_under_one_label():
    """Keying on the label would drop one table in silence."""
    rows = _full_rows() + [_row("Census", "SECC Rural", S3 + "other.zip?X-Amz-Signature=9")]
    with pytest.raises(shrug.ShrugCatalogueError, match="twice"):
        shrug.catalogue(session=_catalogue_session(rows=rows), min_rows=1)


def test_a_row_with_no_anchor_has_no_url():
    rows = [_row("Census", "SECC Rural", "")]
    tables = shrug.catalogue(session=_catalogue_session(rows=rows), min_rows=1)
    assert tables["SECC Rural"].url is None


# -------------------------------------------------------------------- presets

def test_preset_resolves_the_long_labels_by_pattern():
    tables = shrug.catalogue(session=_catalogue_session(), min_rows=1)
    got = [t.table_label for t in shrug.resolve_preset("census-directories", tables)]
    assert "2011 Population Census Village Directory" in got
    assert "1991 Population Census Town Directory" in got
    assert len(got) == 7


def test_unknown_preset_lists_the_known_ones():
    tables = shrug.catalogue(session=_catalogue_session(), min_rows=1)
    with pytest.raises(shrug.ShrugCatalogueError, match="census-directories"):
        shrug.resolve_preset("censuses", tables)


def test_preset_pattern_matching_nothing_refuses_the_whole_preset():
    """A partial preset is the silent failure this module exists to refuse.

    Returning the six tables that did match would hand the caller an
    incomplete panel that reads as complete.
    """
    rows = [r for r in _full_rows() if "Town Directory" not in r["table_short_label"]]
    with pytest.raises(shrug.ShrugCatalogueError, match="matched no table"):
        shrug.resolve_preset("census-directories", shrug.catalogue(session=_catalogue_session(rows), min_rows=1))


def test_preset_deduplicates_a_table_two_patterns_both_match():
    tables = shrug.catalogue(session=_catalogue_session(), min_rows=1)
    got = shrug.resolve_preset("caste", tables)
    assert len(got) == len({t.table_label for t in got})


# -------------------------------------------------------------------- size_of

def test_size_of_reads_content_range_because_head_returns_403():
    """A HEAD against a presigned link 403s on a URL that downloads fine.

    Measured 2026-08-14: `Range: bytes=0-0` answers 206 with
    `Content-Range: bytes 0-0/79180800`.
    """
    resp = _Resp(b"\x00", status=206, headers={"Content-Range": "bytes 0-0/79180800"})
    session = _Session({S3: resp})
    assert shrug.size_of(VD11, session=session) == 79180800
    assert session.calls[0][1]["headers"]["Range"] == "bytes=0-0"


def test_size_of_raises_when_there_is_no_content_range():
    """Returning 0 would read as an empty file and pass any size check."""
    session = _Session({S3: _Resp(b"\x00", status=200)})
    with pytest.raises(shrug.ShrugCatalogueError, match="Content-Range"):
        shrug.size_of(VD11, session=session)


def test_size_of_names_the_missing_headers_on_the_stdlib_session():
    """The zero-dependency response object carries no headers to read."""
    session = _Session({S3: _NoHeaderResp(b"\x00", status=206)})
    with pytest.raises(shrug.ShrugCatalogueError, match="commoner-probe\\[http\\]"):
        shrug.size_of(VD11, session=session)


# ---------------------------------------------------------------- fetch_preset

def _fetch_session(payload=b"shrug bytes"):
    body = _catalogue_body(_full_rows())
    return _Session({
        shrug.CATALOGUE_URL: _Resp(body),
        S3: lambda kwargs: (
            _Resp(b"\x00", status=206, headers={"Content-Range": f"bytes 0-0/{len(payload)}"})
            if (kwargs.get("headers") or {}).get("Range")
            else _Resp(payload)
        ),
    })


def test_fetch_preset_writes_the_file_and_a_sha256_manifest_row(tmp_path):
    import hashlib

    session = _fetch_session()
    rows = shrug.fetch_preset("caste", tmp_path, session=session, log=lambda _m: None, min_rows=1)
    assert len(rows) == 2
    written = sorted(p.name for p in tmp_path.glob("*.zip"))
    assert written == ["SECC_Rural-secc_rural.zip", "SECC_Urban-secc_urban.zip"]
    manifest = [json.loads(a) for a in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert {r["sha256"] for r in manifest} == {hashlib.sha256(b"shrug bytes").hexdigest()}
    assert {r["bytes"] for r in manifest} == {len(b"shrug bytes")}
    assert {r["status"] for r in manifest} == {"downloaded"}


def test_manifest_row_drops_the_presigned_signature(tmp_path):
    """The presigned query string is a temporary credential, not provenance."""
    rows = shrug.fetch_preset("caste", tmp_path, session=_fetch_session(), log=lambda _m: None, min_rows=1)
    for row in rows:
        assert "X-Amz-Signature" not in row["url"]
        assert row["url"].startswith("https://shrug-assets-ddl.s3.amazonaws.com/")


def test_manifest_row_carries_the_licence_the_doi_and_the_shrid_unit(tmp_path):
    rows = shrug.fetch_preset("caste", tmp_path, session=_fetch_session(), log=lambda _m: None, min_rows=1)
    row = rows[0]
    assert row["licence"] == "CC BY-NC-SA 4.0"
    assert row["doi"] == shrug.DOI
    assert row["unit"] == "shrid"


def test_fetch_preset_tells_the_caller_the_two_facts(tmp_path):
    messages: list[str] = []
    shrug.fetch_preset("caste", tmp_path, session=_fetch_session(), log=messages.append, min_rows=1)
    text = " ".join(messages)
    assert "shrid is not a village" in text
    assert "pc11_vd_pub_lib" in text
    assert "CC BY-NC-SA 4.0" in text


def test_fetch_preset_leaves_no_part_file_behind(tmp_path):
    shrug.fetch_preset("caste", tmp_path, session=_fetch_session(), log=lambda _m: None, min_rows=1)
    assert list(tmp_path.glob("*.part")) == []


def test_a_second_run_neither_refetches_nor_duplicates_the_row(tmp_path):
    session = _fetch_session()
    shrug.fetch_preset("caste", tmp_path, session=session, log=lambda _m: None, min_rows=1)
    downloads = len([c for c in session.calls if not (c[1].get("headers") or {}).get("Range")])
    rows = shrug.fetch_preset("caste", tmp_path, session=session, log=lambda _m: None, min_rows=1)
    again = len([c for c in session.calls if not (c[1].get("headers") or {}).get("Range")])
    assert again == downloads + 1  # the catalogue is read again, the files are not
    assert {r["status"] for r in rows} == {"skipped_exists"}
    manifest = (tmp_path / "manifest.jsonl").read_text().splitlines()
    assert len(manifest) == 2


def test_fetch_refuses_a_table_whose_row_carries_no_download_anchor(tmp_path):
    rows = [_row("Caste", "SECC Rural", ""), _row("Caste", "SECC Urban", S3 + "u.zip?X-Amz-Signature=1")]
    session = _Session({
        shrug.CATALOGUE_URL: _Resp(_catalogue_body(rows)),
        S3: _Resp(b"x"),
    })
    with pytest.raises(shrug.ShrugCatalogueError, match="no download link"):
        shrug.fetch_preset("caste", tmp_path, session=session, log=lambda _m: None, min_rows=1)


# ------------------------------------------------------------------- coverage

def test_the_public_library_variable_is_recorded_as_2011_only():
    """A caller assuming a three-census panel gets two empty years."""
    assert shrug.census_years_for("pc11_vd_pub_lib") == ("2011",)


def test_an_unmeasured_variable_returns_none_rather_than_every_year():
    """None means "not measured here". It must never read as "all years"."""
    assert shrug.census_years_for("pc11_vd_power_all") is None


def test_village_directory_variable_counts_differ_by_census():
    assert shrug.VILLAGE_DIRECTORY_VARIABLES == {"1991": 100, "2001": 110, "2011": 284}


def test_caveats_name_the_unit_the_coverage_and_the_share_alike_term():
    text = " ".join(shrug.caveats())
    assert "shrid is not a village" in text
    assert "284" in text
    assert "share-alike" in text


# --- review findings, 2026-08-17 -------------------------------------------
# Each of these returned a plausible complete-looking result. The docstrings say
# what the old code reported.


def test_a_short_stream_is_not_reported_as_downloaded(tmp_path):
    """The ranged GET said 79 MB, the body was 11 bytes, and the row said
    `downloaded` with a valid sha256 of the partial file. A sha256 of an
    incomplete table is worse than none, because it reads as verification."""
    body = _catalogue_body(_full_rows())
    session = _Session({
        shrug.CATALOGUE_URL: _Resp(body),
        S3: lambda kwargs: (
            _Resp(b"\x00", status=206, headers={"Content-Range": "bytes 0-0/79180800"})
            if (kwargs.get("headers") or {}).get("Range") else _Resp(b"short bytes")
        ),
    })
    with pytest.raises(shrug.ShrugCatalogueError, match="of 79180800 bytes"):
        shrug.fetch_preset("caste", tmp_path, session=session, log=lambda _m: None,
                           min_rows=1)
    assert list(tmp_path.glob("*.part")) == [], "a failed download leaves no .part"


def test_two_tables_sharing_an_s3_basename_do_not_collide(tmp_path):
    """Both took the destination `download.zip`: the first downloaded, the second
    reported skipped_exists, and the caller could not tell which it held."""
    rows = [
        _row("Caste", "SECC Rural", S3 + "download.zip?X-Amz-Signature=a"),
        _row("Caste", "SECC Urban", S3 + "download.zip?X-Amz-Signature=b"),
    ]
    session = _Session({
        shrug.CATALOGUE_URL: _Resp(_catalogue_body(rows)),
        S3: lambda kwargs: (
            _Resp(b"\x00", status=206, headers={"Content-Range": "bytes 0-0/11"})
            if (kwargs.get("headers") or {}).get("Range") else _Resp(b"shrug bytes")
        ),
    })
    got = shrug.fetch_preset("caste", tmp_path, session=session, log=lambda _m: None,
                             min_rows=1)
    assert len({r["dest"] for r in got}) == 2
    assert len(list(tmp_path.glob("*.zip"))) == 2


def test_a_short_file_on_disk_is_not_reported_as_held(tmp_path):
    """Two zero-byte files produced two rows saying the preset was held, and no
    manifest at all: one reader saw an empty archive, another a complete preset."""
    for name in ("SECC_Rural-secc_rural.zip", "SECC_Urban-secc_urban.zip"):
        (tmp_path / name).write_bytes(b"")
    rows = shrug.fetch_preset("caste", tmp_path, session=_fetch_session(),
                              log=lambda _m: None, min_rows=1)
    assert {r["status"] for r in rows} == {"short_on_disk"}
    assert {r["bytes"] for r in rows} == {0}
    assert (tmp_path / "manifest.jsonl").exists(), "a skip is recorded, not silent"


def test_a_partial_catalogue_is_refused(tmp_path):
    """A three-row answer is an interstitial or a filtered response. `catalogue`
    is public and its row count gets printed as fact."""
    with pytest.raises(shrug.ShrugCatalogueError, match="row"):
        shrug.catalogue(session=_catalogue_session(rows=_full_rows()[:3]))


def test_a_non_object_catalogue_row_names_the_shape_change():
    with pytest.raises(shrug.ShrugCatalogueError, match="not an object"):
        shrug.catalogue(session=_catalogue_session(rows=["nope"] * 3), min_rows=1)


def test_the_dedupe_branch_is_actually_exercised():
    """The old dedupe test used fixtures where no label matched two patterns, so
    it held for any implementation, including one with the guard deleted."""
    rows = [_row("Keys", "Shrid Location Names and Keys", S3 + "keys.zip?X-Amz-Signature=1")]
    tables = shrug.resolve_preset("keys", shrug.catalogue(
        session=_catalogue_session(rows=rows), min_rows=1))
    assert [t.table_label for t in tables] == ["Shrid Location Names and Keys"], (
        "both `shrid.*key` and `location names` match this one label")


def test_a_recovered_table_replaces_its_short_row(tmp_path):
    """The recovery a short row instructs is to delete the file and re-run. The
    key guard then suppressed the new `downloaded` row, so the durable manifest
    reported the old failure with a null digest while the bytes were complete."""
    dest = tmp_path / "SECC_Rural-secc_rural.zip"
    dest.write_bytes(b"")
    (tmp_path / "SECC_Urban-secc_urban.zip").write_bytes(b"")
    first = shrug.fetch_preset("caste", tmp_path, session=_fetch_session(),
                               log=lambda _m: None, min_rows=1)
    assert {r["status"] for r in first} == {"short_on_disk"}

    dest.unlink()
    (tmp_path / "SECC_Urban-secc_urban.zip").unlink()
    shrug.fetch_preset("caste", tmp_path, session=_fetch_session(),
                       log=lambda _m: None, min_rows=1)
    rows = [json.loads(x) for x in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    last = {r["key"]: r for r in rows}          # last record wins
    assert {r["status"] for r in last.values()} == {"downloaded"}
    assert all(r["sha256"] for r in last.values())


def test_an_unchanged_outcome_does_not_append_a_second_row(tmp_path):
    """A re-run that finds the same complete files must not grow the manifest."""
    shrug.fetch_preset("caste", tmp_path, session=_fetch_session(),
                       log=lambda _m: None, min_rows=1)
    shrug.fetch_preset("caste", tmp_path, session=_fetch_session(),
                       log=lambda _m: None, min_rows=1)
    rows = (tmp_path / "manifest.jsonl").read_text().splitlines()
    assert len(rows) == 2
