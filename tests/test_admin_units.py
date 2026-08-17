import pytest

from commoner_probe import admin_units as au

LGD_HEADER = (
    "State Name\tState Code\tDistrict Name\tDistrict Census 2011 Code\t"
    "Subdistrict Name\tSubdistrict Census 2011 Code\tVillage Name\t"
    "Village Census 2011 Code\tVillage Census 2001 Code\t"
    "Gram Panchayat LGD Code\tGram Panchayat Name\n"
)

LGD_ROWS = [
    ("Andhra Pradesh", "28", "Ananthapuramu", "553", "Kalyandurg", "1", "A", "1"),
    ("Andhra Pradesh", "28", "Guntur", "590", "Tenali", "2", "B", "2"),
    ("Andhra Pradesh", "28", "Y.S.R.", "556", "Pulivendla", "3", "C", "3"),
    ("Andhra Pradesh", "28", "West Godavari", "588", "Eluru", "4", "D", "4"),
    ("Andhra Pradesh", "28", "Sri Potti Sriramulu Nellore", "550", "Kavali", "5", "E", "5"),
    ("Andhra Pradesh", "28", "Prakasam", "591", "Ongole", "6", "F", "6"),
    ("Himachal Pradesh", "2", "Hamirpur", "23", "Hamirpur", "7", "G", "7"),
    ("Uttar Pradesh", "9", "Hamirpur", "159", "Hamirpur", "8", "H", "8"),
    ("Bihar", "10", "", "0", "None", "9", "I", "9"),
]


def write_lgd(tmp_path, rows=None):
    lines = [LGD_HEADER]
    for state, scode, dist, dcode, sub, subcode, vill, vcode in (rows or LGD_ROWS):
        lines.append(
            f"{state}\t{scode}\t{dist}\t{dcode}\t{sub}\t{subcode}\t{vill}\t"
            f"{vcode}\t{vcode}\t100{vcode}\tGP{vcode}\n"
        )
    p = tmp_path / "village_to_gp_lgd_codes.tab"
    p.write_text("".join(lines), encoding="utf-8")
    return p


CROSSWALK_HEADER = (
    "udise_state,udise_district,cl9_enrol,dropout_9_10_pct,pc11_state_id,"
    "pc11_district_id,pc11_district_name,sim,quality\n"
)

# Verbatim shape of the real Andhra rows: every one carries pc11 550, Sri Potti
# Sriramulu Nellore, and every one is flagged unmatched.
CROSSWALK_ROWS = [
    ("ANDHRA PRADESH", "ANANTAPUR", "28", "550", "sri potti sriramulu nellore", "0.447", "unmatched"),
    ("ANDHRA PRADESH", "KADAPA", "28", "550", "sri potti sriramulu nellore", "0.302", "unmatched"),
    ("ANDHRA PRADESH", "ANAKAPALLI", "28", "550", "sri potti sriramulu nellore", "0.433", "unmatched"),
    ("ANDHRA PRADESH", "PALNADU", "28", "550", "sri potti sriramulu nellore", "0.513", "unmatched"),
    ("ANDHRA PRADESH", "PRAKASAM", "28", "591", "prakasam", "1.0", "exact"),
    ("UTTAR PRADESH", "HAMIRPUR UP", "9", "159", "hamirpur", "0.9", "strong"),
    ("HIMACHAL PRADESH", "BILASPUR HP", "2", "23", "hamirpur", "0.6", "weak"),
]


def write_crosswalk(tmp_path, rows=None):
    lines = [CROSSWALK_HEADER]
    for state, dist, pstate, pdist, pname, sim, quality in (rows or CROSSWALK_ROWS):
        lines.append(f"{state},{dist},100,3.1,{pstate},{pdist},{pname},{sim},{quality}\n")
    p = tmp_path / "district_crosswalk_udise_to_pc11.csv"
    p.write_text("".join(lines), encoding="utf-8")
    return p


SHRID_HEADER = (
    "shrid2\tstate_name\tdistrict_name\tsubdistrict_name\ttown_name\t"
    "village_name\tplace_name\n"
)

SHRID_ROWS = [
    ("11-28-553-04001-000001", "andhra pradesh", "anantapur", "kalyandurg", "", "a", "a"),
    ("11-28-550-04002-000002", "andhra pradesh", "nellore", "kavali", "", "e", "e"),
    ("11-07-093-00500-000000", "nct of delhi", "north west", "narela", "delhi", "", "delhi"),
]


def write_shrid(tmp_path, rows=None):
    lines = [SHRID_HEADER]
    for shrid, state, dist, sub, town, vill, place in (rows or SHRID_ROWS):
        lines.append(f'"{shrid}"\t"{state}"\t"{dist}"\t"{sub}"\t"{town}"\t"{vill}"\t"{place}"\n')
    p = tmp_path / "shrid_loc_names.tab"
    p.write_text("".join(lines), encoding="utf-8")
    return p


def test_lgd_district_code_zero_is_dropped(tmp_path):
    """The LGD extract carries rows with district code 0. A build that keeps one invents a district."""
    index = au.build(write_lgd(tmp_path))
    assert "0" not in index.districts
    assert index.dropped_lgd_rows == 1
    assert len(index.districts) == 8


def test_unmatched_crosswalk_row_does_not_lend_its_name_to_district_550(tmp_path):
    """All 26 real Andhra crosswalk rows carry id 550 under quality=unmatched.

    Merging those names mapped Anantapur and Y.S.R. to Sri Potti Sriramulu Nellore,
    and the wrong answer carried a real code, a real name and the right state.
    """
    index = au.build(write_lgd(tmp_path), crosswalk_csv=write_crosswalk(tmp_path))
    nellore = index.districts["550"]
    assert "anantapur" not in nellore.variants
    assert "kadapa" not in nellore.variants
    assert index.rejected_crosswalk_rows == 5


def test_resolve_names_the_weak_source_instead_of_a_plausible_neighbour(tmp_path):
    """ANAKAPALLI is a post-2022 district with no Census 2011 code.

    The crosswalk answers 550 for it at similarity 0.433 and flags the row unmatched.
    The resolver must refuse and say which source flagged it, not return Nellore.
    """
    index = au.build(write_lgd(tmp_path), crosswalk_csv=write_crosswalk(tmp_path))
    got = index.resolve("Anakapalli")
    assert got.status == au.WEAK_SOURCE
    assert got.pc11_district_id is None
    assert not got
    assert "unmatched" in got.reason
    assert "0.433" in got.reason
    assert au.SOURCE_UDISE in got.reason


def test_accepted_crosswalk_row_lends_its_name(tmp_path):
    index = au.build(write_lgd(tmp_path), crosswalk_csv=write_crosswalk(tmp_path))
    assert index.resolve("HAMIRPUR UP", state="9").pc11_district_id == "159"
    assert index.districts["159"].variants["hamirpurup"] == au.SOURCE_UDISE


def test_crosswalk_row_of_quality_exact_with_no_code_is_counted(tmp_path):
    """14 real rows are flagged exact and carry an empty pc11_district_id.

    The Andaman islands are among them. A silent skip reads like a successful merge.
    """
    rows = list(CROSSWALK_ROWS) + [
        ("ANDAMAN & NICOBAR ISLANDS", "ANDAMANS", "", "", "andamans", "1.0", "exact"),
    ]
    index = au.build(write_lgd(tmp_path), crosswalk_csv=write_crosswalk(tmp_path, rows))
    assert index.crosswalk_rows_without_code == 1
    assert index.resolve("Andamans").status == au.UNRESOLVED


def test_thirteen_andhra_labels_resolve(tmp_path):
    """The thirteen labels one Andhra consolidation series produced, all real."""
    index = au.build(write_lgd(tmp_path), crosswalk_csv=write_crosswalk(tmp_path))
    expected = {
        "Guntur": "590", "Guntoor": "590",
        "Ysr Kadapa": "556", "Ysr District": "556", "Kadapa": "556",
        "Ananthapuramu": "553", "Ananthapuram": "553",
        "Wesst Godavari": "588",
        "Spsr Nellore District": "550", "Nellore": "550",
        "Prakasham": "591",
    }
    got = {label: index.resolve(label, state="28").pc11_district_id
           for label in expected}
    assert got == expected


def test_ambiguous_label_without_a_state_is_not_guessed(tmp_path):
    """Hamirpur is a district in both Himachal Pradesh and Uttar Pradesh."""
    index = au.build(write_lgd(tmp_path))
    got = index.resolve("Hamirpur")
    assert got.status == au.AMBIGUOUS
    assert got.pc11_district_id is None
    assert "23" in got.reason and "159" in got.reason
    assert index.resolve("Hamirpur", state="9").pc11_district_id == "159"


def test_unknown_label_is_unresolved_not_a_nearest_match(tmp_path):
    index = au.build(write_lgd(tmp_path))
    got = index.resolve("Gunturia")
    assert got.status == au.UNRESOLVED
    assert got.pc11_district_id is None


def test_empty_label_is_unresolved(tmp_path):
    index = au.build(write_lgd(tmp_path))
    assert index.resolve("   ").status == au.UNRESOLVED


def test_variant_carries_the_source_it_came_from(tmp_path):
    index = au.build(write_lgd(tmp_path), shrid_tab=write_shrid(tmp_path))
    variants = index.districts["553"].variants
    assert variants["ananthapuramu"] == au.SOURCE_LGD
    assert variants["anantapur"] == au.SOURCE_SHRUG


def test_shrug_adds_districts_the_village_extract_omits(tmp_path):
    """The LGD extract maps villages to gram panchayats, so a wholly urban district has no row."""
    index = au.build(write_lgd(tmp_path), shrid_tab=write_shrid(tmp_path))
    assert index.districts["93"].name == "north west"
    assert index.districts["93"].state_code == "7"
    assert index.districts["93"].variants["northwest"] == au.SOURCE_SHRUG


def test_build_raises_when_the_lgd_extract_is_absent(tmp_path):
    with pytest.raises(FileNotFoundError):
        au.build(tmp_path / "nothing.tab")


def test_index_round_trips_through_dict(tmp_path):
    index = au.build(write_lgd(tmp_path), crosswalk_csv=write_crosswalk(tmp_path))
    again = au.DistrictIndex.from_dict(index.to_dict())
    assert again.districts == index.districts
    assert again.resolve("Anakapalli").status == au.WEAK_SOURCE
    assert again.dropped_lgd_rows == index.dropped_lgd_rows


def test_shrid_district_segment_is_the_pc11_district_id():
    """shrid2 is 'pc11-state-district-subdistrict-place'; the district segment is the all-India id."""
    parts = au.parse_shrid("11-28-553-04001-000001")
    assert parts.census_year == "11"
    assert parts.state_code == "28"
    assert parts.pc11_district_id == "553"
    assert parts.subdistrict_code == "04001"
    assert parts.place_code == "000001"


def test_parse_shrid_refuses_a_malformed_id():
    with pytest.raises(ValueError):
        au.parse_shrid("28-553-04001")


def test_district_of_today_uses_the_block_code_not_the_district_prefix():
    """A school in Sri Satyasai still carries 2822 for its parent Anantapur.

    Block 282263 is filed under district 2824 SRI SATYASAI. A join on the district
    prefix misassigns every school in a post-2022 district, and reports no error.
    """
    blocks = {"282263": "2824"}
    code = au.parse_udise_code("28226300123")
    assert code.district_of_issue == "2822"
    assert code.block == "282263"
    assert au.district_of_today("28226300123", blocks) == "2824"


def test_district_of_today_returns_none_rather_than_the_prefix():
    assert au.district_of_today("28226300123", {}) is None


def test_parse_udise_code_refuses_a_short_code():
    with pytest.raises(ValueError):
        au.parse_udise_code("2822630012")


# --- review findings, 2026-08-17 -------------------------------------------


def test_a_name_excluded_by_the_state_filter_is_not_reported_absent(tmp_path):
    """The two extracts disagree about the state code for 15 districts, so a
    caller passing the pc11 code was told the name is absent — a false statement
    about an index that holds it."""
    index = au.DistrictIndex(districts={
        "532": au.District("532", "Adilabad", "36", {"adilabad": au.SOURCE_LGD})})
    out = index.resolve("Adilabad", state="28")
    assert out.status == au.STATE_MISMATCH
    assert "36" in out.reason


def test_a_crosswalk_row_whose_own_name_contradicts_the_index_is_refused(tmp_path):
    """quality is a verdict on a name comparison, not proof the code is right. An
    `exact` row attached its label to a district of another name: a real code, a
    real name, the right state, the wrong district."""
    index = au.DistrictIndex(districts={
        "553": au.District("553", "Ananthapuramu", "28",
                           {"ananthapuramu": au.SOURCE_LGD})})
    au._read_crosswalk(
        write_crosswalk(tmp_path, [("AP", "PRAKASAM", "28", "553", "prakasam",
                                    "1.0", "exact")]), index)
    assert index.contradicting_crosswalk_rows == 1
    assert index.resolve("Prakasam", state="28").status != au.RESOLVED


def test_an_accepted_row_for_a_code_the_index_lacks_is_counted(tmp_path):
    """It vanished with no counter moving, so a caller read the other counters as
    proof that every accepted row merged."""
    index = au.DistrictIndex(districts={
        "553": au.District("553", "Ananthapuramu", "28", {"ananthapuramu": "lgd"})})
    au._read_crosswalk(
        write_crosswalk(tmp_path, [("DL", "NEW DELHI", "07", "93", "new delhi",
                                    "1.0", "exact")]), index)
    assert index.crosswalk_rows_for_absent_code == 1


def test_a_float_formatted_code_is_not_a_second_district():
    """These .tab files are dataframe exports, where a numeric column round-trips
    as 553.0 — a string that is not a Census id and joins to nothing."""
    assert au._code("553.0") == "553"
    assert au._code("553") == "553"


def test_a_code_claimed_by_two_states_is_counted_not_absorbed(tmp_path):
    """setdefault kept the first row and filed the second district's name as a
    variant of it, so a query for one returned the other's id."""
    path = tmp_path / "lgd.tab"
    path.write_text(
        "District Census 2011 Code\tDistrict Name\tState Code\n"
        "23\tHamirpur\t2\n23\tSitapur\t9\n", encoding="utf-8")
    index = au.DistrictIndex()
    au._read_lgd(path, index)
    assert index.conflicting_lgd_codes == 1
    assert index.resolve("Sitapur").status != au.RESOLVED


def test_an_extract_that_yields_no_district_raises(tmp_path):
    """An empty index issued a per-label verdict about labels it cannot hold."""
    path = tmp_path / "lgd.tab"
    path.write_text("Wrong Column\tDistrict Name\tState Code\n1\tGuntur\t28\n",
                    encoding="utf-8")
    with pytest.raises(ValueError, match="no district at all"):
        au.build(path)


def test_an_unparsable_shrid_row_is_counted(tmp_path):
    """A renamed shrid2 column made the pass contribute nothing and report
    nothing, which is indistinguishable from passing no extract."""
    lgd = tmp_path / "lgd.tab"
    lgd.write_text("District Census 2011 Code\tDistrict Name\tState Code\n"
                   "553\tAnanthapuramu\t28\n", encoding="utf-8")
    shrid = tmp_path / "shrid.tab"
    shrid.write_text("shrid2\tdistrict_name\nnot-a-shrid\tAnanthapuramu\n",
                     encoding="utf-8")
    index = au.build(lgd, shrid_tab=shrid)
    assert index.dropped_shrid_rows == 1
