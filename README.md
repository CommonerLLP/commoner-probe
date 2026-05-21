# sansad-crawler

Data-pulling crawler for Indian Parliament — Lok Sabha and Rajya Sabha questions and standing committee reports.

**Scope**: This repo handles data acquisition only — crawling, downloading PDFs, structural extraction (Q/A splitting, ATR linkage), and entity resolution. Classification, discourse analysis, and dossier generation live in a separate downstream repo.

## Install

```bash
pip install -e ".[pdf,http]"
```

Extras:
- `http` — uses `requests` for HTTP (recommended; falls back to stdlib `urllib` without it)
- `pdf` — uses `pdfminer.six` for PDF text extraction; falls back to system `pdftotext`

## Commands

### `sansad-crawl crawl`

Crawl Lok Sabha and/or Rajya Sabha parliamentary questions.

```bash
sansad-crawl crawl \
  --topic examples/topics/libraries.json \
  --out data/libraries \
  --house both \
  --from-date 2022-01-01 \
  --to-date 2026-01-01
```

Key flags:
- `--topic` — path to topic profile JSON (defines search queries and ministry filters)
- `--out` — corpus output directory
- `--house` — `ls`, `rs`, or `both` (default: both)
- `--no-download` — skip PDF downloads; record metadata only
- `--max-records N` — stop after N new records per house (smoke-test brake)
- `--max-buckets N` — only run the first N query/ministry combinations
- `--sessions` — Rajya Sabha session range, e.g. `230-267` (default: `1-267`)
- `--with-entities` — resolve asker names to stable `entity_id`s via the entity store

**RS behaviour note**: all rows matching the ministry filter are kept; no in-crawler classification is applied. Downstream consumers should classify using their own classifiers.

### `sansad-crawl crawl-committees`

Crawl standing committee reports (LS DRSCs and RS DRSCs).

```bash
sansad-crawl crawl-committees \
  --topic examples/topics/libraries.json \
  --out data/committees \
  --house both \
  --committees finance,education
```

Key flags:
- `--committees` — comma-separated committee slugs; omit for all committees
- `--lok-sabha-no` — LS number for LS reports (default: 18)

Available LS committees: `agriculture`, `chemicals`, `coal`, `communications`, `consumer_affairs`, `defence`, `energy`, `external_affairs`, `finance`, `housing`, `labour`, `petroleum`, `railways`, `rural_development`, `social_justice`, `water_resources`

Available RS committees: `commerce`, `education`, `health`, `home_affairs`, `industry`, `personnel`, `science`, `transport`

### `sansad-crawl extract-answers`

Extract structured (question/answer) and (recommendation/response) pairs from downloaded PDFs.

```bash
sansad-crawl extract-answers --out data/libraries
```

Writes `answers.jsonl` with `kind` = `qa_response` | `atr_response` | `dfg_recommendation`.
Requires `pip install -e ".[pdf]"` for PDF text extraction.

### `sansad-crawl extract-atr-linkage`

Parse ATR titles to find which original committee report each Action Taken Report responds to.

```bash
sansad-crawl extract-atr-linkage --out data/committees
```

Writes `atr_linkage.jsonl` with `atr_key` → `references_report_key` mappings.

### `sansad-crawl validate`

Validate every JSONL file in a corpus directory against its JSON Schema.

```bash
sansad-crawl validate --out data/libraries
```

Requires `pip install "sansad-crawler[dev]"`. Exits 0 when all records
are valid; exits 1 and prints line numbers + field paths on failure.
Use `--max-errors N` to control how many errors are shown per file.

## Topic profile format

A topic profile is a JSON file with:

```json
{
  "name": "libraries",
  "description": "...",
  "search_groups": {
    "public_libraries": ["public library", "rural library"],
    "policy": ["National Mission on Libraries", "RRRLF"]
  },
  "lok_sabha_ministries": ["CULTURE", "EDUCATION"],
  "rajya_sabha_ministry_likes": ["CULTURE", "EDUCATION"]
}
```

Fields `tag_rules`, `classifier`, and `fallback_tag` from the old `sansad-semantic-crawler` format are silently ignored — classification is handled downstream.

See `examples/topics/` for full examples.

## Output files

Each corpus directory contains:

| File | Contents |
|------|----------|
| `manifest.jsonl` | One record per question or committee report |
| `_runs.jsonl` | Audit log: scope, topic hash, errors, per-bucket counts |
| `answers.jsonl` | Extracted Q/A and recommendation/response pairs |
| `atr_linkage.jsonl` | ATR → original report linkages |
| `pdfs/ls/` | Downloaded LS PDFs |
| `pdfs/rs/` | Downloaded RS PDFs |
| `crawl.log` | Human-readable crawl progress log |

For complete field-level documentation of every output stream — including all four
manifest record shapes, controlled vocabularies, and join keys — see
[`docs/SCHEMAS.md`](docs/SCHEMAS.md).

## Entity resolution (`--with-entities`)

When `--with-entities` is passed to `crawl`, asker names are resolved to stable `entity_id` values from a local entity store backed by the sansad.in MP roster API. On first run the store is populated automatically; subsequent runs reuse the cached store.
