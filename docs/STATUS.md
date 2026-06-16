# Status — commoner-probe

Last recomputed: 2026-06-16
Branch: `feat/mca-csr-adapter`

## Integrity

- Full suite (`.venv/bin/python -m pytest`) -> **295 passed, 1 skipped**, no deselection. The schema-validation tests run clean; the earlier "39 skipped, 2 deselected" was an artifact of running the wrong interpreter (system `python3.13`), not the repo venv.
- Lint (`.venv/bin/ruff check .`) -> clean (one F401 unused-import in `examples/usage.py` fixed 2026-06-16).
- `git diff --check` -> clean.
- **Toolchain note:** the repo venv (`.venv`, Python 3.14.5) HAS `jsonschema` 4.26.0 and `ruff` 0.15.16. Always run tests/lint via `.venv/bin/...`, not a bare system python. (`pandas` is absent from the venv; nothing in the repo imports it.)

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

- Chhattisgarh/Jharkhand DMFT structured finance endpoints: unproven source discovery.

(Resolved 2026-06-16: `bd` installed via Homebrew 1.0.5 and dolt DB rebuilt from `issues.jsonl` — `bd ready`/`bd list` work, 43 issues all closed. Schema-test and ruff "blocks" were false — both tools live in `.venv`; see Integrity.)

## Commit Gap

Before closeout commit: maintain/PM files only. No push requested.
