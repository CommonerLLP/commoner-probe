# TODO: Finance Document Disclosure Adapters

Status: scoped from SevenT4 Ahmedabad and Delhi finance work.

Commoner-probe should absorb the reusable acquisition and parsing capabilities
developed in SevenT4, without becoming a SevenT4 city-analysis repo. The right
boundary is:

- commoner-probe discovers, downloads, OCRs, parses, and provenance-logs public
  disclosure documents;
- SevenT4 consumes those outputs to build city-specific finance layers,
  walkability analysis, and explanatory atlas views.

## Why This Belongs Here

SevenT4 has now produced two different municipal-finance acquisition patterns:

- Ahmedabad: municipal budget and balance-sheet pages, Gujarati/English budget
  PDFs, OCR of dense numeric pages, Gujarati numeral normalization, local
  label parsing, and road-spend evidence mining from long budget books.
- Delhi: GNCTD finance pages, Detailed Demands for Grants pages, legacy Delhi
  budget iframe pages, MCD nested menu JSON/HTML, and source-first manifesting
  with optional PDF download.

Those are not just city scripts. They are portal and document-disclosure
capabilities commoner-probe should own.

## Source Capabilities To Port From SevenT4

### Ahmedabad Finance

SevenT4 files:

- `scripts/recipes/ahmedabad/fetch_city_budget.py`
- `scripts/recipes/ahmedabad/ocr_city_budget.py`
- `scripts/recipes/ahmedabad/parse_city_budget.py`
- `scripts/recipes/ahmedabad/mine_amc_road_spend.py`

Reusable capabilities:

- municipal finance-page link discovery from public HTML pages;
- budget versus balance-sheet/audit-report source classes;
- fiscal-year parsing from labels, URLs, and dates such as `31-03-2024`;
- deterministic PDF filename generation;
- dense numeric page selection using `pdftotext`;
- OCR runner for scanned or low-quality numeric pages;
- Gujarati plus English OCR mode;
- Gujarati digit normalization;
- city-specific label dictionaries for budget-line extraction;
- page classification for code tables, narrative pages, ward tables, and
  contractor-candidate pages;
- raw evidence preservation with year, source PDF, page, code, department, and
  raw line before any interpretation.

What should stay in SevenT4:

- turning parsed AMC lines into atlas layers;
- Ahmedabad-specific judgments about AMTS, MJ Library, roads, property tax,
  and devolution;
- public UI/console presentation.

### Delhi Finance

SevenT4 files:

- `scripts/recipes/delhi/acquire_finance.py`
- `tests/test_delhi_finance_acquisition.py`
- `scripts/probe_topics/seventy_fourth_amendment.json`

Reusable capabilities:

- GNCTD budget document discovery from official finance pages;
- Detailed Demands for Grants index/detail page traversal;
- legacy Delhi/budget-crawler style `td.subheading` plus iframe PDF discovery;
- MCD nested JSON menu walking;
- extraction of HTML embedded inside JSON fields;
- MCD budget row filtering for budget, estimates, receipts, income,
  expenditure, RBE, and BE documents;
- source-first document manifests with publisher, source page, document type,
  fiscal year, local path, hash, and status;
- optional PDF download through the shared HTTP policy surface;
- run logs that record scope, source surfaces, document count, and errors.

What should stay in SevenT4:

- Delhi special-case interpretation across GNCTD, MCD, NDMC, DCB, DDA, Union
  control, and NCT constitutional status;
- joining finance documents to Delhi atlas layers.

## Target Architecture In Commoner-Probe

### Generic Document Discovery Layer

Create:

- `commoner_probe/document_discovery.py`
- `tests/test_document_discovery.py`

Responsibilities:

- `DiscoveredDocument` dataclass;
- fiscal-year parser shared by Ahmedabad and Delhi;
- `clean_text`;
- safe filename generation;
- HTML anchor extraction;
- iframe extraction;
- nested JSON walker;
- HTML-in-JSON extraction;
- sha256 after download;
- source URL/path preservation.

The generic layer should not know about Ahmedabad or Delhi.

### Finance Package

Create:

- `commoner_probe/finance/__init__.py`
- `commoner_probe/finance/ahmedabad.py`
- `commoner_probe/finance/delhi.py`
- `tests/test_finance_ahmedabad.py`
- `tests/test_finance_delhi.py`

Ahmedabad adapter responsibilities:

- discover AMC budget PDFs;
- discover AMC balance-sheet/audit-report PDFs;
- expose OCR page-selection helpers;
- expose Gujarati numeric/label parsing helpers;
- expose road-spend evidence mining as raw evidence rows, not interpreted
  finance claims.

Delhi adapter responsibilities:

- discover GNCTD budget documents;
- discover detailed demands for grants;
- discover legacy Delhi budget references;
- discover MCD budget documents through nested menus;
- normalize rows into `DiscoveredDocument`.

### Schema And Corpus API

Create:

- `commoner_probe/schemas/manifest_disclosure_document.schema.json`

Modify:

- `commoner_probe/records.py`
- `commoner_probe/corpus.py`
- `commoner_probe/validate.py` if explicit schema routing is needed

Add:

- `ManifestDisclosureDocumentRecord`
- `Corpus.manifest_documents()`

Record fields:

- `key`
- `kind = "disclosure_document"`
- `publisher`
- `jurisdiction`
- `government`
- `document_type`
- `fiscal_year`
- `title`
- `source_page`
- `url`
- `pdf_path`
- `sha256`
- `status`
- `run_id`
- `probed_at`

### CLI

Modify:

- `commoner_probe/cli.py`

Add:

```bash
commoner-probe finance-docs \
  --jurisdiction ahmedabad \
  --scope all \
  --out data/ahmedabad-finance \
  --download
```

```bash
commoner-probe finance-docs \
  --jurisdiction delhi \
  --scope all \
  --out data/delhi-finance \
  --download
```

Flags:

- `--jurisdiction ahmedabad|delhi`
- `--scope all|budget|balance-sheet|roads|gnctd|legacy|mcd`
- `--out`
- `--download`
- `--sleep`
- `--max-records`
- `--refresh`

Optional later:

- `--socks`, but only if implemented through `commoner_probe.http_client`, not
  ad hoc curl or urllib.

### HTTP Policy

Modify only if necessary:

- `commoner_probe/http_client.py`
- `commoner_probe/base.py`

Required:

- no direct `urllib` in new adapters;
- no ad hoc `curl` subprocess in library code;
- SSRF guard remains the single gate;
- robots/rate-limit/retry/cache policy remains shared;
- binary downloads use the same guarded session surface as metadata fetches;
- URL path/query encoding remains deterministic.

## Tests To Add

### Generic Discovery Tests

- fiscal-year parser accepts `2026-27`, `22-23`, `2015-16 VOA`;
- fiscal-year parser rejects invalid `2026-26`;
- fiscal-year parser derives `2023-24` from `31-03-2024`;
- HTML anchor parser extracts labels and absolute URLs;
- iframe parser extracts PDF URLs;
- nested JSON walker finds `pdfPath`, `downloadUrl`, `href`, and HTML strings;
- safe filename preserves `.pdf` after truncation;
- sha256 is computed for downloaded files.

### Ahmedabad Tests

- AMC budget page sample returns only budget PDFs;
- AMC balance-sheet page sample returns audit/account PDFs;
- Gujarati digits normalize to ASCII digits;
- OCR dense-page selector ranks pages by numeric-token count;
- budget label parser finds revenue expense, capital transfer, capital expense,
  loan charges, and grand total from OCR text;
- road-spend miner classifies code-table, narrative, ward-table, and
  contractor-candidate pages;
- road-spend miner emits raw evidence rows with source page and raw line.

### Delhi Tests

Port the SevenT4 tests from `tests/test_delhi_finance_acquisition.py`:

- GNCTD finance link extraction;
- URL-year fallback when title year is invalid;
- no page-title fallback for unrelated chrome PDFs;
- detailed demands index traversal;
- MCD nested menu JSON parsing;
- HTML embedded in JSON parsing;
- income/expenditure RBE rows retained;
- MCD budget menu-guide discovery;
- legacy `td.subheading` plus iframe discovery;
- filename truncation preserves `.pdf`.

### CLI And Schema Tests

- `finance-docs --jurisdiction ahmedabad --no-download` writes
  `manifest.jsonl`;
- `finance-docs --jurisdiction delhi --no-download` writes `manifest.jsonl`;
- manifest validates against `manifest_disclosure_document.schema.json`;
- `_runs.jsonl` records scope, jurisdiction, source surfaces, and errors;
- runlog redacts keys/tokens/auth if proxy or header config is added.

## Built-In Topic Profile

Move the SevenT4 profile:

- from `scripts/probe_topics/seventy_fourth_amendment.json`
- to `examples/topics/seventy_fourth_amendment.json`
- and `commoner_probe/example_topics/seventy_fourth_amendment.json`

Then ensure:

```bash
commoner-probe init-topic \
  --name seventy_fourth_amendment \
  --out topic.json
```

works.

## Implementation Order

1. Add `document_discovery.py` with unit tests.
2. Add manifest schema, typed record, and `Corpus.manifest_documents()`.
3. Add Ahmedabad finance adapter and tests.
4. Add Delhi finance adapter and tests.
5. Add `finance-docs` CLI and CLI smoke tests.
6. Add built-in 74th Amendment topic profile.
7. Update README with finance document examples.
8. Run full tests.
9. Commit as one coherent commoner-probe feature branch, or split into
   discovery/schema, Ahmedabad, Delhi, and CLI commits.

## Acceptance Criteria

- Commoner-probe can discover Ahmedabad AMC budget and balance-sheet documents
  into schema-valid `manifest.jsonl`.
- Commoner-probe can discover Delhi GNCTD and MCD finance documents into
  schema-valid `manifest.jsonl`.
- Ahmedabad OCR and raw budget-line parsing helpers are available as library
  functions and covered by deterministic fixture tests.
- Delhi nested JSON, iframe, and detail-page discovery are covered by
  deterministic fixture tests.
- No new adapter bypasses `commoner_probe.http_client`.
- SevenT4 can later replace its local finance acquisition scripts with calls to
  commoner-probe outputs.
