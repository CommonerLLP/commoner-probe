# SPDX-License-Identifier: MIT
"""Assign a point to SHRUG village, assembly constituency, and LGD panchayat keys."""

from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass
from pathlib import Path

from .admin_units import AMBIGUOUS, RESOLVED, UNRESOLVED, _code


@dataclass(frozen=True)
class Constituency:
    ac08_id: str
    weight: float


@dataclass(frozen=True)
class SpatialAssignment:
    status: str
    village_id: tuple[str, str, str, str] | None = None
    shrid2: str | None = None
    constituencies: tuple[Constituency, ...] = ()
    gram_panchayat_lgd_code: str | None = None
    gram_panchayat_candidates: tuple[str, ...] = ()
    reason: str = ""

    @property
    def primary_constituency(self) -> Constituency | None:
        return self.constituencies[0] if self.constituencies else None


def _village_key(row: dict, *, polygon: bool = False) -> tuple[str, str, str, str]:
    if polygon:
        columns = ("pc11_state_id", "pc11_district_id", "pc11_subdistrict_id",
                   "pc11_town_village_id")
    else:
        columns = ("pc11_state_id", "pc11_district_id", "pc11_subdistrict_id",
                   "pc11_village_id")
    return tuple(_code(row.get(column)) for column in columns)


class SpatialIndex:
    """Keep the source's split weights and refuse ambiguous polygon hits."""

    def __init__(self, villages, pc11_keys, constituency_keys, lgd_rows):
        try:
            from shapely.geometry import Point  # noqa: F401
        except ImportError as exc:
            raise ImportError("SpatialIndex needs commoner-probe[geo]") from exc
        required = {"pc11_state_id", "pc11_district_id", "pc11_subdistrict_id",
                    "pc11_town_village_id", "geometry"}
        if not required.issubset(villages.columns):
            raise ValueError(f"village polygons lack columns: {sorted(required - set(villages.columns))}")
        required_pc11 = {"shrid2", "pc11_state_id", "pc11_district_id",
                         "pc11_subdistrict_id", "pc11_village_id"}
        if not required_pc11.issubset(pc11_keys.columns):
            raise ValueError(f"Census keys lack columns: {sorted(required_pc11 - set(pc11_keys.columns))}")
        required_ac = {"shrid2", "ac08_id", "fragment_wt_con08_norm"}
        if not required_ac.issubset(constituency_keys.columns):
            raise ValueError(f"constituency keys lack columns: {sorted(required_ac - set(constituency_keys.columns))}")
        if villages.crs is None:
            raise ValueError("village polygons have no coordinate reference system")
        self.villages = villages.to_crs("EPSG:4326")
        self.shrid_by_village = {}
        for values in pc11_keys.itertuples(index=False, name=None):
            row = dict(zip(pc11_keys.columns, values))
            key = _village_key(row)
            shrid = str(row.get("shrid2") or "")
            if key in self.shrid_by_village and self.shrid_by_village[key] != shrid:
                self.shrid_by_village[key] = None
            else:
                self.shrid_by_village[key] = shrid
        weights = {}
        for values in constituency_keys.itertuples(index=False, name=None):
            row = dict(zip(constituency_keys.columns, values))
            shrid = str(row.get("shrid2") or "")
            ac = str(row.get("ac08_id") or "")
            weight = row.get("fragment_wt_con08_norm")
            if not shrid or not ac or weight is None or math.isnan(float(weight)):
                continue
            key = shrid, ac
            weights[key] = max(weights.get(key, 0.0), float(weight))
        self.ac_by_shrid = {}
        for (shrid, ac), weight in weights.items():
            self.ac_by_shrid.setdefault(shrid, []).append(Constituency(ac, weight))
        self.gp_by_village = {}
        required_lgd = {"State Code", "District Census 2011 Code",
                        "Subdistrict Census 2011 Code", "Village Census 2011 Code",
                        "Gram Panchayat LGD Code"}
        lgd_count = 0
        for row in lgd_rows:
            lgd_count += 1
            if not required_lgd.issubset(row):
                raise ValueError(f"LGD extract lacks columns: {sorted(required_lgd - set(row))}")
            key = tuple(_code(row.get(column)) for column in (
                "State Code", "District Census 2011 Code",
                "Subdistrict Census 2011 Code", "Village Census 2011 Code"))
            gp = _code(row.get("Gram Panchayat LGD Code"))
            if gp:
                self.gp_by_village.setdefault(key, set()).add(gp)
        if not lgd_count:
            raise ValueError("LGD extract yielded no village rows")

    @classmethod
    def from_files(cls, village_gpkg: str | Path, pc11_dta: str | Path,
                   constituency_dta: str | Path, lgd_tab: str | Path, *,
                   state_code: str) -> SpatialIndex:
        """Read local source files; callers acquire them through their owning repos."""
        try:
            import geopandas as gpd
            import pandas as pd
        except ImportError as exc:
            raise ImportError("SpatialIndex.from_files needs commoner-probe[geo]") from exc
        if not re.fullmatch(r"[0-9]{1,2}", state_code):
            raise ValueError("state_code must be a Census 2011 numeric code")
        state_code = state_code.zfill(2)
        villages = gpd.read_file(village_gpkg,
                                 where=f"pc11_state_id = '{state_code}'")
        if villages.empty:
            raise ValueError(f"village polygons have no state {state_code}")
        pc11_keys = pd.concat(
            (chunk[chunk["pc11_state_id"].astype(str).str.zfill(2) == state_code]
             for chunk in pd.read_stata(pc11_dta, convert_categoricals=False,
                                        chunksize=100_000)),
            ignore_index=True,
        )
        constituency_keys = pd.concat(
            (chunk[chunk["shrid2"].str.startswith(f"11-{state_code}-", na=False)]
             for chunk in pd.read_stata(constituency_dta, convert_categoricals=False,
                                        chunksize=100_000)),
            ignore_index=True,
        )
        if pc11_keys.empty or constituency_keys.empty:
            raise ValueError(f"SHRUG key tables have no state {state_code}")
        with Path(lgd_tab).open(encoding="utf-8", errors="ignore") as fh:
            return cls(villages, pc11_keys, constituency_keys,
                       csv.DictReader(fh, delimiter="\t"))

    def assign(self, lon: float, lat: float) -> SpatialAssignment:
        from shapely.geometry import Point

        point = Point(lon, lat)
        hits = self.villages.iloc[self.villages.sindex.query(point, predicate="intersects")]
        hits = hits[hits.geometry.covers(point)]
        if hits.empty:
            return SpatialAssignment(UNRESOLVED, reason="point is outside village polygons")
        keys = {_village_key(row, polygon=True) for row in hits.to_dict("records")}
        if len(keys) != 1:
            return SpatialAssignment(AMBIGUOUS, reason="point intersects multiple villages")
        key = next(iter(keys))
        shrid = self.shrid_by_village.get(key)
        constituencies = tuple(sorted(self.ac_by_shrid.get(shrid, ()),
                                       key=lambda item: (-item.weight, item.ac08_id)))
        candidates = tuple(sorted(self.gp_by_village.get(key, set())))
        reasons = []
        if shrid is None:
            reasons.append("village has no unique SHRUG key")
        elif not constituencies:
            reasons.append("SHRUG key has no 2008 constituency row")
        if not candidates:
            reasons.append("village has no LGD gram panchayat row")
        elif len(candidates) > 1:
            reasons.append("multiple gram panchayats")
        return SpatialAssignment(
            RESOLVED, village_id=key, shrid2=shrid,
            constituencies=constituencies,
            gram_panchayat_lgd_code=candidates[0] if len(candidates) == 1 else None,
            gram_panchayat_candidates=candidates,
            reason="; ".join(reasons),
        )
