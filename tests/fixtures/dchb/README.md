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
