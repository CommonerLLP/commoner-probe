# State Of Brain — commoner-probe — 2026-06-16

## Active Frame

`commoner-probe` is Layer 0 acquisition for public disclosure sources. It can
bundle evidence across sources, but must keep unlike source families separate
and preserve provenance.

## Current Decisions

- MCA CSR acquisition is live and stable enough for schema/CLI:
  `commoner-probe mca-csr`.
- DMFT / PMKKKY acquisition should use the public name `mines-dmft`, not
  `mom-dmft`. The latter is too opaque and reads as "mom".
- `mines-dmft` covers Ministry of Mines national static CSV snapshots and
  Odisha DMF state/district source endpoints. It is raw acquisition, not parsed
  DMFT facts yet.
- `evidence dmft` is a query-result bundle: executive disclosure and Sansad
  oversight are shown side by side, never merged into a single pseudo-table.

## Source Boundaries

- Ministry of Mines CSVs are cumulative/current snapshots with
  `source_last_modified`; they are not FY-wise data.
- Odisha DMF exposes more detailed state/district JSON/report surfaces and is
  the first district-wise implementation target.
- Sansad Q/A answers are legislative oversight records dated by question/answer,
  not a replacement for ministry-published source data.
- MCA CSR compares CSR reporting/spending companies. It does not expose CSR
  consultants, vendors, NGOs, or implementing agencies.

## Open Tensions

- `jsonschema` is missing in the current shell, so schema-validation tests are
  partially deselected during broad verification.
- Ruff is not installed/importable in the current shell.
- Chhattisgarh and Jharkhand structured DMFT finance endpoints are still not
  proven; district NIC/S3WaaS pages may only support document/governance
  acquisition.

## Next Thought

The next session should run the live `mines-dmft` acquisition into
`data/mines-dmft`, run the Sansad `mines_dmft_pmkkky` crawl, and then generate a
real `evidence dmft` bundle. After that, add parsed DMFT record streams.
