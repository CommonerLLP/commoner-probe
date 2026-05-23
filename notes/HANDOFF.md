# Handoff

## What changed this session

- Developed architecture decision memo at `../_org/docs/architecture-decision-memo-2026-05.{md,html,pdf}` (prior session) — covers full org-wide repo topology, naming rationale, migration roadmap
- Established names: `probe` (Layer 0, acquisition) and `compose` (Layer 1, analytics)
- Wrote concrete 2-session code refactor plan: `.ai/plans/phase1-probe-split.md`
- Identity of probe grounded in Browne's sousveillance + Ambedkar's census blue books — "seizing the state's own paperwork and turning it into evidence"
- Zotero item confirmed: Simone Browne, *Dark Matters* (2015), item_key EG7ZPN8H

## Open queue — what is next

- **Session 1 (this repo):** Execute the plan at `.ai/plans/phase1-probe-split.md`
  1. Port `bkt_no_match` + classifier log fields into `sansad.py` (13-line diff)
  2. Port `crawl_composition()` + `committee_members.jsonl` into `committees.py`
  3. Add `filter_fn: Callable | None = None` to `TopicProfile` in `topics.py`
  4. Move `neva.py` verbatim from sansad-semantic-crawler
  5. Standardise HTTP client (port academiaindia `fetch.py` → `http_client.py`)
  6. Commit, tag `v0.3.0`
- **Session 2 (sansad-semantic-crawler):** Gut forked modules, depend on `sansad-crawler>=0.3.0`
- **Phase 2 (later):** Rename packages to `probe` / `compose` on PyPI and in imports
