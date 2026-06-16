# Session Log

## 2026-06-16 — toolchain-repair-and-ssrf-fix

**Decisions made:**
- SSRF guard keeps NO name-allowlist bypass; every host must clear the resolved-IP policy.
- Keep `bd init`'s auto-commit `267014f` as-is — the agent-surface files are load-bearing for the gemini/codex/agy/claude/Hermes fleet, not Claude-only cruft.
- Run all tests/lint through `.venv/bin/...`, never bare system python3.13.

**Facts verified:**
- `elibrary.sansad.in` → `164.100.85.146` (public NIC IP); passes the SSRF guard without any allowlist.
- Repo venv (Python 3.14.5) has jsonschema 4.26.0 + ruff 0.15.16; full suite `.venv/bin/python -m pytest` → 305 passed, 1 skipped (no deselection); ruff clean.
- `bd` 1.0.5 (Homebrew) works after rebuilding the dolt DB from `issues.jsonl` (43 issues, all closed). bd 1.0.5 intentionally tracks `.beads/interactions.jsonl` (removed it from `.beads/.gitignore`).

**Errors caught (by user / self):**
- User corrected my single-agent assumption: the workspace is a multi-agent fleet → agent-surface files are not cruft. Logged in `_org/mistakes.md`.
- Self-caught: my "interactions.jsonl should be untracked" claim relied on the pre-`bd init` gitignore; re-checked `git check-ignore` and reversed.

**Commits pushed:**
- `e440f46` fix(ssrf): remove name-allowlist bypass in url_safety guard
- `3a827ff` docs: correct false jsonschema/ruff-missing caveat; fix lint
- (`267014f` bd init auto-commit — kept)

**Concurrency:** another agent was live in-repo (untracked `examples/topics/narcotics_substance.json`); all commits staged by explicit path, never `git add -A`.

## 2026-06-16 — mines-dmft-acquisition

**Decisions made:**
- Public source-family name is `mines-dmft`, not `mom-dmft`; the latter is unclear.
- DMFT evidence bundles keep executive disclosure and Sansad oversight separate.
- Ministry of Mines CSVs are cumulative/current snapshots keyed by source `Last-Modified`, not FY-wise data.
- `mines-dmft` acquisition is raw Layer 0 source-file capture; parsed DMFT facts come later.
- `/pm` now has a repo-local profile and `docs/STATUS.md` cockpit for future recomputation.

**Facts verified:**
- Ministry of Mines webportal bundle exposes four static DMFT CSV assets under `/webportal/assets/img/`.
- The four Ministry CSVs include 23 state rows in state-level files and 14 national sector rows.
- Odisha DMF has proven state/district JSON and report-page surfaces documented in `notes/dmft-source-intake.md`.
- Local ignored data directory was renamed from `data/mom-dmft` to `data/mines-dmft`.

**Errors caught:**
- User caught that `mom-dmft` is a poor public name. Renamed CLI, manifest kind, schema, topic, docs, tests, and notes to `mines-dmft` / `mines_dmft_*`.
- User repeatedly corrected ripgrep path usage; future sessions must call Homebrew ripgrep explicitly as `/opt/homebrew/bin/rg`.

**Commits:**
- `d3d223f docs: map CSR and DMFT source contracts`
- `2cc9b23 feat: add DMFT evidence bundle`
- `10cdcde feat: add mines DMFT acquisition`

**Verification:**
- `pytest tests/test_dmft_mines.py tests/test_evidence_dmft.py tests/test_init_topic_cli.py tests/test_docs_sync.py` -> 15 passed, 1 skipped.
- `python3.13 -m commoner_probe mines-dmft --out /tmp/mines-dmft-dry --sources mines-gov-in --dry-run` -> emitted four `MINES_DMFT` records.
- `pytest -k 'not test_mca_csr_manifest_schema_is_bundled_and_validates_record and not test_mines_dmft_manifest_schema_is_bundled_and_validates_record'` -> 255 passed, 39 skipped, 2 deselected.
- `git diff --check` -> clean.

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
