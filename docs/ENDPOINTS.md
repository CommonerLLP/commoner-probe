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

- page: `GET https://mcacdm.nic.in/csr-data`
- export: `POST https://mcacdm.nic.in/cdm/export.php`

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

## State RERA project listings (`rera`)

`commoner-probe rera` downloads the project listing of a state Real Estate
Regulatory Authority portal. Karnataka (`--state KA`), Haryana (`--state HR`),
and Tamil Nadu (`--state TN`) are verified against the live portals on
November 11, 2026.

Source contract (Karnataka, `https://rera.karnataka.gov.in`):

- form page: `GET /viewAllProjects` (HTTP 200, about 6.5 MB). `GET
  /projectViewDetails` answers HTTP 405.
- listing: `POST /projectViewDetails` as `application/x-www-form-urlencoded`
  with `project=&firm=&appNo=&regNo=&district=<option value>&subdistrict=0&btn1=Search`.
  The `district` value is the option's `value` attribute exactly, and
  `Bengaluru  Rural` has two spaces.
- no token, cookie, login, or CAPTCHA. `robots.txt` answers 404.
- every response carries about 6.5 MB of page chrome. The adapter waits at
  least 5 seconds between requests and reads the form page only for
  `--all-districts` and `--list-districts`.

Outputs:

- `manifest.jsonl` records with `kind = "rera_listing"`
- `rera_project_rows.jsonl` rows with `kind = "rera_project_row"`
- the stored response for each request under `listings/IN-KA/`

The parser reads the `<table id="approvedTable">` and anchors every column on
its exact header string. A missing header raises, so a portal redesign fails
instead of returning mislabelled rows. The schemas are in
[SCHEMAS.md](SCHEMAS.md), and the captured fixture and header list are in
`tests/fixtures/rera_karnataka/README.md`.

Example:

```bash
commoner-probe rera --state KA --out data/rera
```

Source contract (Haryana, `https://haryanarera.gov.in`, `--state HR`, verified
November 11, 2026):

- listing: `GET /admincontrol/registered_projects/2` is the Gurugram authority
  (1,086 rows, about 2.6 MB), and `GET /admincontrol/registered_projects/1` is
  the Panchkula authority, which covers every other Haryana district (1,132
  rows). Select one with `--authority gurugram|panchkula`.
- the whole table is in the HTML (the DataTable paginates in the browser). No
  token, cookie, login, or CAPTCHA. `robots.txt` answers 404.
- `Project ID` is `NA` for 37 Gurugram projects, so the row key is the
  registration certificate number. The adapter splits that cell into
  `registration_no`, `approved_on` (the `DATED` date), and `lapsed`.
- the `View Quarterly Progress` link opens an index of quarters with a `POST`
  form per quarter. The adapter records the link in `fields` and never fetches
  it, or any other link in a row.

The parser reads `<table id="compliant_hearing">` and anchors every column on
its exact header string. The captured fixtures and header list are in
`tests/fixtures/rera_haryana/README.md`.

```bash
commoner-probe rera --state HR --out data/rera
```

Source contract (Tamil Nadu, `https://rera.tn.gov.in`, `--state TN`, verified
November 11, 2026):

- listings: `GET /registered-building/tn` (402 rows for 2026, about 1.4 MB) and
  `GET /registered-layout/tn` (3,945 rows, about 11 MB). Select one with
  `--kind building|layout`. The whole table is in the HTML. No login or CAPTCHA.
- an older year: the page has a `year` select (2026, 2025, and 2024) in a
  `POST` form with a Laravel `_token`. `POST _token=<token>&year=<year>` to the
  same URL, in the same session, returns that year (428 building rows for 2025).
  The adapter raises when the page has no token. A session that keeps no cookies
  gets HTTP 419, so an older year needs `commoner-probe[http]`. Select it with
  `--year`.
- `robots.txt` has `Disallow: /uploads/` and `Disallow: /*.pdf$`.
  `make_session` enforces only the first, because `urllib.robotparser` ignores
  the `*` wildcard. The adapter refuses both before any request, and records
  every PDF link in `fields` without fetching it.
- the `/building/offline/<year>` pages (2017 to 2025) are static archives with 8
  columns and PDF links, not a token form. The adapter doesn't read them.
- the "Last Updated on" stamp beside the year form equals the newest
  registration date in that year's list (10/11/2026 for 2026, 29/12/2025 for
  2025), so the freshness check uses the registration date.
- `Approval Details` is free text. The adapter types the CMDA planning-permission
  number and the Greater Chennai Corporation building-permission number when the
  text gives them in the usual shape, and the coordinates when they are decimal
  degrees. 212 of 222 CMDA rows and 162 of 169 Greater Chennai Corporation rows
  of the 2026 building list get a typed number, and 333 of 402 rows get
  coordinates. The full text stays in `fields`.

The parser reads `<table id="example1">` and anchors every column on its exact
header string. The captured fixtures and header list are in
`tests/fixtures/rera_tamil_nadu/README.md`.

```bash
commoner-probe rera --state TN --out data/rera
commoner-probe rera --state TN --kind layout --year 2025 --out data/rera
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
parsing Statement 15/16 out of the PDFs is a public-finance concern.

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

## ORGI / Census of India (`census`)

Publisher: Office of the Registrar General & Census Commissioner, via the
data.gov.in (OGD) API. Contract verified 2026-07-30.

Source contract:

- catalogue: `GET https://api.data.gov.in/lists?filters[title]=<q>&limit&offset`
- resource:  `GET https://api.data.gov.in/resource/<id>?limit&offset`

Auth: `api-key` in the query string. Read from `$DATA_GOV_IN_KEY`, else the
org's shared secrets file. **Because the key is a query parameter, the request
URL is a credential** — manifest rows and error text carry a key-free form.

Surfaces:

| surface | resources | one resource covers | note |
|---|---:|---|---|
| `pca` | 2,225 | a state (rows to ward level) | Primary Census Abstract — population, households, age 0-6, SC and ST counts by person/male/female. 2001 and 2011 |
| `village-amenities` | 1,128 | a district | 396 fields; `public_library` and `public_reading_room` are SEPARATE availability flags (A=1/NA=2) plus distance-range codes |
| `town-amenities` | per district | a district | 232 fields: demographics, health, education. **No Statement V** |
| `town-directory` | 380 | a state | a place INDEX — 8 fields, codes and names, no amenities |

Traps, all measured:

- Title filters match **loosely** — `town amenities` returns "Digital Payments
  Data: New Town Kolkata". Every hit is re-checked against the surface pattern.
- Titles carry two formats: `Abstract, 2001 - Delhi` and `Abstract 2011 -
  Rajasthan`. The year is read as a token, not by position.
- The API is slow and intermittent: a 100-row page took 11.8 s, a 100-item
  listing timed out, and one query errored then succeeded moments later.
- **The urban public-library COUNT is not on this API.** Town Directory
  Statement V lives only in the DCHB Part A PDFs. Never sum it with the rural
  availability flag — that is the wrong "~75,000 libraries".
- 2011 vintage: districts created since (all of Telangana, splits elsewhere) do
  not exist in these codes.

Outputs: `manifest.jsonl` (`kind: orgi_census_resource`) + `rows/<surface>__<id>.jsonl`.

## OpenCity CKAN (`ckan`)

Publisher: the body named in each dataset's `organization`, for example the
Greater Bengaluru Authority or the Greater Chennai Corporation. data.opencity.in,
operated by Oorvani Foundation, is a CKAN mirror. Contract verified
2026-11-04.

Source contract:

- dataset: `GET https://data.opencity.in/api/3/action/package_show?id=<slug>`
- search:  `GET https://data.opencity.in/api/3/action/package_search?q=<q>&rows=<n>`
- resource: the `url` of each entry in `result.resources`, for example
  `https://data.opencity.in/dataset/<dataset-uuid>/resource/<resource-uuid>/download/<file>`

Auth: none. The Action API answers `{"success": true, "result": {...}}`.

robots.txt: `Disallow: /api/` and `Crawl-Delay: 10`. The probe calls the
Action API with `respect_robots=False` (owner decision A, November 12, 2026)
and keeps the robots check on every resource download. See
[`CLI.md`](CLI.md#commoner-probe-ckan--ckan-open-data-portal-resources).

Traps:

- A slug that no longer exists answers HTTP 404 with `success: false`. The probe
  turns it into one `error` record and goes on to the next dataset.
- Two resources in one dataset can share a `name`. The filename gets the
  resource ID appended so the files don't collide.

Outputs: `manifest.jsonl` (`kind: ckan_resource`) + one file per resource under
`<host>/<dataset>/`.

## Municipal finance index pages (`municipal-budget`)

Publishers: the Government of NCT of Delhi Finance Department
(`finance.delhi.gov.in`) and the Municipal Corporation of Delhi
(`mcdonline.nic.in`). Contract verified
2026-11-12. Auth: none.

Source contract:

- GNCTD listings: `GET https://finance.delhi.gov.in/finance/{budget-glance,receipts-budget,budget-speech,gender-budget}`.
  A Drupal `generic_multiple_files` view with one PDF per fiscal year.
- GNCTD Detailed Demands, two hops: `GET .../finance/detailed-demands-grants-0`
  lists one detail page per year (`.../detailed-demands-grants-2026-27`, 22 on
  2026-11-12), and each detail page lists that year's volumes.
- MCD menu API: `GET https://mcdonline.nic.in/portal/{showSubMenu,showContent,showPDFContent}?menuguide=<guid>`.
  The probe starts from two guides, follows child menus whose title looks like a
  budget menu, and stops at `--max-menu-guides` guides. The document table is an
  HTML string inside the `menuName` field of the JSON.

robots.txt: `finance.delhi.gov.in/robots.txt` answers 403 to every User-Agent
while the pages answer 200. The probe skips the check for that host only (owner
decision B, November 12, 2026). See
[`CLI.md`](CLI.md#commoner-probe-municipal-budget--city-budget-documents).

Ahmedabad Municipal Corporation (`ahmedabadcity.gov.in`) is not in the registry.
Its host needs legacy TLS renegotiation and omits its intermediate certificate
(owner decision, November 13, 2026; parked in `commoner-probe-h7k`).
`parse_anchor_index` and the `anchor_index` template stay for it.

Traps:

- A `ViewFile` link can answer an HTML page. The probe records
  `error: "body isn't a PDF"` and writes nothing.
- On a GNCTD Detailed Demands detail page the volumes carry bare numbers as file
  names and the title carries no year, so the parser drops them (sevent4
  parity; follow-up `commoner-probe-6e7`).
- A GNCTD title can carry a malformed year, `Budget Speech Hindi 2026-26`. The
  year comes from the file URL instead.
- MCD titles span two fiscal years, for example `RBE 22-23 BE 2023-24`. The
  first year wins.
- Drupal `?page=N` pagers are not followed.

Outputs: `manifest.jsonl` (`kind: municipal_budget_document`) + one PDF per
document under `<portal-code>/`.

## NITI Aayog Annual Reports (`niti-annual-report`)

Publisher: NITI Aayog. Contract verified live 2026-07-30. Carries the "Young
Professionals" headcount — the 2024-25 report states 26 Consultant
Grade II, 71 Consultant Grade I and 116 Young Professionals.

- listing: `GET https://www.niti.gov.in/publication/annual-report`
- documents: `/sites/default/files/<YYYY-MM>/<name>.pdf`

**English only** — the Hindi editions are detected and skipped. Detection is
load-bearing, not decorative: one Hindi edition is named
`Annual Report 2024-25 Hindi_V3 LOWRES.pdf`, which neither a parenthesis-anchored
nor a `\b`-anchored match catches, so without it a caller asking for English
receives a Hindi PDF.

Traps, each measured:

- **Every PDF link appears twice** — an undeduped parse doubles the corpus.
- **The upload directory looks like a fiscal year.** Files live under
  `/sites/default/files/2025-02/`, which matches a year pattern and invents
  `2020-02` and `2023-02`. Parse the year from the FILENAME.
- **The second year may be two or four digits** — `Annual-Report-2022-2023-…`
  becomes `2022-20` under a two-digit tail.
- **`/annual-reports` and `/documents/annual-report` return 404 with a 41 KB
  body**, which passes a "did we get bytes" check.
- **The WAF 403s our default user-agent** because it carries a URL fragment.
  robots.txt itself PERMITS this crawl (`can_fetch` true for `*` and for this
  tool; no `Disallow: /`) — the 403 is on reading robots.txt, and this repo
  treats an unreadable robots.txt as disallow-all, so the probe refused itself
  over a file that says yes. Fixed with a shorter honest UA
  (`commoner-probe/<ver> (research)`, verified 200). The robots check stays ON,
  no `respect_robots=False`, and no browser token — that would contradict the
  standing NAI decision.

Outputs: `manifest.jsonl` (`kind: niti_annual_report`) + one PDF per year.
