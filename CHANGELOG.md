# Changelog

## Unreleased

### Added

- **`commoner-probe nada` — survey documentation from a NADA catalogue.** MoSPI's
  eSankhyiki API serves aggregated indicator rows; the survey instruments — NSS
  schedules, the written sample design, technical reports — live in a separate
  NADA (National Data Archive) instance. NADA is World Bank software, so the
  adapter is parameterised by `--base-url` rather than written twice:
  `microdata.gov.in/NADA` (187 studies) and `censusindia.gov.in/nada` (40,254)
  are both verified. Two manifest kinds, `nada_study` and `nada_resource`, both
  registered with `validate`. An opt-in `--extract-text` second pass runs the
  existing pdftotext -> pdfminer -> OCR chain over what was downloaded, making
  no network calls.

  Four source traps are encoded because each one produces a confident wrong
  answer: study routes key on the DDI `idno` and a numeric id returns HTTP 400;
  unknown API subroutes return the *study payload* with HTTP 200 instead of an
  error, so a body whose `idno` is not the one requested is refused; the
  related-materials page 5xxs for some studies, so `resources_status` separates
  "the page failed" from "no documents"; and downloads serve
  `application/octet-stream` for PDFs, so the filename comes from
  `data-filename`, never the content type.

  Bounded by construction: `--max-studies` is required for an enumeration run,
  there is no `--all`, downloads are capped per study at 25, and hitting a bound
  prints how many remain plus the exact command that continues. Microdata files
  are login-gated and deliberately out of scope.

### Fixed

- **The HTTP client ignored 429 entirely.** The retry loop backed off on 5xx and
  network errors only, so "429 Too Many Requests" was returned to the caller as
  though it were a successful response and the next request went out on the
  ordinary schedule — the one status that exists to say *slow down*, treated as
  if the portal had said yes. `Retry-After` was not read anywhere in the module,
  and the backoff was a deterministic `2 ** attempt` with no jitter, so parallel
  clients retried in lockstep. 429 is now retried, `Retry-After` is honoured up
  to a 30s cap (above which the request raises rather than blocking the process
  for as long as the portal named), and the backoff carries equal jitter. The
  zero-dependency stdlib fallback still has no retry and is documented as
  unsuitable for volume crawling.

- **A resume with no surviving checkpoint left the partials in place.** Skipping
  malformed progress lines was right; doing nothing when *none* survives was
  not. A kill during the very first checkpoint write leaves that document's
  records already flushed with no valid line describing them, so the run
  appended from there and duplicated the first document in the published
  corpus. Truncating to the last good offset and truncating to zero are the same
  rule, and the code now treats them that way.

- **`validate` was also skipping `dpe_csr_document`.** Found by generalising the
  census finding rather than waiting for it to be reported: an audit of every
  `kind` this package writes into `manifest.jsonl` turned up one more
  unregistered, with a schema that had existed all along. A nonsense DPE record
  (`status: "NONSENSE_STATUS"`) validated as "ok" before, and fails now. A test
  now walks the emitted kinds and asserts each resolves, so the next adapter
  cannot ship the same hole.
- **A resume required only one of the two partials.** If the district-rows
  partial was lost while the answers partial survived — precisely what an
  external-volume blip does, which is the failure this path exists for — the
  resume skipped every completed key and published an empty rows file over the
  real district rows. Both partials are now required.
- **A torn checkpoint line was trusted.** A process killed mid-write leaves an
  incomplete offset; parsing it either raised on every subsequent resume or
  truncated a partial through the middle of an earlier record while still
  treating its key as done. Only fully-parsing lines count now.

Post-merge Codex wave on #90 and #91. Five findings, all verified against the
tree — two of them data-loss, and one of them invalidated a claim this repo had
already reported as verified.

- **`validate` was silently skipping both new manifest kinds.** Neither
  `orgi_census_resource` nor `niti_annual_report` was registered with
  `_pick_schema_name`, whose unknown-kind path returns `None`. Proved by
  corrupting a census record (`level: "NOT_A_VALID_ENUM_VALUE"`,
  `status: "bogus"`) — `validate` still reported "1 records — ok". Every
  "validate passes" claim made for those adapters was therefore vacuous.
- **An interrupted publish could destroy the whole NeVA extraction.** If the
  process stopped after the atomic replace but before the progress file was
  removed, the next run created an empty partial, skipped every checkpointed
  key, and replaced the good `answers.jsonl` with that empty file. A resume now
  requires the partial to exist; a progress file without one means the publish
  already succeeded, so it is cleared and the corpus left alone.
- **A resume could duplicate a half-written document.** An interruption between
  a record write and its checkpoint left the record in the partial with its key
  absent, so the document was reprocessed and appended twice. Each checkpoint
  now records both partials' byte offsets, and a resume truncates back to the
  last pair — exactly idempotent.
- **A `--max-rows` pull marked the resource complete.** `load_seen()` then
  skipped it forever, so a later unrestricted run left a smoke-test subset in a
  corpus that looked whole. Row-limited pulls are now `partial`, which is
  deliberately not terminal.
- **The API key could reach the on-disk HTTP cache.** `requests-cache` persists
  the prepared request URL, and the OGD contract puts the credential in it.
  Caching is now suppressed for those calls by *detecting* `cache_disabled()`
  rather than passing `expire_after=0`, which reaches
  `requests.Session.request()` on a non-caching install and raises TypeError —
  as the first cut of this fix did.


## 0.11.0 (2026-07-30)

Two new acquisition surfaces, so a minor bump under the pre-1.0 rule — plus the
change that makes a long corpus pass survivable.

**Two source families the org covered nowhere:** ORGI / Census of India
(REQ-0045) and NITI Aayog Annual Reports (REQ-0020's residual). Both
live-verified end to end, and both shipped with the traps encoded rather than
described: the Census adapter refuses to let a rural availability flag be summed
with an urban count, and the NITI adapter reads its fiscal year from the filename
because the upload directory `/2025-02/` matches a year pattern.

**One request is deliberately NOT closed.** REQ-0045 asked for all libraries.
The rural half is fully served and the urban public-library count is not on the
API, so it stays in-progress with the remaining route named. Reporting it as
delivered would hand the requester coverage they do not have.

**NeVA extraction now checkpoints**, after three consecutive corpus passes were
lost whole — at 14 minutes, at 100 minutes, and at 2h34m on the final write. The
first run under the new code completed and added **1,325 Q/A records**, inside
the 961-1,744 band predicted from a 60-document sample before the run.

### Added

- **`commoner-probe niti-annual-report`** — NITI Aayog Annual Reports (REQ-0020
  residual). English only; the Hindi editions are detected and skipped, which is
  load-bearing because one is named `Annual Report 2024-25 Hindi_V3 LOWRES.pdf`
  and neither a parenthesis- nor `\b`-anchored match catches it. Live-verified:
  the 2024-25 report downloads at 9.35 MB with a text layer and states 26
  Consultant Grade II, 71 Consultant Grade I and **116 Young Professionals** —
  the contractualisation headcount the request was filed for.
  - robots.txt PERMITS this crawl; the probe was refusing itself because NITI's
    WAF 403s the *fetch of robots.txt* when the UA carries a URL fragment, and an
    unreadable robots.txt means disallow-all here. Fixed with a shorter honest UA
    — the check stays on, with no opt-out and no browser token.
  - Three listing traps encoded: duplicate links, the upload directory
    `/2025-02/` masquerading as a fiscal year, and four-digit second years.

- **`commoner-probe census` — ORGI / Census of India acquisition** (REQ-0045,
  theright2read). A source family the org covered nowhere. Four surfaces from
  the data.gov.in API: `pca`, `village-amenities`, `town-amenities`,
  `town-directory`, each with a provenance manifest and a typed corpus stream.
  - **The District Census Handbook PDFs are not the primary route.** The request
    named ~640 DCHBs at ~18 MB each — ~11.5 GB plus PDF table extraction — but
    the same content is served as structured rows: 2,225 PCA resources and 1,128
    Village Amenities resources. PCA 2011 carries `scheduled_castes_population_*`
    and `scheduled_tribes_population_*` by person/male/female with codes down to
    ward, which is the denominator layer under every per-capita claim in the org.
  - **The urban library count IS still behind the PDFs**, and the request asked
    for all libraries, not the rural ones. Checked rather than assumed: "Complete
    Town Directory" is a place index (8 fields, codes only); "Town Amenities" —
    all 232 field names read — runs demographics → health → education and stops
    before Statement V; catalogue titles `library`/`libraries`/`recreational`
    return only funding schemes. Recorded in `census.py` as
    `URBAN_LIBRARY_COUNT_UNAVAILABLE`, and kept as a well-searched NOT FOUND
    rather than a proof of absence.
  - **The library fields are differently typed and the adapter refuses to
    conflate them.** Rural is an availability flag per settlement (plus a
    distance-range code); urban is a count that itself merges Public Library with
    Public Reading Room. Summing them is the wrong "~75,000 libraries".
  - The `api-key` rides in the query string, so the request URL is a credential:
    manifests and error text carry a key-free form.
  - The sample-key guard compares a **digest, not a prefix** — every data.gov.in
    key begins `579b464db66e`, registered ones included, so the first cut of that
    guard refused the org's own key.

### Changed

- **NeVA extraction checkpoints, so an interrupted pass resumes instead of
  restarting.** Records accumulated in a list and were written once at the very
  end, which made a full Gujarat `--ocr` run — about 2.5 hours — all-or-nothing.
  Three consecutive runs were lost to it on 2026-07-29/30: killed at 14 minutes,
  killed at 100 minutes, and the third failing at **2h34m on the final write
  itself**, when the external volume holding the corpus blinked out and the
  single `open()` got `ENOENT`. Records now stream to `.partial` files as they
  are produced and every processed key is appended to `.neva_extract_progress`,
  so a re-run skips completed documents. Progress is marked at every exit — not
  on entry, which would silently drop a document that crashed mid-way, and not
  only on success, which would re-OCR every no-split document at ~1.2s each. The
  atomic replace still happens at the end, so a consumer never reads a
  half-written corpus, and a completed run leaves no `.partial` or progress file.
  Verified by SIGKILL on a 40-document corpus of real PDFs: killed after 3, then
  resumed to 27 records with no duplicates and no re-reads.
  A resumed run's stats cover that invocation only, so the log now names both
  the run's counts and the artefact's total rather than conflating them.
- `--refresh`'s help text no longer implies it applies to NeVA corpora. It never
  reached that path: a corpus with `questions.jsonl` always re-extracts.

## 0.10.1 (2026-07-29)

Fixes only — no new capability, no schema or CLI contract change, so a patch
rather than a minor. Two of these were **live breakage on shipped code**, and
neither was visible to the test suite:

- `commoner-probe mca-csr` had been dead at the TLS layer since 2026-07-02. MCA
  installed a certificate with a single SAN, `DNS:mcacdm.nic.in`, and `BASE_URL`
  hardcoded `www.`. Every test asserts the URL *string*; none reaches the host,
  so four weeks of total failure stayed green.
- `prs --surface mp-track --house rs` wrote nothing and exited 0 for its whole
  life (REQ-0044). The RS CSV diverges from the LS CSV on 13 of 27 column names,
  and the adapter was built and fixtured against LS only.

The rest close five Codex findings, each reproduced by execution before being
touched. Four of the seven fixes in this release are corrections to earlier
fixes in the same session — recorded plainly because the pattern is the point:
in this cycle a fix drew a finding of its own seven rounds running, in two
recurring shapes (*a label asserting more than was verified*, and *a guard that
cannot see its own edge case*).

### Fixed

- **`prs --surface mp-track --house rs` wrote nothing and exited 0.** REQ-0044
  (zero-hour). The Rajya Sabha CSV names its identity column `mp_index`; the
  adapter read `mp_election_index`, which exists only in the Lok Sabha CSV — so
  all 828 RS rows were dropped for want of a key, and the command created no
  file, no directory, no log line, and exited 0 while `--dry-run` resolved the
  CSV URL correctly. The adapter was built and tested against the LS surface
  only, and the fixture inherited its column names, so no test could see it.
  13 of the 27 columns diverge (the activity counts carry an `ag_` prefix and
  attendance is `avg_attendance`), and every one of them is read when building a
  record — an index-only fix would still have emitted null metrics. RS names are
  now normalized on read. Live: **0 records before, 828 after** (727 with
  non-zero debate counts), LS unchanged at 544, all 1,372 pass `validate`.
- **An MP Track CSV that yields no usable rows now raises, both ways.** Exiting 0
  with no output is what made the above invisible for a day. The guard covers a
  CSV whose every row lacked an identity **and** one that parsed to zero rows at
  all — an empty or header-only body on an HTTP 200, which the first cut of this
  guard skipped, reproducing the very failure it was written to stop. Neither
  case can be a resume: a resume has parsed rows whose keys are already
  terminal, so it still writes nothing and stays quiet, as it should.
- **Each preserved spool gets its own recovery path.** The recovery file was a
  fixed `manifest.recover.jsonl`, and `rename()` replaces its destination on
  POSIX — so a second failed append silently destroyed the first invocation's
  only copy of its rows while reporting the new one as preserved. The name is now
  pid-scoped with a counter: the pid keeps concurrent probes apart, the counter
  keeps successive failures in one process apart.
- **The user-facing MCA link in `EXPLAINER.html` reaches the apex host.** The
  adapter, docs and tests moved off `www.mcacdm.nic.in`, but the explainer's
  reader-facing link did not, so anyone clicking it got the same
  hostname-verification failure the code fix removed. Found by a repo-wide
  search the original fix should have run.
- **The recovery file now survives the cleanup that used to delete it.** The
  spool was spared from `probe()`'s cleanup by repointing `_spool_path` at the
  kept `.recover.jsonl` — but the cleanup unlinks whatever that attribute
  names, so every failed append deleted the rows while the raised error told
  the operator the path they were preserved at. The rename-failure branch lost
  them the same way. A flag now marks the spool as preserved and the cleanup
  removes only an unpreserved one. The prior test asserted the repoint, which
  was the bug; the property — the named file still exists after the run — is
  asserted against a full `probe()` walk.
- **A shared manifest append is now record-aligned.** The spool was copied out
  with `shutil.copyfileobj`, which writes buffer-sized chunks: a chunk can end
  mid-record, letting a concurrent writer's chunk land inside the row and
  leaving malformed JSONL. Each record is now one `write()` under `O_APPEND`,
  so every row is indivisible without needing a lock the other manifest writers
  in this package do not take. (Measured: a 4,096-byte block copy of these rows
  does end mid-record.)
- **A failed append preserves the spool by renaming it, never by reading it.**
  Reading it back to write a recovery file would reintroduce the OOM the spool
  exists to prevent, on exactly the path where the walk was large enough to
  fail — and a `MemoryError` there would have let the cleanup delete the rows
  outright. The rename is O(1); if even that fails, the spool is left in place
  and the error says where.
- **Wayback capture staging moved from memory to disk.** `probe()` held every
  row of a walk in a list until the end, so `--prefix` without `--max-records`
  — an advertised way to walk every URL under a host — grew with the whole
  capture history and could be OOM-killed before anything was written. Rows now
  spool to a scratch file beside the manifest and stream onto it in one pass;
  peak memory is one row regardless of history size. The spool is removed on
  success, on failure, and on an early `break`.
- **A part-way manifest append now names where the rows went.** If the final
  copy hits an I/O error, this invocation's rows are written to a
  `.recover.jsonl` sibling and the error says so. The manifest is deliberately
  **not** truncated back: on a shared append-only file that is the bug this
  replaced, and a loud recoverable failure beats a silent partial one.
- **MCA CSR acquisition reaches the apex host, not `www`.** The certificate MCA
  installed on 2026-07-02 carries a single SAN, `DNS:mcacdm.nic.in`, so every
  request to `www.mcacdm.nic.in` failed hostname verification before a redirect
  could be followed — `commoner-probe mca-csr` had been dead at the TLS layer
  since that date, and nothing in the repo noticed. `BASE_URL` is now the apex,
  verified live end to end: FY 2023-24 and FY 2014-15 both re-download
  byte-identical to the corpus already on disk, so nothing about the data
  changed — only the route to it. FY 2024-25 is still unpublished; the portal
  offers FY 2014-15 to FY 2023-24, and requesting the missing year trips the
  CSV-header guard instead of writing an error page to disk.
- **`extract-answers --ocr` no longer loses a Q/A record it already had.**
  The OCR read was accepted whenever it recovered the portal `subject`, without
  checking that the Q/A boundary survived — so a document whose OCR fixed the
  subject but mangled the boundary went from one record to none, while the run
  log counted it as `recovered`. Reproduced on `796a834`: `ocr_recovered=1` with
  `qa_records=0`. Acceptance now requires the OCR text to split.
- **`extract-answers --ocr` now reaches documents that yield no record at all.**
  OCR ran only when `quality == "low"` — the portal-subject check — and was
  accepted only on that same check. But the corpus-scale problem is the two-column
  boundary: on the Gujarat corpus **3,122 of 6,384 questions produce no Q/A
  record**, and in 28 of 30 sampled the boundary word `જવાબ` is present but
  glyph-corrupted (`જિાબ`, `જલાફ`, `જવયબ`) — the same cmap corruption that
  produces `low`, landing where it is fatal instead of merely degrading. Page
  count does not discriminate (173/200 non-producing are single-page, against
  162/200 of a producing control) and neither does `low` (29/30 against 26/30).
  OCR is now also attempted when the text layer yields no split, and accepted
  when the OCR read splits. Measured over 60 sampled documents, that moves
  recovery from 16/60 to 26/60. Recovered-by-boundary documents are reported
  separately as `new_records`, because new records and better text in existing
  records are worth different things. Supersedes the acceptance rule described
  under 0.10.0.
  - **`quality` and `text_source` are orthogonal, and only the second is settled
    by running OCR.** `quality: "ocr"` means an OCR re-read that *passed the
    reference check*; a document recovered only because OCR restored the
    boundary, while its subject check still fails, keeps the OCR read's own
    verdict (`low`) and records `text_source: "ocr"`. The first cut of this
    change stamped `ocr` on both, which asserted a verification that had not
    happened and would have let unverified glyph-corrupted text read as trusted.
  - The run log reports `unrecovered=` rather than `still_low=`. The gate now
    deliberately re-reads documents whose subject was never `low`, so "still
    low" no longer described what was counted.

## 0.10.0 (2026-07-29)

One new capability, one breaking change, and two waves of post-merge review
findings — four of which were live breakage, including two that made
`commoner-probe validate` reject data this repo had already written (PRs
#73-#79).

A minor bump rather than a patch: `sansad` now exits non-zero where it exited
0, and the pre-1.0 rule in `ROADMAP.md` puts any breaking change in the minor
slot.

### Changed — read this before upgrading

- **`sansad` now exits non-zero when a crawl's every bucket errored.** Anything
  gating on this command's exit code changes behaviour. A `partial` run — some
  buckets returned — still exits 0.
- `answers_neva_qa_response.quality` and `neva_district_row.quality` gain `ocr`;
  both records gain an optional `text_source` (`text_layer` | `ocr`).
- `_runs.jsonl` records gain an optional `status`. **Not required** — a corpus
  written before this release has no value there, and absent means unaudited,
  not clean.

### Added

- **`status` on every `_runs.jsonl` record** (`complete` / `partial` /
  `failed`), derived from `bucket_attempts` and never from `added`. Closes
  REQ-0043 (zero-hour): a run that reached nothing and a genuinely quiet source
  both wrote `added: 0` and exited 0, so the artefact could not tell them apart.
  `RunLog.finish()` returns the status; `RunLog.statuses` collects every run
  finished on the instance.
- **Corpus-truncation audit recipe** in `docs/SCHEMAS.md` — how to tell an
  operator-capped crawl from a failed one from a genuinely thin source, using
  `scope.max_records` and `status`. Includes a worked example against a real
  1,964-run corpus.

- **OCR fallback on acquisition-time PDF extraction** —
  `textparse.ocr_pdf_text()` rasterizes one page with poppler and reads it with
  tesseract, parameterized on `lang`/`dpi`/`psm`, with an injectable runner. It
  is the last rung of this module's existing fallback chain (`pdftotext` →
  `pdfminer` → OCR), which nine modules already use. A missing toolchain raises
  `OcrUnavailable` rather than returning empty text, because empty text reads
  as "the page had no words".
- **`extract-answers --ocr`** (NeVA corpora; `--ocr-pages` sets how many pages,
  default 1). A document whose text layer fails the reference check is re-read
  by OCR and re-classified. **The OCR result is accepted only if it recovers
  the portal subject the text layer could not** — an OCR pass that also fails
  leaves the record exactly as it was, rather than replacing it with different
  unverified text. Recovered records carry `quality: "ocr"` and
  `text_source: "ocr"`; the run log reports `recovered` and `still_low`
  separately, so the pass reports what it actually bought. Off by default: it
  shells out and costs about a second per page.
  - Why OCR here and not glyph repair: these documents are **not scans**. All
    30 sampled carry a Gujarati Unicode text layer, but the font subset's
    ToUnicode cmap is partially shifted and the corruption is
    position-dependent, so no doc-wide substitution can undo it (a
    substring-repair prototype recovered 1 of 110). The glyphs *draw*
    correctly, so a fresh render is pristine and OCR reads what a reader
    reads — 0.993 median title-line similarity against 0.942 from the text
    layer, better on 28 of 30. That margin does not transfer to real scans.

### Fixed

- **`visible_text` no longer fuses adjacent block elements.** Parser fragments
  were joined with nothing between them, so `<div>Ministry</div><div>of
  Finance</div>` read as `Ministryof Finance`; `require_text` then failed and a
  real capture was stored as `shell_only` — the silent-success path this module
  exists to close, reappearing in the fix for it. Block boundaries now emit a
  separator and inline elements do not, so `Mini<b>s</b>try` stays one word
  (the tag-stripping regex this replaced broke it into three).
- **A failed Wayback walk no longer deletes another writer's manifest rows.**
  The failure path restored `manifest.jsonl` to a byte offset taken before the
  walk, which discards anything a concurrent probe appended to the shared file
  in the meantime. Rows are now staged in memory and appended in one pass on
  success, so the "whole history or nothing" guarantee costs no other corpus
  its data. An early `break` or `max_records` still keeps its rows.

### Fixed — regressions introduced within this cycle

Codex reviewed PRs #73–#77 after they merged. Each finding was verified against
the tree before being touched; a merged PR is never proof a comment was
addressed. Two of these broke `validate` on shipped output.

- **`validate` rejected every legacy `_runs.jsonl` row.** `status` was marked
  required, so a corpus written before the field existed failed validation —
  contradicting the pre-`status` audit workflow documented in the same release.
  `status` is now optional; absent means unaudited, not clean.
- **`validate` rejected the output of a successful OCR run.**
  `neva_district_row.schema.json` allowed only `clean`/`repaired`/`low`/`unknown`,
  but an OCR-recovered document propagates `quality: "ocr"` onto its district
  rows. The enum now includes `ocr`, and the schema carries `text_source`.
- **`status` and `text_source` never reached the typed API.** `RunRecord`,
  `AnswerNevaQaResponse` and `NevaDistrictRowRecord` did not declare them, and
  `_from_dict` drops unknown keys — so every consumer of `Corpus.runs()`,
  `.answers_neva_qa()` or `.neva_district_rows()` silently lost the new
  provenance. This is the third time this exact failure has shipped; the fields
  are now declared.
- **`visible_text` split words on `del`, `ins`, `label`, `output`, `big`,
  `strike` and `acronym`** — phrasing elements missing from the inline
  allowlist, so `Min<del>is</del>try` read as `Min is try` and could again
  misfile a real page as `shell_only`. The allowlist is now complete.
- **`ocr_pdf_text` discarded the rasterizer's exit code.** A malformed PDF or
  out-of-range page exits nonzero and writes no PNG, so the function returned
  `""` instead of raising, and the caller recorded neither an error nor an
  attempt — the exact silent-success the exception exists to prevent.
- **`probe_ls_mpcode` enforced `--max-records` without recording it** in the
  run scope, so the new corpus-truncation audit would report `null` for a
  genuinely capped LS crawl and misclassify truncation as a thin source.

### Decided, not changed

- **`abhilekh-patal` will not present a browser User-Agent to clear the NAI
  WAF challenge.** Every honest `commoner-probe` identity is challenged from
  India egress, and only a mainstream browser token returns the catalogue. The
  repo's decision is to stay honest and therefore not fetch this source: a
  complete, live-tested adapter sits idle by choice. Provenance is the product
  in litigation-adjacent work, and a client identity that is not true is a poor
  foundation for it — even though nothing here is technically disallowed (the
  site publishes no robots.txt at all, so the WAF is the only barrier). No code
  change; recorded so the idle adapter is not read as a bug. `--user-agent`
  remains for an operator making that call explicitly and on the record.

### Not changed, and why

REQ-0043 also reported that `sansad --member` silently caps at 25 rows per
member. It does not. All 1,964 runs in the reporting corpus record
`scope.max_records: 25` — the caller's own `--max-records`, whose default is
`None`. The flag was already explicit in the CLI and already written to the run
log, so the REQ's first two acceptance criteria were met by the shipped code.
The audit recipe above is the durable fix for that class of misreading.

## 0.9.0 (2026-07-28)

Five new acquisition surfaces, a headless-browser fallback, and a fix wave that
closed three silent-success paths in the module written to prevent silent
success (PRs #59–#70).

### Changed — read this before upgrading

These are behaviour changes, not additions. A pinned consumer will see them.

- **`render` now exits 1 where it exited 0.** A shell capture in the default
  preview mode (`--out` omitted, which forces a dry run) previously reported
  success, because `dry_run` overwrote the computed verdict. `dry_run` is now a
  separate boolean field and the verdict survives. Anything gating on this
  command's exit code changes behaviour.
- **An HTTP 4xx/5xx page is recorded as `error`, not `downloaded`.** Playwright
  returns a response for error pages rather than raising, so a verbose 403 that
  cleared the visible-text floor used to record as a successful capture.
- **`rendered_page` manifest keys and destination filenames changed for URLs
  carrying a query string.** The slug was host+path only, so `/search?q=alpha`
  and `/search?q=beta` shared one key and one file and the second write
  silently replaced the first. A digest of the query and fragment is now folded
  into the identity. An existing rendered corpus will not line up with a re-run.
- **`manifest_rendered_page.status` no longer accepts `dry_run`** (a mode is not
  a verdict). No released version ever wrote that value into a manifest — dry
  runs do not append — so no existing corpus is invalidated.
- **`manifest_ministry_ddg.wayback_status` gains `save-pending`.** With
  `--wayback-save`, a capture already on the index is no longer labelled
  `captured`, which credited this acquisition with a snapshot it did not make.
- **`DEFAULT_SETTLE_MS` raised 2,500 → 8,000.** Measured on mospi.gov.in, the
  old value captured the empty React root in 4 of 5 cold trials and the full
  page in 1. A value that passes occasionally is worse than one that fails
  consistently, because the occasional pass is what gets recorded as proof.

### Added

- **`wayback`** — Internet Archive capture history from the CDX index
  (`kind: wayback_capture`). `--prefix` matches everything under a host or path,
  `--from-date`/`--to-date` take a year, year-month, or full 14-digit stamp,
  `--collapse-digest` reports when a page *changed* rather than when it was
  *crawled*, and `--only-ok` drops redirects and error pages. Pagination follows
  the API's opaque `resumeKey`. A URL with no captures yields nothing; an
  unreachable index raises rather than being recorded as "never archived" — the
  index answers 5xx, resets connections, and times out often enough that the
  same query returned `200 []` and then `503` three seconds apart.
- **`abhilekh-patal`** — National Archives of India **catalogue** acquisition
  (`kind: nai_catalogue_record`). Catalogue only, deliberately: search and
  metadata are open, but the scans sit behind a paid reproduction-ordering flow,
  so `status` never leaves `metadata_only`. Requires India-region egress, and
  the site's WAF challenges every `commoner-probe` User-Agent — so the honest
  default exits 1 with the cause named, `--user-agent` makes the choice
  explicit, and the identity used is stamped into every record.
- **`render`** — headless-browser fallback acquisition for JS-rendered portals a
  plain GET cannot read, with a shell-vs-content assertion. Shells are written
  to a separate directory and never recorded as success. A fallback, not a
  default.
- **`courts`** — India court data over the Indian Kanoon API wire contract
  (reimplemented, no vendored code), with eCourts reachable only across a
  subprocess boundary so its GPL-3.0 licence is never linked into this MIT
  package. **Not live-verified:** no successful call has been made.
- **`cag`** — CAG State Finance Accounts (Vol-II) acquisition.
- **PRS Report Summaries and Vital Stats** (`--surface report-summaries` /
  `vital-stats`) and **Bill Track** (`--surface bill-track`), completing the PRS
  adapter. Bill Track reads status from the span's text, because the site emits
  `class="status-pending"` on every row regardless of the real status.
- **Wayback provenance on acquisition** — `attach_snapshot()` wired into
  `MinistryDDGProbe` behind `--wayback` (CDX reads only) and `--wayback-save`.
  Save Page Now is opt-in only: a probe does not make an outward-facing write to
  a public permanent archive as a side effect of acquiring a file.
- **DDG registry** extended to 9 portals with Ports/Shipping/Waterways.

### Fixed

- **`latest_capture()` discarded the index-unavailable reason**, so a CDX 503
  during a read-only check wrote `wayback_status: "unarchived"` — asserting no
  capture exists when the check merely failed.
- **`visible_text()` counted markup-declared hidden subtrees**, which a
  preloaded panel could use to lift an empty shell over the text floor.
- **`ecourts_command()` used `str.split()`**, which breaks a quoted executable
  path containing spaces and keeps the quote characters. Now `shlex.split()`.
- **`courts --ecourts` ignored `--dry-run`** and appended to `manifest.jsonl`
  unconditionally, so a dry run mutated an existing corpus.
- **`cag_state_account` was never registered** for schema validation or corpus
  streaming, so the adapter shipped unreachable from both.
- **LS question-list pager**: `pageNo` is 1-indexed and `pageNo=0` returns HTTP
  500; the guard raises before any request (REQ-0040).
- **`dae` withdrawn from the DDG registry** hours after being added. Its listing
  parsed nine clean rows; its documents cannot be downloaded at all, because
  they sit on a host serving an incomplete certificate chain. The check that was
  cheap to run passed; the check the adapter exists for was never run.
- **Documentation**: the eCourts example in the README failed at argparse as
  written (`--ecourts-arg --court` reads as a missing value). A docs-sync test
  now parses every `commoner-probe` command in the README.

## 0.8.0 (2026-07-19)

Nine new acquisition adapters, five new extraction modules, and major new
modes on existing adapters (PRs #28–#57).

### Added

- **`questions-list`** — pre-admission daily List of Questions and Bulletin
  PDFs from sansad.in, both Houses. Section-aware parsing (LS carries starred
  and written sections with overlapping question numbers in one PDF; RS
  arrives pre-split), stated-total reconciliation onto the manifest
  (`parse_status`/`question_rows_expected`/`corrigenda_present`), corrigenda
  regions excluded from row parsing.
- **`prs`** — PRS Legislative Research MP Track CSV acquisition
  (internal-research-only licensing posture; `source: prs` segregation).
- **`mospi`** — MoSPI eSankhyiki statistics API client
  (PLFS/UDISE/AISHE/HCES/NAS/ASI). India egress or `socks5h` proxy required;
  per-pull provenance manifest rows with CSV sha256.
- **`ministry-ddg`** — ministries' own Detailed Demands for Grants series
  from their listing pages (registry: dea, mha, doe, dolr, moefcc, mopng,
  dst; three listing-page templates).
- **`legacy-dspace`** — generic legacy-DSpace (XMLUI/JSPUI) adapter
  parameterised by `--base-url`/`--handle-prefix`; first target Assam
  Legislative Assembly Digital Library (2,922 items verified live).
- **`doe-pay-allowances`** — DoE "Annual Report on Pay and Allowances"
  series, 2014-15 onward, `text_layer` recorded for scanned editions.
- **`attendance`** — Lok Sabha member-wise sitting attendance (sansad.in
  native API).
- **`myneta`** — ADR/MyNeta LS2024 candidate affidavits (assets,
  liabilities, declared criminal cases, age, education, profession).
- **`dpe-csr`** — DPE CPSE CSR document acquisition via the proven
  `/cms/wp-json` contract.
- **Extraction modules**: `extract_debates.py` (structured speeches from
  debate PDFs), `neva_text.py` (Gujarati NeVA two-column Q/A split via
  pdftotext -layout geometry, reference-calibrated glyph repair with honest
  clean/repaired/low quality, district-table rows), `vacancy.py` (typed
  sanctioned/filled/vacant annexure rows, evasive-answer marking),
  `outsourcing.py` (typed headcount/spend/vacancy/mention signals over
  committee-report text), `csr/compare.py` (MCA CSR aggregations).
- **`sansad` modes**: `tabled` (generic eLibrary title-search with
  all-bitstream sha256 provenance), `--all` (full-corpus enumeration with
  per-window resume and suspect-marking; refuses to run unscoped),
  `--mp-code` (identity-safe per-member retrieval; RS code-pinned, LS
  roster-resolved with term-window guard, name mode warns identity-UNSAFE).
- **`debates --house rs|both`** — RS `BusinessVerbatim` source contract.
- **RS committee slug aliases** — `culture` → `transport`, `environment` →
  `science` for the multi-mandate DRSCs (#33).

### Fixed

- **RS PDF downloads 406'd** on the JSON Accept header reused from the API
  headers (`debates`, then the same latent class hardened in `committees`);
  download failures are recorded, not swallowed.
- **`split_qa` RS mis-split**: the page-header date marker could win over
  the real reply marker, putting question bodies in the answer half of every
  RS PDF.
- **`debates` resume-staleness**: `load_seen` was status-blind and failed
  downloads recorded a terminal-looking "ok"; statuses are now honest
  (`metadata_only`/`downloaded`/`download_error`) with legacy back-compat.
- **`questions-list` review sweep** (#57): RS ministry values no longer
  carry the inline "be pleased to state:" suffix; `count_mismatch`
  documents are retryable with rows replaced per `source_pdf` (never
  duplicated, stale rows cleared on zero-row reparse); `--max-records`
  brakes per document (RS returns starred+unstarred in one response);
  dry-run is side-effect-free.
- **`prs`**: pre-encoded `file_path` values no longer double-encode; the
  page→CSV request pair honors the crawl delay on zero-dependency installs.
- **`ddg`**: PDF bytes readable on zero-dependency installs
  (`StdlibResponse.content`), paginated ministry listings followed, robots
  cache keyed per user agent.
- **`csr/mca` SSRF guard**: configurable `source_page`/`export_url` no
  longer bypass `is_safe_url`.
- **`ManifestQaRecord.mp_code`**: the identity-safe member code survives the
  Corpus round-trip (appended after positional fields).
- **Runs schema**: `floor_debate` kind added (every debates corpus had
  failed `validate` since the kind shipped).

### Docs

- Canonical `SCOPE.md` / `ROADMAP.md` / `ARCHITECTURE.md` (#50).

## 0.7.0 (2026-07-03)

### Added

- **`indiacode` source** — acquires India Code (indiacode.nic.in) state Acts
  plus every amendment/rule/regulation/notification/order/circular/
  ordinance/statute found on each Act's page. `is_amendment` is derived from
  description text (the site doesn't have a distinct amendment category).
  Verified live against the West Bengal Public Libraries Act, 1979.
- **`state-assembly` registry + coverage probe** — `commoner_probe.neva_portals`
  bakes in the `portal_code -> state_code/chamber/state_name` mapping for
  all 31 NeVA assembly portals + 6 Legislative Council portals;
  `state-assembly` gained `--all`/`--list-portals`, and a new
  `state-assembly-probe` subcommand does a lightweight per-portal
  data-depth check (session/question/paper/member counts) without a full
  crawl — useful because NeVA portal reachability doesn't imply data depth.

### Fixed

- **`indiacode` resume-with-downloads**: a metadata-only (`--no-download`)
  pass followed by a downloads-enabled rerun on the same corpus directory
  downloaded files to disk but left `manifest.jsonl` rows stuck at
  `status: "pending"` (no `dest`/`sha256`) — a downstream reader had no way
  to discover the file was actually there. Found by Codex's automated
  review on PR #20. `load_seen()` now tracks last-known status per key, and
  only genuinely-terminal statuses (`downloaded`, `skipped_exists`,
  `no_pdf_found`) are skipped on rerun.

## 0.6.1 (2026-07-03)

### Fixed

- **`iit_gandhinagar` department extraction**: found by running against the live page for the first time (the ported code had never been exercised against it before) — the pipe-delimited department list has no closing terminator, so the last department could bleed into trailing nav-menu text (`"Sustainable Development Find Out More Apply Now Staff Non"`), and a section sub-heading ("Interdisciplinary Centers") glued onto a neighboring department name with no separating pipe (`"Physics Interdisciplinary Centers Archaeological Sciences"`). Both are now split out and discarded.
- **`iit_hyderabad` `_SKIP_RE`**: the live site's actual results-notification wording has extra words between "of" and "results" and an inflected "Provisionally" rather than "provisional" — a naive exact-phrase match let one results-notification PDF slip through as a fake job posting.

## 0.6.0 (2026-07-03)

### Added

- **`iit_gandhinagar` parser** — IIT Gandhinagar's rolling "Professor of Practice" page (`/careers/pop`) lists all eligible departments as a single pipe-separated block with no per-department PDF or closing date; this parser explodes it into one ad per department (falling back to a hardcoded 18-department list if the live pipe-block can't be found), and routes every other IITGN careers page to `generic`.
- **`iit_hyderabad` parser** — IITH mixes permanent faculty listings with rolling project/research postings (JRF/SRF/RA/postdoc) on one careers page; this parser adds department extraction and accurate `post_type` classification (via the new `parsers.parser_utils` helpers) and skips result/cancellation notices that `generic` would otherwise misclassify as postings.
- **`parsers.parser_utils`** — shared link/date/classification helpers (`is_recruitment_link`, `classify_post_type`, `extract_department`, `iter_recruitment_links`, etc.) factored out so site-specific parsers stop re-implementing the same regex logic independently.

### Fixed

- **`iim_recruit` `apply_url`**: PDF-based ads and the no-PDF-found rolling stub both hardcoded `apply_url: None`; they now carry the ad's own PDF URL (loop case) and the careers-page URL (stub case) respectively.

## 0.5.1 (2026-06-25)

### Added

- **`TopicProfile.record_filter_fn`** — an optional record-level acquisition filter, `record_filter_fn(record) -> bool`, applied in `probe_ls`/`probe_rs` after the full Q/A record is built but before it is downloaded, enriched, appended, added to `seen`, or counted. Unlike `filter_fn` (which sees only `title`+`query` at acquisition), it sees the whole record — including fields such as `answer_text` that exist only post-construction — so callers that must match on those can filter at acquisition time instead of dropping rows afterwards. This keeps `--max-records` and the per-bucket `no_match`/`kept` counters aligned with the rows actually kept. `None` (the default) preserves existing behaviour.

- **`academic-jobs` fetch resilience** — `AcademicJobsProbe` now keeps institutions visible when their listing page misbehaves: a 4xx that still serves a substantial body is parsed (some Drupal career portals answer the listing alongside a 404); a registry `robots_override: true` retries past a blanket robots disallow for official public-recruitment sources (the `http_client` session gains a per-call `respect_robots` opt-out); and a registry `fallback_pdf_url` is parsed directly when the listing fetch or parse fails (keeps e.g. IIT Madras visible when its portal is down). Each ad record now carries a `source_method` (`official scrape` / `public-interest override` / `fallback PDF`), and `fetch_status` gains `robots_blocked`.

### Fixed

- **RS per-bucket `no_match` counter**: the normal end-of-bucket audit record in `probe_rs` hardcoded `no_match=0`, so `filter_fn` drops were under-reported in `_runs.jsonl` on every bucket that did not hit `max_records`. It now records the actual `bkt_no_match`, matching the early-return path and `probe_ls`.

## 0.5.0 (2026-06-25)

### Added

- **`commoner-probe budget`** — acquire Union Budget Statement of Budget Estimates (SBE) spreadsheets and RBI "State Finances: A Study of Budgets" documents with SHA-256 provenance. `budget` optional extra (lxml) powers RBI document discovery.
- **`commoner-probe academic-jobs`** — crawl Indian HEI career pages for faculty-recruitment advertisements via a bundled institution registry and per-institution parsers (`generic`, `iim_recruit`, `iit_kanpur`, `anna_university`, `private_university`, `iit_indore`, `iit_rolling`, `jnu`). `academia` optional extra (beautifulsoup4, pdfminer.six).
- **`commoner-probe bills`** — acquire sansad.in bill / legislation records for both houses.
- **`commoner-probe debates`** — acquire Lok Sabha per-sitting-day floor-debate transcript PDFs.
- **Manifest schemas + typed records**: `manifest_budget`, `manifest_academic_job`, `manifest_bill`, `manifest_floor_debate`, each with a `Manifest*Record` dataclass and a `Corpus.manifest_*()` reader.

### Fixed

- `http_client`: robots.txt fetching now has a 10s timeout, fixing an unbounded hang on slow or unresponsive hosts.

## 0.4.1 (2026-06-24)

### Changed

- Package metadata: author now points to the **CommonerLLP** GitHub org; added maintainers (Sreeram N R and skishchampi) and an Organization link under project URLs.

## 0.4.0 (2026-06-22)

### Added

- **`commoner-probe mca-csr`** — download MCA CDM CSR company-spend CSV exports by financial year.
- **`manifest_mca_csr` schema** and `ManifestMcaCsrRecord` / `Corpus.manifest_mca_csr()` for typed access to MCA CSR manifest records.
- **`commoner-probe mines-dmft`** — acquire Ministry of Mines / Odisha DMFT public disclosure files with source provenance.
- **`commoner-probe evidence dmft`** — build side-by-side DMFT evidence bundles from executive disclosure and Sansad oversight records.
- **`docs/ENDPOINTS.md`** — public source-family endpoint reference.
- **`narcotics_substance` built-in topic** for NDPS, trafficking, and substance-abuse oversight records.

### Changed

- **Relicensed**: AGPL-3.0-or-later → MIT, so `commoner-probe` can be the permissive shared acquisition floor that downstream repos (including the non-AGPL `sansad-semantic-crawler`) depend on without copyleft friction.
- `commoner_probe.csr.mca` now uses the verified MCA CDM live contract: `GET /csr-data` for the CSRF-bearing form and `POST /cdm/export.php` for CSV export.
- Public packaging now includes only release-facing docs; local coordination files (`notes/`, `memory/`, `.ai/`, `.beads/`, `.codex/`, `WORKING.md`, `TODO.md`) are ignored and removed from the tracked public tree.
- `scripts/check_leaks.py` now blocks private coordination paths if they are accidentally staged.

## 0.3.0 (2026-06-06)

### Breaking changes

- **Package renamed**: `sansad-crawler` → `commoner-probe`. Update your `pip install` and imports.
  - Python: `from sansad_crawler import ...` → `from commoner_probe import ...`
  - CLI: `sansad-crawl` → `commoner-probe`
  - Subcommands renamed: `crawl` → `sansad`, `crawl-committees` → `committees`, `extract-atr-linkage` → `atr-linkage`
- **New subcommand added**: `state-assembly` (NeVA state assembly portals)
- **Schema field renamed**: `crawled_at` → `probed_at` in all output records
- **Relicensed**: MIT → AGPL-3.0-or-later

### Added

- **`commoner-probe state-assembly`** — probe NeVA state assembly portals (`{portal}.neva.gov.in`). Writes `questions.jsonl`, `questions_unlisted.jsonl`, `members.jsonl`, `papers_laid.jsonl`. Tested on Gujarat assembly 15.
- **HTTP hardening** (`commoner_probe/http_client.py`): SSRF guard, robots.txt checking, per-domain rate limiting (1 req/s), exponential backoff (3 retries), optional `requests_cache` (6h TTL). Install via `pip install commoner-probe[cache]`.
- **Committee composition** (`CommitteeProbe.probe_composition()`): writes `committee_members.jsonl`.
- **`filter_fn` hook on `TopicProfile`**: callable injected by analytics layer at runtime.
- **`classifier_config` in `TopicProfile`**: propagated to `_runs.jsonl` for corpus auditability.
- **JSON schemas for new outputs**: `committee_members`, `state_assembly_question`, `state_assembly_question_unlisted`, `state_assembly_member`, `state_assembly_paper_laid`.
- **`commoner-probe init-topic`**: write a bundled example topic profile to disk (built-ins: `libraries`, `home_affairs_starred`, `affirmative_action`).
- **Single-sourced version**: `__version__` reads from `importlib.metadata` with pyproject fallback.
- **GitHub Actions**: CI (matrix 3.10–3.12, ruff, pytest) and OIDC PyPI release workflow.
- **`MANIFEST.in`**, **`CONTRIBUTING.md`**, **`CODE_OF_CONDUCT.md`** (Contributor Covenant v2.1).

### Changed

- Base class `BaseCrawler` → `BaseProbe`; `crawl_ls`/`crawl_rs` → `probe_ls`/`probe_rs`; `crawl_composition` → `probe_composition`.
- User-Agent: `commoner-probe/0.3.0`.
- HTTP cache env var: `COMMONER_CACHE_DIR` (was `SANSAD_CACHE_DIR`; old name still honoured with deprecation warning).

---

## 0.2.0 (2026-05-21)

### Added

- **`docs/SCHEMAS.md`** — complete field-level reference for every output
  stream: all four manifest record shapes (LS Q/A, RS Q/A, LS committee,
  RS committee), `_runs.jsonl`, three `answers.jsonl` kinds,
  `atr_linkage.jsonl`, and five `entities/*.jsonl` files. Includes
  controlled vocabularies and join-key documentation.

- **JSON Schemas** — twelve Draft-2020-12 schemas shipped as package data
  under `sansad_crawler/schemas/`. Exposed via
  `sansad_crawler.schemas.load(name)` and `schemas.list_all()`.

- **`sansad_crawler/records.py`** — typed dataclasses for every record kind
  (`ManifestQaRecord`, `ManifestCommitteeReportRecord`, `AnswerQaResponse`,
  `AnswerAtrResponse`, `AnswerDfgRecommendation`, `AtrLinkageRecord`,
  `RunRecord`). Each has `from_dict()` that tolerates unknown keys and
  missing optional fields.

- **`sansad_crawler/corpus.py`** — `Corpus` class with streaming iterators
  (`manifest_qa`, `manifest_committee_reports`, `answers_qa`, `answers_atr`,
  `answers_dfg`, `atr_linkages`, `runs`, `entities`), join helpers
  (`join_qa`, `join_atr_chain`), and an opt-in `to_dataframe(stream)` that
  requires `pip install sansad-crawler[pandas]`.

- **`sansad-crawl stats`** — new CLI subcommand that prints corpus health:
  record counts by house/year/ministry/committee/report_type, answers
  extraction coverage, entity resolution rate, and date ranges. Use
  `--json` for machine-readable output.

- **`sansad-crawl validate`** — new CLI subcommand that validates every
  JSONL file in a corpus against its JSON Schema. Requires
  `pip install sansad-crawler[dev]`. Prints line numbers and JSON pointers
  on failure; exits 1 on any error.

- **`[dev]` optional-dependency group** — `jsonschema>=4.20` and
  `pytest>=7`. Install with `pip install sansad-crawler[dev]`.

- **`[pandas]` optional-dependency group** — `pandas>=2.0`. Install with
  `pip install sansad-crawler[pandas]`.

- **`examples/usage.py`** — demonstration script for the `Corpus` API.

### Changed (non-breaking)

- `sansad_crawler.__init__` now re-exports `Corpus`, `QaPair`, `AtrChain`,
  all record dataclasses, and the `schemas` module.
- `run_id` and `crawled_at` in manifest schemas changed from `required` to
  optional (always present in freshly crawled corpora; may be absent in
  synthetic or backfilled data).

### Unchanged

Crawler behaviour, extractor logic, and manifest field set are unchanged.
All corpora produced by v0.1.0 load and validate cleanly under v0.2.0.

---

## 0.1.0 (2026-05-21)

Initial release. Lok Sabha + Rajya Sabha Q/A crawler, standing-committee
report crawler, regex-based Q/A and ATR extractors, ATR linkage extractor,
entity resolution, four CLI subcommands (`crawl`, `crawl-committees`,
`extract-answers`, `extract-atr-linkage`).
