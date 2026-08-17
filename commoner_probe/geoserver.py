# SPDX-License-Identifier: MIT
"""Extract point features from a GeoServer that publishes WMS and nothing else.

Indian state spatial-data infrastructures are GeoServer deployments, and many of
them expose WMS while deliberately disabling WFS. WMS is a *picture* service: it
answers "what does this look like" and, through ``GetFeatureInfo``, "what is at
this pixel". It is not a data service. This module makes an honest data extract
out of it anyway, and — just as importantly — refuses to pretend when it cannot.

Written from Andhra Pradesh's APSAC (``apsac.ap.gov.in/geoserver``), which carries
528 layers including school, anganwadi, welfare-institution, road and boundary
layers. Nothing here is Andhra-specific; the traps below are GeoServer's, not the
state's, and the same code should work against any state's deployment.

THE TRAP THAT MAKES NAIVE EXTRACTION WRONG, and it fails silently
================================================================
``GetFeatureInfo`` does not query the data. It hit-tests the **rendered symbol**
under the pixel you name, using whatever style the server has set as default. If
that default style draws a small point marker, a query only returns a feature
when it lands inside those few pixels, and the sweep silently returns a fraction
of the layer while looking completely successful.

Measured on APSAC's school layer: the server's default style yielded 19,090
schools. The same sweep with a 200-pixel square symbol yielded **58,301** — 3.05x
more — and a verification pass then found zero further features. The first number
had no error, no warning, and no missing-data indicator. It was simply wrong.

The fix is ``SLD_BODY``: send your own style with a symbol large enough that any
feature near the query point is under it. :func:`big_symbol_sld` builds one.

**Therefore: a bare GetFeatureInfo count is not a count.** Treat any extraction
that did not override the style as a lower bound of unknown tightness.

WFS IS OFTEN DISABLED, AND THAT IS A HARD LIMIT
===============================================
Always try WFS first — it returns real geometry and makes this whole module
unnecessary. :func:`wfs_status` reports what the server says. APSAC answers every
WFS request, at every version, with::

    org.geoserver.platform.ServiceException: Service WFS is disabled

When WFS is off, **only point layers are honestly recoverable**. A polygon or a
road line cannot be reconstructed from point hit-tests; you would be inventing
geometry. This module extracts points and refuses lines and polygons rather than
returning something plausible. Boundaries and road networks must come from
another source (Survey of India, SHRUG, OSM, the department's own download).

THE SAME GROUND APPEARS UNDER SEVERAL WORKSPACES
================================================
State portals republish one dataset under a campaign workspace, a department
workspace and a generic one. On APSAC the anganwadi layer exists as both
``gatishakti:AnganwadiCentres`` and ``Andhra-AnganwadiCentres:Andhra-AnganwadiCentres``
and both yield exactly 53,682 centres. That is not waste — **agreement between
two independently-swept workspaces is the best completeness check available**
when there is no authoritative count to compare against. Use it.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence
from urllib.parse import urlencode

from .http_client import make_session

__all__ = [
    "GeoServer",
    "Tile",
    "big_symbol_sld",
    "wfs_status",
]

# A GetFeatureInfo response is capped by FEATURE_COUNT; the server may also have
# its own ceiling. Hitting the cap means "there are at least this many here",
# which is the signal to subdivide rather than a result.
DEFAULT_FEATURE_COUNT = 400
DEFAULT_IMAGE_PX = 101          # odd, so there is an exact centre pixel
DEFAULT_SYMBOL_PX = 200


def big_symbol_sld(layer: str, *, size_px: int = DEFAULT_SYMBOL_PX,
                   geometry: str = "Point") -> str:
    """A style whose point symbol is large enough to be hit from anywhere nearby.

    This is the whole trick. The server's default style decides what
    ``GetFeatureInfo`` can find, so we replace it with one drawing a square of
    ``size_px``. Any feature within roughly half that many pixels of the query
    point then falls under the cursor and is returned.

    ``size_px`` trades recall against the cap: a larger symbol finds more per
    request but reaches ``FEATURE_COUNT`` sooner and forces more subdivision.
    200 px against a 101 px image worked well on APSAC.
    """
    if geometry != "Point":
        raise ValueError(
            f"big_symbol_sld only makes sense for point layers, not {geometry!r}. "
            "GetFeatureInfo hit-tests rendered symbols; a line or polygon cannot "
            "be recovered this way — get its geometry from a real vector source.")
    return (
        '<StyledLayerDescriptor version="1.0.0" '
        'xmlns="http://www.opengis.net/sld">'
        f"<NamedLayer><Name>{layer}</Name><UserStyle><FeatureTypeStyle><Rule>"
        "<PointSymbolizer><Graphic>"
        '<Mark><WellKnownName>square</WellKnownName>'
        "<Fill><CssParameter name=\"fill\">#000000</CssParameter></Fill></Mark>"
        f"<Size>{size_px}</Size>"
        "</Graphic></PointSymbolizer>"
        "</Rule></FeatureTypeStyle></UserStyle></NamedLayer>"
        "</StyledLayerDescriptor>")


def wfs_status(base: str, *, session: Any = None, timeout: int = 60) -> dict[str, Any]:
    """Ask whether WFS works, and report the server's own words if it does not.

    Call this FIRST. If WFS is enabled, use it and ignore the rest of this
    module: it returns real geometry for every feature type, including the lines
    and polygons that WMS extraction cannot honestly recover.
    """
    sess = session if session is not None else make_session()
    out: dict[str, Any] = {"enabled": False, "versions": {}, "message": None}
    for version in ("2.0.0", "1.1.0", "1.0.0"):
        url = f"{base.rstrip('/')}/wfs?" + urlencode(
            {"service": "WFS", "version": version, "request": "GetCapabilities"})
        try:
            body = sess.get(url, timeout=timeout).text[:4000]
        except Exception as exc:  # network shape varies by session backend
            out["versions"][version] = f"error: {type(exc).__name__}"
            continue
        if "WFS_Capabilities" in body or "<FeatureType" in body:
            out["versions"][version] = "ok"
            out["enabled"] = True
        else:
            msg = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()
            out["versions"][version] = "disabled"
            out["message"] = out["message"] or msg[:200]
    return out


@dataclass
class Tile:
    """A bounding box in EPSG:4326 degrees, and where it came from."""

    west: float
    south: float
    east: float
    north: float
    depth: int = 0

    @property
    def span(self) -> float:
        return max(self.east - self.west, self.north - self.south)

    def quarter(self) -> list["Tile"]:
        mx = (self.west + self.east) / 2
        my = (self.south + self.north) / 2
        d = self.depth + 1
        return [
            Tile(self.west, self.south, mx, my, d),
            Tile(mx, self.south, self.east, my, d),
            Tile(self.west, my, mx, self.north, d),
            Tile(mx, my, self.east, self.north, d),
        ]

    def offset(self, fraction: float = 0.5) -> "Tile":
        """The same-sized box shifted by a fraction of its own span.

        The verification pass uses this. Re-running an identical grid proves
        nothing — it asks the same questions and gets the same answers. A grid
        offset by half a tile queries the ground *between* the original query
        points, so finding no new features there is evidence of saturation
        rather than of repetition.
        """
        dx = (self.east - self.west) * fraction
        dy = (self.north - self.south) * fraction
        return Tile(self.west + dx, self.south + dy,
                    self.east + dx, self.north + dy, self.depth)


@dataclass
class GeoServer:
    """A WMS-only GeoServer, swept for point features.

    ``base`` is the GeoServer root, e.g. ``https://apsac.ap.gov.in/geoserver``.
    """

    base: str
    session: Any = None
    delay: float = 0.0
    feature_count: int = DEFAULT_FEATURE_COUNT
    image_px: int = DEFAULT_IMAGE_PX
    symbol_px: int = DEFAULT_SYMBOL_PX
    log: Callable[[str], None] = print
    _stats: dict[str, int] = field(default_factory=lambda: {"requests": 0, "capped": 0})

    def __post_init__(self) -> None:
        self.session = self.session if self.session is not None else make_session()
        self.base = self.base.rstrip("/")

    # ---------------------------------------------------------------- discovery
    def layers(self, *, timeout: int = 180) -> list[str]:
        """Every named layer the server advertises, from WMS GetCapabilities."""
        url = f"{self.base}/wms?" + urlencode(
            {"service": "WMS", "version": "1.3.0", "request": "GetCapabilities"})
        body = self.session.get(url, timeout=timeout).text
        names = re.findall(r"<Name>([^<]+)</Name>", body)
        return sorted({n for n in names if ":" in n})

    # ------------------------------------------------------------------- fetch
    def features_at(self, layer: str, tile: Tile, *, timeout: int = 120) -> list[dict]:
        """GetFeatureInfo at the centre of ``tile``, with our own big symbol.

        Returns the raw GeoJSON feature list. A result of exactly
        ``feature_count`` means the response was capped and the caller must
        subdivide — it is a "there are more" signal, not a count.
        """
        half = self.image_px // 2
        params = {
            "service": "WMS", "version": "1.1.1", "request": "GetFeatureInfo",
            "layers": layer, "query_layers": layer,
            "bbox": f"{tile.west},{tile.south},{tile.east},{tile.north}",
            "srs": "EPSG:4326",
            "width": self.image_px, "height": self.image_px,
            "x": half, "y": half,
            "info_format": "application/json",
            "feature_count": self.feature_count,
            "SLD_BODY": big_symbol_sld(layer, size_px=self.symbol_px),
        }
        url = f"{self.base}/wms?" + urlencode(params)
        if self.delay:
            time.sleep(self.delay)
        body = self.session.get(url, timeout=timeout).text
        self._stats["requests"] += 1
        try:
            feats = json.loads(body).get("features", [])
        except json.JSONDecodeError:
            # A GeoServer error is served as XML or HTML with a 200. Surface the
            # server's own words: silently returning [] here would read as "no
            # features here", which is the expensive kind of wrong.
            msg = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()
            raise RuntimeError(f"{layer}: non-JSON response: {msg[:200]}") from None
        if len(feats) >= self.feature_count:
            self._stats["capped"] += 1
        return feats

    # ------------------------------------------------------------------- sweep
    def sweep(self, layer: str, bbox: Sequence[float], *, start_span: float = 2.0,
              min_span: float = 1 / 32, key: str | None = None,
              on_batch: Callable[[dict[str, dict]], None] | None = None,
              tolerate_tile_errors: bool = True,
              status: dict | None = None) -> dict[str, dict]:
        """Recursively subdivide ``bbox`` until no tile is capped, collecting points.

        ``key`` names the attribute that identifies a feature (a school code, an
        AWC id). When given, features are deduplicated on it; otherwise the
        server's own feature id is used.

        ``tolerate_tile_errors`` defaults to True **because of a real incident**:
        on 2026-08-15 a single tile in a layer's far corner returned a non-JSON
        error, the exception propagated, and the whole layer aborted having
        written zero rows. The run log then showed "0 rows", which reads exactly
        like "this layer is empty" rather than "this layer crashed". Failed tiles
        are collected and reported instead, so a partial sweep is visibly partial.
        """
        west, south, east, north = bbox
        queue: list[Tile] = []
        lat = south
        while lat < north:
            lon = west
            while lon < east:
                queue.append(Tile(lon, lat,
                                  min(lon + start_span, east),
                                  min(lat + start_span, north)))
                lon += start_span
            lat += start_span

        found: dict[str, dict] = {}
        failures: list[tuple[Tile, str]] = []
        capped: list[Tile] = []
        while queue:
            tile = queue.pop()
            try:
                feats = self.features_at(layer, tile)
            except Exception as exc:
                if not tolerate_tile_errors:
                    raise
                failures.append((tile, str(exc)[:120]))
                continue
            if len(feats) >= self.feature_count and tile.span > min_span:
                queue.extend(tile.quarter())
                continue
            if len(feats) >= self.feature_count:
                # The tile is at `min_span` and still capped, so the walk cannot
                # subdivide further. A cap means "there may be more", so this
                # leaf is INCOMPLETE. Ingesting it as a complete result truncates
                # the densest clusters, which are the ones a reader most wants.
                capped.append(tile)
            for f in feats:
                props = f.get("properties", {}) or {}
                ident = str(props.get(key)) if key else str(f.get("id"))
                if ident and ident not in ("None", ""):
                    # Keep the WHOLE feature. Storing only `properties` dropped
                    # the coordinates, and a point extractor that returns no
                    # points answers a different question than the one asked.
                    found[ident] = f
            if on_batch and len(found) % 500 < len(feats):
                on_batch(found)

        if failures:
            self.log(f"  {layer}: {len(failures)} tile(s) failed and were SKIPPED — "
                     f"this sweep is PARTIAL, not complete")
            for tl, msg in failures[:5]:
                self.log(f"    {tl.west},{tl.south},{tl.east},{tl.north}: {msg}")
        if capped:
            self.log(f"  {layer}: {len(capped)} tile(s) hit the feature cap at the "
                     f"minimum span — this sweep is PARTIAL, and those tiles are "
                     f"a LOWER BOUND")
        if status is not None:
            # The caller cannot see a log line. `verify` must not certify a
            # sweep it cannot see the holes in.
            box = lambda t: (t.west, t.south, t.east, t.north)  # noqa: E731
            status.update(failed=[box(t) for t, _ in failures],
                          capped=[box(t) for t in capped],
                          partial=bool(failures or capped))
        return found

    def verify(self, layer: str, bbox: Sequence[float], known: Iterable[str], *,
               start_span: float = 2.0, key: str | None = None) -> dict[str, Any]:
        """Re-sweep on an OFFSET grid and report what the first pass missed.

        This is the only honest way to claim a sweep is complete. Re-running the
        same grid re-asks the same questions; an offset grid interrogates the
        gaps between them. On APSAC's school layer this returned 58,301 against
        58,301 with zero new features, which is what turns a floor into a count.
        """
        known = set(known)
        west, south, east, north = bbox
        # Shift by half a CELL, not half the region: a 4-degree box with
        # 2-degree cells must move 1 degree. Moving half the region queries
        # ground outside it and leaves the leading edge untested.
        #
        # And never by more than half the region itself. A box smaller than one
        # cell would otherwise move clean off its own ground: (76,12,77,13) with
        # a 2-degree cell became (77,13,78,14), where finding nothing new
        # certifies saturation over a region this layer never claimed. Bounded
        # per axis, because a box can be wide and short.
        step_x = min(start_span, east - west) / 2
        step_y = min(start_span, north - south) / 2
        shifted = (west + step_x, south + step_y, east + step_x, north + step_y)
        status: dict = {}
        got = self.sweep(layer, shifted, start_span=start_span, key=key, status=status)
        new = set(got) - known
        partial = bool(status.get("partial"))
        if partial:
            self.log(f"  {layer}: the verification pass is itself PARTIAL — "
                     f"{len(status.get('failed', []))} failed and "
                     f"{len(status.get('capped', []))} capped tile(s). "
                     "Saturation is NOT claimed.")
        return {
            "pass1": len(known),
            "pass2": len(got),
            "new": len(new),
            "recall": (len(known & set(got)) / len(known)) if known else 0.0,
            # An empty `new` proves saturation only when the second pass
            # actually asked every question. A pass with holes produces the
            # same empty set for the opposite reason.
            "saturated": not new and not partial,
            "partial": partial,
            "failed_tiles": status.get("failed", []),
            "capped_tiles": status.get("capped", []),
            "new_ids": sorted(new)[:50],
        }

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._stats)
