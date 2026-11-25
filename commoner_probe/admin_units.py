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

The quality flag also passes rows that carry no code at all. Measured 2026-08-17:
**32 of the 782 rows have an empty ``pc11_district_id``** — 10 flagged exact, 4
strong, 3 weak and 15 unmatched, the Andaman islands among them.
:attr:`DistrictIndex.crosswalk_rows_without_code` counts every one of them,
including the rejected ones, because a row that names a district and maps it
nowhere reads like a successful merge. :attr:`DistrictIndex.crosswalk_rows_without_code` counts them, because a
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
import difflib
import math
import re
import unicodedata
from dataclasses import dataclass, field, replace
from pathlib import Path

__all__ = [
    "ACCEPTED_CROSSWALK_QUALITY",
    "AMBIGUOUS",
    "District",
    "DistrictIndex",
    "Block",
    "BlockIndex",
    "Subdistrict",
    "Habitation",
    "HabitationIndex",
    "PlaceResolution",
    "LinkageScore",
    "LinkageModel",
    "fit_linkage",
    "linkage_precision",
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
    "build_blocks",
    "build_habitations",
    "read_habitations",
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
            # Counted before the quality gate, because a row can be both rejected
            # AND code-less: 15 of the unmatched rows carry no district id, and the
            # gate's `continue` hid every one of them from this counter.
            if not code:
                index.crosswalk_rows_without_code += 1
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


DISTRICT_ALIASES = {
    "saran": "chaprasaran",
    "chapra": "chaprasaran",
    "kaimur": "kaimurbhabhua",
    "bhabhua": "kaimurbhabhua",
}


def _district_key(label: str) -> str:
    key = norm(label)
    return DISTRICT_ALIASES.get(key, key)


@dataclass(frozen=True)
class Block:
    block_id: str
    name: str
    district_id: str
    district_name: str
    census_subdistrict_code: str | None = None
    state_id: str | None = None
    state_name: str | None = None


@dataclass(frozen=True)
class Subdistrict:
    code: str
    name: str
    district_name: str


@dataclass(frozen=True)
class Habitation:
    hab_id: str
    name: str
    block_id: str
    district_id: str
    population: int | None = None
    lon: float | None = None
    lat: float | None = None


@dataclass(frozen=True)
class PlaceResolution:
    status: str
    candidates: tuple[Block | Subdistrict | Habitation, ...] = ()
    grade: str = "none"
    score: float = 0.0

    def __bool__(self) -> bool:
        return self.status == RESOLVED


@dataclass(frozen=True)
class LinkageScore:
    offered: int
    resolved: int
    correct: int
    precision: float | None

    @property
    def error_rate(self) -> float | None:
        return 1 - self.precision if self.precision is not None else None


def _name_agreement(left: str, right: str) -> str:
    left_key, right_key = norm(left), norm(right)
    if left_key == right_key:
        return "exact"
    if difflib.SequenceMatcher(None, left_key, right_key).ratio() >= 0.9:
        return "near"
    return "weak"


@dataclass(frozen=True)
class LinkageModel:
    """Name-agreement likelihood ratios fitted to labelled match and nonmatch pairs."""

    log_ratios: dict[str, float]

    def score(self, left: str, right: str) -> float:
        return self.log_ratios[_name_agreement(left, right)]

    def resolve(self, index: HabitationIndex, label: str, block_id: str,
                *, min_log_ratio: float = 0.0) -> PlaceResolution:
        pool = index._by_block.get(str(block_id), ())
        if not norm(label) or not pool:
            return PlaceResolution(UNRESOLVED)
        scored = [(self.score(label, h.name), h) for h in pool]
        maximum = max(score for score, _ in scored)
        best = tuple(h for score, h in scored if score == maximum)
        if maximum < min_log_ratio:
            return PlaceResolution(UNRESOLVED, best, "clerical_review", maximum)
        return PlaceResolution(RESOLVED if len(best) == 1 else AMBIGUOUS,
                               best, "fellegi_sunter", maximum)


def fit_linkage(labelled_pairs: list[tuple[str, str, bool]]) -> LinkageModel:
    """Fit smoothed m/u agreement rates from independently labelled pairs."""
    levels = ("exact", "near", "weak")
    matches = {level: 1 for level in levels}
    nonmatches = {level: 1 for level in levels}
    positives = negatives = 0
    for left, right, same_place in labelled_pairs:
        level = _name_agreement(left, right)
        if same_place:
            matches[level] += 1
            positives += 1
        else:
            nonmatches[level] += 1
            negatives += 1
    if not positives or not negatives:
        raise ValueError("linkage calibration needs matches and nonmatches")
    return LinkageModel({
        level: math.log((matches[level] / (positives + len(levels)))
                        / (nonmatches[level] / (negatives + len(levels))))
        for level in levels
    })


@dataclass
class BlockIndex:
    blocks: dict[str, Block]
    subdistricts: dict[tuple[str, str], Subdistrict] = field(default_factory=dict)

    def resolve(self, label: str, district: str, *,
                state: str | None = None) -> PlaceResolution:
        """Resolve a block name inside a district.

        The PMGSY master is national, and six district names occur in two
        states (Aurangabad, Balrampur, Bijapur, Bilaspur, Hamirpur,
        Pratapgarh). Pass ``state`` (a PMGSY state id or name) to keep the
        other state's blocks out. Without it, a shared name is AMBIGUOUS.
        The Census fallback does not use ``state``.
        """
        district_keys = {norm(district), _district_key(district)}
        key = norm(label)
        if not key:
            return PlaceResolution(UNRESOLVED)
        state_key = norm(state) if state is not None else None
        blocks = tuple(b for b in self.blocks.values()
                       if district_keys & {norm(b.district_id), norm(b.district_name)}
                       and norm(b.name) == key
                       and (state_key is None
                            or state_key in {norm(b.state_id or ""), norm(b.state_name or "")}))
        if blocks:
            return PlaceResolution(RESOLVED if len(blocks) == 1 else AMBIGUOUS,
                                   blocks, "exact_both" if len(blocks) == 1
                                   and blocks[0].census_subdistrict_code else "exact_pmgsy", 1.0)
        census = tuple(s for s in self.subdistricts.values()
                       if _district_key(s.district_name) in district_keys
                       and norm(s.name) == key)
        if census:
            return PlaceResolution(RESOLVED if len(census) == 1 else AMBIGUOUS,
                                   census, "exact_census", 1.0)
        return PlaceResolution(UNRESOLVED)


@dataclass
class HabitationIndex:
    habitations: dict[str, Habitation]
    _by_block: dict[str, tuple[Habitation, ...]] = field(init=False, repr=False)
    _keys: dict[str, str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        by_block = {}
        for habitation in self.habitations.values():
            by_block.setdefault(habitation.block_id, []).append(habitation)
        self._by_block = {block: tuple(items) for block, items in by_block.items()}
        self._keys = {h: norm(h.name) for h in self.habitations.values()}

    def resolve(self, label: str, block_id: str, *, min_score: float = 0.9) -> PlaceResolution:
        if not 0 <= min_score <= 1:
            raise ValueError("min_score must be between 0 and 1")
        key = norm(label)
        if not key:
            return PlaceResolution(UNRESOLVED)
        pool = self._by_block.get(str(block_id), ())
        exact = tuple(h for h in pool if self._keys[h] == key)
        if exact:
            return PlaceResolution(RESOLVED if len(exact) == 1 else AMBIGUOUS,
                                   exact, "exact", 1.0)
        contained = tuple(h for h in pool if len(self._keys[h]) >= 6
                          and self._keys[h] in key)
        if contained:
            longest = max(len(self._keys[h]) for h in contained)
            best = tuple(h for h in contained if len(self._keys[h]) == longest)
            return PlaceResolution(RESOLVED if len(best) == 1 else AMBIGUOUS,
                                   best, "contained", 1.0)
        scored = [(difflib.SequenceMatcher(None, key, self._keys[h]).ratio(), h)
                  for h in pool if self._keys[h]]
        if not scored:
            return PlaceResolution(UNRESOLVED)
        maximum = max(score for score, _ in scored)
        if maximum < min_score:
            best = tuple(h for score, h in scored if score == maximum)
            return PlaceResolution(UNRESOLVED, best, "below_threshold", maximum)
        best = tuple(h for score, h in scored if score == maximum)
        return PlaceResolution(RESOLVED if len(best) == 1 else AMBIGUOUS,
                               best, "similarity", maximum)


def build_blocks(master_xls: str | Path, *, shrid_tab: str | Path | None = None) -> BlockIndex:
    """Read PMGSY blocks and attach unique Census subdistrict matches when present."""
    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError("build_blocks needs commoner-probe[pandas,xls]") from exc
    rows = pd.read_excel(master_xls)
    required = {"DISTRICT_ID", "DISTRICT_NAME", "BLOCK_ID", "BLOCK_NAME"}
    if not required.issubset(rows.columns):
        raise ValueError(f"PMGSY master lacks columns: {sorted(required - set(rows.columns))}")
    blocks = {}
    for row in rows.to_dict("records"):
        block_id = _code(row["BLOCK_ID"])
        if (not block_id or block_id == "nan" or pd.isna(row["BLOCK_NAME"])
                or pd.isna(row["DISTRICT_NAME"])):
            continue
        block = Block(block_id, str(row["BLOCK_NAME"]),
                      _code(row["DISTRICT_ID"]), str(row["DISTRICT_NAME"]),
                      state_id=_code(row["STATE_ID"]) if "STATE_ID" in row else None,
                      state_name=str(row["STATE_NAME"]) if "STATE_NAME" in row else None)
        if block_id in blocks and blocks[block_id] != block:
            raise ValueError(f"PMGSY block id {block_id} names different blocks")
        blocks[block_id] = block
    if shrid_tab is not None:
        census = {}
        subdistricts = {}
        tab_has_state = False
        with Path(shrid_tab).open(encoding="utf-8", errors="ignore") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                try:
                    parts = parse_shrid(_cell(row, "shrid2"))
                except ValueError:
                    continue
                district = _cell(row, "district_name")
                name = _cell(row, "subdistrict_name")
                state = norm(_cell(row, "state_name"))
                tab_has_state = tab_has_state or bool(state)
                key = (state, _district_key(district), norm(name))
                census.setdefault(key, set()).add(parts.subdistrict_code)
                subdistricts.setdefault((_district_key(district), parts.subdistrict_code),
                                        Subdistrict(parts.subdistrict_code, name, district))
        # A district name can occur in two states. Without state names in the
        # extract, a block in a shared-name district gets no Census code.
        district_states = {}
        for block in blocks.values():
            district_states.setdefault(_district_key(block.district_name), set()).add(
                norm(block.state_name or block.state_id or ""))
        for block_id, block in tuple(blocks.items()):
            district = _district_key(block.district_name)
            if tab_has_state:
                state = norm(block.state_name or "")
            elif len(district_states[district]) > 1:
                continue
            else:
                state = ""
            codes = census.get((state, district, norm(block.name)), set())
            if len(codes) == 1:
                blocks[block_id] = replace(block, census_subdistrict_code=next(iter(codes)))
    else:
        subdistricts = {}
    if not blocks:
        raise ValueError("PMGSY master yielded no blocks")
    return BlockIndex(blocks, subdistricts)


def build_habitations(rows: list[dict]) -> HabitationIndex:
    """Index PMGSY habitation rows; callers can read a shapefile with GeoPandas."""
    required = {"HAB_ID", "HAB_NAME", "BLOCK_ID", "DISTRICT_I"}
    habitations = {}
    for row in rows:
        if not required.issubset(row):
            raise ValueError(f"PMGSY habitation lacks columns: {sorted(required - set(row))}")
        hab_id = _code(row["HAB_ID"])
        if not hab_id:
            continue
        geometry = row.get("geometry")
        population = row.get("TOT_POPULA")
        habitation = Habitation(
            hab_id, str(row["HAB_NAME"]), _code(row["BLOCK_ID"]),
            _code(row["DISTRICT_I"]),
            int(population) if population not in (None, "")
            and not (isinstance(population, float) and math.isnan(population)) else None,
            float(geometry.x) if geometry is not None else None,
            float(geometry.y) if geometry is not None else None,
        )
        feature_key = f"{hab_id}:{habitation.block_id}:{habitation.lon}:{habitation.lat}"
        if feature_key in habitations and habitations[feature_key] != habitation:
            raise ValueError(f"PMGSY habitation feature {feature_key} has conflicting fields")
        habitations[feature_key] = habitation
    if not habitations:
        raise ValueError("PMGSY habitation source yielded no rows")
    return HabitationIndex(habitations)


def read_habitations(shapefile: str | Path) -> HabitationIndex:
    """Read a PMGSY habitation shapefile with the optional geo dependency."""
    try:
        import geopandas as gpd
    except ImportError as exc:
        raise ImportError("read_habitations needs commoner-probe[geo]") from exc
    frame = gpd.read_file(shapefile)
    if frame.crs is None:
        raise ValueError("PMGSY habitation source has no coordinate reference system")
    return build_habitations(frame.to_crs("EPSG:4326").to_dict("records"))


def linkage_precision(index: HabitationIndex,
                      labelled: list[tuple[str, str, str | None]], *,
                      model: LinkageModel | None = None) -> LinkageScore:
    """Measure precision against labelled (name, block_id, true_hab_id) rows."""
    correct = resolved = 0
    for name, block_id, true_id in labelled:
        match = model.resolve(index, name, block_id) if model else index.resolve(name, block_id)
        if match:
            resolved += 1
            correct += match.candidates[0].hab_id == true_id
    return LinkageScore(len(labelled), resolved, correct,
                        correct / resolved if resolved else None)
