# DCHB Town Release fixture — provenance

`town_release_1300_trimmed.xlsx` is the **real** Nagaland (state code 13) Town
Release, acquired 2026-07-31 from ORGI's NADA catalogue via
`commoner-probe nada --study DH_2011_1301_PART_A_DCHB_MON`, then trimmed to the
header row plus the first three towns. Nothing inside a kept row was edited:
the shared-string table, the column references and the cell types are the
source's own.

Trimmed rather than synthesised on purpose. A hand-built spreadsheet would test
the parser against the layout I *believe* the source has; this tests it against
the layout the source actually has — including the empty cells and the
`TK/P/L:` placeholder that sits in the State Name column of every data row.

## What the real file establishes

Verified across Maharashtra (`2700`) and Nagaland (`1300`), identical headers at
identical column references:

| ref | column |
|---|---|
| A / B | State Code / State Name |
| C / D | District Code / District Name |
| E / F | Sub District Code / Sub District Name |
| G / H | Town Code / Town Name |
| I / J | Total Households / Total Population of Town |
| OR / OS | Govt.-Public Library — Status A(1)/NA(2) / **Numbers** |
| OT / OU | Private-Public Library — Status / **Numbers** |
| OY / OZ | Govt.-Public Reading Room — Status / **Numbers** |
| PA / PB | Private-Public Reading Room — Status / **Numbers** |

Full-file totals, for anyone checking the parser against the source:
Nagaland 26 towns / 11 districts, 5 govt + 0 private libraries.
Maharashtra 535 towns / 35 districts, 436 govt + 952 private = 1,388 libraries,
reading rooms separate at 1,621.

## The rule this data exists to protect

These are **counts**. The rural Village Amenities equivalent is an
**availability flag** (A=1/NA=2) per village. Adding them produces the
widely-cited and wrong "~75,000 libraries" — because the rural figure counts
*villages that have a library*, not libraries. Reading rooms are likewise a
separate facility from libraries and are never added to them.

## `Town Statement-V_1101.xls`

The **real** Statement V for Sikkim's North District, extracted unmodified from
`DH_2011_1101-North_District.zip` inside ORGI NADA catalog 13990, acquired
2026-08-01. Genuine OLE2/BIFF8 binary, 8 rows × 23 columns, one town.

**The filename is kept exactly as the source publishes it, and that is
deliberate**: the sheet carries neither the state nor the district code, so
`Town Statement-V_<district>.xls` is the only in-band source of them. Renaming
this fixture to something tidier would throw away data the parser needs.

**Why Sikkim needs a second reader at all.** 34 of 35 states ship a state-level
`DH_2011_DCHB_Town_Release_<code>.xlsx`. Sikkim ships four per-district ZIPs of
`Town Statement-V_<district>.xls` instead — same data, older format.

**What it establishes.** Column 21 is `Public libraries`, column 22 is
`Reading rooms` — separate, as the corrected request spec says. And it carries
the cell grammar that spec warned about: a cell holds **either a count or the
nearest town and its distance**. Mangan (NP) reads `1.0` for libraries but
`GANGTOK(67)` for medical colleges — meaning none here, nearest 67 km away. An
integer parse silently drops exactly the towns that lack the facility, turning
"absent" into "unknown" or worse into a skipped row.

## `DH_2011_1101-North_District.zip`

The **real** ZIP ORGI serves for Sikkim's North District (NADA catalog 13990,
acquired 2026-08-01), trimmed to the two members the reader needs:
`Town Statement-V_1101.xls` and `Appendix_I_1101.xls`. Both are unmodified.

**Why the reader takes the ZIP and not the loose .xls.** The `1101` in the
filename is ORGI's DCHB ordinal — state code plus a district counter — and is
**not** the 2011 Census district code the rest of the corpus joins on. North
District's census code is **241**, and the only in-band place it appears is the
`Appendix_I` header cell: `District: North  District (241)`. A reader given the
loose Statement V file cannot know it, and a reader that copies `1101` writes a
key that silently fails to join. (Codex, PR #104.)
