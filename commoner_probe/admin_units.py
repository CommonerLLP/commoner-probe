# SPDX-License-Identifier: MIT
"""Index Indian administrative units, and resolve free-text labels against the index.

The index is built from three local extracts, and each one is a different piece of
software with a different vintage. Nothing here fetches anything.

* **LGD village-to-gram-panchayat extract** (``village_to_gp_lgd_codes.tab``,
  638,847 data rows, SHRUG redistribution of the Local Government Directory).
  It carries the current LGD district spelling beside the Census 2011 district code.
* **SHRUG location names** (``shrid_loc_names.tab``, 596,389 data rows). It carries
  the Census 2011 spelling for every ``shrid2``.
* **UDISE-to-Census district crosswalk** (``district_crosswalk_udise_to_pc11.csv``,
  782 rows). It carries the UDISE spelling and its own match-quality column.

``pc11_district_id`` is the key. SHRUG's ``shrid2`` is built on the Census 2011
hierarchy, so its third segment IS the all-India Census 2011 district id, and a
village-level join keys on shrid with no name matching at all. Measured 2026-08-17:
631 of 631 district segments in ``shrid_loc_names.tab`` are Census 2011 district ids.

A district label is not a key. One Andhra series produced ``Guntur`` and ``Guntoor``,
``Ysr Kadapa``, ``Ysr District`` and ``Kadapa``, ``Ananthapuramu`` and
``Ananthapuram``, ``Wesst Godavari``, ``Spsr Nellore District`` and ``Nellore``, and
``Prakasham``. Each variant split one district into two, and the analysis
under-reported its own coverage without a single error.

THE TRAP: A CONFIDENCE COLUMN A CONSUMER CAN IGNORE IS NOT A SAFEGUARD
=====================================================================
The UDISE crosswalk flags 192 of its 782 rows ``quality="unmatched"``, and 177 of
those 192 still carry a populated ``pc11_district_id``. Re-measured against the file
on 2026-08-17: the quality distribution is 530 exact, 32 strong, 28 weak, 192
unmatched, the unmatched similarities run 0.265 to 0.85, and **59 unmatched rows
carry id 550, Sri Potti Sriramulu Nellore** — the single most common id among them.

Merging those names mapped Anantapur and Y.S.R. to Nellore. The output looked
exactly like a correct one: a real code, a real name, the right state. So
:func:`build` accepts a crosswalk name only under ``exact`` or ``strong``, and
:meth:`DistrictIndex.resolve` returns :data:`WEAK_SOURCE` with the flag and the
similarity for a label whose only evidence is a rejected row. It never falls
through to the plausible neighbour.

The quality flag also passes rows that carry no code at all: 15 of the unmatched
rows have an empty ``pc11_district_id``, the Andaman islands among them. :attr:`DistrictIndex.crosswalk_rows_without_code` counts them, because a
row that names a district and maps it nowhere reads like a successful merge.

SECOND TRAP: ADMINISTRATIVE CODES ARE HISTORIC
=============================================
Andhra Pradesh reorganised 13 districts into 26 in 2022. A UDISE school code is
state(2) district(2) block(2) school(5), and the district digits record the district
that ISSUED the code. A school in Sri Satyasai still reads ``2822`` for its parent
Anantapur, while its block ``282263`` is filed under district ``2824`` SRI SATYASAI.
So :func:`parse_udise_code` names its district field ``district_of_issue``, and
:func:`district_of_today` joins on the 6-digit block code. A join on the district
prefix misassigns every school in a post-2022 district and reports nothing.

THIRD TRAP: A DISTRICT CODE OF 0
================================
The LGD extract carries rows with district code ``0``. Measured 2026-08-17: 55,290
such rows, plus 15,154 rows with a blank code or name. A build that keeps them
invents a district. :func:`build` drops them and reports the count.

STATE CODES DRIFT BETWEEN VINTAGES
==================================
The ``state`` argument of :meth:`DistrictIndex.resolve` filters on the state code the
index recorded, and the sources disagree where a state was created after 2011. LGD
files Leh and Kargil under state 37 Ladakh; ``shrid2`` files the same districts under
state 01 Jammu and Kashmir. Fifteen districts carry codes the two extracts disagree
about — all ten in Telangana, both in Ladakh, and the three former union territories.
:meth:`DistrictIndex.resolve` therefore answers :data:`STATE_MISMATCH`, naming the
code the index holds, rather than reporting the name absent.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "ACCEPTED_CROSSWALK_QUALITY",
    "AMBIGUOUS",
    "District",
    "DistrictIndex",
    "RESOLVED",
    "STATE_MISMATCH",
    "Resolution",
    "SOURCE_LGD",
    "SOURCE_SHRUG",
    "SOURCE_UDISE",
    "Shrid",
    "UNRESOLVED",
    "UdiseCode",
    "WEAK_SOURCE",
    "build",
    "district_of_today",
    "norm",
    "parse_shrid",
    "parse_udise_code",
]

SOURCE_LGD = "lgd_village_to_gp"
SOURCE_SHRUG = "shrug_shrid_loc_names"
SOURCE_UDISE = "udise_pc11_crosswalk"

ACCEPTED_CROSSWALK_QUALITY = frozenset({"exact", "strong"})

RESOLVED = "resolved"
UNRESOLVED = "unresolved"
AMBIGUOUS = "ambiguous"
WEAK_SOURCE = "weak_source"
#: The name is in the index, and the caller's state code excluded it. Distinct
#: from UNRESOLVED, because "no such name" and "not in that state" send a caller
#: to different fixes.
STATE_MISMATCH = "state_mismatch"


def norm(label: object) -> str:
    """Fold a label to a comparison key: ascii, lowercase, alphanumeric only."""
    text = unicodedata.normalize("NFKD", str(label or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", text.lower())


# Spelling drift no character comparison closes. Every entry was read in a real
# document. Each target is the normalised LGD spelling, because the index carries
# the LGD name: an earlier table folded toward common usage ("Ananthapuramu" ->
# "anantapur"), pointed the aliases away from the names they had to match, and
# resolved nothing.
ALIASES = {
    "guntoor": "guntur",
    "anantapur": "ananthapuramu",
    "ananthapur": "ananthapuramu",
    "anantapuramu": "ananthapuramu",
    "ananthapuram": "ananthapuramu",
    "ysrkadapa": "ysr",
    "ysrkadaapa": "ysr",
    "ysrdistrict": "ysr",
    "ysrkadapadistrict": "ysr",
    "kadapa": "ysr",
    "cuddapah": "ysr",
    "wesstgodavari": "westgodavari",
    "nellore": "sripottisriramulunellore",
    "spsrnellore": "sripottisriramulunellore",
    "spsrnelloredistrict": "sripottisriramulunellore",
    "prakasham": "prakasam",
    "vijayanagaram": "vizianagaram",
    "vishakhapatnam": "visakhapatnam",
}


@dataclass
class District:
    """One Census 2011 district, with every name variant and the source of each."""

    pc11_district_id: str
    name: str
    state_code: str
    variants: dict[str, str] = field(default_factory=dict)


@dataclass
class Resolution:
    """The outcome of one lookup. Falsy unless the status is :data:`RESOLVED`."""

    status: str
    pc11_district_id: str | None = None
    name: str | None = None
    state_code: str | None = None
    reason: str = ""

    def __bool__(self) -> bool:
        return self.status == RESOLVED


@dataclass
class Shrid:
    """The five segments of a SHRUG ``shrid2``."""

    census_year: str
    state_code: str
    pc11_district_id: str
    subdistrict_code: str
    place_code: str


@dataclass
class UdiseCode:
    """The four segments of an 11-digit UDISE school code.

    ``district_of_issue`` is the 4-digit state-and-district prefix as issued. It is
    not the district of today. Use :func:`district_of_today` with a block map.
    """

    state: str
    district_of_issue: str
    block: str
    school: str


@dataclass
class DistrictIndex:
    """District reference keyed on ``pc11_district_id``, with its own build counts."""

    districts: dict[str, District] = field(default_factory=dict)
    dropped_lgd_rows: int = 0
    rejected_crosswalk_rows: int = 0
    crosswalk_rows_without_code: int = 0
    #: Accepted crosswalk rows whose code the index does not hold. Without this
    #: they vanished, and a caller read the two counters above as proof that every
    #: accepted row merged.
    crosswalk_rows_for_absent_code: int = 0
    #: Accepted rows whose own pc11 name contradicts the name the index holds for
    #: that code. The quality flag passes them, and merging them attaches a label
    #: to a different district.
    contradicting_crosswalk_rows: int = 0
    #: Shrid rows whose `shrid2` would not parse. Counted rather than swallowed, so
    #: a renamed column cannot make this pass look like passing no extract.
    dropped_shrid_rows: int = 0
    #: District codes two states both claim. Zero in the 2026-08-17 extract.
    conflicting_lgd_codes: int = 0
    refused_labels: dict[str, str] = field(default_factory=dict)

    def resolve(self, label: str, state: str | None = None) -> Resolution:
        """Resolve a free-text district label to one Census 2011 district code.

        Matching is exact on the folded key, then on the alias table. Two candidate
        districts return :data:`AMBIGUOUS`, and a label whose only evidence is a
        crosswalk row its own source flagged returns :data:`WEAK_SOURCE`. A guess is
        never returned: a silently wrong district survives every downstream check.
        """
        key = norm(label)
        if not key:
            return Resolution(UNRESOLVED, reason="empty label")
        for candidate_key in (key, ALIASES.get(key)):
            if candidate_key is None:
                continue
            found = self._candidates(candidate_key, state)
            if len(found) == 1:
                d = found[0]
                return Resolution(RESOLVED, d.pc11_district_id, d.name, d.state_code)
            if len(found) > 1:
                codes = ", ".join(d.pc11_district_id for d in found)
                return Resolution(
                    AMBIGUOUS,
                    reason=f"{label!r} names {len(found)} districts ({codes}); pass a state code",
                )
        if key in self.refused_labels:
            return Resolution(WEAK_SOURCE, reason=self.refused_labels[key])
        # The name may be in the index and excluded by the STATE filter. The two
        # extracts disagree about the state code for 15 districts — all ten
        # Telangana ones, both in Ladakh, and the three former union territories —
        # so a caller passing the pc11 state code was told the name is absent.
        # That is a false statement about the index, and the caller has no way to
        # learn which code the index holds.
        if state is not None:
            elsewhere = self._candidates(key, None) or (
                self._candidates(ALIASES[key], None) if ALIASES.get(key) else [])
            if elsewhere:
                held = ", ".join(sorted({d.state_code for d in elsewhere}))
                return Resolution(
                    STATE_MISMATCH,
                    reason=(f"{label!r} is in the index under state code(s) {held}, not "
                            f"{state!r}. The two extracts disagree about the state code "
                            "for 15 districts. Resolve without a state code, or pass the "
                            "code the index holds."),
                )
        return Resolution(UNRESOLVED, reason=f"{label!r} matches no district name in the index")

    def _candidates(self, key: str, state: str | None) -> list[District]:
        want = _state_key(state)
        return [
            d for d in self.districts.values()
            if key in d.variants and (want is None or _state_key(d.state_code) == want)
        ]

    def to_dict(self) -> dict:
        return {
            "districts": {
                code: {
                    "pc11_district_id": d.pc11_district_id,
                    "name": d.name,
                    "state_code": d.state_code,
                    "variants": dict(d.variants),
                }
                for code, d in self.districts.items()
            },
            "dropped_lgd_rows": self.dropped_lgd_rows,
            "rejected_crosswalk_rows": self.rejected_crosswalk_rows,
            "crosswalk_rows_without_code": self.crosswalk_rows_without_code,
            "crosswalk_rows_for_absent_code": self.crosswalk_rows_for_absent_code,
            "contradicting_crosswalk_rows": self.contradicting_crosswalk_rows,
            "dropped_shrid_rows": self.dropped_shrid_rows,
            "conflicting_lgd_codes": self.conflicting_lgd_codes,
            "refused_labels": dict(self.refused_labels),
        }

    @classmethod
    def from_dict(cls, payload: dict) -> DistrictIndex:
        return cls(
            districts={code: District(**d) for code, d in payload["districts"].items()},
            dropped_lgd_rows=payload.get("dropped_lgd_rows", 0),
            rejected_crosswalk_rows=payload.get("rejected_crosswalk_rows", 0),
            crosswalk_rows_without_code=payload.get("crosswalk_rows_without_code", 0),
            crosswalk_rows_for_absent_code=payload.get("crosswalk_rows_for_absent_code", 0),
            contradicting_crosswalk_rows=payload.get("contradicting_crosswalk_rows", 0),
            dropped_shrid_rows=payload.get("dropped_shrid_rows", 0),
            conflicting_lgd_codes=payload.get("conflicting_lgd_codes", 0),
            refused_labels=dict(payload.get("refused_labels", {})),
        )


def _state_key(state: object) -> str | None:
    text = str(state or "").strip()
    if not text:
        return None
    return str(int(text)) if text.isdigit() else norm(text)


def _code(value: object) -> str:
    """One district code, normalised.

    These ``.tab`` files are dataframe exports, where a numeric column round-trips
    as ``553.0``. Passing that through made it its own district: the index then
    held both ``553`` and ``553.0``, split the variants across the two, and
    returned a string that is not a Census 2011 id and joins to nothing.
    """
    text = str(value or "").strip().strip('"')
    if text.isdigit():
        return str(int(text))
    if re.fullmatch(r"\d+\.0+", text):
        return str(int(float(text)))
    return text


def _cell(row: dict, column: str) -> str:
    return (row.get(column) or "").strip().strip('"')


def build(lgd_tab: str | Path, *, shrid_tab: str | Path | None = None,
          crosswalk_csv: str | Path | None = None) -> DistrictIndex:
    """Build the district index from the local extracts.

    ``lgd_tab`` is required and sets the canonical name and state code. ``shrid_tab``
    adds the Census 2011 spellings, and adds the districts the village extract omits:
    it maps villages to gram panchayats, so a wholly urban district has no row in it.
    Measured 2026-08-17: 607 districts from LGD, 32 more from SHRUG, 639 in total.
    ``crosswalk_csv`` adds UDISE spellings, under its quality gate.
    """
    index = DistrictIndex()
    _read_lgd(Path(lgd_tab), index)
    # An index of zero districts issues a per-label verdict about labels it cannot
    # hold. Rename one column in the extract and every later resolve() answers
    # "matches no district name in the index" — an absence claim from an empty
    # instrument, which is the failure this package exists to refuse.
    if not index.districts:
        raise ValueError(
            f"{lgd_tab} yielded no district at all, and {index.dropped_lgd_rows} row(s) "
            "were dropped. The column names have changed: this build needs "
            "'District Census 2011 Code', 'District Name' and 'State Code'. An empty "
            "index cannot report an absence.")
    if shrid_tab is not None:
        _read_shrid_names(Path(shrid_tab), index)
    if crosswalk_csv is not None:
        _read_crosswalk(Path(crosswalk_csv), index)
    return index


def _read_lgd(path: Path, index: DistrictIndex) -> None:
    if not path.exists():
        raise FileNotFoundError(f"{path} — the district index needs the LGD village extract")
    with path.open(encoding="utf-8", errors="ignore") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            code = _code(_cell(row, "District Census 2011 Code"))
            name = _cell(row, "District Name")
            if not code or code == "0" or not name:
                index.dropped_lgd_rows += 1
                continue
            state = _code(_cell(row, "State Code"))
            district = index.districts.get(code)
            if district is None:
                index.districts[code] = District(code, name, state)
                index.districts[code].variants[norm(name)] = SOURCE_LGD
                continue
            # A code claimed by two STATES is two districts, and filing the second
            # name as a variant of the first answers a query for one with the
            # other's id. Today's extract has no such code — 0 across 638,847 rows
            # — so this counts rather than raises, and a new vintage says so.
            if _state_key(state) != _state_key(district.state_code):
                index.conflicting_lgd_codes += 1
                index.refused_labels.setdefault(
                    norm(name),
                    f"district code {code} is claimed by state {district.state_code} "
                    f"({district.name}) and state {state} ({name}); the extract "
                    "cannot say which district this label names",
                )
                continue
            district.variants.setdefault(norm(name), SOURCE_LGD)


def _read_shrid_names(path: Path, index: DistrictIndex) -> None:
    if not path.exists():
        raise FileNotFoundError(f"{path} — pass no shrid_tab rather than a missing one")
    with path.open(encoding="utf-8", errors="ignore") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            try:
                parts = parse_shrid(_cell(row, "shrid2"))
            except ValueError:
                # Counted, not swallowed. A renamed `shrid2` column made this pass
                # contribute nothing and report nothing, so the result was
                # indistinguishable from passing no shrid extract at all.
                index.dropped_shrid_rows += 1
                continue
            name = _cell(row, "district_name")
            if not name:
                continue
            district = index.districts.setdefault(
                parts.pc11_district_id,
                District(parts.pc11_district_id, name, parts.state_code),
            )
            district.variants.setdefault(norm(name), SOURCE_SHRUG)


def _read_crosswalk(path: Path, index: DistrictIndex) -> None:
    if not path.exists():
        raise FileNotFoundError(f"{path} — pass no crosswalk_csv rather than a missing one")
    with path.open(encoding="utf-8", errors="ignore") as fh:
        for row in csv.DictReader(fh):
            label = _cell(row, "udise_district")
            code = _code(_cell(row, "pc11_district_id"))
            quality = _cell(row, "quality").lower()
            if quality not in ACCEPTED_CROSSWALK_QUALITY:
                index.rejected_crosswalk_rows += 1
                if label:
                    index.refused_labels.setdefault(
                        norm(label),
                        f"{SOURCE_UDISE} flagged {label!r} as {quality!r} "
                        f"at similarity {_cell(row, 'sim')} against district {code or 'none'}; "
                        "refusing to emit a mapping its own source could not make",
                    )
                continue
            if not code:
                index.crosswalk_rows_without_code += 1
                continue
            district = index.districts.get(code)
            if district is None:
                # An accepted row for a code the index does not hold used to vanish
                # with no counter moving, so a caller read
                # `rejected_crosswalk_rows=0` as "every accepted row merged" while
                # the label resolved UNRESOLVED.
                index.crosswalk_rows_for_absent_code += 1
                continue
            if not label:
                index.crosswalk_rows_without_code += 1
                continue
            pc11_name = _cell(row, "pc11_district_name")
            # `quality` is a verdict on a name comparison, not proof the code is
            # right. A row flagged `exact` whose own pc11 name is not a name the
            # index already carries for that code would attach its label to a
            # different district: a real code, a real name, the right state, the
            # wrong district. The gate cannot catch it, because the row passed it.
            if pc11_name and norm(pc11_name) not in district.variants:
                index.contradicting_crosswalk_rows += 1
                index.refused_labels.setdefault(
                    norm(label),
                    f"{SOURCE_UDISE} maps {label!r} to district {code}, calling it "
                    f"{pc11_name!r}, while the index holds {district.name!r} for that "
                    "code; refusing a mapping whose own name contradicts the index",
                )
                continue
            district.variants.setdefault(norm(label), SOURCE_UDISE)
            if pc11_name:
                district.variants.setdefault(norm(pc11_name), SOURCE_UDISE)


def parse_shrid(shrid: str) -> Shrid:
    """Split a ``shrid2`` into its segments, or raise ValueError.

    The third segment is the all-India Census 2011 district id, which is the key the
    index carries. It is returned unpadded, as the LGD extract writes it.
    """
    parts = str(shrid or "").strip().strip('"').split("-")
    if len(parts) != 5 or not parts[0] or not parts[1] or not parts[2]:
        raise ValueError(f"{shrid!r} is not a shrid2 of the form year-state-district-subdistrict-place")
    return Shrid(parts[0], _code(parts[1]), _code(parts[2]), parts[3], parts[4])


def parse_udise_code(code: str) -> UdiseCode:
    """Split an 11-digit UDISE school code into state, district-of-issue, block, school."""
    text = re.sub(r"\D", "", str(code or ""))
    if len(text) != 11:
        raise ValueError(f"{code!r} is not an 11-digit UDISE school code")
    return UdiseCode(text[:2], text[:4], text[:6], text[6:])


def district_of_today(school_code: str, block_districts: dict[str, str]) -> str | None:
    """The current district of a school, joined on its 6-digit block code.

    ``block_districts`` maps a 6-digit block code to the district code that holds the
    block today. Returns None when the block is absent, and never the district prefix
    of the school code: that prefix is the district of issue, and for a post-2022
    Andhra district it names the wrong district.
    """
    return block_districts.get(parse_udise_code(school_code).block)
