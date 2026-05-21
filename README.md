# sansad-crawler

A Python library and CLI for pulling structured data from the Indian Parliament.
Built for researchers, journalists, and civic technologists who want to study
parliamentary behaviour, policy trends, and government accountability — without
writing scraper boilerplate.

## Why this exists

Sansad.in hosts a remarkable public record: every question asked in Lok Sabha
and Rajya Sabha since independence, thousands of standing committee reports,
floor debates, bills, and MP profiles. The data is all there. The problem is
that it lives across three separate portals with inconsistent APIs, no bulk
export, and PDFs that require extraction to read programmatically.

`sansad-crawler` handles the entire acquisition pipeline:

```
sansad.in APIs  →  manifest.jsonl  →  PDFs  →  answers.jsonl  →  your analysis
                    (metadata)                  (structured text)
```

Classification, topic modelling, and dossier generation are intentionally out
of scope and live downstream. This library does one thing — get the data into
clean, schema-validated JSONL — and does it reliably.

---

## Install

```bash
pip install "sansad-crawler[all]"
```

For schema validation and tests:

```bash
pip install "sansad-crawler[all,dev]"
```

---

## Five-minute quickstart

### Step 1 — Write a topic profile

Create `topic.json` to declare what you want to pull:

```json
{
  "name": "climate",
  "description": "Climate change and environmental policy",
  "search_groups": {
    "climate": ["climate change", "global warming", "net zero"],
    "air_quality": ["air pollution", "AQI", "particulate matter"]
  },
  "lok_sabha_ministries": ["ENVIRONMENT", "POWER", "PETROLEUM"],
  "rajya_sabha_ministry_likes": ["ENVIRONMENT", "POWER", "PETROLEUM"]
}
```

### Step 2 — Crawl questions

```bash
sansad-crawl crawl \
  --topic topic.json \
  --out data/climate \
  --house both \
  --from-date 2019-01-01
```

This pulls every parliamentary question on your topic from both houses and
writes `data/climate/manifest.jsonl` — one record per question.

### Step 3 — Crawl committee reports

```bash
sansad-crawl crawl-committees \
  --topic topic.json \
  --out data/climate-committees \
  --house both
```

Writes one record per standing committee report (LS and RS DRSCs).

### Step 4 — Extract text from PDFs

```bash
sansad-crawl extract-answers --out data/climate
sansad-crawl extract-answers --out data/climate-committees
```

Parses downloaded PDFs into `answers.jsonl`: Q/A pairs, committee
recommendations, and government responses.

### Step 5 — Load in Python

```python
from sansad_crawler import Corpus

c = Corpus("data/climate")

# Every Q/A record
for r in c.manifest_qa():
    print(r.date, r.house, r.ministry, r.title)

# Full text pairs
for pair in c.join_qa():
    if pair.answers:
        print(pair.manifest.title)
        print(pair.answers[0].question_text[:200])
```

---

## What you can study

### Parliamentary questions (Lok Sabha + Rajya Sabha)

The Q/A corpus is the primary instrument for studying how MPs hold the
executive accountable. Each record carries:

- Who asked (MP name, party, state — resolvable to a stable `entity_id` with `--with-entities`)
- Which ministry answered
- The question number, type (starred / unstarred), date, and session
- The full PDF and — after `extract-answers` — extracted question text and answer text

**Typical research questions**: ministry responsiveness rates, which MPs ask
the most questions by topic, how the same policy question evolves across
sessions, party-level questioning patterns.

```python
from sansad_crawler import Corpus
from collections import Counter

c = Corpus("data/climate")
ministry_counts = Counter(r.ministry for r in c.manifest_qa())
for ministry, n in ministry_counts.most_common(10):
    print(f"{ministry}: {n}")
```

### Standing committee reports (LS + RS DRSCs)

Committee reports are a richer but less-studied record. They come in four
shapes:

| `report_type` | What it is |
|---|---|
| `demands_for_grants` | Annual budget scrutiny — the committee dissects ministry spending |
| `bill` | The committee's examination of a pending bill before it passes |
| `subject` | Own-initiative policy investigation — deepest substantive record |
| `action_taken` | The government's formal response to the committee's recommendations |

The ATR linkage (`extract-atr-linkage`) connects each Action Taken Report
back to the original recommendations it responds to, enabling lifecycle
analysis: *recommendation → government rejection/acceptance → follow-up*.

```python
from sansad_crawler import Corpus

c = Corpus("data/climate-committees")

# Track the full recommendation lifecycle
for chain in c.join_atr_chain():
    print(f"Report: {chain.original and chain.original.title}")
    print(f"  Recommendations: {len(chain.original_observations)}")
    print(f"  Government responses: {len(chain.atr_answers)}")
```

---

## All commands

### `sansad-crawl crawl` — Q/A questions

```bash
sansad-crawl crawl \
  --topic topic.json \
  --out data/climate \
  --house both \
  --from-date 2019-01-01 \
  --to-date 2026-01-01
```

Key flags:

| Flag | Default | What it does |
|---|---|---|
| `--topic` | required | Path to topic profile JSON |
| `--out` | required | Output corpus directory |
| `--house` | `both` | `ls`, `rs`, or `both` |
| `--from-date` | — | Earliest question date (YYYY-MM-DD) |
| `--to-date` | — | Latest question date |
| `--qtype` | `both` | `starred`, `unstarred`, or `both` |
| `--sessions` | `1-267` | Rajya Sabha session range |
| `--no-download` | off | Skip PDF downloads; metadata only |
| `--with-entities` | off | Resolve asker names to stable entity IDs |
| `--max-records N` | — | Stop after N new records per house (smoke-test) |
| `--max-buckets N` | — | Only run the first N search/ministry combos |
| `--reset` | off | Wipe existing manifest and start fresh |

### `sansad-crawl crawl-committees` — Committee reports

```bash
sansad-crawl crawl-committees \
  --topic topic.json \
  --out data/committees \
  --house both \
  --committees finance,education
```

Key flags:

| Flag | Default | What it does |
|---|---|---|
| `--committees` | all | Comma-separated committee slugs |
| `--lok-sabha-no` | `18` | LS number for LS reports |
| `--from-date` / `--to-date` | — | Date range filter |
| `--no-download` | off | Skip PDF downloads |

**Available LS committees** (16 DRSCs):
`agriculture`, `chemicals`, `coal`, `communications`, `consumer_affairs`,
`defence`, `energy`, `external_affairs`, `finance`, `housing`, `labour`,
`petroleum`, `railways`, `rural_development`, `social_justice`, `water_resources`

**Available RS committees** (8 DRSCs):
`commerce`, `education`, `health`, `home_affairs`, `industry`, `personnel`,
`science`, `transport`

### `sansad-crawl extract-answers` — PDF text extraction

```bash
sansad-crawl extract-answers --out data/climate
sansad-crawl extract-answers --out data/climate --refresh  # re-extract everything
```

Reads `manifest.jsonl` and downloaded PDFs; writes `answers.jsonl` with:

- `qa_response` — (question_text, answer_text) pairs from Q/A PDFs
- `atr_response` — (recommendation_no, recommendation_text, response_text) triples from ATR PDFs
- `dfg_recommendation` — numbered observation paragraphs from DFG/Bill/Subject PDFs

Requires `pip install "sansad-crawler[pdf]"`.

### `sansad-crawl extract-atr-linkage` — ATR → original report

```bash
sansad-crawl extract-atr-linkage --out data/committees
```

Writes `atr_linkage.jsonl` — each ATR record linked back to the report it responds to.
Run once per committee corpus; safe to re-run (idempotent overwrite).

### `sansad-crawl stats` — Corpus health

```bash
sansad-crawl stats --out data/climate
sansad-crawl stats --out data/climate --json   # machine-readable
```

### `sansad-crawl validate` — Schema validation

```bash
sansad-crawl validate --out data/climate
```

Validates every JSONL file against its JSON Schema. Exits 1 and prints
field-level errors if anything is malformed. Requires `[dev]` extra.

---

## Topic profile

A topic profile is a JSON file that controls what the crawler pulls:

```json
{
  "name": "libraries",
  "description": "Public library infrastructure and policy",
  "search_groups": {
    "public_libraries": ["public library", "rural library"],
    "policy": ["National Mission on Libraries", "RRRLF"]
  },
  "lok_sabha_ministries": ["CULTURE", "EDUCATION"],
  "rajya_sabha_ministry_likes": ["CULTURE", "EDUCATION"]
}
```

- `search_groups` — keyword groups for LS full-text search. Each query runs
  independently; results are union-deduped on `key`.
- `lok_sabha_ministries` — exact ministry filter for LS (case-sensitive, matches
  the `dc.relation.ministry` field).
- `rajya_sabha_ministry_likes` — ministry LIKE filter for RS (prefix match).

See `examples/topics/` for working examples.

---

## Output files

| File | Contents |
|------|----------|
| `manifest.jsonl` | One record per question or committee report |
| `_runs.jsonl` | Audit log: scope, topic hash, errors, per-bucket counts |
| `answers.jsonl` | Extracted Q/A and recommendation/response pairs |
| `atr_linkage.jsonl` | ATR → original report linkages |
| `pdfs/ls/` | Downloaded LS PDFs |
| `pdfs/rs/` | Downloaded RS PDFs |
| `crawl.log` | Human-readable crawl progress log |

For complete field-level documentation — all four manifest record shapes,
controlled vocabularies, and join keys — see [`docs/SCHEMAS.md`](docs/SCHEMAS.md).

---

## Entity resolution (`--with-entities`)

Pass `--with-entities` to `sansad-crawl crawl` to resolve asker names to
stable `entity_id` values. On first run the entity store is populated from
the sansad.in MP roster API; subsequent runs reuse the local cache.

Resolved entity IDs join across corpora and sessions — useful for studying
the same MP's questioning behaviour over time or across houses.

---

## Python API

```python
from sansad_crawler import Corpus

c = Corpus("data/climate")

# Typed iterators
for r in c.manifest_qa():               # ManifestQaRecord
    ...
for r in c.manifest_committee_reports(): # ManifestCommitteeReportRecord
    ...
for r in c.answers_qa():                # AnswerQaResponse
    ...
for r in c.answers_atr():              # AnswerAtrResponse
    ...
for r in c.answers_dfg():              # AnswerDfgRecommendation
    ...
for r in c.atr_linkages():             # AtrLinkageRecord
    ...
for r in c.runs():                     # RunRecord
    ...

# Join helpers
for pair in c.join_qa():               # manifest + extracted answers
    ...
for chain in c.join_atr_chain():       # ATR + original report + observations
    ...

# pandas (pip install sansad-crawler[pandas])
df = c.to_dataframe("manifest_committee_reports")
```

See [`examples/usage.py`](examples/usage.py) for a runnable walkthrough.

---

## Upcoming

The following data sources are available on sansad.in and are candidates for
future crawlers in this library. PRs welcome.

### Floor debates

The sansad.in eLibrary exposes full debate proceedings via
`api_ls/debate/text-of-debate` (structured JSON, 17th Lok Sabha onwards). Each
debate record covers a single day and house: the type of business (bill, motion,
statement by minister, zero hour), the member who spoke, and the verbatim text.
This is the richest longitudinal record of what MPs say on the floor — far more
than Q/A — and an essential dataset for speech analysis, political communication
research, and accountability journalism.

### Bills and legislation

`sansad.in/ls/legislation/bills` lists every government and private member's
bill introduced since independence, with introduction date, debate dates, status
at each stage (introduced / committee referral / passed in LS / passed in RS /
presidential assent), and gazette notification. A bills crawler would let
researchers track legislative velocity, committee scrutiny rates, and what
happens to private member bills over time.

### MP profiles and career timelines

Beyond the MP roster already used for entity resolution, sansad.in exposes
structured biographical data for each member: constituency, state, party,
terms served, educational background, and declared profession. Pairing this
with the Q/A corpus and committee membership data would enable studies of how
MP background, seniority, and party affiliation predict parliamentary
participation and questioning patterns.
