# State of Brain

## Active frame

**Probe as sousveillance infrastructure.** The guiding concept is Browne's sousveillance
(watching back from below using the master's own documentation infrastructure) fused with
Ambedkar's blue-book method (poring over state records as forensic evidence of
constitutional failure). probe does not describe itself as an "Indian government scraper"
— that naturalises the state as a geographic category. It describes itself as seizing the
state's *obligation* to disclose.

> "probe automates the move Ambedkar made with census blue books and Browne made with
> Fanon's FBI file: seizing the state's own paperwork and turning it into evidence."

## Architectural fact to hold

The missing `filter_fn: Callable | None = None` hook on `TopicProfile` is the root cause
of the sansad-crawler / sansad-semantic-crawler fork. Until it exists, the classify()
call is hardwired in the crawl loop and the loop cannot live in a pure acquisition layer.
Adding this hook is the first concrete act of Session 1.

## Unresolved tensions

- **narcotrek GPL barrier**: code cannot merge into probe (MIT). Schema convergence is
  deferred — expose a stdlib-only `sansad_crawler.core.runlog` for narcotrek to adopt
  eventually.
- **`extractors.py` LLM dep**: `CompositionExtractor` imports `LLMClassifier` — stays in
  compose. The design question of whether to expose a hook for it (like `filter_fn`) is
  not yet resolved.
- **Rename timing**: `probe` / `compose` are the target names but the PyPI rename and
  import rename are Phase 2. Do not conflate with the Phase 1 structural merge.
- **Three divergent runlog schemas**: sansad-crawler (optional classifier fields),
  sansad-semantic (required), narcotrek (different shape entirely). Will need a
  convergence pass after the structural merge.

## Key references in Zotero corpus

- Browne, *Dark Matters* (2015) — item_key EG7ZPN8H — sousveillance, racializing surveillance
- Cohen (2019) — item_key KPTKNW4K — "forensic sociality"
- Ambirajan (1999) — Ambedkar's blue-book method
