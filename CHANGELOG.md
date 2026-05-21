# Changelog

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
