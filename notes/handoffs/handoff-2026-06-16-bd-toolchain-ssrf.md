# Handoff — commoner-probe — 2026-06-16

## What Changed This Session

- Added `commoner-probe evidence dmft` to bundle Ministry of Mines/DMFT disclosure records with Sansad Q/A oversight records without flattening the two source families.
- Added bundled Sansad topic `mines_dmft_pmkkky` for Ministry of Mines DMFT/PMKKKY questions.
- Added `commoner-probe mines-dmft` Layer 0 acquisition for:
  - Ministry of Mines static DMFT CSVs under `https://mines.gov.in/webportal/assets/img/`
  - Odisha DMF static JSON and state report-page endpoints under `https://dmf.odisha.gov.in`
- Renamed the public source-family surface from the confusing `mom-dmft` to `mines-dmft`.
- Renamed local ignored data directory from `data/mom-dmft` to `data/mines-dmft` and corrected paths in its local `manifest.jsonl`.
- Added `manifest_mines_dmft` schema, `ManifestMinesDmftRecord`, and `Corpus.manifest_mines_dmft()`.
- Updated source-family notes and integration smoke docs.

## Verification

- `pytest tests/test_dmft_mines.py tests/test_evidence_dmft.py tests/test_init_topic_cli.py tests/test_docs_sync.py` -> 15 passed, 1 skipped.
- `python3.13 -m commoner_probe mines-dmft --out /tmp/mines-dmft-dry --sources mines-gov-in --dry-run` -> emitted four `MINES_DMFT` Ministry CSV manifest records.
- `python3.13 -m commoner_probe init-topic --name mines_dmft_pmkkky --out /tmp/mines_dmft_pmkkky.json --force` -> wrote bundled topic.
- `pytest -k 'not test_mca_csr_manifest_schema_is_bundled_and_validates_record and not test_mines_dmft_manifest_schema_is_bundled_and_validates_record'` -> 255 passed, 39 skipped, 2 deselected.
- `git diff --check` -> clean.

**Correction (2026-06-16):** the `jsonschema`/`ruff` "missing" notes above were a
wrong-interpreter artifact (system `python3.13`). The repo venv has both. Run via
`.venv/bin/python -m pytest` -> 295 passed, 1 skipped (no deselection); `.venv/bin/ruff
check .` -> clean. `bd` is now installed (Homebrew 1.0.5) with its dolt DB rebuilt
from `issues.jsonl`.

## Commits

- `10cdcde feat: add mines DMFT acquisition`
- `2cc9b23 feat: add DMFT evidence bundle`
- `d3d223f docs: map CSR and DMFT source contracts`

## What Is Next

- Run `commoner-probe mines-dmft --out data/mines-dmft --sources mines-gov-in,odisha` live to refresh the canonical ignored data corpus under the new path.
- Run the Sansad crawl with `mines_dmft_pmkkky`, then `extract-answers`, then `evidence dmft`.
- Add parsed record streams later: `dmft_financial_summary`, `dmft_sector_summary`, `dmft_project`, and `dmft_governance_document`.
- Continue source discovery for Chhattisgarh and Jharkhand structured DMFT finance endpoints.
- Build MCA CSR comparison utilities over the 10-year MCA corpus.
