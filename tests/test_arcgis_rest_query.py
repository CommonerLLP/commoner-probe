"""ArcGIS REST query adapter. No test touches the network."""

from __future__ import annotations

import hashlib
import json
import urllib.parse
from pathlib import Path

import pytest

from commoner_probe import http_client as hc
from commoner_probe.arcgis_rest_query import ArcGISError, query_layer

LAYER = "https://example.test/arcgis/rest/services/X/MapServer/0"


def _feat(i: int) -> dict:
    return {
        "type": "Feature",
        "properties": {"id": i},
        "geometry": {"type": "Point", "coordinates": [-82.4, 42.9]},
    }


def _page(n_start: int, n: int, *, flag: str | None = None) -> dict:
    page: dict = {"type": "FeatureCollection", "features": [_feat(n_start + i) for i in range(n)]}
    if flag == "top":
        page["exceededTransferLimit"] = True
    elif flag == "properties":
        page["properties"] = {"exceededTransferLimit": True}
    return page


class FakeResponse:
    def __init__(self, body: bytes):
        self._body = body
        self.status_code = 200
        self.headers: dict = {}

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=16384):
        yield self._body


class StubSession:
    def __init__(self, pages: list[dict]):
        self.pages = list(pages)
        self.urls: list[str] = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        return FakeResponse(json.dumps(self.pages.pop(0)).encode())

    def params(self, i: int) -> dict[str, str]:
        return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.urls[i]).query))


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(hc.time, "sleep", lambda s: None)


def test_pages_until_transfer_limit_clears(tmp_path):
    session = StubSession([_page(0, 2, flag="top"), _page(2, 1)])
    result = query_layer(session, LAYER, page_size=2, out_dir=tmp_path)
    assert [f["properties"]["id"] for f in result["features"]] == [0, 1, 2]
    assert result["type"] == "FeatureCollection"
    assert len(session.urls) == 2
    assert session.params(0)["resultOffset"] == "0"
    assert session.params(0)["resultRecordCount"] == "2"
    assert session.params(1)["resultOffset"] == "2"
    assert session.params(0)["f"] == "geojson"
    assert session.params(0)["outSR"] == "4326"
    assert session.urls[0].startswith(LAYER + "/query?")


def test_flag_in_properties_is_also_honoured(tmp_path):
    session = StubSession([_page(0, 2, flag="properties"), _page(2, 1)])
    result = query_layer(session, LAYER, page_size=2, out_dir=tmp_path)
    assert len(result["features"]) == 3
    assert len(session.urls) == 2


def test_geometry_envelope_is_encoded(tmp_path):
    session = StubSession([_page(0, 1)])
    env = {"xmin": -82.5, "ymin": 42.8, "xmax": -82.3, "ymax": 43.0}
    query_layer(session, LAYER, geometry=env, out_dir=tmp_path)
    p = session.params(0)
    assert p["geometry"] == "-82.5,42.8,-82.3,43.0"
    assert p["geometryType"] == "esriGeometryEnvelope"
    assert p["inSR"] == "4326"
    assert p["spatialRel"] == "esriSpatialRelIntersects"


def test_where_fields_and_geometry_flag_are_sent(tmp_path):
    session = StubSession([_page(0, 1)])
    query_layer(
        session, LAYER, where="adminAreaId='06194'", out_fields="adminAreaNameEng",
        return_geometry=False, out_dir=tmp_path,
    )
    p = session.params(0)
    assert p["where"] == "adminAreaId='06194'"
    assert p["outFields"] == "adminAreaNameEng"
    assert p["returnGeometry"] == "false"
    assert "geometry" not in p


def test_one_file_and_one_manifest_record_per_page(tmp_path):
    session = StubSession([_page(0, 2, flag="top"), _page(2, 1)])
    query_layer(session, LAYER, page_size=2, out_dir=tmp_path)
    records = [json.loads(x) for x in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert len(records) == 2
    for i, rec in enumerate(records):
        data = Path(rec["dest"]).read_bytes()
        assert rec["sha256"] == hashlib.sha256(data).hexdigest()
        assert rec["bytes"] == len(data)
        assert rec["kind"] == "arcgis_query_page"
        assert rec["result_offset"] == i * 2
        assert rec["source_url"] == session.urls[i]
        assert rec["status"] == "downloaded"
    assert records[0]["feature_count"] == 2
    assert records[1]["feature_count"] == 1
    assert records[0]["dest"] != records[1]["dest"]


def test_error_object_raises_and_writes_nothing(tmp_path):
    err = {"error": {"code": 400, "message": "Pagination is not supported.", "details": []}}
    with pytest.raises(ArcGISError, match="Pagination is not supported"):
        query_layer(StubSession([err]), LAYER, out_dir=tmp_path)
    assert not (tmp_path / "manifest.jsonl").exists()


def test_error_on_later_page_never_returns_partial(tmp_path):
    err = {"error": {"code": 500, "message": "boom"}}
    with pytest.raises(ArcGISError, match="boom"):
        query_layer(StubSession([_page(0, 2, flag="top"), err]), LAYER, page_size=2, out_dir=tmp_path)


def test_empty_page_with_flag_raises_instead_of_looping(tmp_path):
    with pytest.raises(ArcGISError, match="no features"):
        query_layer(StubSession([_page(0, 0, flag="top")]), LAYER, out_dir=tmp_path)


def test_arcgis_cli_prints_summary(tmp_path, capsys, monkeypatch):
    from commoner_probe import cli

    monkeypatch.setattr(cli, "make_session", lambda: StubSession([_page(0, 2)]))
    args = cli.build_parser().parse_args(
        ["arcgis-query", "--layer-url", LAYER, "--where", "1=1", "--out", str(tmp_path)]
    )
    args.func(args)
    out = json.loads(capsys.readouterr().out)
    assert out["features"] == 2
    assert out["pages"] == 1
