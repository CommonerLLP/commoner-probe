# TODO

## Current

- [ ] Verify the real MCA CSR export endpoint before any live MCA CSR run.
- [ ] Add `manifest_mca_csr` schema only after the endpoint and final record contract are verified.
- [ ] Decide whether `commoner_probe.csr.mca` should get a CLI subcommand after live endpoint verification.

## Future

- Phase 2: rename packages to `probe` / `compose` on PyPI + import rename
- narcotrek schema convergence: expose stdlib-only `sansad_crawler.core.runlog`
- HTTP client: evaluate stale-if-error + per-domain rate-limit against all existing corpora before enabling cache globally

## Archive

- [x] 2026-06-07 — Added MCA CSR acquisition adapter in `commoner_probe.csr.mca`; committed as `032ec83`.
- [x] 2026-06-07 — Session 1 probe split / v0.3.0 work is complete in canonical remote history.
