# Command reference

Every subcommand, its flags, and a worked example.
`commoner-probe <command> --help` says the same thing at the terminal.

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
| `--topic` | required* | Path to topic profile JSON (*unless `--member`, `--entity-id`, `--mp-code`, or `--all`) |
| `--member` | — | Per-member retrieval by name prefix (identity-UNSAFE where names collide — prefer `--mp-code`) |
| `--mp-code N` | — | Identity-safe per-member retrieval by stable member code; requires explicit `--house ls` or `--house rs` |
| `--all` | off | Full-corpus enumeration: every question, no topic/member filter |
| `--out` | required | Output corpus directory |
| `--house` | `both` | `ls`, `rs`, or `both` |
| `--from-date` | — | Earliest question date (YYYY-MM-DD) |
| `--to-date` | — | Latest question date |
| `--qtype` | `both` | `starred`, `unstarred`, or `both` |
| `--sessions` | `1-267` | Rajya Sabha session range (must be explicit with `--all`) |
| `--no-download` | off | Skip PDF downloads; metadata only |
| `--with-entities` | off | Resolve asker names to stable entity IDs |
| `--max-records N` | — | Stop after N new records per house (smoke-test) |
| `--max-buckets N` | — | Only run the first N search/ministry combos |
| `--reset` | off | Wipe existing manifest and start fresh |
| `--reset-window ID` | — | Force re-crawl of one enumeration window (repeatable) |

**Member-ID retrieval** (`--mp-code`) is the identity-safe per-member mode.
Name-prefix retrieval (`--member`) mis-attributes wherever names collide
across members or terms (a real incident: a name-prefix pull returned an
earlier, different RS MP's 2012 records under a current member's assumed
on-file name). LS and RS member codes are separate numbering spaces, so
`--mp-code` refuses `--house both`. RS retrieval pins the code in the API
whereclause and drops any row echoing a different `mp_code`; LS retrieval
resolves the code against the sansad.in roster, then exact-name-joins the
portal question list scoped to the member's `lastLoksabha` (the LS question
list carries names, not codes — the roster resolution warns when another
roster entry shares the resolved name, and only the member's last Lok Sabha
is covered).

```bash
commoner-probe sansad --mp-code 2372 --house rs --sessions 260-267 --out data/sanjay-singh
```

**Full-corpus enumeration** (`--all`) pages through every question — LS in
calendar-month windows over `--from-date`/`--to-date`, RS one window per
session in `--sessions`. Window state goes to `_windows.jsonl`: a window whose
run recorded errors is marked `"status": "suspect"` and re-crawled on the next
run; only complete, non-suspect windows are skipped on resume.

```bash
commoner-probe sansad --all \
  --out data/sansad-full \
  --house both \
  --from-date 2024-07-01 --to-date 2024-07-31 \
  --sessions 264-265 \
  --no-download
```

**Lok Sabha by term and session** (`--loksabha`) takes a second route for the
same records: the LS portal's own question list, one session at a time, instead
of DSpace calendar months. Prefer it whenever the session numbers are known.
Measured 2026-08-14 on lkNo 18 session 8, 4,500 records both ways:

| | DSpace (`--from-date`/`--to-date`) | portal list (`--loksabha`) |
|---|---|---|
| requests | ~18 per calendar month | **5 for the session** |
| `pdf_url` on the record | none — every download resolves item → bundle → bitstream | **present on every row** |

Neither carries question or answer text; both leave it in the PDF. Session
numbers here are **LS-relative** (`8` for Monsoon 2026, not the continuous
`271`), so window ids carry the term: `ls:18:8`. `--loksabha` requires
`--house ls` and an explicit `--sessions`.

```bash
commoner-probe sansad --all \
  --out data/ls-18 \
  --house ls \
  --loksabha 18 --sessions 1-8 \
  --no-download
```

**The DSpace path lags.** Measured 2026-08-14 over an identical span, the portal returned
34,724 LS records and DSpace 29,970 — with **zero** records DSpace had and the portal did
not. The 4,754 difference is almost entirely one whole recent session: eLibrary had not yet
indexed it, and its newest record anywhere was four months old. Use the portal path for
anything recent, and never use DSpace as a completeness check on the current session. DSpace
remains the right tool when session numbers are unknown, it is the only source for
`sansad tabled`, and it alone carries `uuid`/`handle`/`uri`.

### `commoner-probe sansad sessions` — which sessions exist

A read-only lookup. No `--out`, no manifest, no run log.

One term:

```bash
commoner-probe sansad sessions --house ls --loksabha 17
```

Every Lok Sabha term, then the continuous Rajya Sabha catalogue as JSON:

```bash
commoner-probe sansad sessions --house ls
commoner-probe sansad sessions --house rs --json
```

The two Houses number sessions differently and the command normalises them: `--loksabha`
selects a Lok Sabha term and is **rejected** for the Rajya Sabha, which is permanent and
numbers continuously. `--json` emits one schema for both.

Live as of 2026-08-14: the Lok Sabha catalogue knows terms **13 to 18** (1999-2026); the
Rajya Sabha catalogue holds **271 sessions**, session 1 (1952) to 271 (2026). A Lok Sabha
budget session is usually **split** into two periods so committees can examine the Demands
for Grants in the recess, so `periods` is a list — reading `[0]` loses half the session.

**A listed session is not a promise that questions exist for it.** RS session 264 appears in
the catalogue with sitting dates and returns zero questions from rsdoc. This answers *when
the House sat*, never *what can be fetched*.

### `commoner-probe sansad tabled` — tabled papers / title search

The Parliament Digital Library holds more than Q&A — Papers Laid on the
Table, reports, and reviews have no question number and never match the
Q&A category facet. The `tabled` mode searches the eLibrary by title
(or full text) with no category filter and downloads every PDF bitstream
of each matching item with per-bitstream provenance (sha256, bytes,
source URL).

```bash
commoner-probe sansad tabled \
  --query '"Delhi Public Library"' \
  --title-filter 'review|annual report|account' \
  --max-records 20 \
  --out data/tabled-dpl
```

Solr ORs bare terms — `--query 'library review'` matches every title
containing *either* word, which can be tens of thousands of items with
multi-MB scans each. Quote phrases, and use `--title-filter` /
`--max-records` / `--max-pages` to keep runs bounded.

| Flag | Default | What it does |
|---|---|---|
| `--query` | required | Title search query (Solr syntax) |
| `--out` | required | Output corpus directory |
| `--title-filter` | — | Keep only titles matching this regex (case-insensitive) |
| `--full-text` | off | Search full text instead of titles only |
| `--size` | `100` | Results per search page |
| `--max-pages N` | — | Stop after N search pages (smoke-test) |
| `--max-records N` | — | Stop after N new records (smoke-test) |
| `--no-download` | off | Record metadata without downloading bitstreams |

Records land in `manifest.jsonl` as `kind: "tabled_paper"`; PDFs under
`pdfs/tabled/`. Note `elibrary.sansad.in` has been observed to fail DNS
resolution from some non-India network paths (a DNS-level geo-fence);
when that happens the command fails with an explicit geo-fence message
pointing at India-egress, rather than a bare traceback.

### `commoner-probe questions-list` — pre-admission question lists + Bulletins

```bash
commoner-probe questions-list \
  --out data/questions-list-2026-monsoon \
  --house both \
  --from-date 2026-07-20 \
  --to-date 2026-07-24 \
  --sessions 8,271
```

Writes `manifest.jsonl` document records for daily List of Questions and
Bulletin PDFs exposed by the Sansad item-wise business APIs. Downloaded PDFs
go under `pdfs/questions-list/`; parsed question rows go to
`questions_list.jsonl` when the PDF text layout exposes block boundaries.

| Flag | Default | What it does |
|---|---|---|
| `--out` | required | Output corpus directory |
| `--house` | `both` | `ls`, `rs`, or `both` |
| `--from-date` | required | Earliest sitting date (YYYY-MM-DD) |
| `--to-date` | required | Latest sitting date (YYYY-MM-DD) |
| `--loksabhas` | `18` | Lok Sabha numbers for LS session-date enumeration |
| `--sessions` | all | Session numbers. LS and RS use different numbering; for 2026-07-20, live check found LS `8` and RS `271`. |
| `--no-download` | off | Record PDF URLs only |
| `--max-records N` | — | Stop after N new document records |
| `--dry-run` | off | Enumerate scoped dates without API document calls |
| `--reset` | off | Wipe existing manifest/question rows and start fresh |

Live-smoke example:

```bash
commoner-probe questions-list \
  --out /tmp/questions-list-smoke \
  --house ls \
  --from-date 2026-07-20 \
  --to-date 2026-07-20 \
  --sessions 8 \
  --max-records 1
```

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
| `--committees` | all | Comma-separated committee slugs. RS aliases for multi-mandate committees: `culture` → `transport`, `environment` → `science` |
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

Q/A records whose question asks for vacancy disclosures additionally emit
typed rows to `vacancy_rows.jsonl` (`ministry / org_unit / service / group /
category / sanctioned / in_position / vacant / date_of_data`), tagged with the
table `layout` that produced them (`in_answer_summary`, `annexure_cadre_matrix`).
A vacancy question answered without a sanctioned/vacant table emits a single
marker record — `layout: "evasive"` for boilerplate/aggregate-only refusals
(the refusal is itself data), `layout: "unknown"` for a genuine parse miss.

**Outsourcing/consultancy signals (committee reports).** For ATR and
observations-bearing committee reports, the full report text is also
scanned for outsourcing signals, emitted to `outsourcing_rows.jsonl`:
`headcount` (a number adjacent to a workforce term — contractual staff,
consultants, outsourced, Young Professionals, daily wagers), `spend`
(a rupee amount on a consultancy/professional-services term's line,
normalised to INR with crore/lakh multipliers), `vacancy` (the
"X of Y posts ... vacant" prose pair), and `mention` (a term with no
number — the withholding is itself data). Every row carries the source
line as context and its line number for citation.

**NeVA (Gujarati) corpora.** When the corpus directory carries a
`questions.jsonl` (the state-assembly layout) instead of `manifest.jsonl`,
`extract-answers` runs the Gujarati NeVA extractor instead: the two-column
પ્રશ્ન|જવાબ layout is split by column geometry into `neva_qa_response`
records, and district→figures table rows land in `neva_district_rows.jsonl`
(district matched verbatim against the 33-district Gujarat gazetteer;
figures in print order; Gujarati numerals translated). A share of Gujarat
NeVA PDFs carries a broken embedded-font ToUnicode map that garbles the
Gujarati text layer; every record carries a `quality` verdict — `clean`
(portal metadata subject found verbatim), `repaired` (found after a
glyph-repair map derived by aligning the clean subject against its garbled
rendering), or `low` (unrecoverable text layer: the OCR backlog). The
corruption is sometimes many-to-one, so repair is only applied where it
can be proven against the reference line — never guessed.

Requires `pip install "commoner-probe[pdf]"` (or a `pdftotext` binary on PATH).

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

`--portal`/`--state` probe a single NeVA portal. `--all` crawls every
registered assembly portal instead (one subdirectory per portal under
`--out`), and `--list-portals` prints the bundled `portal_code -> state_code
/ chamber / state_name` registry (31 assemblies + 6 Legislative Councils)
and exits.

```bash
commoner-probe state-assembly --list-portals
commoner-probe state-assembly --all --out data/state-assemblies --assemblies 15
```

### `commoner-probe state-assembly-probe` — NeVA coverage probe

NeVA's own status is ~28 of 36 Houses signed on with ~20 fully digital — so
portal *reachability* does not imply data *depth*. This is a lightweight,
per-portal presence check (not a crawl): it finds the latest assembly with
sessions, samples one sitting date's question/paper counts, and counts
members, without persisting any records.

```bash
commoner-probe state-assembly-probe --out data/neva-coverage.jsonl
commoner-probe state-assembly-probe --portals gujarat,bla --include-councils
```

| Flag | Default | What it does |
|---|---|---|
| `--out` | stdout only | Also append one JSONL coverage record per portal to this file |
| `--portals` | all 31 assemblies | Comma-separated portal_codes to limit the probe to |
| `--include-councils` | off | Include the 6 Legislative Council portals |
| `--max-assembly` | `20` | Highest assembly number to scan per portal |

### `commoner-probe mca-csr` — MCA CSR company-spend exports

```bash
commoner-probe mca-csr \
  --out data/mca-csr \
  --years 2022-23
```

Downloads CSV exports from the MCA CDM CSR data page and writes one
`manifest.jsonl` record per financial year. Use `--dry-run` to print manifest
records without opening a network session.

### `commoner-probe mines-dmft` — Ministry of Mines / DMFT files

```bash
commoner-probe mines-dmft \
  --out data/mines-dmft \
  --sources mines-gov-in,odisha
```

Downloads raw Ministry of Mines static CSV snapshots and Odisha DMFT public
JSON/report surfaces. Use `--dry-run` to print manifest records without opening
network sessions.

### `commoner-probe mospi` — MoSPI eSankhyiki statistics API

The eSankhyiki portal fronts MoSPI's statistical datasets (PLFS, AISHE,
UDISE, ASI, NAS, HCES registered; extensible) behind a REST API with one
route family per dataset. The client wraps indicator discovery, filter
discovery, paginated tidy-row pulls to CSV, and an exhaustive per-year
dump mode — every pull gets a provenance manifest row (endpoint, exact
params, row count, CSV sha256).

```bash
commoner-probe mospi --list-datasets
commoner-probe mospi --dataset UDISE --indicators
commoner-probe mospi --dataset UDISE --filters --param indicator_code=41
commoner-probe mospi --dataset UDISE --pull \
  --param indicator_code=41 --param year=2024-25 --param state_code=8 \
  --out data/mospi
commoner-probe mospi --dataset UDISE --dump-all \
  --param indicator_code=41 --out data/mospi
```

Filter codes are dataset-specific API codes (PLFS `state_code=99` is
All-India, AISHE uses `37`) — always read them from `--filters`, never
guess. Omitting `state_code` returns all states in one pull.

Two environment notes: `api.mospi.gov.in` is TCP-blocked from at least
some non-India network paths — run from an India-egress host or set
`HTTPS_PROXY=socks5h://...` to an India-region relay. The API's TLS
chain uses a government CA missing from Python's default `certifi`
bundle; point `REQUESTS_CA_BUNDLE` at a system bundle that carries it
(e.g. `/etc/ssl/cert.pem` on macOS).

### `commoner-probe nada` — survey documentation from a NADA catalogue

The eSankhyiki API (above) gives you aggregated numbers. This gives you the
survey itself: the NSS questionnaires, the written sample design, the technical
reports.

Those live in a NADA (National Data Archive) instance — World Bank software, so
one adapter reaches many catalogues. Pick one with `--base-url`:

| instance | holds |
|---|---|
| `https://microdata.gov.in/NADA` (default) | MoSPI — 187 studies |
| `https://censusindia.gov.in/nada` | ORGI — 40,254 studies |

```bash
commoner-probe nada --list-collections
commoner-probe nada --out data/nada --query NSS --max-studies 3 --dry-run
commoner-probe nada --out data/nada --query NSS --max-studies 3
commoner-probe nada --out data/nada --extract-text
```

Each study gives you:

- the DDI metadata, including the sample design in the statistician's own
  words, at `study_desc.method.data_collection.sampling_procedure`
- the questionnaire and report PDFs, with sha256
- the variable and data-file listings

`--extract-text` is a second pass over what is already on disk. It makes no
network calls. Add `--ocr` for documents whose text layer yields nothing.

> **`--max-studies` is required.** There is no default meaning "all" and no
> `--all` flag. Walking a whole government catalogue should be a number you
> chose, not one you inherited.
>
> Start small and raise it. Documents already on disk are recorded
> `skipped_exists` and are not re-fetched, so a bigger bound resumes rather than
> restarts. `--max-docs-per-study` (default 25) caps downloads per study — one
> study can list 60+ documents.

**The microdata files themselves are not acquired.** They are login-gated, and
this tool holds no credentials. That is a deliberate posture, not a missing flag.

### `commoner-probe dchb-town` — urban public libraries from the Census

The Census counts libraries twice, in two different ways.

| | rural | urban |
|---|---|---|
| source | Village Amenities (above) | DCHB **Town Release** |
| unit | one **village** | one **town** |
| value | a flag: has a library, yes/no | a **count**: how many |

The urban half lives in a spreadsheet attached to every District Census
Handbook record. It is a **state-level** file, so about 36 of them cover India —
not the ~640 district PDFs it looks like at first.

```bash
# 1. acquire any one district's DCHB record for the state you want
commoner-probe nada --base-url https://censusindia.gov.in/nada \
    --out data/dchb --study DH_2011_2725_PART_A_DCHB_PUNE

# 2. read the Town Release that came with it
commoner-probe dchb-town --out data/census-towns \
    data/dchb/docs/DH_2011_2725_PART_A_DCHB_PUNE/DH_2011_DCHB_Town_Release_2700.xlsx
```

You get one row per town in `town_amenity_rows.jsonl`:

- Govt and Private library counts, separately
- reading-room counts, separately again
- census state / district / town codes, which join back to the rural corpus

> **Never add the rural and urban numbers together.**
>
> The rural figure counts *villages that have a library* — not libraries. Adding
> it to a library count gives the widely-cited and wrong "~75,000 public
> libraries".
>
> Every row here declares `measure: "count"`, and the schema pins it, so the two
> cannot be mixed up quietly. Reading rooms are a different facility again, and
> are never folded into the library totals.

**TLS note.** `censusindia.gov.in` sends its certificate without the
intermediate, so Python cannot verify the chain (curl can — it fetches the
missing piece itself). Build a bundle and point `REQUESTS_CA_BUNDLE` at it:

```bash
curl -s -o inter.crt http://repository.emsign.com/certs/emSignSSLCAG1.crt
openssl x509 -inform DER -in inter.crt -out inter.pem
cat "$(python -c 'import certifi;print(certifi.where())')" inter.pem > bundle.pem
export REQUESTS_CA_BUNDLE=$PWD/bundle.pem
```

Do not turn verification off. The CLI detects this failure and prints the same
guidance instead of a traceback.

### `commoner-probe courts` — India court records

```bash
export INDIAN_KANOON_TOKEN=...
commoner-probe courts --query "right to livelihood" \
  --doctypes supremecourt --max-records 20 --out data/courts
```

Searches the Indian Kanoon API and writes one `manifest.jsonl` record per
result (`kind = "court_record"`, `provider = "indiankanoon"`). Metadata-only
by default; `--download` additionally fetches each judgment's original source
file, which is a separate billed API call per document. Omitting `--out`
prints a preview and writes nothing.

The token is read **only** from `INDIAN_KANOON_TOKEN` — never a CLI flag, so
it stays out of shell history and process listings. Indian Kanoon is a paid
commercial index: the judgments are public record, the retrieval is what you
pay for, and your queries do leave your machine.

`--from-date`/`--to-date` take `DD-MM-YYYY`, the API's own format, and are
passed through unmodified.

**eCourts is reached across a process boundary, and never bundled:**

```bash
export COMMONER_PROBE_ECOURTS_CMD=/path/to/ecourts
commoner-probe courts --ecourts --ecourts-arg=--court --ecourts-arg=delhi \
  --out data/courts
```

Option-shaped passthrough arguments need the `=` form. Written as
`--ecourts-arg --court`, argparse reads `--court` as the next option rather
than as this one's value, and the command fails during parsing.

`openjustice-in/ecourts` is GPL-3.0 and commoner-probe is MIT, so it is
neither imported nor declared a dependency — install it yourself and point
the environment variable at it. Its output is carried through verbatim under
`raw`, with `raw_sha256` over the canonical JSON, because this repo has not
verified that tool's field shape and will not invent a mapping for it.

### `commoner-probe render` — headless-browser fallback

```bash
commoner-probe render --url https://www.data.gov.in/catalogs \
  --require-text "Ministry of" --wait-for ".catalog-list" \
  --out data/rendered
```

A **fallback**, not the default path: a real browser costs orders of magnitude
more than a GET, so use it only where the fetch layer genuinely cannot read the
page. Needs `pip install playwright && playwright install chromium`, which is
deliberately not a dependency of the default install.

The point is the assertion, not the browser. JS-heavy portals do not fail
loudly — `data.gov.in`, `lokdhaba.ashoka.edu.in` and `myneta.info` all answer
HTTP 200 with a well-formed document for every path, including invented ones,
so a status-code check records a clean acquisition of a page containing none of
the data. This command refuses to record success when it only captured a shell:
the record gets `status: "shell_only"`, the snapshot is written to a *separate*
`rendered_shells/` directory so a directory glob can't mistake it for content,
and the exit code is 1.

**Byte size cannot make that distinction.** Measured 2026-07-26:
`data.gov.in/catalogs` returns 1,000,989 bytes of HTML carrying 1,850
characters of visible text, while a fully-rendered `prsindia.org/billtrack`
returns 407,356 bytes carrying 67,372. The empty page is two and a half times
*larger*, because a ~1 MB inline `window.__NUXT__` payload is script, not
content. So the check counts visible text after script/style removal, and
`--require-text` — a string you know the real page contains — is the strong
form of it.

### `commoner-probe doe-pay-allowances` — DoE Pay & Allowances annual reports

```bash
commoner-probe doe-pay-allowances \
  --out data/doe-pay-allowances \
  --years 2022-23,2023-24
```

Downloads the "Annual Report on Pay and Allowances of Central Government
Civilian Employees" series from doe.gov.in (all years on the listing page
unless `--years` narrows it) with one `manifest.jsonl` record per report.
Each record carries `text_layer: false` when the edition is a flattened scan
that needs OCR (the 2022-23 edition is one). doe.gov.in's WAF resets
back-to-back requests, so the default `--sleep` is 3 seconds. Use `--dry-run`
to enumerate the listing without downloading.

### `commoner-probe attendance` — Lok Sabha member-wise sitting attendance

```bash
commoner-probe attendance \
  --out data/attendance \
  --loksabhas 18 \
  --sessions 5
```

Acquires member-wise sitting attendance via the sansad.in native attendance
API (`api_ls/member/getMemberAttendanceMemberWise`) — one `manifest.jsonl`
record per member per session, with `signed_days_count` and `division`.
Supersedes an earlier PRS-attendance want (primary source, no ToS question).
`--sessions` defaults to every session in the `AllLoksabhaAndSessionDates`
catalog for the given `--loksabhas`. Use `--dry-run` to list candidate
(loksabha, session) windows without fetching.

### `commoner-probe myneta` — ADR/MyNeta candidate affidavits (Lok Sabha 2024)

```bash
commoner-probe myneta \
  --out data/myneta \
  --constituency-ids 579
```

Acquires self-declared ECI-affidavit candidate summaries from myneta.info
(Association for Democratic Reforms) for Lok Sabha 2024: assets, liabilities,
declared criminal cases (read from the site's own Crime-O-Meter gauge value),
age, education, self/spouse profession, and the per-candidate source URL.
`--constituency-ids` defaults to all 543 constituencies. Use `--dry-run` to
list candidate IDs per constituency without fetching affidavit pages.

### `commoner-probe prs` — PRS Legislative Research

```bash
# per-MP participation
commoner-probe prs \
  --out data/prs \
  --surface mp-track \
  --house both \
  --loksabhas 17,18

# every bill PRS tracks, with its legislative status
commoner-probe prs \
  --out data/prs \
  --surface bill-track

# committee/CAG report summaries, with the PDFs
commoner-probe prs \
  --out data/prs \
  --surface report-summaries \
  --download
```

Acquires PRS Legislative Research surfaces for internal research only. Rows are
stamped `source: prsindia.org` so downstream consumers can segregate PRS-derived
data and avoid republication of PRS text. PRS `robots.txt` declares
`Crawl-delay: 10`, so the default `--sleep` is 10 seconds.

**`--surface mp-track`** — per-MP participation rows for the 17th/18th Lok Sabha
and Rajya Sabha. Metadata-only by default; pass `--download` to retain the source
CSV under `csv/prs-mp-track/` with a sha256.

**`--surface bill-track`** — one record per bill (title, URL, slug, and
legislative status such as Passed / Lapsed / Withdrawn / Pending). The whole
listing renders in a single page with no pagination, so this is one request;
`--house` and `--loksabhas` do not apply. Metadata only — per-bill detail pages
are a separate surface. Bill Track is a *tracker*, so re-running appends a new
row only for bills whose status has actually changed, giving the corpus a
status history per bill rather than duplicates.

**`--surface report-summaries`** and **`--surface vital-stats`** — the two
policy publication listings (442 report summaries and 24 vital stats, live
2026-07-28). They render from the same template, so one parser serves both;
records differ by `kind` (`prs_report_summary` / `prs_vital_stats`) and
`surface`. Neither paginates — `?page=1` returns the identical first row — so
each is one request. Metadata only by default; `--download` also fetches each
item's PDF into `pdf/prs-<surface>/` with a sha256, and a response that is not
a PDF (a WAF interstitial answers 200 with HTML) is recorded as `status:
"error"` rather than counted as an acquisition.

### `commoner-probe wayback` — Internet Archive capture history

```bash
commoner-probe wayback \
  --out data/wayback \
  --url mospi.gov.in \
  --collapse-digest \
  --only-ok
```

Lists what the Internet Archive already holds for a URL. This is the archival
counterpart to the `wayback_*` provenance fields other adapters attach: those
record **one** snapshot as a file is acquired, whereas this answers *when did
this page change, and what did it say before* — for sources nobody captured at
the time.

- `--prefix` matches everything under a host or path instead of that exact URL.
  Live 2026-07-28, `mospi.gov.in` with `--prefix` reached **362 distinct URLs**
  in 3,000 captures.
- `--from-date` / `--to-date` take a bare year, a year-month, or a full
  14-digit stamp.
- `--collapse-digest` drops consecutive captures whose content digest is
  unchanged — the difference between *when the page changed* and *how often it
  was crawled*.
- `--only-ok` keeps HTTP 200 captures, dropping the redirects and error pages
  the crawler also recorded.

Records are `metadata_only`: the index is a listing, and each row carries a
citable `snapshot_url`. Pagination follows the API's opaque `resumeKey`, so a
full history walks in batches rather than an offset.

**A URL with no captures produces no records; an unreachable index raises.**
Those are opposite facts and the API makes them easy to confuse — "no captures"
is HTTP 200 with a body of `[]`, while the index answers 5xx, resets the
connection, or times out often enough that the *same* query returned `200 []`
and then `503` three seconds apart. Reading an outage as "never archived" would
record a fact about the Internet Archive as a fact about the source, so the
listing retries with backoff and then fails loudly rather than writing a short
or empty corpus.

### `commoner-probe abhilekh-patal` — National Archives of India catalogue

```bash
commoner-probe abhilekh-patal \
  --out data/nai \
  --query "police" \
  --max-pages 20
```

Acquires the **catalogue** of Abhilekh Patal, the National Archives of India's
digital records portal — one record per archival description, carrying the
archive's own identifier, year, page count, language and keywords.

**It does not acquire documents, and it never claims to.** Search and metadata
are open, but the scans sit behind a paid reproduction-ordering flow (the
results page carries `Cart`/`Order` markup and the site publishes a
cancellation and refund policy). So `status` is always `metadata_only`; there
is no `dest` and no `sha256`. Live 2026-07-28, a `police` query reports 59,414
records across 5,942 pages.

Two access facts, both measured rather than assumed:

- **India-region egress is required.** From elsewhere the AWS WAF answers
  HTTP 202 with a Human Verification page and no catalogue, and a real headless
  Chromium executing JS does not clear it either — `commoner-probe render` is
  not a workaround. Run this from an India egress path.
- **The WAF challenges every `commoner-probe` User-Agent**, including the
  scheme-free form that cleared `mha.gov.in`. Acquiring this source therefore
  means deciding what identity to present, so the probe will not decide for
  you: the honest default is kept, a challenge **fails loudly with exit 1**
  rather than writing an empty corpus, and `--user-agent` lets an operator
  choose explicitly. Whatever is used is stamped into every record's
  `user_agent` field, so the corpus records how it was obtained.

Pagination does not use a query parameter — `?Page.Number=N` on the search URL
is silently ignored and returns page 0, so a crawler that trusted it would
re-record the first ten results forever. The adapter uses the site's own
`/Category/Search/PaginationScroll` endpoint instead. `--max-pages` and
`--max-records` are brakes; without either it walks the whole result set at ten
records per page.

### `commoner-probe bills` — bills & legislation catalog

```bash
commoner-probe bills \
  --out data/bills \
  --house both
```

| Flag | Default | What it does |
|---|---|---|
| `--out` | required | Output corpus directory |
| `--house` | `both` | `ls`, `rs`, or `both` (the endpoint lives under `api_rs` for both) |
| `--bill-type` | all types | Filter by bill type, e.g. `Government` or `Private Member` |
| `--max-records` | — | Stop after N new records per house (smoke-test brake) |
| `--dry-run` | off | Emit one planning record per house without fetching |

### `commoner-probe debates` — Lok Sabha floor-debate transcripts

```bash
commoner-probe debates \
  --out data/debates \
  --loksabhas 18 \
  --download
```

| Flag | Default | What it does |
|---|---|---|
| `--out` | required | Output corpus directory |
| `--loksabhas` | `18` | Comma-separated Lok Sabha numbers, e.g. `17,18` |
| `--sessions` | all | Comma-separated session numbers to limit to |
| `--from-date` / `--to-date` | — | ISO date bounds (YYYY-MM-DD) |
| `--max-records` | — | Stop after N new records per Lok Sabha |
| `--download` | off | Download each day's transcript PDF (+ sha256) |
| `--dry-run` | off | List candidate sitting dates without fetching PDFs |

### `commoner-probe indiacode` — state Acts, amendments, rules, notifications

```bash
commoner-probe indiacode --out data/indiacode --states "West Bengal,Sikkim"
```

Acquires India Code (indiacode.nic.in) state statutory instruments: the Act
itself plus every Rule, Regulation, Notification, Order, Circular, Ordinance,
and Statute found on that Act's page. Amendments are not a distinct category
on the site — they surface as Notification (occasionally Rule) rows whose
description contains "Amendment"; each record's `is_amendment` flag is
derived from that text. `--list-states` prints the bundled state -> parent-
handle registry (36 states/UTs); Central Acts are a separate collection tree
and out of scope.

```bash
commoner-probe indiacode --list-states
commoner-probe indiacode --out data/indiacode --all-states --max-acts 5
```

| Flag | Default | What it does |
|---|---|---|
| `--out` | required unless `--list-states` | Output corpus directory |
| `--states` | — | Comma-separated state names, e.g. `'West Bengal,Sikkim'` |
| `--all-states` | off | Probe every registered state |
| `--list-states` | off | Print the state -> parent-handle table and exit |
| `--max-acts` | — | Stop after N Acts per state (smoke-test brake) |
| `--no-download` | off | Record instruments without downloading PDFs |
| `--rpp` | `100` | Results per browse page (India Code enumeration) |
| `--dry-run` | off | Emit one planning record per state without fetching |

### `commoner-probe legacy-dspace` — legacy DSpace (XMLUI/JSPUI) portals

```bash
commoner-probe legacy-dspace \
  --out data/assam-ala \
  --base-url https://aladigitallibrary.in \
  --portal-name assam-ala
```

Acquires items from a legacy DSpace instance with no working REST API
(state-legislature digital libraries and similar archives), via its browse
index and item/bitstream pages. Parameterised by `--base-url` and
`--handle-prefix` (default `123456789`, the DSpace default) — not specific
to any one portal. First verified target: the Assam Legislative Assembly
Digital Library (DSpace 6.3, 2,922 items). Metadata-only by default; use
`--download` to also fetch bitstream PDFs. `--dry-run` lists candidate
handles from the browse index without fetching item pages.

Distinct from `commoner-probe indiacode`, which targets indiacode.nic.in's
JSPUI theme specifically (different browse-page markup) — the two adapters
are kept separate rather than forcing one regex set across both themes.

### `commoner-probe budget` — Union Budget & RBI State-Finances files

```bash
commoner-probe budget \
  --out data/budget \
  --sources union-budget \
  --demands 101
```

| Flag | Default | What it does |
|---|---|---|
| `--out` | required | Output directory |
| `--sources` | `union-budget` | Comma-separated: `union-budget`, `rbi-state-finances` |
| `--demands` | `101` | Comma-separated Union Budget demand numbers, e.g. `101,1,33` |
| `--rbi-url` | RBI default | RBI State-Finances publication page to discover documents from |
| `--dry-run` | off | Print manifest records without writing (offline for `union-budget`) |

### `commoner-probe academic-jobs` — HEI faculty-recruitment ads

```bash
commoner-probe academic-jobs \
  --out data/academic-jobs \
  --institutions iit-kharagpur
```

| Flag | Default | What it does |
|---|---|---|
| `--out` | required | Output directory |
| `--institutions` | all in registry | Comma-separated institution ids (e.g. `iit-kharagpur`) |
| `--registry` | bundled | Path to an alternative `institutions_registry.json` |
| `--no-download` | off | Skip PDF download + text extraction (listing-page heuristics only) |
| `--dry-run` | off | List which institutions would be probed without fetching |

### `commoner-probe evidence dmft` — cross-source evidence bundle

```bash
commoner-probe evidence dmft \
  --mines-dmft-dir data/mines-dmft \
  --sansad-dir data/sansad/mines-dmft-pmkkky \
  --out data/evidence/dmft.json
```

Builds a JSON bundle with separate `executive_disclosure` and
`parliamentary_oversight` sections. It does not merge unlike source families
into one table.

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

