# Output schema reference

Complete field-level documentation for every file that `commoner-probe` writes.
Each section gives a **field table** with five columns:

| Column | Meaning |
|---|---|
| Field | JSON key as written in the file |
| Type | JSON type (or Python type notation for compound values) |
| Required | Always present (`yes`), present only when a condition is met (`cond`), or never required (`no`) |
| Enum / format | Legal values or format note |
| Provenance | Source module : approx line |

> **Joins** between files are listed at the end of this document.  
> **Controlled vocabularies** (enum values shared across files) are also listed
> at the end.

---

## `manifest.jsonl`

One record per downloaded parliamentary question or committee report.
Append-only; each record is a self-contained JSON object on its own line.
Records come in **five shapes** discriminated by `kind` and, for parliamentary
records, `house`.

### Shape A — Lok Sabha Q/A (`kind = "qa"`, `house = "Lok Sabha"`)

| Field | Type | Required | Enum / format | Provenance |
|---|---|---|---|---|
| `key` | string | yes | `"LS\|{qtype_char}\|{qno}\|{date}"` | sansad.py:33 |
| `run_id` | string | cond | UUID4 hex (32 chars); present in all freshly probed corpora | sansad.py:258 |
| `kind` | string | yes | `"qa"` | sansad.py:259 |
| `house` | string | yes | `"Lok Sabha"` | sansad.py:260 |
| `uuid` | string | yes | LS e-library item UUID | sansad.py:261 |
| `handle` | string | cond | LS e-library handle; may be null | sansad.py:262 |
| `title` | string | yes | Question title from `dc.title` | sansad.py:263 |
| `date` | string | yes | ISO date `YYYY-MM-DD` or `""` | sansad.py:264 |
| `qtype` | string | yes | See vocabulary; `""` if not set | sansad.py:265 |
| `qno` | string | yes | Question number string | sansad.py:266 |
| `session` | string | yes | Session number from `dc.identifier.sessionnumber` | sansad.py:267 |
| `loksabhanumber` | string | yes | Lok Sabha number from `dc.identifier.loksabhanumber` | sansad.py:268 |
| `ministry` | string | yes | Ministry name; falls back to crawl filter value | sansad.py:269 |
| `askers` | string[] | yes | Raw asker names as listed in the record | sansad.py:270 |
| `asker_details` | object[] | yes | One element per asker: `{name, party, party_name, house}`; `party`/`party_name`/`house` may be null if roster lookup fails | sansad.py:117-127 |
| `asker_entity_ids` | (string\|null)[] | yes | Parallel to `askers`; `null` when not resolved | sansad.py:130 |
| `responder_entity_id` | string\|null | yes | Reserved; always `null` in current version | sansad.py:131 |
| `responder_role_at_event` | string\|null | yes | Reserved; always `null` in current version | sansad.py:132 |
| `uri` | string | yes | Persistent URI from `dc.identifier.uri` | sansad.py:271 |
| `source` | string | yes | `"elibrary.sansad.in"` | sansad.py:272 |
| `found_via_group` | string | yes | Topic search_group name that found this record | sansad.py:273 |
| `found_via_query` | string | yes | Exact query string | sansad.py:274 |
| `probed_at` | string | cond | ISO datetime of probe (seconds precision); present in all freshly probed corpora | sansad.py:275 |
| `language_classified` | string[] | yes | Default `["en"]`; set by extractor if different | sansad.py:288 |
| `pdf_url` | string | cond | Absolute PDF URL; present only when download succeeded | sansad.py:286 |
| `pdf_path` | string | cond | Path relative to corpus `out_dir`; present only when download succeeded | sansad.py:287 |

---

### Shape B — Rajya Sabha Q/A (`kind = "qa"`, `house = "Rajya Sabha"`)

| Field | Type | Required | Enum / format | Provenance |
|---|---|---|---|---|
| `key` | string | yes | `"RS\|{qtype_char}\|{qno}\|{date}"` | sansad.py:33 |
| `run_id` | string | cond | UUID4 hex; present in all freshly probed corpora | sansad.py:400 |
| `kind` | string | yes | `"qa"` | sansad.py:402 |
| `house` | string | yes | `"Rajya Sabha"` | sansad.py:403 |
| `qslno` | string\|null | yes | RS serial question number | sansad.py:404 |
| `ses_no` | integer\|null | yes | Rajya Sabha session number | sansad.py:405 |
| `title` | string | yes | Question title (`qtitle`) | sansad.py:406 |
| `date` | string | yes | ISO date `YYYY-MM-DD` converted from `DD.MM.YYYY` | sansad.py:407 |
| `qtype` | string | yes | See vocabulary | sansad.py:408 |
| `qno` | string | yes | Question number | sansad.py:409 |
| `ministry` | string | yes | Ministry name from `min_name` | sansad.py:410 |
| `askers` | string[] | yes | Raw asker name; single-element list from `name` field | sansad.py:411 |
| `asker_details` | object[] | yes | Same structure as Shape A | sansad.py:117-127 |
| `asker_entity_ids` | (string\|null)[] | yes | Parallel to `askers` | sansad.py:130 |
| `responder_entity_id` | string\|null | yes | Reserved; always `null` | sansad.py:131 |
| `responder_role_at_event` | string\|null | yes | Reserved; always `null` | sansad.py:132 |
| `question_text` | string\|null | yes | Full question text from API (RS only); may be null | sansad.py:412 |
| `answer_text` | string\|null | yes | Full answer text from API (RS only); may be null | sansad.py:413 |
| `pdf_url` | string\|null | yes | PDF URL (English); may be null | sansad.py:414 |
| `pdf_url_hindi` | string\|null | yes | Hindi PDF URL; may be null | sansad.py:415 |
| `pdf_path` | string | cond | Relative path; present only when download succeeded | sansad.py:428 |
| `source` | string | yes | `"rsdoc.nic.in"` | sansad.py:416 |
| `found_via_query` | string | yes | Ministry filter string used as crawl query | sansad.py:417 |
| `status` | string | yes | Raw question status from API | sansad.py:418 |
| `probed_at` | string | cond | ISO datetime of probe; present in all freshly probed corpora | sansad.py:419 |
| `language_classified` | string[] | yes | Default `["en"]` | sansad.py:429 |

**Note — field divergence vs Shape A**: RS Q/A records lack `uuid`, `handle`,
`session`, `loksabhanumber`, `uri`, `found_via_group`. They carry `qslno`,
`ses_no`, `question_text`, `answer_text`, `pdf_url_hindi`, `status` instead.
Both shapes share `key`, `run_id`, `kind`, `house`, `title`, `date`, `qtype`,
`qno`, `ministry`, `askers`, `asker_details`, `asker_entity_ids`,
`responder_entity_id`, `responder_role_at_event`, `source`, `found_via_query`,
`probed_at`, `language_classified`, `pdf_url`, `pdf_path`.

---

### Shape C — LS committee report (`kind = "committee_report"`, `house = "Lok Sabha"`)

| Field | Type | Required | Enum / format | Provenance |
|---|---|---|---|---|
| `key` | string | yes | `"LS\|{slug}\|{report_no}\|{lok_sabha_no}"` | committees.py:339 |
| `run_id` | string | cond | UUID4 hex; present in all freshly probed corpora | committees.py:345 |
| `kind` | string | yes | `"committee_report"` | committees.py:347 |
| `house` | string | yes | `"Lok Sabha"` | committees.py:344 |
| `report_type` | string | yes | See `report_type` vocabulary | committees.py:348 |
| `presented_via` | string | yes | See `presented_via` vocabulary | committees.py:349 |
| `committee_slug` | string | yes | Slug key (e.g. `"finance"`, `"health"`) | committees.py:350 |
| `committee_name` | string | yes | Human-readable committee name | committees.py:351 |
| `report_no` | string\|integer\|null | yes | Report number from API (integer or string depending on house) | committees.py:352 |
| `loksabha_no` | integer\|string | yes | Lok Sabha number (e.g. `18`) | committees.py:353 |
| `title` | string | yes | Subject of report | committees.py:354 |
| `title_hindi` | string\|null | yes | Hindi subject; may be null | committees.py:355 |
| `language_classified` | string[] | yes | `["en"]` | committees.py:356 |
| `date` | string | yes | Best-available presentation/adoption date (ISO) | committees.py:357 |
| `date_presented_ls` | string | yes | ISO date or `""`; from `PresentedInLS` | committees.py:358 |
| `date_laid_rs` | string | yes | ISO date or `""`; from `LaidInRS` | committees.py:359 |
| `date_presented_speaker` | string | yes | ISO date or `""`; from `PresentedToSpeaker` | committees.py:360 |
| `date_adoption` | string | yes | ISO date or `""`; from `dateOfAdoption` | committees.py:361 |
| `pdf_url` | string\|null | yes | English PDF URL | committees.py:362 |
| `pdf_url_hindi` | string\|null | yes | Hindi PDF URL; may be null | committees.py:363 |
| `pdf_path` | string | cond | Relative path; present only when download succeeded | committees.py:375 |
| `source` | string | yes | `"sansad.in/api_ls/committee"` | committees.py:364 |
| `probed_at` | string | cond | ISO datetime; present in all freshly probed corpora | committees.py:365 |

---

### Shape D — RS committee report (`kind = "committee_report"`, `house = "Rajya Sabha"`)

| Field | Type | Required | Enum / format | Provenance |
|---|---|---|---|---|
| `key` | string | yes | `"RS\|{slug}\|{report_no}"` | committees.py:457 |
| `run_id` | string | cond | UUID4 hex; present in all freshly probed corpora | committees.py:464 |
| `kind` | string | yes | `"committee_report"` | committees.py:466 |
| `house` | string | yes | `"Rajya Sabha"` | committees.py:465 |
| `report_type` | string | yes | See `report_type` vocabulary | committees.py:467 |
| `presented_via` | string | yes | `"rs_only"` or `"none"`; see vocabulary | committees.py:461 |
| `committee_slug` | string | yes | Slug key | committees.py:468 |
| `committee_name` | string | yes | Human-readable committee name | committees.py:469 |
| `report_no` | string\|integer\|null | yes | Report number from API | committees.py:470 |
| `title` | string | yes | Subject of report | committees.py:471 |
| `title_hindi` | string\|null | yes | Hindi subject; may be null | committees.py:472 |
| `language_classified` | string[] | yes | `["en"]` | committees.py:473 |
| `date` | string | yes | Best-available date (ISO) | committees.py:474 |
| `date_presentation` | string | yes | ISO date or `""`; from `dateOfPresentation` | committees.py:476 |
| `date_adoption` | string | yes | ISO date or `""`; from `dateOfAdoption` | committees.py:477 |
| `pdf_url` | string\|null | yes | English PDF URL | committees.py:478 |
| `pdf_url_hindi` | string\|null | yes | Hindi PDF URL; may be null | committees.py:479 |
| `pdf_path` | string | cond | Relative path; present only when download succeeded | committees.py:490 |
| `source` | string | yes | `"sansad.in/api_rs/committee"` | committees.py:480 |
| `probed_at` | string | cond | ISO datetime; present in all freshly probed corpora | committees.py:481 |

**Note — field divergence between LS and RS committee reports**: LS reports carry
`loksabha_no`, `date_presented_ls`, `date_laid_rs`, `date_presented_speaker`.
RS reports carry `date_presentation` instead. Both shapes share all other fields.

---

### Shape E — MCA CSR company-spend export (`kind = "mca_csr_company_spend"`)

One record per MCA CDM CSR CSV export produced by `commoner-probe mca-csr`.
Source page verified on 2026-06-16: `https://www.mcacdm.nic.in/csr-data`.
Download endpoint: `POST https://www.mcacdm.nic.in/cdm/export.php`.

| Field | Type | Required | Enum / format | Provenance |
|---|---|---|---|---|
| `key` | string | yes | `"MCA_CSR\|FY {YYYY-YY}"` | csr/mca.py |
| `kind` | string | yes | `"mca_csr_company_spend"` | csr/mca.py |
| `record_type` | string | yes | `"mca_csr_company_spend"` | csr/mca.py |
| `year` | string | yes | `YYYY-YY` | csr/mca.py |
| `financial_year` | string | yes | `FY YYYY-YY` | csr/mca.py |
| `filename` | string | yes | `mca_csr_company_spend_{year}.csv` | csr/mca.py |
| `dest` | string | yes | Local CSV path | csr/mca.py |
| `source_page` | string | yes | MCA CDM CSR page URL | csr/mca.py |
| `url` | string | yes | MCA CDM export endpoint URL | csr/mca.py |
| `status` | string | yes | `pending`, `dry_run`, `downloaded`, `skipped_exists` | csr/mca.py |
| `sha256` | string | cond | 64-char lowercase hex; present when file exists/downloaded | csr/mca.py |
| `timestamp_utc` | string | yes | ISO datetime | csr/mca.py |
| `probed_at` | string | yes | ISO datetime | csr/mca.py |

The CSV header currently emitted by MCA CDM is:
`Company Name`, `Financial Year`, `PSU/Non-PSU`, `CSR State`,
`CSR Development Sector`, `CSR Sub Development Sector`, and
`Project Amount Spent (In INR Cr.)`.

---

### Shape F — India Code state instrument (`kind = "indiacode_instrument"`)

One record per Act, amendment, rule, regulation, notification, order,
circular, ordinance, or statute produced by `commoner-probe indiacode`.
Source verified live 2026-07 against indiacode.nic.in.

| Field | Type | Required | Enum / format | Provenance |
|---|---|---|---|---|
| `key` | string | yes | `"INDIACODE\|{state_handle}\|{act_handle}\|{instrument_type}\|{lang}[\|{filename}]"` | indiacode.py |
| `kind` | string | yes | `"indiacode_instrument"` | indiacode.py |
| `record_type` | string | yes | `"indiacode_instrument"` | indiacode.py |
| `source` | string | yes | `"indiacode.nic.in"` | indiacode.py |
| `state` | string | yes | State/UT name | indiacode.py |
| `state_handle` | string\|null | cond | India Code parent-collection handle | indiacode.py |
| `act_handle` | string\|null | cond | India Code item handle for this Act | indiacode.py |
| `act_id` | string\|null | cond | Site's internal "Act ID" metadata field | indiacode.py |
| `act_no` | string\|null | cond | "Act Number" metadata field | indiacode.py |
| `act_year` | string\|null | cond | "Act Year" metadata field | indiacode.py |
| `short_title` | string\|null | cond | "Short Title" metadata field | indiacode.py |
| `department` | string\|null | cond | "Department" metadata field | indiacode.py |
| `act_type` | string\|null | cond | "Type" metadata field, e.g. `"STATE"` | indiacode.py |
| `location` | string\|null | cond | "Location" metadata field | indiacode.py |
| `instrument_type` | string\|null | cond | `act`, `rule`, `regulation`, `notification`, `order`, `circular`, `ordinance`, `statute` | indiacode.py |
| `is_amendment` | boolean\|null | cond | `true` when the instrument's description matches `/\bamendment\b/i`; always `false` for `instrument_type = "act"` | indiacode.py |
| `instrument_date` | string\|null | cond | Date as shown on the site (not normalized) | indiacode.py |
| `description` | string\|null | cond | English description; the Act's own `short_title` for `instrument_type = "act"` | indiacode.py |
| `description_hi` | string\|null | cond | Hindi description, when present | indiacode.py |
| `lang` | string\|null | cond | `en` or `hi` | indiacode.py |
| `actid` | string\|null | cond | Site's per-Act subordinate-document folder key; `null` for the Act's own record | indiacode.py |
| `filename` | string\|null | cond | Source filename, e.g. `"32.pdf"` | indiacode.py |
| `source_url` | string\|null | cond | Absolute PDF URL | indiacode.py |
| `dest` | string\|null | cond | Local path relative to corpus `out_dir`; present after download | indiacode.py |
| `status` | string | yes | `pending`, `dry_run`, `downloaded`, `skipped_exists`, `no_pdf_found`, `fetch_error`, `unknown_state` | indiacode.py |
| `sha256` | string | cond | 64-char lowercase hex; present when downloaded/skipped_exists | indiacode.py |
| `error` | string | cond | Present on `fetch_error` | indiacode.py |
| `probed_at` | string | yes | ISO datetime | indiacode.py |

Central Acts live in a separate India Code collection tree and are out of
scope for this adapter (state statutory instruments only).

---

## `_runs.jsonl`

One record per crawl invocation (one per `crawl_ls` / `crawl_rs` / `crawl_committees` call).
Produced by `commoner_probe/runlog.py`.

| Field | Type | Required | Enum / format | Provenance |
|---|---|---|---|---|
| `run_id` | string | yes | UUID4 hex; same value stamped on manifest records | runlog.py:136 |
| `kind` | string | yes | `"committee_report"` or `"qa"` | runlog.py:94 |
| `scope` | object | yes | Crawl parameters: `house`, `from_date`, `to_date`, `max_records`, `download`, etc. Free-form per crawler kind | runlog.py:95 |
| `topic_name` | string | yes | `TopicProfile.name` | runlog.py:96 |
| `topic_path` | string | yes | Path to the topic JSON on disk at probe time | runlog.py:97 |
| `topic_hash` | string | yes | `"sha256:{hex}"` of raw topic file bytes | runlog.py:98 |
| `classifier_mode` | string | yes | Always `""` in this version (reserved for schema compat) | runlog.py:99 |
| `classifier_config_redacted` | object | yes | Always `{}` in this version; secrets would be redacted | runlog.py:100 |
| `tool_version` | string | yes | `commoner-probe` package version at probe time | runlog.py:101 |
| `started_at` | string | yes | ISO datetime | runlog.py:102 |
| `ended_at` | string\|null | yes | ISO datetime; `null` if run did not finish | runlog.py:103 |
| `added` | integer | yes | Records added in this run | runlog.py:104 |
| `errors` | object[] | yes | `[{where: str, error: str}, ...]`; empty list if clean | runlog.py:105 |
| `bucket_attempts` | object[] | yes | Per-bucket attempt log; schema is free-form. See conventional keys below | runlog.py:106-111 |
| `elapsed_ms` | number | yes | Wall-clock milliseconds | runlog.py:183 |

**Conventional keys in `bucket_attempts[]` for Q/A crawls** (`kind = "ls_qa"` / `"rs_qa"`):

| Key | Type | Note |
|---|---|---|
| `kind` | string | `"ls_qa"` or `"rs_qa"` |
| `group` | string | Topic search_group name (LS only) |
| `query` | string | Search query or ministry like-string |
| `ministry` | string | Ministry filter value |
| `session` | integer | RS session number (RS only) |
| `raw_returned` | integer | Records returned by API before any filter |
| `after_date_filter` | integer | Records after date-range filter |
| `kept` | integer | Records written to manifest |
| `skipped_seen` | integer | Records skipped as already in manifest |
| `elapsed_ms` | number | Bucket wall-clock ms |
| `error` | string\|null | Exception string if bucket failed |

**Conventional keys in `bucket_attempts[]` for committee crawls**:

| Key | Type | Note |
|---|---|---|
| `committee_slug` | string | Committee slug |
| `house` | string | `"ls"` or `"rs"` |
| `pages_fetched` | integer | Number of API pages fetched |
| `raw_returned` | integer | Records from API |
| `kept` | integer | Records written |
| `elapsed_ms` | number | Bucket wall-clock ms |
| `error` | string\|null | Exception string if failed |

---

## `answers.jsonl`

One record per extracted text unit from a downloaded PDF.
Written by `commoner-probe extract-answers`. Three record shapes discriminated by `kind`.

### Common header fields (all three shapes)

| Field | Type | Required | Enum / format | Provenance |
|---|---|---|---|---|
| `key` | string | yes | Same as the parent manifest record's `key` | answers.py:543 |
| `run_id` | string | yes | Same as the parent manifest record's `run_id` | answers.py:544 |
| `source_pdf` | string | yes | PDF path relative to corpus `out_dir` | answers.py:545 |
| `extracted_at` | string | yes | ISO datetime (UTC) | answers.py:546 |
| `language_classified` | string[] | yes | `["en"]` | answers.py:547 |
| `source_report_type` | string\|null | yes | `report_type` from the manifest record; `null` for Q/A | answers.py:553 |

### Shape `kind = "qa_response"` (from Q/A PDFs)

Emitted by `split_qa()` in `answers.py`.

| Field | Type | Required | Enum / format | Provenance |
|---|---|---|---|---|
| `kind` | string | yes | `"qa_response"` | answers.py:103 |
| `question_text` | string | yes | Full question half of the PDF | answers.py:104 |
| `answer_text` | string | yes | Full answer half | answers.py:105 |
| `confidence` | number | yes | `0.85` (answer > 50 chars) or `0.5` | answers.py:106 |
| `extractor` | string | yes | `"answers_regex_v1"` | answers.py:107 |
| `boundary_marker` | string | yes | Regex match that split the PDF | answers.py:108 |
| `question_subject` | string | cond | All-caps subject line; omitted when parser found nothing | answers.py:113 |
| `question_stem` | string | cond | "Will the Minister … state:" fragment; omitted when absent | answers.py:115 |
| `question_body` | string | cond | `(a)/(b)/(c)/(d)` sub-questions; omitted when absent | answers.py:117 |
| `answer_minister_name` | string | cond | Minister name stripped from answer prelude; omitted when absent | answers.py:119 |
| `answer_body` | string | cond | Answer text with minister-name prelude removed; omitted when absent | answers.py:121 |

### Shape `kind = "atr_response"` (from Action Taken Report PDFs)

Emitted by `split_atr()`. One record per recommendation/response pair.

| Field | Type | Required | Enum / format | Provenance |
|---|---|---|---|---|
| `kind` | string | yes | `"atr_response"` | answers.py:306 |
| `recommendation_no` | integer | yes | Serial recommendation number | answers.py:307 |
| `recommendation_text` | string | yes | Verbatim recommendation text | answers.py:308 |
| `response_text` | string | yes | Government response; may be `""` if reply boundary not found | answers.py:309 |
| `confidence` | number | yes | `0.9` / `0.5` / `0.4` | answers.py:310 |
| `extractor` | string | yes | `"answers_regex_v1"` | answers.py:311 |

### Shape `kind = "dfg_recommendation"` (from non-ATR committee PDFs)

Emitted by `split_dfg()`. One record per numbered observation paragraph.
Used for Demands for Grants, Bill scrutiny, and Subject reports; the
`source_report_type` field distinguishes them.

| Field | Type | Required | Enum / format | Provenance |
|---|---|---|---|---|
| `kind` | string | yes | `"dfg_recommendation"` | answers.py:368 |
| `recommendation_no` | integer | yes | Paragraph number | answers.py:369 |
| `recommendation_text` | string | yes | Observation/recommendation text | answers.py:370 |
| `confidence` | number | yes | `0.8` (> 80 chars) or `0.5` | answers.py:371 |
| `extractor` | string | yes | `"answers_regex_v1"` | answers.py:372 |

---

## `committee_members.jsonl`

One record per committee per `probe_composition()` call.
Written by `CommitteeProbe.probe_composition()`. Source module: `commoner_probe/committees.py`.

| Field | Type | Req | Description | Source |
|---|---|---|---|---|
| `house` | string | yes | `"LS"` or `"RS"` | committees.py:319 |
| `committee` | string | yes | Committee slug, e.g. `"finance"` | committees.py:320 |
| `committee_name` | string | yes | Human-readable committee name | committees.py:321 |
| `committee_code` | integer | yes | sansad.in committeeCode (LS) or mstCommId (RS) | committees.py:322 |
| `source` | string | yes | `"api"` or `"pdf_llm:<filename>"` | committees.py:323 |
| `members` | array | yes | Enriched member records from MPRoster | committees.py:324 |
| `probed_at` | string | yes | ISO datetime of probe | committees.py:325 |

---

## State-assembly outputs (`commoner-probe state-assembly`)

Four JSONL files written by `StateAssemblyCrawler` (NeVA portals).

### `questions.jsonl`

| Field | Type | Req | Description |
|---|---|---|---|
| `key` | string | yes | Unique record key: `{state}\|q\|{asm}\|{sess}\|{date}\|{q_no}` |
| `record_type` | string | yes | `"question"` |
| `source` | string | yes | `"neva"` |
| `state_code` | string | yes | Two-letter CMS state code, e.g. `"GJ"` |
| `portal_code` | string | yes | Portal subdomain, e.g. `"gujarat"` |
| `assembly_no` | integer | yes | Assembly number |
| `session_no` | integer/string | yes | Session code |
| `session_date_id` | integer/string/null | yes | Date ID within session |
| `question_number` | string | yes | Question number |
| `subject` | string | yes | Subject line |
| `question_text` | string | yes | Full question text |
| `ministry` | string | yes | Answering ministry |
| `member_name` | string | yes | MLA name |
| `constituency` | string | yes | Constituency |
| `pdf_urls` | array | yes | PDF download URLs |
| `pdf_path` | string/null | yes | Local PDF path after download |
| `probed_at` | string | yes | ISO datetime of probe |

### `questions_unlisted.jsonl`

Same schema as `questions.jsonl` but `record_type = "question_unlisted"`.

### `members.jsonl`

| Field | Type | Req | Description |
|---|---|---|---|
| `key` | string | yes | `{state}\|member\|{id}` |
| `record_type` | string | yes | `"member"` |
| `source` | string | yes | `"neva"` |
| `state_code` | string | yes | |
| `portal_code` | string | yes | |
| `assembly_no` | integer | yes | |
| `member_id` | integer | yes | Portal member ID |
| `name` | string | yes | Member name |
| `party` | string | yes | Party affiliation |
| `constituency` | string | yes | Constituency |
| `dob` | string | yes | Date of birth (string from portal) |
| `phone` | string | yes | Contact phone |
| `email` | string | yes | Contact email |
| `photo_url` | string | yes | Photo URL |
| `probed_at` | string | yes | ISO datetime of probe |

### `papers_laid.jsonl`

| Field | Type | Req | Description |
|---|---|---|---|
| `key` | string | yes | `{state}\|paper\|{asm}\|{sess}\|{date}\|{seq}` |
| `record_type` | string | yes | `"paper_laid"` |
| `source` | string | yes | `"neva"` |
| `state_code` | string | yes | |
| `portal_code` | string | yes | |
| `assembly_no` | integer | yes | |
| `session_no` | integer/string | yes | |
| `session_date_id` | integer/string/null | yes | |
| `serial_no` | string | yes | Serial number |
| `title` | string | yes | Document title |
| `ministry` | string | yes | Presenting ministry |
| `pdf_urls` | array | yes | PDF URLs |
| `pdf_path` | string/null | yes | Local path after download |
| `probed_at` | string | yes | ISO datetime of probe |

---

## State-assembly outputs (`commoner-probe state-assembly`)

Written by `StateAssemblyCrawler` to the corpus directory. Source module: `commoner_probe/neva.py`.
All four files share: `key`, `record_type`, `source` (`"neva"`), `state_code`, `portal_code`, `assembly_no`, `probed_at`.

### `questions.jsonl` — listed questions (`record_type = "question"`)

| Field | Type | Req | Description |
|---|---|---|---|
| `key` | string | yes | Unique dedup key |
| `record_type` | string | yes | `"question"` |
| `source` | string | yes | `"neva"` |
| `state_code` | string | yes | E.g. `"GJ"` |
| `portal_code` | string | yes | E.g. `"gujarat"` |
| `assembly_no` | integer | yes | Assembly number |
| `session_no` | integer/string | yes | Session code |
| `session_date_id` | integer/string/null | yes | Date-within-session id |
| `question_number` | string | yes | Question number |
| `subject` | string | yes | Question subject |
| `question_text` | string | yes | Full question text |
| `ministry` | string | yes | Answering ministry |
| `member_name` | string | yes | Asker name |
| `constituency` | string | yes | Asker constituency |
| `pdf_urls` | array | yes | PDF URLs |
| `pdf_path` | string/null | yes | Local PDF path after download |
| `probed_at` | string | yes | ISO datetime of probe |

### `questions_unlisted.jsonl` — unlisted questions (`record_type = "question_unlisted"`)

Same fields as `questions.jsonl`; `session_date_id` is always `null`.

### `members.jsonl` — member directory (`record_type = "member"`)

| Field | Type | Req | Description |
|---|---|---|---|
| `key` | string | yes | Unique dedup key |
| `record_type` | string | yes | `"member"` |
| `state_code` | string | yes | State code |
| `member_id` | integer | yes | NeVA member ID |
| `name` | string | yes | Member name |
| `probed_at` | string | yes | ISO datetime of probe |
| `party` | string | — | Party name |
| `constituency` | string | — | Constituency |
| `dob` | string | — | Date of birth |
| `phone` | string | — | Phone number |
| `email` | string | — | Email address |
| `photo_url` | string | — | Member photo URL |

### `papers_laid.jsonl` — papers to be laid (`record_type = "paper_laid"`)

| Field | Type | Req | Description |
|---|---|---|---|
| `key` | string | yes | Unique dedup key |
| `record_type` | string | yes | `"paper_laid"` |
| `source` | string | yes | `"neva"` |
| `state_code` | string | yes | State code |
| `portal_code` | string | yes | Portal code |
| `assembly_no` | integer | yes | Assembly number |
| `session_no` | integer/string | — | Session code |
| `session_date_id` | integer/string/null | — | Date-within-session id |
| `serial_no` | string | — | Serial number |
| `title` | string | — | Paper title |
| `ministry` | string | — | Ministry |
| `pdf_urls` | array | — | PDF URLs |
| `pdf_path` | string/null | — | Local PDF path |
| `probed_at` | string | yes | ISO datetime of probe |

---

## `atr_linkage.jsonl`

One record per Action Taken Report that could be linked to its original report.
Written by `commoner-probe atr-linkage`. Source module: `commoner_probe/atr_linkage.py`.

| Field | Type | Required | Enum / format | Provenance |
|---|---|---|---|---|
| `atr_key` | string | yes | `key` of the ATR manifest record | atr_linkage.py:174 |
| `atr_no` | integer\|string\|null | yes | ATR's own report number | atr_linkage.py:175 |
| `house` | string\|null | yes | `"Lok Sabha"` or `"Rajya Sabha"` | atr_linkage.py:176 |
| `committee_slug` | string\|null | yes | Committee slug | atr_linkage.py:177 |
| `atr_title` | string | yes | ATR title truncated to 200 chars | atr_linkage.py:178 |
| `references_report_no` | integer | yes | Report number of the original report cited | atr_linkage.py:179 |
| `references_report_key` | string\|null | yes | Computed `key` of the original report; `null` when key cannot be determined | atr_linkage.py:180 |
| `extracted_at` | string | yes | ISO datetime | atr_linkage.py:181 |
| `extractor` | string | yes | `"atr_linkage_v1"` | atr_linkage.py:182 |

---

## `entities/people.jsonl`

One record per unique person encountered. Written by `EntityStore.save()`.
Source: `commoner_probe/entities.py`.

| Field | Type | Required | Enum / format | Provenance |
|---|---|---|---|---|
| `entity_id` | string | yes | `"PERSON_{8-hex}_{slug}"` | entities.py:157 |
| `canonical_name` | string | yes | Normalised canonical name | entities.py:166 |
| `alt_names` | string[] | yes | Additional name forms | entities.py:167 |
| `primary_kind` | string | yes | See vocabulary | entities.py:168 |
| `first_seen_at` | string | yes | ISO datetime | entities.py:169 |
| `last_updated_at` | string | yes | ISO datetime | entities.py:170 |

## `entities/mp_memberships.jsonl`

| Field | Type | Required | Enum / format | Provenance |
|---|---|---|---|---|
| `entity_id` | string | yes | FK to `people.jsonl` | entities.py:176 |
| `house` | string | yes | `"ls"` or `"rs"` | entities.py:177 |
| `term` | integer\|null | yes | Lok Sabha number (e.g. `18`) or RS term when known | entities.py:178 |
| `party` | string | yes | Party abbreviation (may be `""`) | entities.py:179 |
| `party_name` | string | yes | Full party name (may be `""`) | entities.py:180 |
| `state` | string\|null | yes | State/UT | entities.py:181 |
| `constituency` | string\|null | yes | Constituency name | entities.py:182 |
| `start` | string\|null | yes | ISO date | entities.py:183 |
| `end` | string\|null | yes | ISO date; `null` = currently sitting | entities.py:184 |
| `fetched_at` | string | yes | ISO datetime | entities.py:185 |

## `entities/committee_memberships.jsonl`

| Field | Type | Required | Enum / format | Provenance |
|---|---|---|---|---|
| `entity_id` | string | yes | FK to `people.jsonl` | entities.py:200 |
| `committee_slug` | string | yes | Committee slug | entities.py:201 |
| `house` | string | yes | `"ls"` or `"rs"` | entities.py:202 |
| `role` | string | yes | `"chairperson"` or `"member"` | entities.py:203 |
| `term` | integer\|null | yes | Lok Sabha number | entities.py:204 |
| `start` | string\|null | yes | ISO date | entities.py:205 |
| `end` | string\|null | yes | ISO date | entities.py:206 |
| `fetched_at` | string | yes | ISO datetime | entities.py:207 |

## `entities/ministerial_appointments.jsonl`

| Field | Type | Required | Enum / format | Provenance |
|---|---|---|---|---|
| `entity_id` | string | yes | FK to `people.jsonl` | entities.py:210 |
| `ministry` | string | yes | Ministry name | entities.py:211 |
| `rank` | string | yes | `"cabinet"`, `"mos_independent"`, or `"mos"` | entities.py:212 |
| `start` | string\|null | yes | ISO date | entities.py:213 |
| `end` | string\|null | yes | ISO date | entities.py:214 |
| `govt_period` | string\|null | yes | e.g. `"Modi-2"`, `"UPA-1"` | entities.py:215 |
| `fetched_at` | string | yes | ISO datetime | entities.py:216 |

## `entities/bureaucratic_postings.jsonl`

| Field | Type | Required | Enum / format | Provenance |
|---|---|---|---|---|
| `entity_id` | string | yes | FK to `people.jsonl` | entities.py:219 |
| `designation` | string | yes | Job title | entities.py:220 |
| `ministry` | string | yes | Ministry name | entities.py:221 |
| `department` | string\|null | yes | Department name | entities.py:222 |
| `cadre` | string\|null | yes | IAS cadre, e.g. `"UP"`, `"TN"` | entities.py:223 |
| `batch` | integer\|null | yes | IAS batch year | entities.py:224 |
| `start` | string\|null | yes | ISO date | entities.py:225 |
| `end` | string\|null | yes | ISO date | entities.py:226 |
| `fetched_at` | string | yes | ISO datetime | entities.py:227 |

---

## Controlled vocabularies

### `kind` (manifest)

| Value | Meaning |
|---|---|
| `"qa"` | Parliamentary question and answer |
| `"committee_report"` | Standing committee report |

### `house` (manifest, `_runs.jsonl` scope, entity files)

| Value | Context |
|---|---|
| `"Lok Sabha"` | manifest records |
| `"Rajya Sabha"` | manifest records |
| `"ls"` | entity files (`mp_memberships`, `committee_memberships`) |
| `"rs"` | entity files |

### `qtype` (manifest — Q/A shapes)

| Value | Meaning |
|---|---|
| `"STARRED"` | Starred question (oral answer expected) |
| `"UNSTARRED"` | Unstarred question (written answer) |
| `""` | Not classified (rare; treat as unstarred) |

### `report_type` (manifest — committee report shapes)

| Value | Meaning |
|---|---|
| `"action_taken"` | Government's Action Taken Report on a prior committee report |
| `"demands_for_grants"` | Annual budget-scrutiny report |
| `"bill"` | Bill-examination report |
| `"subject"` | Own-initiative subject / policy report |
| `"other"` | Title empty or pattern not recognised |

### `presented_via` (manifest — committee report shapes)

| Value | Meaning |
|---|---|
| `"both_houses"` | Presented in both Lok Sabha and Rajya Sabha |
| `"ls_only"` | Presented in Lok Sabha only |
| `"rs_only"` | Presented in Rajya Sabha only |
| `"speaker_only"` | Presented to the Speaker but not yet laid in either house |
| `"none"` | No presentation date available |

### `source` (manifest)

| Value | Which crawler |
|---|---|
| `"elibrary.sansad.in"` | LS Q/A |
| `"rsdoc.nic.in"` | RS Q/A |
| `"sansad.in/api_ls/committee"` | LS committee reports |
| `"sansad.in/api_rs/committee"` | RS committee reports |

### `kind` (answers.jsonl)

| Value | PDF type |
|---|---|
| `"qa_response"` | Q/A PDF |
| `"atr_response"` | Action Taken Report PDF |
| `"dfg_recommendation"` | Any other committee report (DFG, bill, subject) |

### `extractor` (answers.jsonl, atr_linkage.jsonl)

| Value | Module : version constant |
|---|---|
| `"answers_regex_v1"` | answers.py:EXTRACTOR_VERSION |
| `"atr_linkage_v1"` | atr_linkage.py:EXTRACTOR_VERSION |

### `primary_kind` (entities/people.jsonl)

| Value | Meaning |
|---|---|
| `"politician"` | Elected representative or party official |
| `"bureaucrat"` | Civil servant / IAS / IPS |
| `"expert_witness"` | External witness to a committee |
| `"unknown"` | Not yet classified |

---

## Joins

```
manifest.jsonl       key              ←→  answers.jsonl         key
manifest.jsonl       run_id           ←→  _runs.jsonl           run_id
manifest.jsonl       key              ←→  atr_linkage.jsonl     atr_key     (ATR side)
atr_linkage.jsonl    references_report_key  ←→  manifest.jsonl  key         (original report side)
manifest.jsonl       asker_entity_ids[]  ←→  entities/people.jsonl  entity_id
entities/people.jsonl entity_id       ←→  entities/mp_memberships.jsonl          entity_id
entities/people.jsonl entity_id       ←→  entities/committee_memberships.jsonl   entity_id
entities/people.jsonl entity_id       ←→  entities/ministerial_appointments.jsonl entity_id
entities/people.jsonl entity_id       ←→  entities/bureaucratic_postings.jsonl   entity_id
```

**ATR life-cycle chain** (the research-grade unit of analysis):

```
manifest (kind=committee_report, report_type=demands_for_grants | bill | subject)
  └─ answers (kind=dfg_recommendation)         ← committee's original recommendation
  └─ atr_linkage.references_report_key
       └─ manifest (kind=committee_report, report_type=action_taken)
            └─ answers (kind=atr_response)     ← government's response
```

**Corpus key conventions**:

| House | Record kind | Key pattern |
|---|---|---|
| Lok Sabha | Q/A | `LS\|{S\|U}\|{qno}\|{YYYY-MM-DD}` |
| Rajya Sabha | Q/A | `RS\|{S\|U}\|{qno}\|{YYYY-MM-DD}` |
| Lok Sabha | Committee report | `LS\|{committee_slug}\|{report_no}\|{lok_sabha_no}` |
| Rajya Sabha | Committee report | `RS\|{committee_slug}\|{report_no}` |

`pdf_path` values are relative to the corpus `out_dir`. To get an absolute path,
join with the directory that contains `manifest.jsonl`.
