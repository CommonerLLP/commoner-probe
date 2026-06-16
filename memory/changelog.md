# Changelog

## 2026-06-16 · toolchain-repair-and-ssrf-fix · create/edit

- `commoner_probe/url_safety.py` — removed `WHITELISTED_DOMAINS` SSRF bypass; normalize hostname before resolution (commit `e440f46`, pushed).
- `tests/test_url_safety.py` — new; 10 mocked-`getaddrinfo` SSRF-policy tests incl. no-bypass regression.
- `examples/usage.py` — removed unused `pathlib.Path` import (ruff F401).
- `docs/STATUS.md` — corrected false jsonschema/ruff-missing caveat; cleared stale Blocked entries (commit `3a827ff`, pushed).
- `notes/STATE_OF_BRAIN.md`, `notes/HANDOFF.md` — same correction; then rewritten at session close (prior versions archived under `notes/handoffs/` and `notes/state-of-brains/`).
- `.beads/*`, `.codex/`, `AGENTS.md`, `CLAUDE.md`, `.agents/skills/beads/` — auto-committed by `bd init` (`267014f`); kept as-is (multi-agent fleet wiring).
- `../_org/mistakes.md` — logged the "called multi-agent surface files cruft" misjudgment.

## 2026-06-16 · mines-dmft-acquisition · create/edit

- `commoner_probe/dmft/__init__.py` — created DMFT acquisition package.
- `commoner_probe/dmft/mines.py` — added Ministry of Mines/Odisha DMFT raw source-file acquisition.
- `commoner_probe/schemas/manifest_mines_dmft.schema.json` — added schema for `mines_dmft_source_file`.
- `commoner_probe/records.py` — added `ManifestMinesDmftRecord`.
- `commoner_probe/corpus.py` — added `Corpus.manifest_mines_dmft()`.
- `commoner_probe/validate.py` — routed `mines_dmft_source_file` to `manifest_mines_dmft`.
- `commoner_probe/cli.py` — added `commoner-probe mines-dmft` and renamed DMFT evidence input to `--mines-dmft-dir`.
- `commoner_probe/evidence.py` — made DMFT evidence bundle consume `mines-dmft` manifest records.
- `commoner_probe/example_topics/mines_dmft_pmkkky.json` — renamed bundled Sansad topic from `mom_*` to `mines_*`.
- `tests/test_dmft_mines.py` — added adapter/schema/CLI/corpus tests.
- `tests/test_evidence_dmft.py` — updated evidence tests for `mines-dmft` naming.
- `tests/test_docs_sync.py` — added `mines-dmft` to public CLI command sync.
- `tests/test_init_topic_cli.py` — updated bundled topic expectation.
- `docs/INTEGRATION_SMOKE.md` — documented mines-DMFT acquisition and evidence workflow.
- `.claude/pm-profile.md` — added repo PM profile for future `/pm` runs.
- `docs/STATUS.md` — added PM cockpit with source-family stages and blockers.
- `TODO.md` — moved Mines DMFT acquisition to archive and updated current queue to live refresh/evidence.
- `notes/dmft-source-intake.md` — updated local data path naming.
- `notes/source-family-map-csr-dmft.md` — renamed source family to `mines-dmft`.
- `notes/HANDOFF.md` — rewritten for closeout.
- `notes/STATE_OF_BRAIN.md` — rewritten for closeout.
- `notes/handoffs/handoff-2026-06-16-mines-dmft-acquisition.md` — archived prior handoff.
- `notes/state-of-brains/state-of-brain-2026-06-16-mines-dmft-acquisition.md` — archived prior state.
- `memory/changelog.md` — prepended this entry.
- `memory/session-log.md` — prepended session log.

## 2026-06-16 · dmft-dpe-evidence-intake · create/edit

- `notes/dmft-source-intake.md` — recorded Ministry of Mines static DMFT CSV contract and Odisha DMF endpoint contract.
- `notes/dpe-csr-source-intake.md` — recorded DPE `/cms/wp-json` CSR document-disclosure contract.
- `notes/source-family-map-csr-dmft.md` — mapped MCA CSR, DPE CSR, and Mines DMFT boundaries.
- `TODO.md` — updated current queue for MCA CSR comparison, DPE CSR documents, and Mines DMFT acquisition.
- `data/mines-dmft/mines-gov-in/` — local ignored corpus now holds Ministry CSVs and manifest after rename.

## 2026-06-16 · mca-csr-live-endpoint · create/edit

- `commoner_probe/csr/mca.py` — switched MCA CSR adapter to verified MCA CDM `csr-data` / `cdm/export.php` contract and CSV header validation.
- `commoner_probe/schemas/manifest_mca_csr.schema.json` — added schema for MCA CSR manifest records.
- `commoner_probe/validate.py` — routed `mca_csr_company_spend` manifest records to `manifest_mca_csr`.
- `commoner_probe/records.py` — added `ManifestMcaCsrRecord`.
- `commoner_probe/corpus.py` — added `Corpus.manifest_mca_csr()` and DataFrame stream entry.
- `commoner_probe/cli.py` — added `commoner-probe mca-csr`.
- `tests/test_csr_mca.py` — expanded tests for live endpoint contract, schema, CLI dry-run, and corpus stream.
- `README.md` — documented `commoner-probe mca-csr`.
- `docs/SCHEMAS.md` — documented MCA CSR manifest shape.
- `TODO.md` — archived completed MCA CSR verification/schema/CLI work.
- `notes/HANDOFF.md` — updated closeout handoff.
- `notes/STATE_OF_BRAIN.md` — updated active frame after endpoint verification.
- `notes/handoffs/handoff-2026-06-16-mca-csr-live-endpoint.md` — archived prior handoff.
- `notes/state-of-brains/state-of-brain-2026-06-16-mca-csr-live-endpoint.md` — archived prior state.
- `memory/changelog.md` — prepended this entry.
- `memory/session-log.md` — prepended session log.

## 2026-06-07 · mca-csr-adapter · create/edit

- `commoner_probe/csr/__init__.py` — created CSR acquisition package export.
- `commoner_probe/csr/mca.py` — created MCA CSR acquisition adapter with CSRF parsing, dry-run, download, and manifest logging.
- `tests/test_csr_mca.py` — created no-network tests for MCA CSR adapter.
- `notes/HANDOFF.md` — rewritten for maintain closeout.
- `notes/STATE_OF_BRAIN.md` — rewritten for maintain closeout.
- `notes/handoffs/handoff-2026-06-07-mca-csr-adapter.md` — archived prior handoff.
- `notes/state-of-brains/state-of-brain-2026-06-07-mca-csr-adapter.md` — archived prior state.
- `TODO.md` — updated current/archived work.
- `WORKING.md` — cleared Codex row during maintain.
- `memory/changelog.md` — prepended this entry.
- `memory/session-log.md` — prepended session log.

## 2026-05-23 · probe-split-planning · create/edit

- `.ai/plans/phase1-probe-split.md` — created: concrete 2-session code refactor plan for probe/compose split, with sousveillance identity paragraph
- `notes/HANDOFF.md` — created: session handoff with open queue
- `notes/STATE_OF_BRAIN.md` — created: active frame, unresolved tensions, key Zotero refs
- `TODO.md` — created: current and future tasks
- `memory/MEMORY.md` — created: memory index
- `memory/project-identity.md` — created: probe identity memory
- `memory/architecture-root-cause.md` — created: filter_fn root cause memory
- `memory/changelog.md` — created
- `memory/session-log.md` — created
- `WORKING.md` — updated: cleared active session row
