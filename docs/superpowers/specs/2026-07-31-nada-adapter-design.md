# NADA platform adapter — design

Date: 2026-07-31
Status: approved, not yet implemented

Subordinate to `SCOPE.md`. This is an implementation input for one adapter, not a
scope, roadmap, or architecture document. Where it disagrees with `SCOPE.md`,
`ROADMAP.md`, or `docs/ARCHITECTURE.md`, those win.

## Why

MoSPI publishes its survey documentation through a NADA instance at
`microdata.gov.in/NADA`. The NSS questionnaires (schedules), technical
documents, reports, and the written sample design for each round live there.
Nothing in this repo reaches it: `mospi.py` is a client for the eSankhyiki
statistics REST API on a different host, and returns aggregated indicator rows,
not survey instruments.

The target is enumeration: walk the catalogue, and for any NSS round obtain its
questionnaire and its methodology rather than hand-picking one PDF at a time.

NADA is World Bank software, not a MoSPI product. `censusindia.gov.in/nada` runs
the same application with the same API and 40,254 studies (verified 2026-07-31).
A per-instance adapter would have to be written twice, so this one is
parameterised by base URL. The Census instance is also the acquisition surface
the urban half needs; serving it is a consequence of this design, not a
commitment made here.

## Scope

In scope, for one NADA instance:

- Enumerate collections and studies, with filtering and a bounded default.
- Per study: DDI metadata (which carries the methodology prose), the
  related-materials document list, the document files themselves, and the
  variable and data-file listings as metadata.
- An opt-in second pass that extracts text from the downloaded PDFs.

Out of scope, deliberately:

- **Microdata data files.** They are login-gated
  (`/catalog/{id}/get-microdata` redirects to a login form, verified
  2026-07-31). This adapter acquires no credentials and implements no login.
  Recorded in the module docstring so a later session does not read the absence
  as an unfinished feature.
- Any full-catalogue run. The adapter is bounded by default; a bulk acquisition
  is a separate decision by Commoner.
- Parsing questionnaire structure out of the PDFs. Acquisition and text
  extraction only; interpretation is a consumer's job.

## Where the code lives

`commoner_probe/nada.py`, a platform adapter beside `legacy_dspace.py` — one
module, many instances, selected by `--base-url`. `mospi.py` is not modified:
different host, different platform, and `api.mospi.gov.in` requires India egress
while `microdata.gov.in` does not.

Text extraction stays in this package. The org registry assigns generic
text/PDF extraction to `partial-recall` *for retrieval*; extraction that runs on
a PDF this repo just downloaded, to produce text a downstream repo consumes, is
acquisition. `textparse` is already imported by nine modules here, and this
package declares `dependencies = []`, so a Layer 0 path cannot import Layer 1.

## Source contract

Every statement below was verified live against `microdata.gov.in` on
2026-07-31 from US egress. No India relay is required.

1. **Reachability and robots.** `microdata.gov.in` answers HTTP 200. Its
   `/robots.txt` returns 404, and `http_client._get_robot_parser` fails open on
   404 — only 401 and 403 mean disallow-all. The crawl is permitted under this
   repo's own stricter-than-RFC-9309 policy, with the robots check left on.

2. **Study routes key on `idno`, not the numeric id.**
   `/api/catalog/1` returns HTTP 400 `{"status":"failed","message":"IDNO-NOT-FOUND"}`.
   `/api/catalog/DDI-IND-MOSPI-NSSO-68Rnd-Sch1.0-July2011-June2012` returns the
   full record. The numeric `id` from search results is used only to build
   HTML page URLs.

3. **Unknown API subroutes return the study payload with HTTP 200.** Both
   `/api/catalog/{idno}/resources` and `/api/catalog/{idno}/related_materials`
   return 22,378 bytes — byte-identical to the bare study route — rather than a
   resource list or an error. An adapter that called either and recorded
   "resources fetched" would store the wrong object and report success. This
   adapter calls neither. The study fetch asserts the payload shape
   (`status == "success"` and a `dataset` object carrying the requested `idno`)
   before accepting it.

4. **The document list exists only as HTML.** `/catalog/{numeric_id}/related-materials`
   returns the resource table. Documents are grouped in `<fieldset><legend>`
   blocks; each entry carries a `<span class="resource-info" id="{resource_id}">`
   with the title, and an `<a href=".../catalog/{id}/download/{resource_id}"
   data-filename="...">`.

5. **That page can fail.** Study 40 returns HTTP 500 while studies 1, 2 and 150
   return 200. "The page errored" and "this study has no documents" are
   therefore different facts and get different records — see `resources_status`
   below. Collapsing them is the silent-success failure this repo has shipped
   repeatedly.

6. **Resource type is an open set.** It comes from the `<legend>` text. Study 1
   yields `Questionnaires`; studies 2 and 150 yield `Reports`,
   `Technical documents`, `Other Materials`. Recorded as a free string. No enum,
   because an enum would reject an unseen legend on a corpus that already
   validates.

7. **Downloads mislabel their content type.** `download/6420` returns
   `Content-Type: application/octet-stream` with
   `Content-Disposition: attachment; filename="Schedule_68_1_0_type1.pdf"`, and
   the body is a real PDF 1.5 of 672,031 bytes. Filename and extension come from
   `data-filename` or `Content-Disposition`, never from the content type.

8. **Methodology is in the API.** `dataset.metadata.study_desc.method.data_collection`
   carries `sampling_procedure` (the full written sample design),
   `time_method`, and `data_collectors`.

9. **Search.** `/api/catalog/search` reports `found` 187 for the whole
   catalogue. `ps` sets page size, `page` advances (`offset` moves with it),
   `collection=PLFS` filters to 14, and `sk=NSS` free-text matches 129.
   `/api/catalog/collections` returns 9 collections.

10. **Metadata at source can be internally inconsistent.** Study 1 is titled
    "July 2011 - June 2012" (NSS 68th round) while its
    `method.data_collection.time_method` reads "July 2007-June 2008". The
    adapter records both as found and corrects neither.

## CLI

```
commoner-probe nada [--base-url URL] [--out DIR] ...
```

`--base-url` defaults to `https://microdata.gov.in/NADA`.

| flag | effect |
|---|---|
| `--list-collections` | print the 9 collections and exit |
| `--collection CODE` | restrict enumeration to one collection (`collection=`) |
| `--query TEXT` | free-text catalogue search (`sk=`) |
| `--study IDNO` | acquire exactly one study; repeatable |
| `--max-studies N` | bound the run; **required for any enumeration run** — that is, whenever `--study`, `--list-collections` and `--extract-text` are all absent |
| `--max-docs-per-study N` | default 25 |
| `--no-download-docs` | list documents without fetching them |
| `--dry-run` | enumerate and report what would be fetched; fetch nothing |
| `--extract-text` | run the extraction pass instead of acquiring |
| `--ocr` | in the extraction pass, allow the OCR rung |
| `--sleep SECONDS` | default 2.0 |
| `--out DIR` | required; the corpus directory |

`--max-studies` being mandatory for an enumeration run is deliberate: there is
no invocation of this command that walks 187 (or 40,254) studies because the
operator forgot a flag.

## Bounded by construction

The library imposes the scope discipline; the operator should not have to supply
it from good intentions. A new user must not be able to fire thousands of
requests at a government portal by accident, and the CLI's shape should teach
the bounded habit rather than document it.

This is already the house pattern — `--max-acts`, `--max-buckets`,
`--max-records`, `--max-pages` are labelled "smoke-test brake" across the
existing adapters, and five subcommands carry `--dry-run`. What this adapter
adds is making the brake **non-optional** for the unbounded surface.

| bound | value |
|---|---|
| `--max-studies` | required for enumeration runs; no default that means "all" |
| `--max-docs-per-study` | default 25 — study 150 alone lists 63 resources, so ten studies could otherwise mean 600 files unasked |
| `--sleep` | default 2.0s, above the 1 req/s floor the shared client enforces per domain |
| concurrency | none; requests are serial by construction |
| `--dry-run` | enumerate and list what would be fetched, with counts, fetching no document |

Two properties make small bounds practical rather than punitive:

- **Re-running is cheap.** Documents already on disk record `skipped_exists`
  and are not re-fetched, so raising `--max-studies` and running again resumes
  rather than restarts.
- **Hitting the bound says so, with the next command.** When a run stops on a
  brake it reports how many studies remain and prints the exact invocation that
  continues from there. A bound that stops silently teaches nothing; the message
  is where the operator learns the scope habit.

There is no `--all`. Acquiring the full catalogue requires deliberately passing
a large `--max-studies`, which is a decision someone made rather than a default
someone inherited.

## Politeness posture, and one gap it depends on

Inherited from `http_client` and unchanged here: per-domain rate limiting at 1
req/s enforced globally across sessions, an honest User-Agent naming the tool so
operators can make contact, robots.txt checked per domain before the first
request, SSRF guard, and exponential backoff to a 30s cap.

**Gap, verified 2026-07-31 by reading `RetrySession._request`:** the retry loop
backs off on `500 <= status < 600` and on network exceptions only. **HTTP 429 is
not retried and not backed off** — it returns to the caller like a success, and
the caller's next request goes out on the ordinary 1 req/s schedule. The
`Retry-After` header is not read anywhere in the module. The backoff is
`2 ** attempt` with no jitter. The module docstring says "Government portals
429/503 without warning" while the code handles only 503.

This matters for exactly the reason the bounds above exist: 429 is the portal
asking for a slower rate, and ignoring it is how a polite crawler becomes a
banned one. The successful packages in this space treat it as table stakes —
`urllib3.Retry` defaults to `respect_retry_after_header=True` and carries 429 in
its status list, urllib3 2.x added `backoff_jitter`, and Scrapy's retry
middleware retries 429 while AutoThrottle adapts the delay to observed latency.

**This is shared Layer 0 infrastructure used by every adapter, so it is not
fixed inside this adapter.** It is a small separate change — retry 429, honour
`Retry-After` (capped), add full jitter to the backoff — and is proposed as its
own PR. This spec depends on it only in the sense that every adapter does.

## Help text

`--help` is part of the interface, and the standard here is set by what the
successful CLIs do: `pip`, `gh`, `docker` and `rg` all put worked examples in
the help itself, because an example is what a new operator actually reads.

Baseline, measured 2026-07-31: per-flag help in this CLI is genuinely good —
`census --surface` and `--api-key` explain the trap, not just the type — but
there are **zero epilogs across all 33 subcommands**, so no subcommand shows an
example, and flags with defaults (`--sleep`) print no default and sometimes no
help line at all.

`nada` ships with:

- An **epilog carrying four runnable examples**, in the order a new operator
  meets them: list collections; dry-run a bounded search; acquire three studies;
  extract text. Requires `RawDescriptionHelpFormatter` so the block survives
  wrapping.
- **Defaults shown** for every flag that has one, and a help line on every flag
  including `--sleep`.
- The subcommand description naming what the surface *is* — a NADA instance,
  which instances are known to work, and that microdata itself is login-gated
  and out of scope, so nobody goes looking for a flag that does not exist.
- The `--max-studies` help stating plainly that it is required and why.

If this reads well, the same epilog-with-examples treatment is worth a sweep
across the other subcommands. That is a separate change and is not part of this
adapter.

## Outputs

One output directory per instance:

```
manifest.jsonl                     # nada_study and nada_resource rows
metadata/{idno}.json               # full DDI payload
variables/{idno}.json              # variable listing
data_files/{idno}.json             # data-file listing
docs/{idno}/{filename}             # downloaded documents
text/{idno}/{resource_id}.txt      # only after --extract-text
```

`{idno}` is slugified for the filesystem; the manifest carries the true `idno`.

## Manifest records

Two kinds, one row per acquired artefact. This matches the question consumers
actually ask — "every questionnaire across all NSS rounds", "every technical
document in PLFS" — which a nested list would force every consumer to flatten.
It also matches this repo's provenance contract: `sha256`, fetch status, source
URL and filename are per-file properties, and one row per file is the shape
`validate` checks.

### `nada_study`

| field | notes |
|---|---|
| `key` | `NADA\|{host}\|{idno}` |
| `kind`, `record_type` | `nada_study` |
| `source` | host of `--base-url` |
| `base_url` | the instance |
| `idno`, `catalog_id` | DDI id; numeric id used for HTML URLs |
| `title`, `subtitle` | as published |
| `collection` | `repositoryid` |
| `authoring_entity`, `nation`, `year_start`, `year_end` | as published |
| `study_type` | `survey`, `table`, … as published |
| `metadata_path`, `metadata_sha256` | the stored DDI payload |
| `sampling_procedure_chars` | 0 when the methodology prose is absent |
| `resources_status` | `ok` or `unavailable` (fact 5) |
| `resources_found` | count listed; 0 is meaningful only when status is `ok` |
| `variables_path`, `variables_count` | null when not fetched |
| `data_files_path`, `data_files_count` | null when not fetched |
| `checked_at`, `fetched_at` | see below |
| `tool_version` | |
| `error` | null, or the failure text when `resources_status` is `unavailable` |

Volatile source counters (`total_views`, `total_downloads`) are not recorded.
They change on every re-fetch and would make every diff noisy without telling a
consumer anything about the artefact.

### `nada_resource`

| field | notes |
|---|---|
| `key` | `NADA\|{host}\|{idno}\|{resource_id}` |
| `kind`, `record_type` | `nada_resource` |
| `source`, `base_url` | as above |
| `idno`, `catalog_id` | join key back to the study row |
| `resource_id` | NADA's own id |
| `resource_type` | free string from the `<legend>` (fact 6) |
| `title` | resource-info span text |
| `filename` | from `data-filename` / `Content-Disposition` (fact 7) |
| `url` | absolute download URL |
| `fetch_status` | `downloaded`, `skipped_exists`, `listed`, `failed` |
| `path`, `sha256`, `bytes` | null unless `downloaded` or `skipped_exists` |
| `content_type` | as served, recorded even though it is wrong |
| `text_path`, `text_chars`, `text_status`, `ocr_used` | null until `--extract-text` |
| `checked_at`, `fetched_at` | see below |
| `error` | null, or the failure text when `fetch_status` is `failed` |

### `checked_at` vs `fetched_at`

`fetched_at` is set **only when bytes were actually retrieved**. On a
`skipped_exists` or `listed` row it is null, and `checked_at` carries the time
the adapter looked.

Nine existing adapters write `fetched_at` on `skipped_exists` rows, where it
means "when we looked" rather than "when we fetched"; that is open provenance
debt in `TODO.md`. This adapter does not become the tenth. Fixing the other nine
is out of scope here.

## Extraction pass

`--extract-text` does not fetch. It reads `manifest.jsonl`, walks rows whose
`fetch_status` is `downloaded` or `skipped_exists`, and for each runs
`textparse.extract_pdf_text` (pdftotext → pdfminer). With `--ocr`, a result of
zero characters falls through to `textparse.ocr_pdf_text` as the next rung —
the same chain and the same opt-in posture as the NeVA path.

Separate from acquisition on purpose: extraction is slow and OCR is slower, so
coupling them means an extraction failure costs the fetch progress and a re-run
re-hits the portal. Rows are updated in place by key, so the pass is resumable
and re-runnable.

`extract_pdf_text` returns `""` both for "this PDF contains no text" and for
"every extractor failed", so the pass never records a bare character count:

| `text_status` | meaning |
|---|---|
| `extracted` | non-empty text from the pdftotext/pdfminer chain |
| `ocr_recovered` | chain returned empty, OCR returned text |
| `empty` | chain returned empty; OCR not attempted (no `--ocr`) |
| `failed` | OCR was attempted and also returned nothing |

`ocr_used` is a boolean the pass observes directly, because it calls the OCR
rung itself. The specific extractor that succeeded within `extract_pdf_text` is
**not** recorded: that function returns a bare string, so the adapter cannot
observe which rung produced it, and a label must not assert more than was
checked.

## Registration

Both kinds are registered in `validate._pick_schema_name`, with
`commoner_probe/schemas/manifest_nada_study.schema.json` and
`manifest_nada_resource.schema.json`. Two corpus streams
(`Corpus.manifest_nada_studies`, `Corpus.manifest_nada_resources`) and their
typed dataclasses follow the existing pattern.

This is not optional. An unregistered kind makes `validate` abstain and print
"ok", which is how `census` and `niti-annual-report` shipped with vacuous
validation. The test added in PR #95 walks every emitted kind and will fail if
either registration is missed.

Schema `fetch_status` and `text_status` are closed enums over the values listed
above; `resource_type` is an open string (fact 6).

## Error handling

| condition | behaviour |
|---|---|
| study route 400 `IDNO-NOT-FOUND` | raise with the idno named; do not fall back to the numeric id |
| study payload missing `dataset` or carrying a different `idno` | raise; never accept a body that does not answer the question asked (fact 3) |
| related-materials 5xx | `resources_status: "unavailable"` with the error; the study row is still written and the run continues to the next study |
| a document download fails | `fetch_status: "failed"` with the error; the run continues to the next document |
| every study in a run failed to produce a study row | non-zero exit, per the repo convention |

A partially-degraded run — some studies `unavailable`, some documents `failed` —
exits 0 and reports the counts. Only a run in which no study was acquired at all
exits non-zero. The counts are printed, so a run that wrote 3 studies with 0
documents cannot read as a clean success.
| a run that acquires nothing because everything was already on disk | exits 0; `skipped_exists` is not failure |

## Testing

Fixtures are the responses captured during this design: the study payload, the
`related-materials` HTML for studies 1 and 2, a search page, the collections
payload, and a small PDF. One regression test per source-contract fact:

1. numeric id raises rather than silently returning nothing (fact 2)
2. a resource-list parse fed the study payload raises instead of reporting zero
   resources (fact 3)
3. legends produce the right `resource_type` grouping, including an
   unseen legend that must not be rejected (facts 4, 6)
4. a 500 on related-materials yields `resources_status: "unavailable"` and a
   study row, distinguishable from a study with zero documents (fact 5)
5. filename comes from `data-filename`/`Content-Disposition`, not the
   `application/octet-stream` content type (fact 7)
6. `skipped_exists` rows carry `checked_at` and a null `fetched_at`
7. an empty extraction without `--ocr` records `empty`, not `extracted` with
   zero chars; with `--ocr` and a still-empty result, `failed`
8. both kinds pass `commoner-probe validate`, and a corrupted row fails it —
   the check that the `census`/`niti` registrations lacked

Each test is verified to fail before its fix exists.

## Live verification before the work is called done

On three NSS studies, end to end:

1. enumerate via `--query NSS`, bounded
2. metadata written, `sampling_procedure_chars` greater than zero
3. at least one document with `resource_type` `Questionnaires` downloaded, read
   back as a valid PDF, sha256 matching the manifest
4. `--extract-text` produces non-empty text with `text_status: "extracted"`
5. `commoner-probe validate` passes on the corpus

Study 40 is checked separately as the `resources_status: "unavailable"` case.

No bulk run. Acquiring the full catalogue is a separate decision.
