# commoner-probe

Sousveillance infrastructure for the state's mandatory disclosure systems.

A commoner probes the state's own paperwork — parliamentary questions, committee
reports, state assembly records — and turns it into evidence. `commoner-probe`
automates the acquisition so you can focus on the analysis.

```bash
pip install "commoner-probe[all]"
import commoner_probe as probe   # alias used throughout CommonerLLP toolchain
```

---

## Why this exists

Parliamentary questions, committee reports, and state assembly records are
mandatory disclosures — the state is legally obligated to publish them under
the Right to Information Act 2005, Section 4. The data exists. The problem
is that it lives across undocumented portals with inconsistent APIs, no bulk
export, and PDFs that require extraction to read programmatically.

`commoner-probe` handles the entire acquisition pipeline:

```
state disclosure portals  →  manifest.jsonl  →  PDFs  →  answers.jsonl  →  your analysis
                              (metadata)                  (structured text)
```

Classification, topic modelling, and dossier generation are intentionally out
of scope. This library does one thing — acquire the data into clean,
schema-validated JSONL — and does it reliably.

---

## Install

```bash
pip install "commoner-probe[all]"          # requests + PDF extraction
pip install "commoner-probe[all,dev]"      # + schema validation and tests
```

---

## Five-minute quickstart

### Step 1 — Write a topic profile

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

### Step 2 — Probe parliamentary questions

```bash
commoner-probe sansad \
  --topic topic.json \
  --out data/climate \
  --house both \
  --from-date 2019-01-01
```

Writes `data/climate/manifest.jsonl` — one record per question from both houses.

### Step 3 — Probe committee reports

```bash
commoner-probe committees \
  --topic topic.json \
  --out data/climate-committees \
  --house both
```

One record per standing committee report (LS and RS DRSCs).

### Step 4 — Extract text from PDFs

```bash
commoner-probe extract-answers --out data/climate
commoner-probe extract-answers --out data/climate-committees
```

Parses downloaded PDFs into `answers.jsonl`: Q/A pairs, committee
recommendations, and government responses.

### Step 5 — Load in Python

```python
import commoner_probe as probe

c = probe.Corpus("data/climate")

for r in c.manifest_qa():
    print(r.date, r.house, r.ministry, r.title)

for pair in c.join_qa():
    if pair.answers:
        print(pair.manifest.title)
        print(pair.answers[0].question_text[:200])
```

---

## What you can study

### Parliamentary questions (Lok Sabha + Rajya Sabha)

Each record carries who asked (MP name, party, state), which ministry answered,
question number, type (starred / unstarred), date, session, and the full PDF.
After `extract-answers` — extracted question and answer text.

**Typical research questions**: ministry responsiveness rates, which MPs ask
the most questions by topic, how the same policy question evolves across
sessions, party-level questioning patterns.

```python
import commoner_probe as probe
from collections import Counter

c = probe.Corpus("data/climate")
ministry_counts = Counter(r.ministry for r in c.manifest_qa())
for ministry, n in ministry_counts.most_common(10):
    print(f"{ministry}: {n}")
```

### Standing committee reports (LS + RS DRSCs)

Committee reports come in four shapes:

| `report_type` | What it is |
|---|---|
| `demands_for_grants` | Annual budget scrutiny — the committee dissects ministry spending |
| `bill` | The committee's examination of a pending bill before it passes |
| `subject` | Own-initiative policy investigation — deepest substantive record |
| `action_taken` | The government's formal response to the committee's recommendations |

Action Taken Reports (ATRs) are the government's formal written responses to
committee recommendations. The `atr-linkage` command connects each ATR back
to the original report, enabling lifecycle analysis:
*recommendation → government rejection/acceptance → follow-up*.

```python
import commoner_probe as probe

c = probe.Corpus("data/climate-committees")

for chain in c.join_atr_chain():
    print(f"Report: {chain.original and chain.original.title}")
    print(f"  Recommendations: {len(chain.original_observations)}")
    print(f"  Government responses: {len(chain.atr_answers)}")
```

### State assembly records (NeVA portals)

From 2020, sub-national governments have been adopting NIC's NeVA (National
e-Vidhan Application) infrastructure under a centrally sponsored scheme run
by the Ministry of Parliamentary Affairs. Most state assemblies are onboarding,
though coverage varies. The `state-assembly` command probes any NeVA portal:

```bash
commoner-probe state-assembly \
  --portal gujarat \
  --state GJ \
  --out data/gujarat-assembly \
  --assemblies 15
```

---

## All commands

### `commoner-probe sansad` — parliamentary questions

```bash
commoner-probe sansad \
  --topic topic.json \
  --out data/climate \
  --house both \
  --from-date 2019-01-01 \
  --to-date 2026-01-01
```

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

### `commoner-probe committees` — standing committee reports

```bash
commoner-probe committees \
  --topic topic.json \
  --out data/committees \
  --house both \
  --committees finance,education
```

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

### `commoner-probe extract-answers` — PDF text extraction

```bash
commoner-probe extract-answers --out data/climate
commoner-probe extract-answers --out data/climate --refresh
```

Reads `manifest.jsonl` and downloaded PDFs; writes `answers.jsonl` with:

- `qa_response` — (question_text, answer_text) pairs from Q/A PDFs
- `atr_response` — (recommendation_no, recommendation_text, response_text) triples from ATR PDFs
- `dfg_recommendation` — numbered observation paragraphs from DFG/Bill/Subject PDFs

Requires `pip install "commoner-probe[pdf]"`.

### `commoner-probe atr-linkage` — ATR → original report

```bash
commoner-probe atr-linkage --out data/committees
```

Writes `atr_linkage.jsonl` — each ATR linked back to the report it responds to.
Safe to re-run (idempotent overwrite).

### `commoner-probe state-assembly` — state legislature records

```bash
commoner-probe state-assembly \
  --portal gujarat \
  --state GJ \
  --out data/gujarat \
  --assemblies 15
```

### `commoner-probe stats` — corpus health

```bash
commoner-probe stats --out data/climate
commoner-probe stats --out data/climate --json
```

### `commoner-probe validate` — schema validation

```bash
commoner-probe validate --out data/climate
```

Validates every JSONL file against its JSON Schema. Exits 1 on errors.
Requires `[dev]` extra.

---

## Topic profile

Controls what the probe acquires:

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
- `lok_sabha_ministries` — exact ministry filter for LS (case-sensitive).
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
| `probe.log` | Human-readable probe progress log |

For complete field-level documentation see [`docs/SCHEMAS.md`](docs/SCHEMAS.md).

---

## Entity resolution (`--with-entities`)

Pass `--with-entities` to `commoner-probe sansad` to resolve asker names to
stable `entity_id` values. On first run the entity store is populated from
the sansad.in MP roster; subsequent runs reuse the local cache.

Resolved entity IDs join across corpora and sessions — useful for studying
the same MP's questioning behaviour over time or across houses.

---

## Python API

```python
import commoner_probe as probe

c = probe.Corpus("data/climate")

# Typed iterators
for r in c.manifest_qa():                 # ManifestQaRecord
    ...
for r in c.manifest_committee_reports():  # ManifestCommitteeReportRecord
    ...
for r in c.answers_qa():                  # AnswerQaResponse
    ...
for r in c.answers_atr():                 # AnswerAtrResponse
    ...
for r in c.answers_dfg():                 # AnswerDfgRecommendation
    ...
for r in c.atr_linkages():                # AtrLinkageRecord
    ...
for r in c.runs():                        # RunRecord
    ...

# Join helpers
for pair in c.join_qa():                  # manifest + extracted answers
    ...
for chain in c.join_atr_chain():          # ATR + original report + observations
    ...

# pandas (pip install commoner-probe[pandas])
df = c.to_dataframe("manifest_committee_reports")
```

See [`examples/usage.py`](examples/usage.py) for a runnable walkthrough.

---

## License

GNU Affero General Public License v3 or later (AGPL-3.0-or-later).

`commoner-probe` is sousveillance infrastructure — built for the commons, not
for commercial extraction. AGPL ensures that anyone running a modified version
as a service must publish their source. Researchers, journalists, and civic
tech organisations are unaffected.

---

## Upcoming

### Floor debates

sansad.in exposes full debate proceedings via `api_ls/debate/text-of-debate`
(structured JSON, 17th Lok Sabha onwards). Each record covers a single day:
type of business, member who spoke, and verbatim text. The richest longitudinal
record of what MPs say on the floor.

### Bills and legislation

`sansad.in/ls/legislation/bills` lists every bill since independence with
introduction date, debate dates, and status at each stage. Enables tracking
legislative velocity, committee scrutiny rates, and private member bill outcomes.

### MP profiles and career timelines

Structured biographical data for each member: constituency, state, party, terms
served, educational background, declared profession. Pairs with the Q/A corpus
for studies of how MP background predicts parliamentary participation.
