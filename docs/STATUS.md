# Status — commoner-probe

Last recomputed: 2026-06-16
Branch: `feat/mca-csr-adapter`

## Integrity

- Focused tests: `pytest tests/test_dmft_mines.py tests/test_evidence_dmft.py tests/test_init_topic_cli.py tests/test_docs_sync.py` -> 15 passed, 1 skipped.
- Broad tests: `pytest -k 'not test_mca_csr_manifest_schema_is_bundled_and_validates_record and not test_mines_dmft_manifest_schema_is_bundled_and_validates_record'` -> 255 passed, 39 skipped, 2 deselected.
- `git diff --check` -> clean.
- Caveat: full schema-validation tests need `jsonschema`; ruff is not installed/importable in this shell.

## Units

| Unit | Count |
|---|---:|
| JSON schemas | 19 |
| CLI parser entries | 12 |
| Python source files | 28 |
| Test files | 25 |

## Current Gate

Layer 0 acquisition is proven for MCA CSR and Mines DMFT. The next gate is evidence/comparison work over refreshed local corpora.

## Source Families

| Source family | Stage | Notes |
|---|---|---|
| `mca-csr` | schema + corpus stream + CLI | Build comparison utilities over the 10-year MCA CDM CSR corpus. MCA data compares reporting/spending companies, not CSR consultants or implementing agencies. |
| `mines-dmft` | raw acquisition + schema + corpus stream + CLI | Run live refresh into `data/mines-dmft`, then pair with Sansad Q/A using `evidence dmft`. Ministry CSVs are current cumulative snapshots timestamped by `Last-Modified`, not FY-wise files. |
| `mof-dpe-csr` | source contract noted, adapter pending | DPE `/cms/wp-json` proves document disclosure only; spend/project comparison remains blocked until CSRMS or Public Enterprises Survey fields are proven. |

## Blocked

- `bd` workflow: `bd prime` fails because `bd` is not on PATH.
- Full schema tests: blocked on missing `jsonschema`.
- Ruff gate: blocked on missing/import-unavailable ruff.
- Chhattisgarh/Jharkhand DMFT structured finance endpoints: unproven source discovery.

## Commit Gap

Before closeout commit: maintain/PM files only. No push requested.
