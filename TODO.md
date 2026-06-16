# TODO

## Current

- [ ] MCA CSR: build comparison utilities over the 10-year MCA CDM CSR corpus, and keep the boundary explicit that this source compares reporting/spending companies, not CSR consultants or implementing agencies.
- [ ] Mines DMFT / PMKKKY: run live `commoner-probe mines-dmft --out data/mines-dmft --sources mines-gov-in,odisha`, crawl Sansad with `mines_dmft_pmkkky`, and generate `evidence dmft`. See `notes/dmft-source-intake.md`.
- [ ] MoF/DPE CSR: build DPE CPSE CSR document-disclosure acquisition from the proven `/cms/wp-json` contract; keep spend/project comparison blocked until CSRMS or Public Enterprises Survey spend fields are proven. See `notes/dpe-csr-source-intake.md`.
- [ ] Build finance document-disclosure adapters from SevenT4 Ahmedabad and Delhi work. See `docs/TODO-finance-document-disclosure-adapters.md`.

## Future

- Phase 2: rename packages to `probe` / `compose` on PyPI + import rename
- narcotrek schema convergence: expose stdlib-only `sansad_crawler.core.runlog`
- HTTP client: evaluate stale-if-error + per-domain rate-limit against all existing corpora before enabling cache globally

## Archive

- [x] 2026-06-16 — Fixed SSRF guard: removed `WHITELISTED_DOMAINS` name-allowlist bypass in `commoner_probe/url_safety.py`, added hostname normalization + `tests/test_url_safety.py` (commit `e440f46`, pushed).
- [x] 2026-06-16 — Corrected false "jsonschema/ruff missing" caveat (was wrong interpreter); installed `bd` 1.0.5 + rebuilt dolt DB from `issues.jsonl`; removed unused import (commit `3a827ff`, pushed).
- [x] 2026-06-16 — Verified MCA CDM CSR live export endpoint, updated `commoner_probe.csr.mca`, added `manifest_mca_csr` schema, `Corpus.manifest_mca_csr()`, and `commoner-probe mca-csr`.
- [x] 2026-06-16 — Added `commoner-probe mines-dmft` acquisition for Ministry of Mines static CSVs and Odisha DMF source endpoints, plus `manifest_mines_dmft`, corpus stream, and CLI tests.
- [x] 2026-06-07 — Added MCA CSR acquisition adapter in `commoner_probe.csr.mca`; committed as `032ec83`.
- [x] 2026-06-07 — Session 1 probe split / v0.3.0 work is complete in canonical remote history.
