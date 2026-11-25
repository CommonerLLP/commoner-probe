import pandas as pd
import pytest

from commoner_probe import admin_units as au


def test_block_resolves_inside_district_alias_and_refuses_other_district():
    index = au.BlockIndex({
        "11": au.Block("11", "Sadar Chapra", "101", "Chapra(Saran)"),
        "12": au.Block("12", "Sadar Chapra", "102", "Vaishali"),
    })
    found = index.resolve("Sadar Chapra", "Saran")
    assert found.status == au.RESOLVED
    assert found.candidates[0].block_id == "11"
    assert found.grade == "exact_pmgsy"
    assert index.resolve("Sadar Chapra", "Kaimur").status == au.UNRESOLVED


def test_block_resolves_kaimur_district_alias():
    index = au.BlockIndex({
        "11": au.Block("11", "Bhabhua", "101", "Kaimur (Bhabhua)"),
    })
    assert index.resolve("Bhabhua", "Kaimur").candidates[0].block_id == "11"


def test_block_matches_census_subdistrict_only_when_unique(tmp_path, monkeypatch):
    master = pd.DataFrame([{
        "DISTRICT_ID": 101, "DISTRICT_NAME": "Chapra(Saran)",
        "BLOCK_ID": 11, "BLOCK_NAME": "Sadar Chapra",
    }])
    monkeypatch.setattr(pd, "read_excel", lambda _: master)
    shrid = tmp_path / "shrid.tab"
    shrid.write_text(
        "shrid2\tdistrict_name\tsubdistrict_name\n"
        "11-10-101-00500-000001\tSaran\tSadar Chapra\n"
    )
    index = au.build_blocks(tmp_path / "master.xls", shrid_tab=shrid)
    assert index.blocks["11"].census_subdistrict_code == "00500"
    assert index.resolve("Sadar Chapra", "Chapra(Saran)").grade == "exact_both"


def test_habitation_ambiguity_is_not_silently_resolved():
    index = au.build_habitations([
        {"HAB_ID": 1, "HAB_NAME": "Rampur", "BLOCK_ID": 11, "DISTRICT_I": 101,
         "TOT_POPULA": 100},
        {"HAB_ID": 2, "HAB_NAME": "Rampur", "BLOCK_ID": 11, "DISTRICT_I": 101,
         "TOT_POPULA": 200},
    ])
    match = index.resolve("Rampur", "11")
    assert match.status == au.AMBIGUOUS
    assert {h.hab_id for h in match.candidates} == {"1", "2"}
    assert index.resolve("Rampur", "12").status == au.UNRESOLVED
    review = index.resolve("Rampoor", "11")
    assert review.status == au.UNRESOLVED
    assert review.candidates


def test_repeated_habitation_id_with_two_points_is_ambiguous():
    from shapely.geometry import Point

    index = au.build_habitations([
        {"HAB_ID": 1, "HAB_NAME": "Rampur", "BLOCK_ID": 11,
         "DISTRICT_I": 101, "geometry": Point(86.8, 24.7)},
        {"HAB_ID": 1, "HAB_NAME": "Rampur", "BLOCK_ID": 11,
         "DISTRICT_I": 101, "geometry": Point(86.9, 24.7)},
    ])
    match = index.resolve("Rampur", "11")
    assert match.status == au.AMBIGUOUS
    assert len(match.candidates) == 2
    assert {h.lon for h in match.candidates} == {86.8, 86.9}
    measured = au.linkage_precision(index, [("Rampur", "11", "1")])
    assert measured.resolved == 0
    assert measured.error_rate is None


def test_habitation_linkage_reports_score_and_measured_precision():
    index = au.build_habitations([
        {"HAB_ID": 1, "HAB_NAME": "Bishunpurnagar", "BLOCK_ID": 11, "DISTRICT_I": 101},
        {"HAB_ID": 2, "HAB_NAME": "Rampur", "BLOCK_ID": 11, "DISTRICT_I": 101},
    ])
    match = index.resolve("Bishunpurnagor", "11")
    assert match.status == au.RESOLVED
    assert match.grade == "similarity"
    assert 0.9 <= match.score < 1
    measured = au.linkage_precision(index, [
        ("Bishunpurnagor", "11", "1"),
        ("Rampur", "11", "2"),
        ("unknown", "11", None),
    ])
    assert (measured.offered, measured.resolved, measured.correct) == (3, 2, 2)
    assert measured.precision == 1.0
    assert measured.error_rate == 0.0


def test_labelled_pair_model_scores_and_exposes_review_candidates():
    model = au.fit_linkage([
        ("Rampur", "Rampur", True),
        ("Bishunpurnagar", "Bishunpurnagor", True),
        ("Rampur", "Bishunpurnagar", False),
        ("Rampur", "Chandipur", False),
    ])
    assert model.score("Rampur", "Rampur") > model.score("Rampur", "Chandipur")
    index = au.build_habitations([
        {"HAB_ID": 1, "HAB_NAME": "Rampur", "BLOCK_ID": 11, "DISTRICT_I": 101},
        {"HAB_ID": 2, "HAB_NAME": "Chandipur", "BLOCK_ID": 11, "DISTRICT_I": 101},
    ])
    match = model.resolve(index, "Rampur", "11")
    assert match.status == au.RESOLVED
    assert match.grade == "fellegi_sunter"
    assert match.candidates[0].hab_id == "1"
    review = model.resolve(index, "Rampur", "11", min_log_ratio=match.score + 1)
    assert review.status == au.UNRESOLVED
    assert review.candidates[0].hab_id == "1"
    held_out = au.linkage_precision(index, [("Rampur", "11", "1")], model=model)
    assert held_out.precision == 1.0


def test_spatial_assignment_keeps_split_weights_and_gp():
    gpd = pytest.importorskip("geopandas")
    from shapely.geometry import Polygon

    from commoner_probe.admin_units_spatial import SpatialIndex

    villages = gpd.GeoDataFrame([{
        "pc11_state_id": "10", "pc11_district_id": "101",
        "pc11_subdistrict_id": "00500", "pc11_town_village_id": "000001",
        "geometry": Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
    }], crs="EPSG:4326")
    pc11 = pd.DataFrame([{
        "pc11_state_id": "10", "pc11_district_id": "101",
        "pc11_subdistrict_id": "00500", "pc11_village_id": "000001",
        "shrid2": "11-10-101-00500-000001",
    }])
    ac = pd.DataFrame([
        {"shrid2": "11-10-101-00500-000001", "ac08_id": "2008-10-001",
         "fragment_wt_con08_norm": 0.7},
        {"shrid2": "11-10-101-00500-000001", "ac08_id": "2008-10-002",
         "fragment_wt_con08_norm": 0.3},
    ])
    lgd = [{
        "State Code": "10", "District Census 2011 Code": "101",
        "Subdistrict Census 2011 Code": "00500", "Village Census 2011 Code": "000001",
        "Gram Panchayat LGD Code": "1234",
    }]
    index = SpatialIndex(villages, pc11, ac, lgd)
    assigned = index.assign(0.5, 0.5)
    assert assigned.status == au.RESOLVED
    assert assigned.shrid2 == "11-10-101-00500-000001"
    assert [(c.ac08_id, c.weight) for c in assigned.constituencies] == [
        ("2008-10-001", 0.7), ("2008-10-002", 0.3)]
    assert assigned.gram_panchayat_lgd_code == "1234"
    assert assigned.gram_panchayat_candidates == ("1234",)
    assert index.assign(2, 2).status == au.UNRESOLVED


def test_spatial_assignment_exposes_ambiguous_gram_panchayats():
    gpd = pytest.importorskip("geopandas")
    from shapely.geometry import Polygon

    from commoner_probe.admin_units_spatial import SpatialIndex

    villages = gpd.GeoDataFrame([{
        "pc11_state_id": "10", "pc11_district_id": "101",
        "pc11_subdistrict_id": "00500", "pc11_town_village_id": "000001",
        "geometry": Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
    }], crs="EPSG:4326")
    pc11 = pd.DataFrame([{
        "pc11_state_id": "10", "pc11_district_id": "101",
        "pc11_subdistrict_id": "00500", "pc11_village_id": "000001",
        "shrid2": "11-10-101-00500-000001",
    }])
    ac = pd.DataFrame([{
        "shrid2": "11-10-101-00500-000001", "ac08_id": "2008-10-001",
        "fragment_wt_con08_norm": 1.0,
    }])
    lgd = [{
        "State Code": "10", "District Census 2011 Code": "101",
        "Subdistrict Census 2011 Code": "00500", "Village Census 2011 Code": "000001",
        "Gram Panchayat LGD Code": code,
    } for code in ("1234", "1235")]
    assigned = SpatialIndex(villages, pc11, ac, lgd).assign(0.5, 0.5)
    assert assigned.gram_panchayat_lgd_code is None
    assert assigned.gram_panchayat_candidates == ("1234", "1235")


def test_block_state_filter_separates_shared_district_names():
    index = au.BlockIndex({
        "315": au.Block("315", "Aurangabad", "31", "Aurangabad",
                        state_id="5", state_name="Bihar"),
        "316": au.Block("316", "Aurangabad", "32", "Aurangabad",
                        state_id="20", state_name="Maharashtra"),
    })
    assert index.resolve("Aurangabad", "Aurangabad").status == au.AMBIGUOUS
    found = index.resolve("Aurangabad", "Aurangabad", state="Bihar")
    assert found.status == au.RESOLVED
    assert found.candidates[0].block_id == "315"
    assert index.resolve("Aurangabad", "Aurangabad", state="20").candidates[0].block_id == "316"


def test_build_blocks_reads_state_columns(tmp_path, monkeypatch):
    master = pd.DataFrame([{
        "STATE_ID": 5, "STATE_NAME": "Bihar",
        "DISTRICT_ID": 101, "DISTRICT_NAME": "Kaimur (Bhabhua)",
        "BLOCK_ID": 11, "BLOCK_NAME": "Bhabhua",
    }])
    monkeypatch.setattr(pd, "read_excel", lambda _: master)
    block = au.build_blocks(tmp_path / "master.xls").blocks["11"]
    assert (block.state_id, block.state_name) == ("5", "Bihar")


def test_spatial_assignment_names_missing_constituency_and_gp():
    gpd = pytest.importorskip("geopandas")
    from shapely.geometry import Polygon

    from commoner_probe.admin_units_spatial import SpatialIndex

    villages = gpd.GeoDataFrame([{
        "pc11_state_id": "10", "pc11_district_id": "101",
        "pc11_subdistrict_id": "00500", "pc11_town_village_id": "000001",
        "geometry": Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
    }], crs="EPSG:4326")
    pc11 = pd.DataFrame([{
        "pc11_state_id": "10", "pc11_district_id": "101",
        "pc11_subdistrict_id": "00500", "pc11_village_id": "000001",
        "shrid2": "11-10-101-00500-000001",
    }])
    ac = pd.DataFrame([{"shrid2": "11-10-999-00000-000000", "ac08_id": "2008-10-001",
                        "fragment_wt_con08_norm": 1.0}])
    lgd = [{"State Code": "10", "District Census 2011 Code": "999",
            "Subdistrict Census 2011 Code": "0", "Village Census 2011 Code": "0",
            "Gram Panchayat LGD Code": "1"}]
    assigned = SpatialIndex(villages, pc11, ac, lgd).assign(0.5, 0.5)
    assert assigned.status == au.RESOLVED
    assert assigned.constituencies == ()
    assert "no 2008 constituency row" in assigned.reason
    assert "no LGD gram panchayat row" in assigned.reason
