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

## Gujarat Maritime Board (GMB)

`commoner-probe gmb` probes gmbports.org (IIS/ASP.NET) for Gujarat Maritime
Board public disclosures, per `_org/requests/0006-...` (from narcotrek).
Content lives at `showpage.aspx?contentid=N`; documents under
`/assets/downloads/`. Ten selectable source classes (`--sources`, `all` =
every class), each a fixed set of `contentid`s:

- **admin-reports** (`56`, `307`) — Administrative (annual) reports, English
  + Gujarati, filtered from the Publications page by keyword
  (`admin`/`annual`/`report`).
- **publications-misc** (`56`) — Maritime Horizon magazine, brochures — same
  Publications page, disjoint keyword filter (`horizon`/`brochure`/
  `magazine`/`newsletter`/`publication`) so the two sources don't overlap.
- **financials** (`50`) — income/expenditure account PDFs.
- **traffic** (`46`, `504`) — **HTML tables**, not PDFs: per-port cargo
  tonnage by jetty class (GMB Jetty / Captive Jetty / Private Jetty /
  Private Port) and named port. Parsed into a tidy long-format CSV
  (`table_section, operator_class, port_or_class, fiscal_year,
  tonnage_lakh_tonnes`); the source HTML is saved alongside for provenance.
  A CSV is written even for an empty page (header-only), so the manifest
  always records that the page was checked.
- **tariff** (`212`), **circulars** (`3208`), **tenders** (`63`), **rti**
  (`69`), **vision-2047** (`30454`), **news-articles** (`876`) — generic
  "save the rendered page HTML + download every linked PDF" handler.

Fetching goes through `http_client.make_session()` (SSRF guard, robots.txt,
1 req/s, backoff, identifying UA) — no raw urllib/requests.

Outputs:

- `manifest.jsonl` records with `kind = "gmb_document"`
- one page-HTML snapshot per `contentid` visited
- one PDF per discovered document (or one derived CSV per traffic page)

Example:

```bash
commoner-probe gmb --out data/gmb --sources admin-reports,traffic
```

**Live-verification status:** gmbports.org was unreachable from the
development environment when this adapter was finalised (DNS resolved;
every TCP connection attempt to port 80/443 timed out) — this looks like a
network-level block (Indian state-government sites are known to
geo/IP-block some hosting and cloud-provider ranges), not a code issue, but
it could not be conclusively ruled out this session. The adapter is built
and offline-tested against the exact `contentid`s and filename examples
documented as verified in the filing spec (`_org/requests/0006-*.md`), but
has not been independently re-verified against the live page structure.
Live-check before a real acquisition run.

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
