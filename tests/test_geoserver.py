# SPDX-License-Identifier: MIT
"""Offline tests for the WMS-only GeoServer adaptor.

No network. The live behaviour these stand in for was verified against
apsac.ap.gov.in on 2026-08-15: 482 layers advertised, and sweeps returning 178
SocialWelfareSchool and 197 TribalWelfareSchool features — the same counts an
independent extractor produced, which is the control that matters.
"""
from __future__ import annotations

import json

import pytest

from commoner_probe.geoserver import GeoServer, Tile, big_symbol_sld


def test_sld_names_the_layer_and_sets_the_size():
    sld = big_symbol_sld("gatishakti:SchoolLocations", size_px=200)
    assert "<Name>gatishakti:SchoolLocations</Name>" in sld
    assert "<Size>200</Size>" in sld
    assert "PointSymbolizer" in sld


@pytest.mark.parametrize("geom", ["LineString", "Polygon", "MultiPolygon"])
def test_sld_refuses_non_point_geometry(geom):
    """A road line cannot be recovered by hit-testing symbols.

    Returning a style for it would invite a caller to sweep a road layer and
    treat whatever comes back as the road network. Refuse instead.
    """
    with pytest.raises(ValueError, match="point layers"):
        big_symbol_sld("x", geometry=geom)


def test_tile_quarter_covers_the_parent_exactly():
    t = Tile(76.0, 12.0, 78.0, 14.0)
    parts = t.quarter()
    assert len(parts) == 4
    assert all(p.depth == 1 for p in parts)
    assert min(p.west for p in parts) == t.west
    assert max(p.east for p in parts) == t.east
    assert sum((p.east - p.west) * (p.north - p.south) for p in parts) == pytest.approx(
        (t.east - t.west) * (t.north - t.south))


def test_offset_grid_moves_off_the_original_query_points():
    """The verification pass depends on this actually shifting.

    An offset of zero would re-ask the same questions and 'confirm' anything.
    """
    t = Tile(76.0, 12.0, 78.0, 14.0)
    o = t.offset(0.5)
    assert (o.west, o.south) == (77.0, 13.0)
    assert o.span == t.span


class _Resp:
    def __init__(self, text):
        self.text = text


class _Session:
    """Serves canned GetFeatureInfo responses, one per request."""

    def __init__(self, bodies):
        self.bodies = list(bodies)
        self.urls = []

    def get(self, url, timeout=None):
        self.urls.append(url)
        return _Resp(self.bodies.pop(0) if self.bodies else json.dumps({"features": []}))


def _fc(n, start=0):
    return json.dumps({"features": [
        {"id": f"f.{i}", "properties": {"code": str(1000 + i)}}
        for i in range(start, start + n)]})


def test_sweep_deduplicates_on_the_named_key():
    """The same feature returned from two overlapping tiles is one feature."""
    sess = _Session([_fc(3), _fc(3), _fc(3), _fc(3)])
    gs = GeoServer("http://x/geoserver", session=sess, feature_count=400)
    got = gs.sweep("ws:layer", (76.0, 12.0, 80.0, 16.0), start_span=2.0, key="code")
    assert set(got) == {"1000", "1001", "1002"}


def test_sweep_sends_our_style_not_the_servers():
    sess = _Session([_fc(1)])
    gs = GeoServer("http://x/geoserver", session=sess)
    gs.sweep("ws:layer", (76.0, 12.0, 77.0, 13.0), start_span=2.0, key="code")
    assert "SLD_BODY" in sess.urls[0]
    assert "GetFeatureInfo" in sess.urls[0]


def test_a_capped_response_subdivides_instead_of_being_believed():
    """Exactly FEATURE_COUNT means 'there are more', never 'there are this many'."""
    sess = _Session([_fc(2), _fc(1, 100), _fc(1, 200), _fc(1, 300), _fc(1, 400)])
    gs = GeoServer("http://x/geoserver", session=sess, feature_count=2)
    got = gs.sweep("ws:layer", (76.0, 12.0, 78.0, 14.0), start_span=2.0, key="code")
    # one capped tile -> four children, so five requests, not one
    assert len(sess.urls) == 5
    assert len(got) == 4


def test_a_non_json_error_is_raised_with_the_servers_own_words():
    sess = _Session(["<ServiceException>Service WFS is disabled</ServiceException>"])
    gs = GeoServer("http://x/geoserver", session=sess)
    with pytest.raises(RuntimeError, match="Service WFS is disabled"):
        gs.features_at("ws:layer", Tile(76.0, 12.0, 77.0, 13.0))


def test_one_bad_tile_does_not_zero_the_whole_layer():
    """The 2026-08-15 incident: a single failing tile aborted a layer to 0 rows.

    A run log then reads '0 rows', which is indistinguishable from an empty
    layer. The sweep must survive the tile and say the result is partial.
    """
    sess = _Session(["<html>gateway timeout</html>", _fc(2), _fc(2), _fc(2)])
    lines = []
    gs = GeoServer("http://x/geoserver", session=sess, log=lines.append)
    got = gs.sweep("ws:layer", (76.0, 12.0, 80.0, 16.0), start_span=2.0, key="code")
    assert got, "a single bad tile must not empty the layer"
    assert any("PARTIAL" in line for line in lines)


def test_verify_reports_saturation_only_when_nothing_new_appears():
    sess = _Session([_fc(3), _fc(3), _fc(3), _fc(3)])
    gs = GeoServer("http://x/geoserver", session=sess)
    out = gs.verify("ws:layer", (76.0, 12.0, 80.0, 16.0),
                    known={"1000", "1001", "1002"}, key="code")
    assert out["new"] == 0 and out["saturated"] is True
    assert out["recall"] == 1.0

    sess2 = _Session([_fc(3, 50)])
    gs2 = GeoServer("http://x/geoserver", session=sess2)
    out2 = gs2.verify("ws:layer", (76.0, 12.0, 77.0, 13.0),
                      known={"1000"}, key="code")
    assert out2["saturated"] is False and out2["new"] == 3
