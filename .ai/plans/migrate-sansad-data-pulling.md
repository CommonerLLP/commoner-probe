# Plan: Migrate data-pulling features from `sansad-semantic-crawler` → `sansad-crawler`

## Context

The user has a working Python project at `Learning/sansad-semantic-crawler` (~9K LOC) that mixes two concerns: (a) pulling data from sansad.in APIs and structuring it, and (b) analyzing the pulled content with regex/embeddings/LLM classifiers. They want this repo (`Learning/sansad-crawler`) to own concern (a) only — crawling, downloading, structural extraction, linking — so the analysis layer can evolve independently in a separate repo. The target repo is currently an empty skeleton with only beads, `.claude/`, and `CLAUDE.md`.

**What migrates**: Lok Sabha + Rajya Sabha question API crawler, standing committee report crawler, regex-based Q/A and ATR splitters, ATR→original-report linkage, entity store, PDF text extraction, shared HTTP/runlog/base utilities, and the CLI subcommands that drive these.

**What stays behind**: topic classification (regex/embeddings/LLM), discourse labelling, weighting, MP/ministry dossiers, aggregations, the knowledge graph DB, and the LLM-based committee composition extractor.

**Decisions already made** (user confirmed):
- Strip `topic.classify()` calls from crawlers — manifest carries pulled data only; classification happens downstream.
- Migrate tests only for kept modules.
- Rename package `sansad_semantic_crawler` → `sansad_crawler`.
- Phased rollout via beads issues.

## Phased migration (11 beads issues)

Each phase below maps to one `bd create`. List dependencies with `bd dep add`. Run `bd ready` between phases. The plan file is treated as the canonical reference — every bead description points back to it.

### Phase 0 — Save plan into the repo
Copy this plan into the target repo so the beads can reference a stable in-repo path. Create `/Users/sreeramramasubramanian/Learning/sansad-crawler/.ai/plans/migrate-sansad-data-pulling.md` containing the contents of this file (verbatim, minus this Phase 0 paragraph if you like, or whole — doesn't matter). Commit it as the first migration commit so subsequent phases can reference it.
**Acceptance**: `.ai/plans/migrate-sansad-data-pulling.md` exists and is committed.

### Phase 1 — Bootstrap package skeleton
Create `pyproject.toml` (name `sansad-crawler`, entry point `sansad-crawl = sansad_crawler.cli:main`, keep only `pdf`/`http` extras, drop `embeddings`/`llm`), `sansad_crawler/__init__.py` (version `0.1.0`, no `graph` re-export), `sansad_crawler/__main__.py`, extend `.gitignore` (`__pycache__/`, `*.egg-info`, `build/`, `dist/`, `data/`, `.venv/`), copy `Makefile` and strip discourse/weights/dossier targets, create empty `tests/__init__.py`.
**Acceptance**: `pip install -e .` succeeds; `python -c "import sansad_crawler"` works.

### Phase 2 — Migrate infrastructure (http_client, runlog, base, textparse)
Copy the four files as-is, then surgical edits:
- `runlog.py`: change `_importlib_version("sansad-semantic-crawler")` → `"sansad-crawler"`. Make `RunLog.start` default `classifier_config={}` (keep field for forward-compat with downstream tooling that may read older `_runs.jsonl`).
- `base.py` (line 77): drop `classifier_mode` param from `BaseCrawler.__init__` and the `self.classifier_mode = ...` assignment. Keep the `TYPE_CHECKING` import of `TopicProfile` (still used for the `topic` arg type hint).
**Depends on**: Phase 1.
**Acceptance**: `from sansad_crawler.base import BaseCrawler` works; ported `test_runlog.py`, `test_textparse.py`, `test_url_encoding.py`, `test_security_hardening.py` pass.

### Phase 3 — Slim `topics.py` to search/ministry config only
Rewrite `topics.py`:
- `TopicProfile` keeps `name`, `description`, `search_groups`, `lok_sabha_ministries`, `rajya_sabha_ministry_likes`. Drop `tag_rules`, `classifier_config`, `fallback_tag`, `tag_labels`, and the `classify()` method.
- `load_topic(path)` drops the `classifier_override` arg and silently ignores unknown JSON keys so existing on-disk topic profiles still load.
- Migrate `examples/topics/libraries.json`, `home_affairs_starred.json`, `affirmative_action.json` with `tag_rules`/`fallback_tag`/`classifier` sections stripped. Drop `libraries_embeddings.json` and `libraries_llm_ollama.json` (analyzer-only).
**Depends on**: Phase 1.
**Acceptance**: `load_topic(...).searches()` returns `[(group, query), …]`; `TopicProfile` has no `classify` attr; ported `test_topics.py` passes after stripping classifier assertions.

### Phase 4 — Migrate crawlers (`sansad.py`, `committees.py`, `members.py`); strip classify
Copy and edit:
- `sansad.py`:
  - Drop `classifier_mode` param from `SansadCrawler.__init__` (line 75) and both `runlog.start(...)` calls (lines 228, 363) — remove `classifier_mode=` and `classifier_config=` kwargs.
  - **LS path line 260**: delete `semantic = self.topic.classify(title, query)` and remove `**semantic` from the `rec = {...}` dict literal.
  - **RS path lines 395–397**: delete `semantic = self.topic.classify(blob)` and the `if not semantic["matches"]: bkt_no_match += 1; continue` guard. Remove `**semantic` from the RS `rec` literal.
  - Behavioural change: RS rows previously filtered out by classifier are now kept. Emit a one-line warning at RS crawl start: `RS: keeping all ministry-matched rows (no in-crawler classification).` Keep `bkt_no_match=0` in `record_bucket` calls (lines 453, 466) for `_runs.jsonl` schema stability.
- `committees.py`:
  - Drop `classifier_mode` param (line 238) and from both `runlog.start(...)` calls (lines 245, 410, 536).
  - **Remove `crawl_composition` method entirely** (line 273) along with the `from .extractors import CompositionExtractor` import — extractors module isn't migrating. Remove the `--crawl-composition` CLI flag in Phase 7.
  - **LS line 432 & RS line 554**: delete `semantic = self.topic.classify(title)` and the `**semantic` spread.
- `members.py`: copy as-is.
**Depends on**: Phases 2, 3.
**Acceptance**: `grep -rn "classify\|tag_rules\|fallback_tag\|classifier_config\|CompositionExtractor" sansad_crawler/` returns zero hits outside `runlog.py`'s historical field name. Ported `test_committees.py` passes.

### Phase 5 — Migrate regex extractors (`answers.py`, `atr_linkage.py`)
Copy both as-is; verify no imports from `discourse`, `classifiers`, `dossier`. Pure-regex Q/A + ATR + DFG splitters and ATR→report linkage.
**Depends on**: Phase 2.
**Acceptance**: Ported `test_answers.py`, `test_atr_linkage.py`, `test_qa_structured_parse.py`, `test_report_type.py` pass.

### Phase 6 — Migrate entity layer (`entities.py`, `resolver.py`)
Copy both as-is; entity resolution (`--with-entities` flag) is data-pulling, not analysis.
**Depends on**: Phase 2.
**Acceptance**: Ported `test_entities.py`, `test_resolver.py`, `test_resolve_askers.py` pass.

### Phase 7 — Slim `cli.py` to four subcommands
Rewrite `cli.py`. Keep only: `crawl`, `crawl-committees`, `extract-answers`, `extract-atr-linkage`. Plus `_build_resolver_if_requested` helper and `parse_session_range`. Drop top-level imports of `aggregations`, `graph`, `discourse`, `dossier`, `export`, `weighting`. Drop the `--classifier` flag from `crawl` and `crawl-committees`, and the `--crawl-composition` flag entirely. Drop all subcommand functions for `analyse-discourse`, `analyse-weights`, `mp-summary`, `analyse-ministry`, `mp-dossier`, `ministry-dossier`, `question-refine`, `build-graph`, `parse`, `export` (10 functions removed).
**Depends on**: Phases 4, 5, 6.
**Acceptance**: `sansad-crawl --help` lists exactly four subcommands; `sansad-crawl crawl --help` shows no `--classifier`.

### Phase 8 — Port remaining cross-cutting tests
Port `test_smoke_fixture.py`, `test_check_leaks.py`, `test_adapters.py`, `test_docs_sync.py` — but only the assertions that cover kept modules; drop fixtures and assertions touching `discourse`/`weighting`/`dossier`/`graph`/`classifiers`. Skip `test_aggregations.py`, `test_classifiers.py`, `test_discourse.py`, `test_discourse_llm.py`, `test_dossier.py`, `test_graph.py`, `test_weighting.py` entirely.
**Depends on**: Phases 4, 5, 6, 7.
**Acceptance**: `pytest -q` is green; total ~14 test files.

### Phase 9 — README, docs, scripts
Write a fresh `README.md` (don't port — source README advertises analysis features that are gone). Sections: install, the four subcommands, manifest schema (post-strip, see below), explicit note that classification lives downstream. Port `docs/INTEGRATION_SMOKE.md` with discourse smoke section removed. Port `scripts/check_leaks.py` only if it doesn't import classifiers. Drop `scripts/generate_defaults_report.py`, `scripts/batch_llm_audit.py`, `scripts/test_atr_linkage.py`, `scripts/sync_agents.py`. Do not port `CHANGELOG.md` — start fresh at v0.1.0.
**Depends on**: Phase 8.
**Acceptance**: README CLI examples run end-to-end against a smoke crawl.

### Phase 10 — End-to-end verification + tag v0.1.0
Run the verification script in the next section, then commit and tag.
**Depends on**: Phase 9.
**Acceptance**: Verification script passes; clean working tree; tag `v0.1.0` exists.

## Cross-cutting rules (every phase enforces)

1. **Commit after every phase.** Each bead closes with one focused commit on `master`: stage only the files the phase touched, commit message `migrate(phase-N): <short title>` with a 1–3 sentence body referencing `.ai/plans/migrate-sansad-data-pulling.md`. After the commit, run `bd close <id>`. No phase leaves uncommitted work behind, even if the next phase will start in the same session.
2. **Import path rewrite**: any `from sansad_semantic_crawler.X import Y` → `from sansad_crawler.X import Y`. Relative imports (`from .X`) stay. Before closing a phase: `grep -rn "sansad_semantic_crawler" sansad_crawler/ tests/` returns zero hits.
3. **No analysis imports leak in**: `grep -rn "from sansad_crawler\.\(discourse\|dossier\|weighting\|aggregations\|graph\|extractors\|classifiers\|export\)" sansad_crawler/ tests/` returns zero hits.
4. **`classifier_mode` removal** appears in six places: `base.BaseCrawler.__init__`, `SansadCrawler.__init__`, `CommitteeCrawler.__init__`, four `runlog.start(...)` call sites (LS + RS in each crawler), and the `--classifier` CLI flag. Remove all.
5. **Keep `_runs.jsonl` schema stable**: `RunLog.start` still accepts `classifier_config` but defaults to `{}`; `bkt_no_match` stays in `record_bucket` calls (always 0 now). This lets downstream tooling reading older corpora continue to work.
6. **`asker_entity_ids` and `--with-entities` survive** — entity resolution is not classification.

## Manifest schema impact

Fields **removed** from `manifest.jsonl` records (LS Q/A, RS Q/A, LS committee reports, RS committee reports): `matches` (bool), `tags` (list[str]), `score` (float), and any other field the old `topic.classify(...).to_dict()` was emitting (`label`, `classifier`, `fallback`).

Fields **retained** for Q/A: `key`, `run_id`, `kind`, `house`, `uuid`, `handle`, `title`, `date`, `qtype`, `qno`, `session`, `loksabhanumber`, `ministry`, `askers`, `asker_entity_ids`, `uri`, `source`, `found_via_group`, `found_via_query`, `crawled_at`, `language_classified`, `pdf_url`, `pdf_path` (when downloaded).

**Behavioural change to call out in README + Phase 4 commit message**: RS path previously dropped rows whose classifier said `matches=false`. After the strip, every row returned by the `min_name like '<ministry>%'` query is kept. Output corpora are a strict superset of the old behaviour for the same topic.

## Critical files to modify

- `sansad-semantic-crawler/sansad_semantic_crawler/cli.py` (617 LOC → ~150 LOC, 4 subcommands)
- `sansad-semantic-crawler/sansad_semantic_crawler/topics.py` (surgical strip of classifier sections; reuse `search_groups`/ministry parsing)
- `sansad-semantic-crawler/sansad_semantic_crawler/sansad.py` (lines 75, 83, 228–229, 260, 363–364, 395–397: drop classifier plumbing + 2 classify call sites)
- `sansad-semantic-crawler/sansad_semantic_crawler/committees.py` (lines 238, 245, 273, 410, 432, 536, 554: drop classifier plumbing, 2 classify call sites, and the `crawl_composition` method)
- `sansad-semantic-crawler/sansad_semantic_crawler/base.py` (line 77: drop `classifier_mode` param)

## Verification (run after Phase 9)

```bash
cd /Users/sreeramramasubramanian/Learning/sansad-crawler
python -m venv .venv && source .venv/bin/activate
pip install -e ".[pdf,http]"

pytest -q                                              # unit tests

sansad-crawl crawl \
  --topic examples/topics/libraries.json \
  --out /tmp/smoke --house ls \
  --max-buckets 1 --max-records 1 --no-download
test -s /tmp/smoke/manifest.jsonl
python -c "import json; r=json.loads(open('/tmp/smoke/manifest.jsonl').readline()); \
  assert 'matches' not in r and 'tags' not in r and 'score' not in r"

sansad-crawl crawl --topic examples/topics/libraries.json \
  --out /tmp/smoke --house ls --max-records 1 --no-download    # idempotency

sansad-crawl extract-answers --out /tmp/smoke                  # graceful when no PDFs
sansad-crawl crawl-committees --topic examples/topics/libraries.json \
  --out /tmp/smoke-cc --house ls --committees finance --max-records 1 --no-download
sansad-crawl extract-atr-linkage --out /tmp/smoke-cc
```

All pass → `git tag v0.1.0`.

## Risks / open items

1. **RS corpus grows silently** — losing the classifier prune means more rows for the same topic; the Phase 4 warning + README note mitigate but don't prevent surprise. Existing corpora won't be rebuilt automatically.
2. **Committee composition data lost** — dropping `crawl_composition` means no committee-member lists. If you want this back, it'd need either a non-LLM API path (verify if `sansad.in/api_ls/committee/committeeMembers` works standalone — the current code only falls back to LLM extraction when the API fails) or a separate follow-up issue.
3. **Topic JSON migration**: if you have on-disk profiles outside the repo, `load_topic` will tolerate the legacy keys but ignore them. Worth a one-line note in the README.
4. **PDF extras**: `extract-answers` silently no-ops without `pdfminer.six`. README must instruct `pip install -e ".[pdf,http]"`.

## Beads bootstrap (run at start of implementation session)

Each `bd create` below carries `--description` (why), `--design` (concrete file-level work, referencing the plan), `--acceptance` (how we know it's done), and `--notes` (anything else the implementer needs). Run them in order, capture each returned `bd-NNN` id, then wire dependencies with `bd dep add`. Each bead is followed by a focused commit per the cross-cutting rule above.

```bash
PLAN=".ai/plans/migrate-sansad-data-pulling.md"

# Phase 0 — Save plan into the repo
P0=$(bd create --type=task --priority=1 \
  --title "migrate phase 0: save plan into .ai/plans/" \
  --description "Land the migration plan as a checked-in reference so every subsequent bead can cite it. All later phases will reference \$PLAN." \
  --design "Create .ai/plans/migrate-sansad-data-pulling.md with the full plan body. Commit on master." \
  --acceptance ".ai/plans/migrate-sansad-data-pulling.md exists and is committed; git log shows the commit." \
  --notes "After commit: bd close \$P0.")

# Phase 1 — Bootstrap package skeleton
P1=$(bd create --type=task --priority=1 \
  --title "migrate phase 1: bootstrap sansad-crawler package skeleton" \
  --description "Stand up the Python package so subsequent phases have a place to drop modules. See \$PLAN §Phase 1." \
  --design "Add pyproject.toml (name 'sansad-crawler'; entry point sansad-crawl=sansad_crawler.cli:main; extras [pdf,http] only). Add sansad_crawler/__init__.py (version 0.1.0, no .graph re-export) and __main__.py. Copy Makefile from source; strip discourse/weights/dossier targets. Extend .gitignore (__pycache__, *.egg-info, build/, dist/, data/, .venv/). Create empty tests/__init__.py." \
  --acceptance "pip install -e . succeeds in a clean venv; python -c 'import sansad_crawler; print(sansad_crawler.__version__)' prints 0.1.0." \
  --notes "Commit message: migrate(phase-1): bootstrap sansad-crawler package skeleton. Then bd close \$P1.")

# Phase 2 — Migrate infrastructure modules
P2=$(bd create --type=task --priority=1 \
  --title "migrate phase 2: copy infra (http_client, runlog, base, textparse)" \
  --description "Land the plumbing every other module depends on. See \$PLAN §Phase 2." \
  --design "Copy http_client.py, runlog.py, base.py, textparse.py from sansad_semantic_crawler/. Edit runlog.py: change _importlib_version('sansad-semantic-crawler') → 'sansad-crawler'; make RunLog.start default classifier_config={}. Edit base.py line 77: drop classifier_mode param and self.classifier_mode assignment from BaseCrawler.__init__; keep TYPE_CHECKING import of TopicProfile. Port tests: test_runlog.py, test_textparse.py, test_url_encoding.py, test_security_hardening.py." \
  --acceptance "from sansad_crawler.base import BaseCrawler succeeds; pytest -q on the four ported tests passes; grep -rn 'sansad_semantic_crawler' sansad_crawler/ tests/ returns zero hits." \
  --notes "Commit per cross-cutting rule. Then bd close \$P2.")

# Phase 3 — Slim topics.py
P3=$(bd create --type=task --priority=1 \
  --title "migrate phase 3: slim topics.py to search/ministry config only" \
  --description "Strip the classifier seam out of topics.py so crawlers can be migrated without bringing classification along. See \$PLAN §Phase 3." \
  --design "Rewrite topics.py: TopicProfile keeps name, description, search_groups, lok_sabha_ministries, rajya_sabha_ministry_likes only. Drop tag_rules, classifier_config, fallback_tag, tag_labels, classify(). load_topic() drops classifier_override arg; silently ignores unknown JSON keys (forward-compat). Port examples/topics/libraries.json, home_affairs_starred.json, affirmative_action.json with tag_rules/fallback_tag/classifier sections removed. Drop libraries_embeddings.json and libraries_llm_ollama.json. Port test_topics.py with classifier assertions removed." \
  --acceptance "load_topic('examples/topics/libraries.json').searches() returns list of (group,query) tuples; hasattr(TopicProfile(), 'classify') is False; ported test_topics.py passes." \
  --notes "Commit per cross-cutting rule. Then bd close \$P3.")

# Phase 4 — Migrate crawlers; strip classify
P4=$(bd create --type=task --priority=1 \
  --title "migrate phase 4: crawlers (sansad.py, committees.py, members.py) — strip classify" \
  --description "Land the LS/RS question crawler and committee report crawler, surgically removing all topic.classify() call sites and the LLM-backed crawl_composition method. See \$PLAN §Phase 4." \
  --design "sansad.py: drop classifier_mode param at line 75; remove classifier_mode= and classifier_config= kwargs from runlog.start calls at lines 228 and 363; delete classify line at 260 and **semantic spread in the LS rec literal; delete classify line at 395 and the matches-guard at 397, plus **semantic in RS rec literal. Keep bkt_no_match=0 in record_bucket calls (lines 453, 466). Print 'RS: keeping all ministry-matched rows (no in-crawler classification).' at RS crawl start. committees.py: drop classifier_mode at line 238; clean runlog.start at lines 245, 410, 536; delete crawl_composition method entirely (line 273) and the from .extractors import CompositionExtractor line; delete classify at lines 432 and 554 with their **semantic spreads. Copy members.py as-is. Port test_committees.py." \
  --acceptance "grep -rn 'classify\\|tag_rules\\|fallback_tag\\|classifier_config\\|CompositionExtractor' sansad_crawler/ returns zero hits (outside runlog.py historical field name); ported test_committees.py passes." \
  --notes "Behavioural change: RS rows previously filtered by classifier are now kept. Document in commit body. Depends on phases 2 and 3.")

# Phase 5 — Regex extractors
P5=$(bd create --type=task --priority=1 \
  --title "migrate phase 5: regex extractors (answers.py, atr_linkage.py)" \
  --description "Land the pure-regex Q/A, ATR, and DFG splitters plus the ATR→original-report linkage extractor. Zero coupling to analysis modules. See \$PLAN §Phase 5." \
  --design "Copy answers.py and atr_linkage.py from sansad_semantic_crawler/. Verify with grep that neither imports discourse, classifiers, dossier. Port tests: test_answers.py, test_atr_linkage.py, test_qa_structured_parse.py, test_report_type.py." \
  --acceptance "Four ported tests pass; grep -rn 'classifiers\\|discourse\\|dossier' sansad_crawler/answers.py sansad_crawler/atr_linkage.py returns zero hits." \
  --notes "Depends on phase 2 (uses textparse, base). Independent of phase 4 — can land first if scheduling helps.")

# Phase 6 — Entity layer
P6=$(bd create --type=task --priority=2 \
  --title "migrate phase 6: entity layer (entities.py, resolver.py)" \
  --description "Land the optional entity resolution layer that backs the --with-entities crawl flag. Entity resolution is data-pulling, not analysis. See \$PLAN §Phase 6." \
  --design "Copy entities.py and resolver.py as-is. Port test_entities.py, test_resolver.py, test_resolve_askers.py." \
  --acceptance "Three ported tests pass; no analysis-module imports introduced." \
  --notes "Depends on phase 2.")

# Phase 7 — Slim cli.py
P7=$(bd create --type=task --priority=1 \
  --title "migrate phase 7: slim cli.py to 4 subcommands" \
  --description "Carve cli.py from 14 subcommands down to the four that survive the migration. See \$PLAN §Phase 7." \
  --design "Rewrite cli.py to expose only: crawl, crawl-committees, extract-answers, extract-atr-linkage. Keep _build_resolver_if_requested helper and parse_session_range. Drop top-level imports of aggregations, graph, discourse, dossier, export, weighting. Drop --classifier flag from crawl and crawl-committees. Drop --crawl-composition flag entirely. Remove the 10 dropped subcommand functions (analyse-discourse, analyse-weights, mp-summary, analyse-ministry, mp-dossier, ministry-dossier, question-refine, build-graph, parse, export)." \
  --acceptance "sansad-crawl --help lists exactly four subcommands; sansad-crawl crawl --help shows no --classifier flag; no --crawl-composition flag anywhere." \
  --notes "Depends on phases 4, 5, 6.")

# Phase 8 — Cross-cutting tests
P8=$(bd create --type=task --priority=2 \
  --title "migrate phase 8: port cross-cutting tests" \
  --description "Bring over remaining tests that span kept modules. See \$PLAN §Phase 8." \
  --design "Port test_smoke_fixture.py, test_check_leaks.py, test_adapters.py, test_docs_sync.py — but only assertions that touch kept modules. Skip test_aggregations.py, test_classifiers.py, test_discourse.py, test_discourse_llm.py, test_dossier.py, test_graph.py, test_weighting.py entirely." \
  --acceptance "pytest -q is green; final test count is roughly 14 files." \
  --notes "Depends on phases 4, 5, 6, 7.")

# Phase 9 — README, docs, scripts
P9=$(bd create --type=task --priority=2 \
  --title "migrate phase 9: README + docs + scripts" \
  --description "Write fresh docs reflecting the narrower crawl-only scope. See \$PLAN §Phase 9." \
  --design "Write a new README.md (don't port — source advertises analyzer features). Sections: install (pip install -e '.[pdf,http]'), the four subcommands, manifest schema (post-strip), explicit note that classification lives downstream. Port docs/INTEGRATION_SMOKE.md with the discourse smoke section removed. Port scripts/check_leaks.py only if it doesn't import classifiers. Drop scripts/generate_defaults_report.py, batch_llm_audit.py, test_atr_linkage.py, sync_agents.py. Do not port CHANGELOG.md." \
  --acceptance "README CLI examples run end-to-end against the smoke fixture; docs/INTEGRATION_SMOKE.md has no references to discourse/weighting/dossier." \
  --notes "Depends on phase 8.")

# Phase 10 — Verification + tag
P10=$(bd create --type=task --priority=1 \
  --title "migrate phase 10: e2e verification + tag v0.1.0" \
  --description "Run the verification script from \$PLAN §Verification, then tag v0.1.0." \
  --design "Run the verification block end-to-end in a fresh venv. Confirm manifest.jsonl entries lack matches/tags/score. Confirm idempotency. Confirm extract-answers and extract-atr-linkage exit zero on the smoke corpus. git tag v0.1.0." \
  --acceptance "All verification commands pass; v0.1.0 tag exists; working tree clean." \
  --notes "Depends on phase 9.")

# Wire dependencies
bd dep add $P1 $P0
bd dep add $P2 $P1
bd dep add $P3 $P1
bd dep add $P4 $P2; bd dep add $P4 $P3
bd dep add $P5 $P2
bd dep add $P6 $P2
bd dep add $P7 $P4; bd dep add $P7 $P5; bd dep add $P7 $P6
bd dep add $P8 $P7
bd dep add $P9 $P8
bd dep add $P10 $P9
```

After bootstrap: `bd ready` should surface only P0; closing it should unblock P1; etc. Each phase ends with a single commit and a `bd close` of that phase's id.

## ⏸ Verification checkpoint — STOP HERE before implementation

After running the `bd create` block above and wiring the `bd dep add` edges, **do not start Phase 1 yet**. Explicitly pause and report back to the user with:

1. The 11 bead ids that were created (P0 … P10).
2. Output of `bd list --status=open` showing titles, ids, and priorities.
3. Output of `bd ready` (should be P0 only).
4. Output of `bd show $P0` (so the user can verify the description/design/acceptance shape on a representative bead).

Then wait for the user to confirm the beads look right before claiming P0 and beginning work. Do not proceed past this checkpoint without explicit user approval — even if the beads look obviously fine.
