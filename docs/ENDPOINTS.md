# Endpoint Reference

This is a public, source-facing reference for the government disclosure
endpoints that `commoner-probe` uses. It documents source contracts, not
private run notes.

## Sansad Q/A

`commoner-probe sansad` probes Lok Sabha and Rajya Sabha question records from
the public Sansad web surfaces. The two houses expose different endpoint
families and response shapes, so the manifest keeps `house` and source metadata
on every record.

Outputs:

- `manifest.jsonl` records with `kind = "qa"`
- optional PDFs under `pdfs/`
- `_runs.jsonl` audit records

Follow-up command:

```bash
commoner-probe extract-answers --out data/<corpus>
```

## Standing Committees

`commoner-probe committees` probes Lok Sabha and Rajya Sabha department-related
standing committee report listings. Report records include report type,
committee slug, dates, PDF URLs, and downloaded PDF paths when downloads are
enabled.

Outputs:

- `manifest.jsonl` records with `kind = "committee_report"`
- optional PDFs under `pdfs/`
- `_runs.jsonl` audit records

Follow-up commands:

```bash
commoner-probe extract-answers --out data/<committee-corpus>
commoner-probe atr-linkage --out data/<committee-corpus>
```

## NeVA State Assembly Portals

`commoner-probe state-assembly` probes public National e-Vidhan Application
portals. Each state deployment has a portal subdomain and CMS state code.

Outputs:

- `questions.jsonl`
- `questions_unlisted.jsonl`
- `members.jsonl`
- `papers_laid.jsonl`

Example:

```bash
commoner-probe state-assembly \
  --portal gujarat \
  --state GJ \
  --out data/gujarat-assembly \
  --assemblies 15
```

`commoner-probe state-assembly --list-portals` prints the bundled
`portal_code -> state_code / chamber / state_name` registry (31 assembly +
6 council portals). `--all` crawls every registered assembly portal instead
of a single `--portal`/`--state`.

`commoner-probe state-assembly-probe` is a lightweight, per-portal coverage
check — it does not persist questions/papers/members. It scans assembly
numbers for the first with sessions, samples one sitting date's counts, and
counts members, emitting one JSONL coverage record per portal. NeVA's own
status is ~28 of 36 Houses signed on with ~20 fully digital, so portal
*reachability* (all 31 assembly portals return HTTP 200) does not imply data
*depth* — use this probe to find out which onboarded houses actually expose
records.

```bash
commoner-probe state-assembly-probe --out data/neva-coverage.jsonl
```

## India Code — state Acts, amendments, rules, notifications

`commoner-probe indiacode` probes India Code (indiacode.nic.in), a legacy
DSpace (XMLUI/JSPUI) install with no working REST API (`/server/api` is
disabled). Verified live 2026-07 against the West Bengal Public Libraries
Act, 1979 (handle `14547`):

- per-state parent collection: `GET /handle/123456789/{state_handle}/`
- per-state Act enumeration (paginated): `GET /handle/123456789/{state_handle}/browse?type=dateissued&rpp=100&offset=N`
- per-Act detail page: `GET /handle/123456789/{item_handle}` — an
  `itemDisplayTable` metadata block (Act ID, Act Number, Enactment Date, Act
  Year, Short Title, Department, Type, Location), the main Act PDF at
  `/bitstream/123456789/{item_handle}/1/{file}.pdf`, and every subordinate
  instrument (Rules, Regulations, Notifications, Orders, Circulars,
  Ordinances, Statutes) embedded directly on the page as Bootstrap modal
  tables, each row linking to `/ViewFileUploaded?path={actid}/{category}individualfile/&file={NN}.pdf`.

Amendments are not a distinct site category — they appear as Notification
(occasionally Rule) rows whose description contains "Amendment"; the adapter
derives `is_amendment` from that text. Filenames are sparse, not a dense
1..N sequence — never assume a range.

The site sits behind Akamai, which 403s the shared `http_client` User-Agent
(it contains a `+https://...` URL fragment, a common bot fingerprint) on
every path, including `robots.txt` itself. The adapter uses a bare
`commoner-probe/<ver> (research)` UA instead (same style as `NEVA_UA`) and
sets `respect_robots=False` — the real `robots.txt`, fetched with a passing
UA, only disallows `/discover` and `/simple-search` (the Discovery search
UI), neither of which this adapter touches.

Outputs:

- `manifest.jsonl` records with `kind = "indiacode_instrument"`
- one PDF per instrument under `pdfs/<state>/<act_handle>/`

Example:

```bash
commoner-probe indiacode --out data/indiacode --states "West Bengal"
```

Central Acts live in a separate collection tree and are out of scope
(state library-law research only).

Known gap: no archive.org/Wayback snapshot-on-fetch — no other adapter in
this repo does that either.

## MCA CSR

`commoner-probe mca-csr` downloads company-spend CSV exports from the MCA CDM
CSR public data page.

Source contract:

- page: `GET https://www.mcacdm.nic.in/csr-data`
- export: `POST https://www.mcacdm.nic.in/cdm/export.php`

Outputs:

- `manifest.jsonl` records with `kind = "mca_csr_company_spend"`
- one CSV per requested financial year

Example:

```bash
commoner-probe mca-csr \
  --out data/mca-csr \
  --years 2022-23,2021-22
```

## Mines DMFT

`commoner-probe mines-dmft` downloads raw Ministry of Mines and Odisha DMFT
public disclosure files. Ministry CSVs are current cumulative snapshots; they
are not year-wise files unless the source itself publishes a period field.

Default source families:

- `mines-gov-in`: Ministry of Mines static CSV snapshots
- `odisha`: Odisha DMFT JSON/report surfaces

Outputs:

- `manifest.jsonl` records with `kind = "mines_dmft_source_file"`
- source files under source-named directories

Example:

```bash
commoner-probe mines-dmft \
  --out data/mines-dmft \
  --sources mines-gov-in,odisha
```

## DMFT Evidence Bundle

`commoner-probe evidence dmft` combines executive disclosure and parliamentary
oversight into a single JSON object without flattening them into one table.

Inputs:

- a `mines-dmft` corpus
- optionally, a Sansad Q/A corpus for DMFT/PMKKKY oversight

Example:

```bash
commoner-probe evidence dmft \
  --mines-dmft-dir data/mines-dmft \
  --sansad-dir data/sansad/mines-dmft-pmkkky \
  --out data/evidence/dmft.json
```

## Ministry Detailed Demands for Grants (DDG)

`commoner-probe ministry-ddg` downloads a ministry/department's own
"Detailed Demands for Grants" series — the object-head-level budget document
(unlike indiabudget.gov.in's "Demand for Grants", a major-head summary only).
Each ministry hosts this on its own site, in its own template; there is no
central index, so the adapter works off a small, individually-verified
registry (`commoner_probe.ddg.MINISTRY_DDG_PORTALS`) rather than a single
endpoint contract.

Outputs:

- `manifest.jsonl` records with `kind = "ministry_ddg_document"`
- downloaded PDFs under `<ministry_code>/`

Example:

```bash
commoner-probe ministry-ddg --out data/ministry-ddg --ministry-code mha
```

**Before adding a new ministry**, or if a registered one starts returning
zero documents, read
[`GOV_SITE_PLATFORMS.md`](GOV_SITE_PLATFORMS.md) — a survey of every ministry
site checked so far, including which ones are JS-rendered SPAs (a large and
growing share, sharing a common platform), WAF-blocked, or unreachable from
a given network egress, and why. It exists specifically so this research
doesn't get silently redone every session.

## CAG State Finance Accounts

`commoner-probe cag` downloads a State's statutory **Finance Accounts** from
cag.gov.in's State-Accounts portal. Volume II carries the detailed statements
(Statement 15 revenue by minor head, Statement 16 capital by minor head) from
which minor-head figures such as `2205-00-105` (Public Libraries, revenue) and
`4202-04-105` (Public Libraries, capital) are read downstream in public-finance.

Unlike the audit-report side of cag.gov.in, State Accounts have no central
index and no per-document detail page: one server-rendered page per State
(`state-accounts-report?defuat_state_id=<id>`) lists the PDFs directly under
its "Finance Accounts" tab. The adapter works off a small, live-verified
registry (`commoner_probe.cag.CAG_ACCOUNTS_STATES`, 25 States with a confirmed
Vol-II); seven States/UTs have no obtainable Vol-II and are documented in
`commoner_probe.cag._UNAVAILABLE`, not crawled.

The fiscal year is read from the page's accordion header, never the filename
(several States ship year-less or misspelled filenames). Acquisition only —
parsing Statement 15/16 out of the PDFs is a public-finance concern (REQ-0003).

Outputs:

- `manifest.jsonl` records with `kind = "cag_state_account"`
- downloaded PDFs under `<state-slug>/`

Example:

```bash
# All 25 States, FY2023-24 Vol-II
commoner-probe cag --out data/cag --years 2023-24 --volumes II
# One State, dry-run (list without downloading)
commoner-probe cag --out data/cag --state Gujarat --years 2023-24 --dry-run
```

## India court records (Indian Kanoon + external eCourts)

`commoner-probe courts` acquires court documents for litigation-adjacent
research. Two providers, and **their licences decide the architecture**:

**Indian Kanoon** — `api.indiankanoon.org`, HTTPS. The wire contract was read
from `sushant354/IKAPI` (MIT) and reimplemented here; no code is vendored.

| endpoint | notes |
|---|---|
| `/search/?formInput=<q>&pagenum=<n>&maxpages=<m>` | `pagenum` is 0-indexed and advances by `maxpages`; the API caps `maxpages` at 100 |
| `/doc/<tid>/`, `/docmeta/<tid>/` | both accept `maxcites` / `maxcitedby` |
| `/docfragment/<tid>/?formInput=<q>` | query-matched fragment |
| `/origdoc/<tid>/` | source file, base64 under a `doc` key |

- **Every endpoint is POST**, with parameters in the query string and no
  request body. A GET returns nothing useful.
- Headers: `Authorization: Token <token>`, `Accept: application/json`.
- Search filters are query *text*, not parameters: `doctypes: <court>`,
  `fromdate: DD-MM-YYYY`, `todate: DD-MM-YYYY`, `sortby: mostrecent`.
- **Errors do not reliably arrive as HTTP status codes.** The body may be JSON
  carrying `errmsg`, or a bare string beginning `error code:`. Both are
  checked — assert on the response shape, never the status code.
- Token comes from `INDIAN_KANOON_TOKEN` only. It is paid and personal.

**eCourts** — `openjustice-in/ecourts` is GPL-3.0; this package is MIT and
published to PyPI, so importing or vendoring it would relicense commoner-probe
for every installer. It is therefore invoked **out-of-process only**: a
separately installed executable named by `COMMONER_PROBE_ECOURTS_CMD`, its
JSON read from stdout. There is no `import ecourts` anywhere in this repo and
no entry for it in `pyproject.toml` — a test enforces both.

Outputs:

- `manifest.jsonl` records with `kind = "court_record"`, discriminated by
  `provider` (`indiankanoon` | `ecourts`)
- downloaded source files under `courts/` (only with `--download`)

## Headless-browser fallback (JS-rendered portals)

`commoner-probe render` has no endpoint contract — it takes any URL. What it
does have is an **acceptance contract**: a capture may not claim success unless
the page actually contains content.

The failure mode it exists for is silent success. These portals answer HTTP 200
with a well-formed HTML document for *every* path, including invented ones:

| portal | stack | what a plain GET returns |
|---|---|---|
| `data.gov.in` | Nuxt | 200 + a ~1 MB shell, catalogue absent |
| `lokdhaba.ashoka.edu.in` | React SPA | 200 + a 486-byte shell (its own API also 502s) |
| `myneta.info` | — | 200 for invented paths |

**A size floor does not separate shell from content.** Measured 2026-07-26 with
a plain GET:

| page | HTML bytes | visible text (script/style removed) |
|---|---|---|
| `data.gov.in/catalogs` | 1,000,989 | 1,850 chars |
| `prsindia.org/billtrack` | 407,356 | 67,372 chars |

The shell is 2.5x larger than the rendered page, because the inline
`window.__NUXT__` payload counts as bytes but is script. So the check measures
visible text, and `--require-text` (a string the real page is known to contain)
is the strong form. Framework markers (`__NUXT__`, `__NEXT_DATA__`,
`<app-root>`) are reported to explain a failure but never define one — a
correctly-rendered Next.js page still carries them.

Outputs:

- `manifest.jsonl` records with `kind = "rendered_page"`; `status` is
  `downloaded` only when the assertion passed, `shell_only` otherwise
- snapshots under `rendered/`, and failed captures under `rendered_shells/` —
  separate directories, because downstream tools glob directories
