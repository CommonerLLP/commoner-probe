# Session Log

## 2026-06-16 — mca-csr-live-endpoint

**Decisions made:**
- The MCA CSR adapter can now use the live MCA CDM route rather than a placeholder.
- A narrow `commoner-probe mca-csr` CLI is justified after endpoint verification.
- `manifest_mca_csr` is now stable enough for schema validation because the source CSV contract was observed live.

**Facts verified:**
- `https://www.mcacdm.nic.in/csr-data` returns the CSR data form with `csrf_token`.
- `POST https://www.mcacdm.nic.in/cdm/export.php` with `financialyear[]=FY 2022-23`, all PSU/state/development-sector filters, matching captcha fields, and `export=true` returns `text/csv`.
- The verified CSV was 13,964,988 bytes and began with `Company Name, Financial Year, PSU/Non-PSU, CSR State, CSR Development Sector, CSR Sub Development Sector, Project Amount Spent (In INR Cr.)`.
- `https://www.csr.gov.in/` returned Akamai Access Denied locally; not needed for the current MCA CDM export.

**Errors caught:**
- Initial POST attempt hit transient DNS/sandbox failure; reran the exact live POST with approved network escalation.

**Commits:** pending at closeout.

**Verification:**
- `pytest tests/test_csr_mca.py -q` -> 7 passed.
- `pytest tests/test_schemas.py -q` -> 33 passed.

## 2026-06-07 — mca-csr-adapter

**Decisions made:**
- MCA CSR raw acquisition belongs in `commoner-probe` as Layer 0 acquisition.
- The old local `csr-crawler` repo should remain archived, not canonical.
- No CLI or schema lock for MCA CSR until the live MCA export endpoint and record contract are verified.

**Facts verified:**
- Canonical local checkout is `commoner-probe/` from `CommonerLLP/commoner-probe`.
- Adapter tests run without network by using fake opener/session objects.

**Errors caught:**
- User caught that the session checkpoint had been committed before `/maintain` ran. Closeout now treats committed checkpoint and maintain as separate gates.

**Commits:**
- `032ec83 feat: add MCA CSR acquisition adapter`

**Verification:**
- `pytest tests/test_csr_mca.py -v` -> 3 passed.
- `pytest tests -v` -> 246 passed, 39 skipped.

## 2026-05-23 — probe-split-planning

**Decisions made:**
- probe = Layer 0 (acquisition), compose = Layer 1 (analytics) — names final
- Identity: sousveillance infrastructure, not "Indian government scraper" — Browne + Ambedkar frame
- Migration order: add to sansad-crawler first (v0.3.0), then gut sansad-semantic — not the reverse
- `extractors.py` (LLM dep) stays in compose — not moving to Layer 0
- narcotrek cannot merge (GPL); schema convergence deferred to later phase
- Rename to probe/compose on PyPI is Phase 2 — do not conflate with Phase 1 structural merge

**Facts verified via partial-recall:**
- Browne, *Dark Matters* (2015), item_key EG7ZPN8H — in corpus, score 0.86
- Key Browne move: opens book with FOIA request for Fanon's FBI file — sousveillance as method
- Anjali Nath, *A Thousand Paper Cuts* (2025), item_key UMGTYHVW — cites Browne/FOIA framing

**Errors caught:**
- Used "Indian government sites" — user corrected: naturalises the state; fixed to "state's mandatory disclosure infrastructure"
- User proposed removing modules from sansad-semantic before porting to sansad-crawler — caught: build breaks; corrected migration order

**Commits:** none this session (planning only)
**Files created:** `.ai/plans/phase1-probe-split.md`
