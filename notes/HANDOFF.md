# Handoff — commoner-probe — 2026-06-07

## What Changed This Session

- Added `commoner_probe.csr.mca` as the durable MCA CSR Layer 0 acquisition adapter.
- Added no-network tests in `tests/test_csr_mca.py` for:
  - CSRF token parsing
  - dry-run behavior without network initialization
  - mocked CSV download plus manifest JSONL writing
- Committed the adapter on branch `feat/mca-csr-adapter`:
  - `032ec83 feat: add MCA CSR acquisition adapter`
- Cleared the Codex `WORKING.md` row during maintain.

## What Is Next

- Verify the real MCA CSR export endpoint manually before any live run.
- Add a dedicated `manifest_mca_csr` schema only after the live endpoint and final record shape are proven.
- Consider exposing a CLI subcommand only after endpoint verification; for now the adapter is importable and tested but not CLI-wired.
- Next org-wide gate is in `partial-recall`: external adapter registry/plugin mechanism.

## Verification

- `pytest tests/test_csr_mca.py -v` -> 3 passed.
- `pytest tests -v` -> 246 passed, 39 skipped.
- `git diff --cached --check` was clean before commit.
