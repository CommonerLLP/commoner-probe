# Session Log

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
