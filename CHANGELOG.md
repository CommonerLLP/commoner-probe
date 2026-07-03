# Changelog

## 0.7.0 (2026-07-03)

### Added

- **`indiacode` source** — acquires India Code (indiacode.nic.in) state Acts
  plus every amendment/rule/regulation/notification/order/circular/
  ordinance/statute found on each Act's page. `is_amendment` is derived from
  description text (the site doesn't have a distinct amendment category).
  Verified live against the West Bengal Public Libraries Act, 1979.
- **`state-assembly` registry + coverage probe** — `commoner_probe.neva_portals`
  bakes in the `portal_code -> state_code/chamber/state_name` mapping for
  all 31 NeVA assembly portals + 6 Legislative Council portals;
  `state-assembly` gained `--all`/`--list-portals`, and a new
  `state-assembly-probe` subcommand does a lightweight per-portal
  data-depth check (session/question/paper/member counts) without a full
  crawl — useful because NeVA portal reachability doesn't imply data depth.

### Fixed

- **`indiacode` resume-with-downloads**: a metadata-only (`--no-download`)
  pass followed by a downloads-enabled rerun on the same corpus directory
  downloaded files to disk but left `manifest.jsonl` rows stuck at
  `status: "pending"` (no `dest`/`sha256`) — a downstream reader had no way
  to discover the file was actually there. Found by Codex's automated
  review on PR #20. `load_seen()` now tracks last-known status per key, and
  only genuinely-terminal statuses (`downloaded`, `skipped_exists`,
  `no_pdf_found`) are skipped on rerun.

## 0.6.1 (2026-07-03)

### Fixed

- **`iit_gandhinagar` department extraction**: found by running against the live page for the first time (the ported code had never been exercised against it before) — the pipe-delimited department list has no closing terminator, so the last department could bleed into trailing nav-menu text (`"Sustainable Development Find Out More Apply Now Staff Non"`), and a section sub-heading ("Interdisciplinary Centers") glued onto a neighboring department name with no separating pipe (`"Physics Interdisciplinary Centers Archaeological Sciences"`). Both are now split out and discarded.
- **`iit_hyderabad` `_SKIP_RE`**: the live site's actual results-notification wording has extra words between "of" and "results" and an inflected "Provisionally" rather than "provisional" — a naive exact-phrase match let one results-notification PDF slip through as a fake job posting.

## 0.6.0 (2026-07-03)

### Added

- **`iit_gandhinagar` parser** — IIT Gandhinagar's rolling "Professor of Practice" page (`/careers/pop`) lists all eligible departments as a single pipe-separated block with no per-department PDF or closing date; this parser explodes it into one ad per department (falling back to a hardcoded 18-department list if the live pipe-block can't be found), and routes every other IITGN careers page to `generic`.
- **`iit_hyderabad` parser** — IITH mixes permanent faculty listings with rolling project/research postings (JRF/SRF/RA/postdoc) on one careers page; this parser adds department extraction and accurate `post_type` classification (via the new `parsers.parser_utils` helpers) and skips result/cancellation notices that `generic` would otherwise misclassify as postings.
- **`parsers.parser_utils`** — shared link/date/classification helpers (`is_recruitment_link`, `classify_post_type`, `extract_department`, `iter_recruitment_links`, etc.) factored out so site-specific parsers stop re-implementing the same regex logic independently.

### Fixed

- **`iim_recruit` `apply_url`**: PDF-based ads and the no-PDF-found rolling stub both hardcoded `apply_url: None`; they now carry the ad's own PDF URL (loop case) and the careers-page URL (stub case) respectively.

## 0.5.1 (2026-06-25)

### Added

- **`TopicProfile.record_filter_fn`** — an optional record-level acquisition filter, `record_filter_fn(record) -> bool`, applied in `probe_ls`/`probe_rs` after the full Q/A record is built but before it is downloaded, enriched, appended, added to `seen`, or counted. Unlike `filter_fn` (which sees only `title`+`query` at acquisition), it sees the whole record — including fields such as `answer_text` that exist only post-construction — so callers that must match on those can filter at acquisition time instead of dropping rows afterwards. This keeps `--max-records` and the per-bucket `no_match`/`kept` counters aligned with the rows actually kept. `None` (the default) preserves existing behaviour.

- **`academic-jobs` fetch resilience** — `AcademicJobsProbe` now keeps institutions visible when their listing page misbehaves: a 4xx that still serves a substantial body is parsed (some Drupal career portals answer the listing alongside a 404); a registry `robots_override: true` retries past a blanket robots disallow for official public-recruitment sources (the `http_client` session gains a per-call `respect_robots` opt-out); and a registry `fallback_pdf_url` is parsed directly when the listing fetch or parse fails (keeps e.g. IIT Madras visible when its portal is down). Each ad record now carries a `source_method` (`official scrape` / `public-interest override` / `fallback PDF`), and `fetch_status` gains `robots_blocked`.

### Fixed

- **RS per-bucket `no_match` counter**: the normal end-of-bucket audit record in `probe_rs` hardcoded `no_match=0`, so `filter_fn` drops were under-reported in `_runs.jsonl` on every bucket that did not hit `max_records`. It now records the actual `bkt_no_match`, matching the early-return path and `probe_ls`.

## 0.5.0 (2026-06-25)

### Added

- **`commoner-probe budget`** — acquire Union Budget Statement of Budget Estimates (SBE) spreadsheets and RBI "State Finances: A Study of Budgets" documents with SHA-256 provenance. `budget` optional extra (lxml) powers RBI document discovery.
- **`commoner-probe academic-jobs`** — crawl Indian HEI career pages for faculty-recruitment advertisements via a bundled institution registry and per-institution parsers (`generic`, `iim_recruit`, `iit_kanpur`, `anna_university`, `private_university`, `iit_indore`, `iit_rolling`, `jnu`). `academia` optional extra (beautifulsoup4, pdfminer.six).
- **`commoner-probe bills`** — acquire sansad.in bill / legislation records for both houses.
- **`commoner-probe debates`** — acquire Lok Sabha per-sitting-day floor-debate transcript PDFs.
- **Manifest schemas + typed records**: `manifest_budget`, `manifest_academic_job`, `manifest_bill`, `manifest_floor_debate`, each with a `Manifest*Record` dataclass and a `Corpus.manifest_*()` reader.

### Fixed

- `http_client`: robots.txt fetching now has a 10s timeout, fixing an unbounded hang on slow or unresponsive hosts.

## 0.4.1 (2026-06-24)

### Changed

- Package metadata: author now points to the **CommonerLLP** GitHub org; added maintainers (Sreeram N R and skishchampi) and an Organization link under project URLs.

## 0.4.0 (2026-06-22)

### Added

- **`commoner-probe mca-csr`** — download MCA CDM CSR company-spend CSV exports by financial year.
- **`manifest_mca_csr` schema** and `ManifestMcaCsrRecord` / `Corpus.manifest_mca_csr()` for typed access to MCA CSR manifest records.
- **`commoner-probe mines-dmft`** — acquire Ministry of Mines / Odisha DMFT public disclosure files with source provenance.
- **`commoner-probe evidence dmft`** — build side-by-side DMFT evidence bundles from executive disclosure and Sansad oversight records.
- **`docs/ENDPOINTS.md`** — public source-family endpoint reference.
- **`narcotics_substance` built-in topic** for NDPS, trafficking, and substance-abuse oversight records.

### Changed

- **Relicensed**: AGPL-3.0-or-later → MIT, so `commoner-probe` can be the permissive shared acquisition floor that downstream repos (including the non-AGPL `sansad-semantic-crawler`) depend on without copyleft friction.
- `commoner_probe.csr.mca` now uses the verified MCA CDM live contract: `GET /csr-data` for the CSRF-bearing form and `POST /cdm/export.php` for CSV export.
- Public packaging now includes only release-facing docs; local coordination files (`notes/`, `memory/`, `.ai/`, `.beads/`, `.codex/`, `WORKING.md`, `TODO.md`) are ignored and removed from the tracked public tree.
- `scripts/check_leaks.py` now blocks private coordination paths if they are accidentally staged.

## 0.3.0 (2026-06-06)

### Breaking changes

- **Package renamed**: `sansad-crawler` → `commoner-probe`. Update your `pip install` and imports.
  - Python: `from sansad_crawler import ...` → `from commoner_probe import ...`
  - CLI: `sansad-crawl` → `commoner-probe`
  - Subcommands renamed: `crawl` → `sansad`, `crawl-committees` → `committees`, `extract-atr-linkage` → `atr-linkage`
- **New subcommand added**: `state-assembly` (NeVA state assembly portals)
- **Schema field renamed**: `crawled_at` → `probed_at` in all output records
- **Relicensed**: MIT → AGPL-3.0-or-later

### Added

- **`commoner-probe state-assembly`** — probe NeVA state assembly portals (`{portal}.neva.gov.in`). Writes `questions.jsonl`, `questions_unlisted.jsonl`, `members.jsonl`, `papers_laid.jsonl`. Tested on Gujarat assembly 15.
- **HTTP hardening** (`commoner_probe/http_client.py`): SSRF guard, robots.txt checking, per-domain rate limiting (1 req/s), exponential backoff (3 retries), optional `requests_cache` (6h TTL). Install via `pip install commoner-probe[cache]`.
- **Committee composition** (`CommitteeProbe.probe_composition()`): writes `committee_members.jsonl`.
- **`filter_fn` hook on `TopicProfile`**: callable injected by analytics layer at runtime.
- **`classifier_config` in `TopicProfile`**: propagated to `_runs.jsonl` for corpus auditability.
- **JSON schemas for new outputs**: `committee_members`, `state_assembly_question`, `state_assembly_question_unlisted`, `state_assembly_member`, `state_assembly_paper_laid`.
- **`commoner-probe init-topic`**: write a bundled example topic profile to disk (built-ins: `libraries`, `home_affairs_starred`, `affirmative_action`).
- **Single-sourced version**: `__version__` reads from `importlib.metadata` with pyproject fallback.
- **GitHub Actions**: CI (matrix 3.10–3.12, ruff, pytest) and OIDC PyPI release workflow.
- **`MANIFEST.in`**, **`CONTRIBUTING.md`**, **`CODE_OF_CONDUCT.md`** (Contributor Covenant v2.1).

### Changed

- Base class `BaseCrawler` → `BaseProbe`; `crawl_ls`/`crawl_rs` → `probe_ls`/`probe_rs`; `crawl_composition` → `probe_composition`.
- User-Agent: `commoner-probe/0.3.0`.
- HTTP cache env var: `COMMONER_CACHE_DIR` (was `SANSAD_CACHE_DIR`; old name still honoured with deprecation warning).

---

## 0.2.0 (2026-05-21)

### Added

- **`docs/SCHEMAS.md`** — complete field-level reference for every output
  stream: all four manifest record shapes (LS Q/A, RS Q/A, LS committee,
  RS committee), `_runs.jsonl`, three `answers.jsonl` kinds,
  `atr_linkage.jsonl`, and five `entities/*.jsonl` files. Includes
  controlled vocabularies and join-key documentation.

- **JSON Schemas** — twelve Draft-2020-12 schemas shipped as package data
  under `sansad_crawler/schemas/`. Exposed via
  `sansad_crawler.schemas.load(name)` and `schemas.list_all()`.

- **`sansad_crawler/records.py`** — typed dataclasses for every record kind
  (`ManifestQaRecord`, `ManifestCommitteeReportRecord`, `AnswerQaResponse`,
  `AnswerAtrResponse`, `AnswerDfgRecommendation`, `AtrLinkageRecord`,
  `RunRecord`). Each has `from_dict()` that tolerates unknown keys and
  missing optional fields.

- **`sansad_crawler/corpus.py`** — `Corpus` class with streaming iterators
  (`manifest_qa`, `manifest_committee_reports`, `answers_qa`, `answers_atr`,
  `answers_dfg`, `atr_linkages`, `runs`, `entities`), join helpers
  (`join_qa`, `join_atr_chain`), and an opt-in `to_dataframe(stream)` that
  requires `pip install sansad-crawler[pandas]`.

- **`sansad-crawl stats`** — new CLI subcommand that prints corpus health:
  record counts by house/year/ministry/committee/report_type, answers
  extraction coverage, entity resolution rate, and date ranges. Use
  `--json` for machine-readable output.

- **`sansad-crawl validate`** — new CLI subcommand that validates every
  JSONL file in a corpus against its JSON Schema. Requires
  `pip install sansad-crawler[dev]`. Prints line numbers and JSON pointers
  on failure; exits 1 on any error.

- **`[dev]` optional-dependency group** — `jsonschema>=4.20` and
  `pytest>=7`. Install with `pip install sansad-crawler[dev]`.

- **`[pandas]` optional-dependency group** — `pandas>=2.0`. Install with
  `pip install sansad-crawler[pandas]`.

- **`examples/usage.py`** — demonstration script for the `Corpus` API.

### Changed (non-breaking)

- `sansad_crawler.__init__` now re-exports `Corpus`, `QaPair`, `AtrChain`,
  all record dataclasses, and the `schemas` module.
- `run_id` and `crawled_at` in manifest schemas changed from `required` to
  optional (always present in freshly crawled corpora; may be absent in
  synthetic or backfilled data).

### Unchanged

Crawler behaviour, extractor logic, and manifest field set are unchanged.
All corpora produced by v0.1.0 load and validate cleanly under v0.2.0.

---

## 0.1.0 (2026-05-21)

Initial release. Lok Sabha + Rajya Sabha Q/A crawler, standing-committee
report crawler, regex-based Q/A and ATR extractors, ATR linkage extractor,
entity resolution, four CLI subcommands (`crawl`, `crawl-committees`,
`extract-answers`, `extract-atr-linkage`).
