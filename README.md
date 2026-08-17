# commoner-probe

[![PyPI](https://img.shields.io/pypi/v/commoner-probe)](https://pypi.org/project/commoner-probe/)
[![Python versions](https://img.shields.io/pypi/pyversions/commoner-probe)](https://pypi.org/project/commoner-probe/)
[![CI](https://github.com/CommonerLLP/commoner-probe/actions/workflows/ci.yml/badge.svg)](https://github.com/CommonerLLP/commoner-probe/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/pypi/l/commoner-probe)](LICENSE)

Sousveillance infrastructure for the state's mandatory disclosure systems.

A commoner probes the state's own paperwork — parliamentary questions, committee
reports, state assembly records — and turns it into evidence. `commoner-probe`
automates the acquisition so you can focus on the analysis.

```bash
pip install "commoner-probe[all]"
```

```python
import commoner_probe as probe   # alias used throughout these docs
```

---

## Why this exists

Parliamentary questions, committee reports, floor debates, bills, state
assembly records, CSR exports, public mining-district disclosures, Union
Budget files, and faculty-recruitment ads from public universities are
mandatory or official public disclosures. The data exists. The problem
is that it lives across undocumented portals with inconsistent APIs, no bulk
export, and PDFs that require extraction to read programmatically.

`commoner-probe` handles the entire acquisition pipeline:

```
public disclosure portals  →  manifest.jsonl  →  files/PDFs  →  extracted records  →  your analysis
                               (metadata)        (raw source)      (structured text)
```

Classification, topic modelling, and dossier generation are intentionally out
of scope. This library does one thing: acquire public disclosure data into
provenance-rich, schema-validated JSONL and source files.

---

## Install

Requires Python 3.10+. Released on [PyPI](https://pypi.org/project/commoner-probe/).

```bash
pip install "commoner-probe[all]"          # everything needed for acquisition + extraction
pip install "commoner-probe[all,dev]"      # + schema validation, tests, lint
```

The core package has **zero required dependencies**; each capability is an extra:

| Extra | Pulls in | Needed for |
|---|---|---|
| `http` | requests | any network acquisition |
| `pdf` | pdfminer.six | `extract-answers`, PDF text extraction |
| `budget` | lxml | `budget` (RBI page discovery) |
| `academia` | beautifulsoup4, pdfminer.six | `academic-jobs` |
| `pandas` | pandas | `Corpus.to_dataframe()` |
| `all` | requests, pdfminer.six, lxml, beautifulsoup4 | everything above except pandas |
| `dev` | jsonschema, pytest, ruff, lxml, beautifulsoup4 | `validate`, running the test suite |

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

### Floor debates (Lok Sabha)

`debates` acquires the Lok Sabha "text of debate" record: one PDF transcript per
*sitting day*. It enumerates sitting dates per Lok Sabha / session, then fetches
each day's transcript (optionally downloading the PDF with a SHA-256). It is a
day-by-day document acquisition — verbatim text and per-speaker segmentation are
left to a downstream extraction step. The richest longitudinal record of what is
said on the floor.

```bash
commoner-probe debates \
  --out data/debates \
  --loksabhas 18 \
  --download
```

### Bills and legislation

`bills` fetches the sansad.in legislation catalog — every bill with its
introduction date, stage dates, and status — deduplicated by a stable key (no
topic profile needed; the bill list is an exhaustive catalog). Enables tracking
legislative velocity, committee-scrutiny rates, and private-member-bill outcomes.

```bash
commoner-probe bills \
  --out data/bills \
  --house both \
  --bill-type "Private Member"
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

### State Acts, amendments, rules, and notifications (India Code)

India Code (indiacode.nic.in) is the government's own statutory-instrument
archive: every state's Acts plus their amendments, rules, regulations,
notifications, orders, circulars, ordinances, and statutes, each with a
downloadable PDF. `indiacode` enumerates a state's full Act catalog and
parses every instrument found on each Act's page.

```bash
commoner-probe indiacode --out data/indiacode --states "West Bengal"
```

```python
import commoner_probe as probe

c = probe.Corpus("data/indiacode")
for r in c.manifest_indiacode():
    if r.is_amendment:
        print(r.state, r.short_title, r.instrument_date, r.description)
```

### MCA CSR company-spend exports

The Ministry of Corporate Affairs CDM CSR data page exposes downloadable CSV
exports by financial year. These records compare reporting/spending companies
and project-sector amounts. They do not identify CSR consultants or implementing
agencies unless MCA publishes that in the source export.

```bash
commoner-probe mca-csr \
  --out data/mca-csr \
  --years 2022-23,2021-22
```

```python
import commoner_probe as probe

c = probe.Corpus("data/mca-csr")
for r in c.manifest_mca_csr():
    print(r.financial_year, r.status, r.filename)
```

### Mines DMFT / PMKKKY disclosures

`mines-dmft` acquires raw Ministry of Mines and Odisha DMFT public disclosure
files. Ministry CSVs are current cumulative snapshots timestamped by the
source; treat them as snapshots, not fiscal-year series.

```bash
commoner-probe mines-dmft \
  --out data/mines-dmft \
  --sources mines-gov-in,odisha
```

Pair the executive disclosure snapshots with Sansad oversight records without
flattening the source families:

```bash
commoner-probe evidence dmft \
  --mines-dmft-dir data/mines-dmft \
  --sansad-dir data/sansad/mines-dmft-pmkkky \
  --out data/evidence/dmft.json
```

### Union Budget & RBI State-Finances

`budget` acquires fiscal source files: Union Budget SBE (Statement of Budget
Estimates) spreadsheets — a static table of per-fiscal-year URL templates expanded
across the requested demand numbers — and RBI State-Finances documents discovered
from the RBI publication page. Each file is downloaded with existence-skip and a
SHA-256, one `budget_source_file` record per file. Acquisition only: the
spreadsheet→rows parsing stays downstream (it needs pandas).

```bash
commoner-probe budget \
  --out data/budget \
  --sources union-budget,rbi-state-finances \
  --demands 101,1,33
```

### Academic faculty-recruitment ads

`academic-jobs` crawls Indian higher-education-institution (HEI) career pages for
faculty-recruitment advertisements, driven by a bundled institution registry. Each
ad becomes one `academic_job_posting` record; fetch/parse failures and
empty-result cases are recorded so coverage gaps are visible rather than silent.
(Migrated from an earlier academic-jobs scraper.)

```bash
commoner-probe academic-jobs \
  --out data/academic-jobs \
  --institutions iit-kharagpur,iit-bombay
```

---

## Commands

39 subcommands across parliament, courts, budgets, census, state registers and archives.
`commoner-probe --help` lists them; **[docs/CLI.md](docs/CLI.md)** documents
each one with a worked example.

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
| `vacancy_rows.jsonl` | Typed sanctioned/in-position/vacant rows from vacancy-disclosure answers |
| `atr_linkage.jsonl` | ATR → original report linkages |
| source CSV/JSON/HTML files | Raw source files for source-specific probes such as MCA CSR and DMFT |
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
for r in c.manifest_mca_csr():            # ManifestMcaCsrRecord
    ...
for r in c.manifest_mines_dmft():         # ManifestMinesDmftRecord
    ...
for r in c.manifest_doe_pay_allowances(): # ManifestDoePayAllowancesRecord
    ...
for r in c.vacancy_rows():                # VacancyRowRecord
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
See [`docs/ENDPOINTS.md`](docs/ENDPOINTS.md) for source-family endpoint notes.
See [`docs/GOV_SITE_PLATFORMS.md`](docs/GOV_SITE_PLATFORMS.md) for which
Union ministry websites are scrapeable (and which are JS-rendered SPAs, WAF-
blocked, or unreachable) — read before adding a new `ministry-ddg` portal.

---

## Contributing

Bug reports, portal breakage reports, and pull requests are welcome at
[github.com/CommonerLLP/commoner-probe](https://github.com/CommonerLLP/commoner-probe).
See [`CONTRIBUTING.md`](CONTRIBUTING.md) for development setup and conventions,
and [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) for community expectations.
Release history lives in [`CHANGELOG.md`](CHANGELOG.md).

Government portals change without notice — if a probe stops working, an issue
with the failing command and its `probe.log` output is the most useful report.

---

## License

MIT License — see [`LICENSE`](LICENSE).

`commoner-probe` is sousveillance infrastructure, built for the commons. It is
released under the permissive MIT license so it can serve as a shared
acquisition floor that any downstream project — including the other repos in the
CommonerLLP federation, whatever their own licenses — can build on without
copyleft friction.

---

## Upcoming

### MP profiles and career timelines

Structured biographical data for each member: constituency, state, party, terms
served, educational background, declared profession. Pairs with the Q/A corpus
for studies of how MP background predicts parliamentary participation.
