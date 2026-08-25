# Changelog

## Unreleased

### Fixed

- **Five dead career URLs in the bundled academia registry (REQ-0077).**
  `iiser-mohali`, `iiser-berhampur`, `iiser-kolkata`, `iim-jammu`, and
  `iim-rohtak` pointed at paths that 404. Replacements come from
  academiaindia PR #92 (live-verified there). `iim-rohtak`'s new URL is
  correct; TLS chain failure at that host is separate and unchanged.

## 0.19.0 — 2026-08-24

**This release is a MINOR.** `manifest_academic_job` gains a field, so the
public output schema grew.

**`academic-jobs` record counts CHANGE for `iit-hyderabad`.** That parser used
to drop results notices and cancellations with a private regex. It now labels
them. A live run went from 85 records to 93. Re-run and re-filter rather than
diffing counts.

### Added

- **`document_class` on `academic_job_posting`.** A career page serves the
  advertisement and its paperwork: the results notice that closes it, the
  manual that governs it, the form you apply on, last year's question papers.
  Eight values — `advertisement` plus `results_notice`, `cancellation`,
  `corrigendum`, `exam_material`, `application_form`, `policy_document` and
  `sanctioned_posts`. The field is not in `required`, so records written before
  it still validate.

  **These are labelled, never dropped.** A skip filter fails silently: a pattern
  that wrongly matches a real advertisement removes it from the corpus and
  nothing says a record went missing. A label is visible and arguable, and the
  consumer filters at render time.

  **The class comes from the link's own text and URL, never from surrounding
  page text.** A career page groups an advertisement with its corrigendum and
  its application form in one cell. Shared context labels the advertisement
  with a sibling's class, and a consumer filtering out non-advertisements then
  hides a genuine opening.

- **`parser_utils.classify_document`**, the shared classifier. Every pattern is
  built with `textparse.term_pattern`, so it survives the dropped `ti`
  ligature. `Sanc oned Faculty Posi ons` and `No fica on of Results` both
  classify. Hand-rolling a single `(?:ti|\s)?` reads `Notifica on` and misses
  `No fica on`, which is what a font dropping both actually produces.

### Fixed

- **The generic parser admitted anything on a recruitment page.** It had no
  filter of any kind, and 38 of 79 bundled institutions use it. A faculty
  recruitment manual entered a corpus as an open post.

- **`iit_hyderabad` dropped records silently.** Its private skip regex removed
  results notices with no trace, and missed manuals, forms and exam material
  entirely. `Previous Question Papers (Permanent Non-Teaching Staff)` shipped
  as a job advertisement. Both halves are now one labelled path.

## 0.18.0 — 2026-08-23

**This release is a MINOR.** `manifest_academic_job` gains a field, so the
public output schema grew. Re-run `academic-jobs` for `iit-hyderabad`: every
row it wrote before this release carries `closing_date: null`, and about a
third of those documents state a date.

### Added

- **`closing_date_status` on `academic_job_posting`**.
  A null `closing_date` meant two opposite things: nobody read the document, or
  the document has no deadline. The field separates four states — `read`,
  `rolling`, `not_found` and `not_examined`. A consumer asking whether a post
  expired or vanished early needs the last two apart. The field is not in
  `required`, so records written before it still validate.

  `rolling` means the document states it has no closing date. It does not mean
  the document says `rolling basis`, which describes the review cadence and is
  compatible with a printed deadline. A non-null `closing_date` always reports
  `read`, whether or not its parser sets the field.

- **`textparse.term_pattern`**, moved from `dopo_catalogue`, which re-exports
  it. It builds a regex that survives the `ti` ligature that PDF extraction
  drops. The trap is not specific to BPRD: IIT Hyderabad's recruitment PDFs
  render `Applica ons` today.

- **`academia.pdf_text.read_deadline`**, returning `(raw_deadline, status)`
  for one document's text.

### Fixed

- **`iit_hyderabad` never opened the advertisement PDF.** It read link titles, which carry no dates, so it reported no deadline
  for every ad it has ever emitted.

- **Two deadline patterns broke on the dropped `ti` ligature.** They anchored
  on `application` and on `Application Last Date`. A document rendering
  `Applica ons` matched neither, and the caller read that as "no deadline
  stated" — a plausible, quiet nothing rather than an error.

- **`find_deadline` read no numeric date after the word `deadline`.** It
  reached a month-name date only. `The deadline for applications is 5:00 pm,
  22/04/2026` returned nothing.

- **`find_deadline` read no day-first ordinal date.** Every month-name pattern
  wanted `August 24, 2026`. `Application Deadline: 24th August 2026` and `Last
  Date to Apply 26th July 2026` both returned nothing. Measured on a live IIT
  Hyderabad corpus, this was the most common missed shape.

- **`find_deadline` read no date after `apply by`.** The pattern is anchored on
  that whole phrase and never on a bare `by`, which also introduces a start
  date.

- **`academic-jobs` now carries the 0.17.0 `user_agent` field on the bundled
  registry.** The field shipped with no bundled row using it, so a default CLI
  run still met the 403 the field exists to clear; only a hand-edited
  `--registry` reached the behaviour. Re-run `academic-jobs` for
  `iim-bangalore` and `iim-bodhgaya`: both wrote a `robots_blocked` row and no
  ads before this change.

  Measured 2026-08-23. Both hosts answer 403 to every `commoner-probe/...`
  User-Agent and 200 to a browser string. `iimb.ac.in` then serves a stock
  Drupal `/robots.txt` that allows the career page, so the User-Agent alone is
  enough. `iimbg.ac.in` refuses `/robots.txt` to every User-Agent with a 403
  carrying a "404 Not Found" body, so it takes `robots_override` as well — the
  disallow-all reading is the gateway's artefact, not a policy the origin
  states. Each row records its reason in the registry.

- **A registry `robots_override` now reaches the institution's documents.** It
  covered the listing-page retry only. A host that refuses `/robots.txt` to
  every User-Agent reads as disallow-all, and that verdict also reached the
  annexure PDF behind the page. The run emitted ads with `pdf_path: null` and
  `pdf_parsed: false`, with no error and no failed-download record. Measured on
  `iim-bodhgaya`: the PDF now lands at 268,271 bytes and its text reaches the
  record. The override covers the institution's own site, ignoring a leading
  `www.`. A third-party link off the page still obeys robots.

## 0.17.0 — 2026-08-23

### Added

- **A registry `user_agent` field for `academic-jobs`.** An institution entry
  may name the User-Agent its own fetches carry; entries that name none share
  one default session, exactly as before. Some career pages sit behind a WAF
  that refuses on the User-Agent string alone — every `commoner-probe/...`
  spelling gets 403 while a browser string gets 200, and Accept headers change
  nothing. The default still identifies the library to portal operators, so a
  departure from it is now one visible registry row rather than a global
  change. `budget/probe.py`'s hardcoded second User-Agent is the precedent this
  generalises.

  The string reaches the PDF and sub-page fetches too, not only the listing.
  A WAF that refuses a listing page refuses the annexure PDF behind it for the
  same reason, so the `Fetcher` is now built per institution rather than per
  run. Institutions whose ad bodies are all PDFs behind the listing would
  otherwise fetch the page at 200 and produce rows with no content.

  It also reaches the robots.txt fetch, which `_get_robot_parser` keys on
  `(domain, user_agent)`. That clears one false-positive shape and not another:
  a host that refuses `/robots.txt` to bots and serves it to a browser needs no
  `robots_override` once its User-Agent gets a real answer, while a gateway
  that returns 403 carrying a 404 body refuses every User-Agent and still does.
  Prefer the User-Agent alone, re-measure, and reach for the override only when
  the refusal survives it.

## 0.16.0 — 2026-08-20

**Re-run any `bills --download` corpus and any Rajya Sabha answer corpus fetched
before this release.** Two acquisition-time checks changed what they record.
`bills --download` no longer re-requests a URL whose outcome the manifest holds,
so a resume that previously spent hours on dead hosts now finishes — but rows
written before 0.16.0 carry no `_runs.jsonl` and cannot say how many URLs they
skipped. And the answer-number guard now reads two document shapes it used to
report as `unreadable`; a corpus keeps its old verdicts until it is re-parsed.

**A note on 0.15.3.** It shipped `bills --download`, a new acquisition surface,
as a patch. `ROADMAP.md` reserves patches for backwards-compatible fixes and
makes a new surface a minor. `v0.15.3` is published and a published tag never
moves, so this entry records the deviation rather than correcting it.

### Added

- **`commoner-probe mirror`.** Walk one host, save every page and document it
  serves, and write three artefacts that stay current as the walk runs:
  `manifest.jsonl` with a sha256 per file, `MANIFEST.txt` in the org's
  staging-manifest format, and `INDEX.md` naming every page. The crawler this
  generalises wrote the last two only after the walk finished, so a run killed
  at its deadline left 540 saved pages with neither. A resume costs no request
  for a file the manifest vouches for, and it still reaches what the first run
  never got to, because a held page is re-read from disk for its links.
  `--verify` re-hashes every file the manifest names. New manifest kind:
  `mirrored_file`.

- **`commoner-probe udise-docs`.** The 86 documents the UDISE+ portal serves
  without an account: the Data Capture Format for each year from DISE 2009-10
  to UDISE+ 2026-27, the annual report booklets, the metadata dictionaries and
  the departmental letters. Two traps handled — the endpoint answers a `.pdf`
  request with a JSON envelope holding base64 under
  `Content-Type: application/json`, and a name that has left the portal's
  Angular bundle answers 200 with a body that is not a PDF. The second has its
  own outcome, `not_pdf`, because a status code cannot detect it. New manifest
  kind: `udise_document`.

- **`commoner_probe.supervisor`.** For acquisitions that outlive one process:
  threads inside one process, SQLite task leases under a unique owner,
  streaming `.part` files, and publish only after a fenced lease check plus one
  atomic rename. Progress reads from the ledger and the finalized files —
  an active file is never completion evidence. Portal-agnostic; a `Fetcher`
  reports its own throttle counters, because a worker exit is not a throttle
  signal.

- **`commoner_probe.dopo_catalogue`.** BPRD's Data on Police Organisations: the
  host is dead, the editions are in the Internet Archive, and
  `wayback-recover` already fetches them. This carries the pinned 13-edition
  catalogue (the URL pattern misses four of them, including both a consumer
  actually used) and `term_pattern()`, which survives the `ti` ligature the
  2016 fonts drop — its tables say `Sanc oned`, so a search for the correct
  spelling returns a plausible, quiet nothing.

- **`bills --retry-failed`.** Re-requests the documents the manifest records as
  failed, and nothing else.

- **`StdlibResponse.headers`.** A case-insensitive header map, so a caller
  reading `Content-Type` behaves the same with and without the `http` extra.
  That difference is what the zero-dependency fallback exists to remove.

### Fixed

- **A `bills --download` resume never reached new work.** It re-requested every
  URL, including the ones already recorded as failed. On one live corpus 29
  URLs name a host that answers on no port, each costing about three minutes of
  retry budget, and 134 minutes of resume across two runs wrote 14 documents.
  A stored `ok` or `failed` outcome now answers without a request. **This
  reverses a fix released in 0.15.3**, which made every failure retry
  automatically; the automatic retry is what stopped a resume finishing. The
  other half of that fix stands: a catalogue with no stored outcome still
  fetches.

- **An aborted `bills` walk exited 0.** One run stopped 16 pages into a 200-row
  walk, left 5,411 of 9,929 bills unenumerated, printed a clean summary and
  returned success. `bills` now writes `_runs.jsonl` with one bucket per House,
  and a House whose walk raises makes the command exit non-zero. A resume that
  legitimately finds nothing new still exits 0.

- **The answer-number guard discarded a number run into a full date.** OCR eats
  the space, as in `QUESTION NO. 2549/04/08/2026`, and the date-tail guard
  consumed only part of the date.

- **The answer-number guard lost a header to a citation above it.** The
  citation window runs 200 characters back and stops only at a period, and
  extracted PDF text prints its boilerplate on periodless lines.

### Changed

- **Every Q/A run-log bucket carries `qno_status`** — `verified`, `mismatch`
  and `unreadable`, zeros included. A mismatch count of zero describes a clean
  slice and a slice the guard could not read at all identically; one consumer
  run returned 25 of 25 unreadable and the count said nothing.

- `runs.schema.json` accepts the `bills` kind.

## 0.15.3 — 2026-08-19

**The bills probe can fetch the documents it catalogues.**

### Added

- **`bills --download`.** A bill record carries up to eight document URLs —
  as-introduced text, the passed versions, the gazette, the synopsis, a
  committee report and errata — and nothing fetched them. A full pull returns
  9,929 bills and 10,506 URLs, so a consumer held a list of names and dates
  with no way to read what any bill says. The flag is **OFF by default**: the
  whole set is about 5 GB and 3.4 hours serial.

  Each field carries its own outcome under a new `documents` key:
  `{"introduced_file": {"url": ..., "path": ..., "status": "ok"}}`. A URL that
  404s and a URL nobody attempted are different facts, and a path alone cannot
  tell them apart. `ManifestBillRecord` carries the field too, so the typed
  iterator sees what the raw manifest holds.

  **A failed document never fails the bill.** The record keeps its name, its
  ministry, its dates and its other seven documents, and `fetch_status` stays
  `ok`. Document acquisition runs independently of catalogue dedup, so
  enabling the flag over a catalogue pulled by an earlier release fetches its
  documents, and a failed document is retried on the next run.

### Changed

- `BaseProbe.write_pdf` moves its body to `base.download_file`, a module-level
  function, so a probe that does not extend `BaseProbe` gets the same
  temp-file-then-rename guarantee. The method delegates and behaves as before.
- The bills fingerprint ignores `documents`. The digest is taken before the
  fetch and the row is written after, so a digest over `documents` never
  matched the row it produced.

Live against sansad.in: three bills, eight documents, 2.7 MB, and
`AS INTRODUCED IN LOK SABHA / Bill No. 153 of 2026` reads back from the first.

## 0.15.2 — 2026-08-18

**Take this if you fetch answers before 2000. 0.15.1 flags every one of them as
a mismatch.**

### Fixed

- **The answer-number guard read the sitting date as the question number.** A
  consumer ran 25 Rajya Sabha records from session 176 with downloads on
  0.15.1. All 25 returned `mismatch`, and the two values across the whole run
  were the day components of the two sitting dates. The pre-2000 Rajya Sabha
  answer prints its labels and values out of order, and gives the anchor no
  separator: `QUESTION NO04.09.1996`, then `ANSWERED ON`, then the subject,
  then the real number. The guard now refuses a date tail after the anchor.
  That layout is unsupported rather than misread, so the record returns
  `unreadable`.
- **A cited question read as the document's own number.** Three records
  survived the first fix and all three were citations: `to the answer to
  Unstarred Question 233 given in the Rajya Sabha on the 1st August, 1995`.
  The citation list gains that phrase. The preposition before `answer` keeps a
  document's own header out, because a header opens `ANSWER TO LOK SABHA
  UNSTARRED QUESTION NO. 2549` with nothing in front of it.

Measured over the same 25 documents: **25 mismatches before, 0 after.** 24 read
as `unreadable` and 1 as `verified`, and that one prints its own number. No
stored data changes. `document_qno_status` changes for pre-2000 records, so
re-run the guard rather than trusting a status written by 0.15.1.

## 0.15.1 — 2026-08-18

**Take this if you read bills. 0.15.0 can abandon a whole house on one bad date.**

### Fixed

- **An impossible calendar date discarded a whole house.** `2025-02-30T00:00:00Z`
  has the shape of an ISO timestamp and names a day that does not exist.
  `strptime` raised a bare `ValueError`, `_record()` catches `UnreadableDate`
  alone, so the error reached the house handler, wrote one `fetch_error`, and
  abandoned every later bill in that house. It now raises `UnreadableDate`, the
  bad field goes to null, and the bill keeps everything else. This is the defect
  0.15.0 fixed for other date shapes and missed for this one.


## 0.15.0 — 2026-08-18

**Two of these change published numbers. Re-run the bills probe, and read the
Python floor before you upgrade.**


### Changed — the minimum Python is now 3.11

**`requires-python` moves from `>=3.10` to `>=3.11`.** Install 0.15.0 on 3.10
and pip will refuse it. Every consumer repo in the org runs 3.14, so nothing
here needs a change. Python 3.10 reaches end of life in October 2026, and
SPEC 0 dropped it in 2024.

The floor bought a real deletion. `doctor` read `pyproject.toml` with a
hand-written scanner, because `tomllib` arrived in 3.11. Seven review findings
in three rounds were all the same defect: escaped quotes, quoted keys, dotted
keys, brackets inside markers, comments after punctuation, and a tool's own
tables read as project metadata. The scanner is gone and `tomllib` reads the
file. `doctor.py` loses 97 lines and gains 64.

`source_version()` uses the parser too. The regex it replaced took the first
`version = "…"` in the file, which is the project's version only while no other
table declares one above it.


**Take this one if you enumerate Lok Sabha sessions, or read any Sansad answer.**
The degrading paginator that 0.14.8 and 0.14.9 built had no production caller,
and an answer can be a different question's document.

### Fixed

- **`assent_date` was not ISO, and a two-year count of bills that became law
  returned 0 instead of 53.** RE-RUN THE BILLS PROBE: rows already on disk carry
  the raw value, and no published figure computed from `assent_date` can be
  trusted until they are re-fetched. The bills endpoint serves TWO date formats
  in one record — five fields as `YYYY-MM-DD HH:MM:SS.0` and `billAssentedDate`
  alone as `DD/MM/YYYY` — and the reader kept the first ten characters, so the
  second shape travelled through under a field name that implies ISO. Measured
  over the live catalogue on 2026-08-17: 3,576 records carry an assent date and
  all 3,576 parse as `%d/%m/%Y`, so this is the source's convention rather than
  corruption. Nothing raised, no field was empty, and the run looked clean; an
  independent `actYear` count in the same record caught it. Every date field is
  now parsed and emitted as ISO, and a value matching no known format raises
  `UnreadableDate` rather than entering the record truncated. A non-string value
  under a date field raises too: an epoch number read as an absent date reports
  "never assented" for a bill that was.

- **The re-run above now repairs the rows already on disk.** Resume compared keys
  alone, so a second run over the same directory found every bill already held
  and wrote nothing — the instruction to re-fetch would have left every wrong
  date exactly where it was. `load_seen()` now maps each key to a digest of what
  that record asserts, and a record whose content changed is written again. The
  row it replaces is then dropped from the manifest, because every reader of
  that file — `Corpus.manifest_bills()` included — streams every line: a
  corrected record appended beside the wrong one would serve both, and double
  the catalogue. An unchanged record still costs nothing. `BillsProbe.compact()`
  scans for duplicate keys rather than trusting what one run rewrote, so a
  repair killed part-way is finished by the next run instead of leaving a pair
  no later run could see.

- **Two unreadable bills in one house were one row.** Every `parse_error` was
  keyed `BILL|<house>|_parse_error`, so a key-indexed consumer collapsed
  distinct failures into one. A parse failure now carries the failing bill's own
  key, and clears when that bill later reads.

- **One unreadable date no longer discards a whole house, or the bill.** The
  crawl caught exceptions per house, so a single bad record would abort the walk
  and leave one `fetch_error` where thousands of good records belonged. The bad
  unit is one FIELD: that date is now null, `fetch_status` is `parse_error`,
  `error` names the field, and the bill's name, ministry, status and file URLs
  are all still there. `unreadable_fields` names every date that failed, because
  the case this exists for is a source-wide shape change where all six fail at
  once. Read `fetch_status` before treating a null date as a real absence.
  `--max-records` counts these rows too, so a smoke run against a changed date
  shape stops at the brake instead of walking the whole catalogue.

- **A date read the same on every Python this package supports.** The ISO-8601
  rung used `datetime.fromisoformat`, which accepts a different set of strings
  on 3.10, 3.11 and later. Two machines walking one source wrote different
  records for the same bill. The accepted shapes are now pinned by pattern, and
  the time half is range-checked rather than counted: `2025-12-20T99:99:99Z`
  passed a digit-shape test and shipped the record as `ok`. A leap second is
  still a real timestamp and still reads.

- **`--dry-run` no longer edits the manifest.** Compaction ran after every
  probe, dry runs included, and the state it rewrites — duplicate keys — is
  exactly what a repair killed part-way leaves behind. A run whose purpose is to
  say what WOULD happen could drop rows from the corpus it was describing.

- **`load_seen()` on `BillsProbe` returns `dict[str, str]`, not `set[str]`** —
  key to a content digest, the same shape `statute_dspace` and
  `drupal_publication_index` already use. Membership tests are unaffected; a
  caller doing set arithmetic on the return value is not.

- **Session enumeration now uses the degrading paginator.** `sansad --all
  --house ls` ran its own page loop, so the degrade, the floor retry, the skip
  and the climb-back reached nothing a user could invoke. `LS_PORTAL_PAGE_SIZE`
  is 1000 and older sessions refuse it, so those two releases fixed a walk the
  CLI never took. A session whose pages could not all be served now leaves the
  window suspect and names the offsets, instead of reporting a total with a
  hole in it.

- **A session that answers every page with a 5xx no longer walks forever.** An
  empty page is the only end-of-data signal and a skipped page makes no
  statement, so a portal that fails everything gave the walk no stopping
  condition: measured 2026-08-16, it passed 4,000 requests and was still going.
  Four skipped pages in a row now end the walk and mark the result incomplete.

- **No row is served twice after a degrade.** A coarser page's boundary can lie
  behind the rows already yielded, which emitted 25 of them again. Downstream
  dedupe by key hid it. Verified over 630 portal shapes: zero lost, zero
  duplicated, zero runaway.

- **The typed reader carries `answer_text_hindi`.** The LS portal serves a
  Hindi answer beside the English one on older sessions and the raw manifest
  carried it, but the schema never declared it and `ManifestQaRecord` never
  named it. Every consumer reading through `Corpus` lost it, and `validate`
  passed. Silent at all three layers.

### Added

- **A Lok Sabha session read is reconciled against the total the portal
  declares.** `totalRecordSize` IS the session total, and a note in this package
  said the opposite — that it echoed the page size — so no completeness check
  was ever built on it. Measured live 2026-08-17: session 8 of the 18th Lok
  Sabha declares 4,500 at page sizes 1, 100 and 1000, on page 1 and page 2
  alike; the 13th Lok Sabha declares 5,082 for session 8 and 8,628 for session
  9. Pass a dict as `totals` to `paginate_ls_question_list` and it reports
  `declared`, `yielded`, `repeated_page` and `complete`. A session that yields
  fewer rows than the portal claims is now left **suspect** for re-crawl, and
  the run log carries `declared_total`. An empty page had been the only
  end-of-data signal, so a portal that stopped serving mid-session produced a
  short read filed as complete — and a complete window is skipped on every
  later run. `complete` is `None` where the envelope declares no total, because
  a missing field is not a verdict.

- **A portal that ignores `pageNo` no longer walks forever.** This endpoint
  mishandles that parameter already — `pageNo=0` answers HTTP 500 — and a
  server re-serving page one satisfies any row count by padding it with copies.
  A page carrying exactly the previous page's question numbers now ends the walk
  and reports `repeated_page`.

- **`textparse.recover_with_ocr` returns a decision, not a string.**
  `extract_pdf_text(ocr=True)` gives back text a caller cannot judge: it cannot
  tell OCR that helped from OCR that read a page of running heads, and
  `chars > 0` counts a scanned page's two ligature artefacts as a successful
  read. `OcrRecovery` carries `accepted`, `reason`, and the before and after
  character counts. It refuses a result shorter than the text layer, because
  writing that back loses a partial extraction to a bad scan. It also declines
  to run at all where the existing text already passes, since rasterising costs
  orders of magnitude more.

- **`answers.looks_like_answer` is the acceptance test for a reply.** It
  requires an ANSWER heading and a letterhead, tolerates `ANSWERED ON`, the
  Devanagari layouts where conjuncts drop, the letterheads that name only a
  ministry, and the Cyrillic letters OCR substitutes for identically drawn Latin
  ones. Requiring the exact English strings called 29 complete replies unusable.

- **`commoner-probe wayback-recover` — recovery, not provenance.** `wayback`
  answers "does a capture exist"; this answers "give me the document" for a
  government file that is gone. It prefers the **largest complete** capture, not
  the newest, because the archive's own newest capture is frequently a truncated
  re-crawl: one observed file carries captures of 14,561,108 and 14,561,045 bytes
  and a newest one cut off at exactly 5 MiB. It verifies the bytes and falls back
  to the next-largest, and it asks the CDX index once per host, because per-URL
  concurrent queries get throttled and a throttled read comes back **empty rather
  than as an error** — that failure once reported 375 of 391 present documents as
  absent. `no-capture`, `unverified`, `fetch-failed` and `throttled` are four
  distinct statements and none of them may be read as absence. Manifest kind
  `wayback_recovery`.

- **`commoner-probe shrug` — the SHRUG village-level panel.** The catalogue is a
  JSON endpoint the download page's table is bound to, so scraping the rendered
  page finds no links at all. Files are presigned S3 objects signed for GET only,
  so a HEAD returns 403 on a URL that downloads fine and sizing uses a ranged
  GET. A short stream is refused rather than hashed: a sha256 of a partial table reads
  as verification of a whole one. A file already on disk is trusted only when its
  size matches AND it still hashes to the digest last recorded for it, because a
  table replaced without changing its length passed a size check while the
  manifest kept the old digest beside it. Every row carries the licence, the DOI and
  the unit, because a shrid is not a village. Manifest kind `shrug_table`.

- **`commoner-probe go-register` — a NIC Government Orders Issue Register.**
  Driven through the existing WebForms client rather than a second one. Dates are
  dd-mm-yyyy with hyphens, and a slashed or impossible date returns the blank
  search form with HTTP 200 — indistinguishable from a genuine absence — so a
  positive control runs before any empty result is reported. A refusal is never an
  absence: a 429, a 500 and a WAF challenge each raise, which matters because the
  default install's stdlib session RETURNS an error body as the response. The
  grid's document links are JavaScript calls, not hrefs. Written against Andhra
  Pradesh; **generality is not verified.**

- **`commoner_probe.admin_units` — a district index and a resolver that never
  guesses.** A district label is not a key. The UDISE crosswalk flags 192 of its
  782 rows `unmatched` and **177 of those still carry a populated district id**,
  59 of them the same id — measured against the file on 2026-08-17. So the
  resolver refuses a mapping its own source flagged weak, refuses a row whose own
  district name contradicts the index, and answers `state_mismatch` rather than
  "absent" when only the state filter excluded a name. Built live against the real
  extracts: 639 districts, and all eleven Andhra spelling variants resolve.

- **The HTTP layer names an AWS WAF challenge instead of reading it as an empty
  result.** `challenge_reason` and `refuse_challenge` detect the
  `x-amzn-waf-action` header at any status, and an empty-bodied 2xx where the
  caller expected content. This is worse than a block because it IS a 2xx:
  `raise_for_status()` passes and `json.loads(b"")` then throws a confusing decode
  error, so the natural next move is to doubt the URL. Measured against a Harvard
  Dataverse API on 2026-08-14, where the DOI had been correct the whole time. A
  204 and a HEAD are legitimately empty, so the empty-body signature is the
  caller's statement rather than a guess.

- **`textparse.word_to_pdf` and `textparse.needs_ocr`.** Some endpoints serve a
  Word document from the same URL and parameters as their PDFs, and only the magic
  bytes tell them apart. `textutil` is not a substitute for the conversion: both
  its txt and html modes FLATTEN the document's tables into one run, so a grid of
  figures arrives as prose and every row is lost, while LibreOffice preserves it —
  that one change recovered 337 rows from a single order. A conversion that
  reports success and writes nothing raises rather than reading as an empty
  document. `needs_ocr` is the routing decision the OCR rung never had: a scanned
  page yields two ligature artefacts, and `chars > 0` counted them as a read.

- **`commoner-probe doctor` — the three versions that are supposed to agree.**
  `importlib.metadata` serves the version recorded at INSTALL time, not the one in
  the tree in front of you, so a stale editable install silently invalidates every
  version gate built on it. This repo's own version test failed for an unknown
  period reading 0.14.7 against a source of 0.14.6, and one consumer ran 0.13.0
  against a declared pin of 0.14.3. `doctor` prints the source version, the
  installed metadata and any declared pin, and exits 1 when two KNOWN numbers
  disagree. An unknown number is reported as unknown and never as agreement.

- **`commoner_probe.checkpoint` and `commoner_probe.reachability`.** A long crawl
  flushes atomically on an interval and on SIGTERM or SIGINT, and heals a torn
  checkpoint even on a run that finishes nothing. `reachability` reports
  from-here against from-India with a positive control on each side, so nobody
  provisions an Indian host for a host that already serves.

- **`commoner_probe.invariants` — the four acquisition invariants as
  callables.** Each is drawn from a defect that produced a plausible,
  complete-looking result rather than an error, and each was previously advice
  in a docstring, which is executed only by whoever remembers it.

  - `unmapped` / `require_full_coverage` — enumerate what the source offers.
    One register published ten drillable reason columns to a map that named six,
    and the four skipped ones were the revealing ones. Checked in both
    directions, because a map naming a column the source dropped reads a
    different column and returns correct-looking numbers.
  - `saturation` — verify with a different query shape, never the same one
    again. Re-running an identical query confirms any systematic miss.
  - `collect` — one bad unit degrades a result to PARTIAL and never empties it.
    A single non-JSON tile made a run record "0 rows" for two layers, which in a
    results table is indistinguishable from "this layer is empty".
    `KeyboardInterrupt` is deliberately not caught: a cancelled crawl is not a
    partial source.
  - `assert_finds` — a positive control precedes any claim of absence. A query
    that raises is a failed control too.

  `geoserver.verify()` now builds its saturation report through the shared
  function, so the GIS case and the general case cannot drift apart. Its
  returned keys are unchanged.

### Fixed

- **The manifest-kind guard could not see a kind held in a constant.** It matched
  only a literal `"kind": "..."` in the source, so `wayback_recovery` reached the
  tree unregistered while the guard passed. A check that sees one spelling of the
  thing it guards is not a guard. It now reads `MANIFEST_KIND` too, and both new
  kinds validate against records the modules actually emitted.

- **Every answer is checked against the question number it prints.** sansad.in
  serves the wrong document under the right URL: fetched live again on
  2026-08-16, `AU2549` returns 637,244 bytes printing QUESTION NO. 2594 and
  `AU2594` returns 424,629 bytes printing 2549. Re-fetching cannot repair a
  source-side swap, and downstream the record is flawless — the key parses, the
  subject is right, the text is a real reply. So records now carry
  `document_qno` and `document_qno_status` (`verified` / `mismatch` /
  `unreadable`), stamped where the requested number and the document are both
  in hand.

  The check reads **inline answer text too**, not only attached PDFs: two
  proven cases carry no PDF at all. A document that states no number of its own
  is `unreadable`, never a mismatch — 4.7% of LS answer PDFs print none. A
  flagged document is kept: it belongs to some question, and a suppressed
  download is harder to notice than a flagged one. Per-window counts go to the
  run log.

  It costs a median **18.9 ms** per answer, measured over 50 live LS answer
  PDFs, so it runs unconditionally rather than behind a flag.

- `extract_pdf_text(..., last_page=N)` bounds the pdftotext rung to the first N
  pages. It bounds the work on a long annexure rather than saving time on a
  typical document — page one and the whole document both measured 19 ms.

## 0.14.9 (2026-08-16)

**Take this one before any further large enumeration.** Page-size degradation
was one-way, so a walk that hit one transient error finished the session at a
smaller page and paid for it in every later request.

### Fixed

- **The walk climbs back after degrading.** A 5xx halves the page size, which
  0.14.8 added, but nothing ever restored it. One transient failure at the
  working size finished the session at half that size, and a few of them
  reached the floor. A walk at `page_size=25` makes forty times the requests of
  one at 1000, and every extra request is another chance to fail: across one
  LS 13 run the loss rose from **1.0% to 43.0%** as the size ratcheted down.

  After `recover_after` consecutive good pages (4 by default) the walk doubles
  the page size again. It never goes past the caller's size, and it climbs only
  where the offset lands on the bigger page's boundary, so no row is stepped
  over.

  A size that fails twice is abandoned for the session. That bounds the cost of
  probing at one wasted request per attempt, and stops the walk asking an old
  session for 1000 rows over and over.

## 0.14.8 (2026-08-15)

**Take this one if you enumerated Lok Sabha 13 or any pre-2004 session with
0.14.4 through 0.14.6.** Those runs recorded whole sessions as unavailable that
the portal will serve, and truncated others at the first failed page.

### Fixed

- **An oversized page request deleted six Lok Sabha 13 sessions.**
  `LS_PORTAL_PAGE_SIZE` is 1000, and the portal answers HTTP 500 rather than a
  smaller page when a session cannot serve one that large. The size it will
  answer shrinks with session age: measured 2026-08-15, LS 13 session 9 serves
  page 1 at `page_size=500` and fails at 1000, session 12 serves at 100 and
  fails at 500, and every session from 2 to 14 serves at `page_size=1`.

  A term-wide enumeration of LS 13 therefore logged `failed: HTTP 500` for
  windows 9 through 14 and finished `DONE added=34849`. The run looked complete
  while the store held 7 of the term's 13 sessions — February 2002 to February
  2004 missing, and the data was there for the asking the whole time.

  `paginate_ls_question_list` now halves the page size on a 5xx and retries the
  same offset, down to `min_page_size` (default 25). Halving preserves offset
  alignment exactly: an offset reached as page n of size s is page 2n-1 of size
  s/2. Sizes snap to multiples of the floor via `_halve_to_multiple`, because
  plain integer halving produces 62 and 31 and leaves offsets that no coarser
  page boundary lands on.

- **A failed page was treated as the end of the session.** LS 13 session 8
  returns 1,000 rows on page 1, HTTP 500 on page 2, and 1,000 rows again on
  page 3. The old loop stopped at the first failure and stored exactly 1,000
  rows for a session holding roughly 5,080 — and a truncated session is
  indistinguishable from a short one. Only an EMPTY page now ends the walk. A
  page that fails at the floor is skipped, its offset range appended to the
  optional `skipped` list so the caller can report the hole instead of
  publishing a total that quietly omits it.

- **Transient failures were recorded as permanent holes.** Twenty-five offsets
  in LS 13 session 9 were skipped as unavailable and every one returned its rows
  when asked again a minute later. The floor now retries `floor_retries` times
  (default 4) with exponential backoff before recording a gap.

- **`_ls_portal_date` mangled older dates.** Lok Sabha 13 pads date components
  instead of zero-filling them, serving `07. 3.2001` where 2026 serves
  `20.07.2026`. Interior spaces are now removed before parsing; previously every
  such date fell through to the truncating branch and was stored as the literal
  string `07. 3.200`, which sorts as nonsense and silently widened one session's
  apparent span to three years.

Nine regression tests in `tests/test_sansad_pagination_degrade.py`, including one
asserting the offset never moves backward: an intermediate version of this fix
restored the page size by flooring the page number, which put the offset back
inside the page that had just failed and span on one offset until it was killed.
## 0.14.7 (2026-08-14)

### Fixed

- **A space-padded month broke the record key.** Older portal rows sometimes pad
  a single-digit month with a space rather than a zero — `25. 4.2001` for 25
  April 2001. `%d.%m.%Y` rejects that, and the passthrough then wrote the raw
  string into `date`, which `stable_key` embeds: the key became
  `LS|U|5669|25. 4.2001`. Eight rows of Lok Sabha 13 carry it, out of 34,849 —
  rare enough to go unnoticed, permanent once written.

  Interior spaces are now stripped before parsing, because the fault is padding
  rather than a different format. Anything still unparseable keeps the existing
  passthrough: a date that cannot be read must stay visibly wrong rather than be
  guessed into something a consumer will trust. There is a test asserting that
  no unreadable value is ever returned in ISO shape.

**Take this one if you need point data out of any Indian state's GeoServer.**
It adds the module, and it documents the trap that makes the naive version of
that job silently return a fraction of the layer.

### Added

- **`commoner_probe.spa_jwt_api` — bulk microdata from a portal that
  gates it behind a mobile OTP.** The Ministry of Education's UDISE+ Data
  Sharing Portal serves six CSV datasets for each academic year since 2018-19.
  The module records the whole route, because each step has a trap that returns
  a plausible wrong answer instead of an error.

  **The expensive one is the all-India sentinel: it is 99, not 0.**
  `stateId=0&districtId=0` answers HTTP 200 with `Content-Type: application/zip`
  and a body that is actually `%PDF-1.7` — the schema document, not data. Every
  `reportId` except 1 then 404s, which reads convincingly as "only one report
  exists". Always check the payload begins `PK`. Never trust the header.

  Also recorded: the portal times out from a non-Indian connection and answers
  from ap-south-1; the API base sits in a 2.25 MB Angular bundle as
  `Y3_apiBaseUrl`, assembled with template literals, so grepping for URL string
  literals finds almost nothing; the auth flow is captcha, send-OTP, verify-OTP,
  and the captcha needs a human, so this module ships no solver.

- **`commoner_probe.geoserver` — point extraction from a WMS-only GeoServer.**
  State spatial-data infrastructures are GeoServer deployments and many publish
  WMS while disabling WFS, so there is no vector download. This sweeps a bounding
  box with `GetFeatureInfo`, subdividing wherever the response hits the feature
  cap.

  **The trap it exists to defeat.** `GetFeatureInfo` does not query the data — it
  hit-tests the *rendered symbol* under the pixel, using the server's default
  style. Where that style draws a small marker, a query only returns a feature
  when it lands inside those few pixels. Measured against Andhra Pradesh's APSAC
  school layer: the default style yielded **19,090** schools; the same sweep with
  a 200-pixel symbol via `SLD_BODY` yielded **58,301** — 3.05x more. The first
  number carried no error, no warning and no missing-data indicator. Use
  `big_symbol_sld()`, and treat any extraction that did not override the style as
  a lower bound of unknown tightness.

  - `wfs_status()` — **call this first.** If WFS is enabled, use it and ignore
    this module: it returns real geometry, including the lines and polygons WMS
    extraction cannot honestly recover. APSAC answers every version with
    `org.geoserver.platform.ServiceException: Service WFS is disabled`.
  - `big_symbol_sld()` **refuses non-point geometry.** A road line cannot be
    recovered by hit-testing symbols; returning a style for it would invite a
    caller to sweep a road layer and believe the result.
  - `Tile.offset()` — the verification pass. Re-running an identical grid asks
    the same questions and "confirms" anything; an offset grid interrogates the
    ground between the original query points. On the APSAC school layer this
    returned 58,301 against 58,301 with zero new features, which is what turns a
    floor into a count.
  - **One failing tile no longer zeroes a layer.** A single bad tile used to
    raise out of the sweep, and the run recorded "0 rows" — indistinguishable in
    a results table from "this layer is empty", which is the expensive kind of
    wrong. Failed tiles are collected and the sweep reports itself PARTIAL.
  - Deduplication across workspaces is deliberately NOT done: state portals
    republish one dataset under several workspaces, and agreement between two
    independently-swept copies is the best completeness check available when no
    authoritative count exists. APSAC's anganwadi layer returns 53,682 under both
    `gatishakti:` and `Andhra-`.

## 0.14.6 (2026-08-14)

**Take this one if you enumerated any Lok Sabha session before August 2015 with
0.14.4 or 0.14.5.** Those runs silently dropped the question and answer text.

### Fixed

- **The portal record mapper discarded inline text.** `_ls_portal_record` emitted
  eighteen fields and none of them was the text, so `--loksabha` enumeration of
  older sessions kept the metadata and threw away the answer. This was invisible
  while the only caller was the per-member path on the current term, where both
  fields are null on every row; extending the mapper to historical sessions in
  0.14.4 made it a data-loss bug.

  Verified against Lok Sabha 15 session 10 (May 2012): 1,000 records, 100%
  `question_text` and 100% `answer_text` after the fix, 0% before. Records now
  carry `question_text`, `answer_text` and `answer_text_hindi`, matching
  `_rs_record`, which has always carried its equivalents. Modern rows keep the
  keys with null values, so a consumer can tell "this session served no text"
  from "we dropped it".

  **The Lok Sabha served inline text until it stopped.** Sampled: LS 14 (2007),
  LS 15 (2010, 2012, 2014) and LS 16 up to session 4 (8 May 2015) all return
  100% on both fields; LS 16 session 5 (13 Aug 2015) onward returns null, as do
  LS 17 and 18 entire. Three things change together at that boundary — the file
  store moves from `Annexture_New` to `lsapps`, Hindi PDFs appear for the first
  time, and the inline text stops.

- **A page that fails mid-session no longer discards the session or claims it
  finished.** Some older sessions answer page 2 with HTTP 500 instead of an
  empty page — LS 15 session 10 does; LS 16 and LS 18 paginate normally. The
  HTTP client has already retried 5xx by then, so it is structural.

  A 500 is **not** treated as end-of-session: it is indistinguishable from a page
  that exists and could not be served, so calling it "done" would truncate a
  session and report success. Rows already fetched are kept, the window is left
  `suspect` so the next run re-crawls it, and the log names how many records
  survived and that the session may be incomplete. A failure on page 1 still
  raises, because there is no partial result to preserve.

## 0.14.5 (2026-08-14)

**Take this one if you enumerate any past term.** You can now ask which
sessions exist, instead of supplying numbers you had no way to obtain.

A patch, not a minor: this adds surface and breaks no caller. The precedent is
0.10.0/0.13.0/0.14.0, each of which took the minor slot for a *contract* change.

### Added

- **`commoner-probe sansad sessions --house ls|rs [--loksabha N] [--json]`** —
  a read-only lookup over the two session calendars. Both were already
  implemented (`ls_portal_sessions`, and the RS equivalent) and neither was
  reachable from the CLI, so 0.14.4's session-scoped enumeration was usable
  only on a term whose numbers you already knew.

  The Houses number sessions differently and the command normalises them.
  `--loksabha` selects a Lok Sabha term and is refused for the Rajya Sabha,
  which is permanent and numbers continuously since 1952. `--json` emits one
  schema for both.

  Live as of release: the Lok Sabha catalogue knows terms **13 to 18**
  (1999-2026); the Rajya Sabha catalogue holds **271 sessions**, session 1
  (1952) through 271 (2026), every one carrying sitting dates.

  A Lok Sabha budget session is usually **split** into two periods, so that
  committees can examine the Demands for Grants during the recess. `periods` is
  therefore a list, and a consumer reading `[0]` loses half the session. This is
  routine — the 17th Lok Sabha split its budget session in 2020, 2021, 2022 and
  2023 alike.

  **A listed session is not a promise that questions exist for it.** RS session
  264 is in the catalogue with sitting dates and returns zero questions from
  rsdoc. The command answers when the House sat, never what can be fetched, and
  the output says so.

### Changed

- `docs/CLI.md` now records that the **eLibrary DSpace enumeration lags**.
  Measured over an identical span: the portal path returned 34,724 Lok Sabha
  records against DSpace's 29,970, with **zero** records DSpace held and the
  portal did not. The gap is almost entirely one whole recent session that
  eLibrary had not yet indexed; its newest record anywhere was four months old.
  DSpace is not deprecated — it needs no session calendar, it is the only source
  for `sansad tabled`, and it alone carries `uuid`/`handle`/`uri` — but it must
  not be used as a completeness check on a recent session.

## 0.14.4 (2026-08-14)

**Take this one if you enumerate Lok Sabha questions.** A second route to the
same records, ~5x cheaper per session, and it carries the answer PDF URL that
the existing route omits entirely.

### Added

- **`sansad --all --house ls --loksabha N --sessions RANGE`** enumerates the
  Lok Sabha through its own portal question list, one session at a time,
  instead of eLibrary DSpace calendar-month windows. Measured on lkNo 18
  session 8, 4,500 records both ways: **5 requests against ~18 per calendar
  month**, and `pdf_url` present on 100% of records against 0% from DSpace.
  That second number is the larger saving — a DSpace record carries no PDF
  link at all, so every answer download first resolves item → bundle →
  bitstream, and at the one-request-per-second floor that cost is paid per
  question across the whole corpus.

  Session numbers here are **LS-relative** (`8` for Monsoon 2026, never the
  continuous `271`), so window ids carry the term: `ls:18:8`. `--loksabha`
  requires `--house ls` and an explicit `--sessions`; combining it with
  `--house both` is refused, because `--sessions` would then mean two
  different numbering spaces at once.

  The DSpace path is unchanged and stays the right tool when the session
  calendar is unknown — it is date-windowed, and it alone carries
  `uuid`/`handle`/`uri`.

  Neither path carries text: `questionText` and `answerText` are null on every
  row of the portal list, verified across a full 1,000-row session. Answer text
  remains a PDF-extraction problem.

  The session-drift guard already used by `--mp-code` applies here too and
  matters more: the endpoint silently ignores an unknown `sessionNumber` and
  answers from the latest session instead, so a row whose `sessionNo` does not
  match the request is skipped and counted, with a warning naming the window.

- **`pageSize` is honoured up to at least 1000** on the portal question list.
  There is no per-session record cap; pages 1-5 of lkNo 18 session 8 return
  1000/1000/1000/1000/500 then empty. Member-less enumeration defaults to 1000;
  the per-member path keeps 100, where paging is not the cost.

### Changed

- `_windows.jsonl` gains an optional `loksabha` field, and `ses_no` now also
  carries LS-relative session numbers for the new path. `docs/SCHEMAS.md`
  updated.

## 0.14.3 (2026-08-04)

**Take this one if you run `cag`, `ministry-ddg` or `doe-pay-allowances`.**
0.14.1 and 0.14.2 can write a record that `commoner-probe validate` rejects on
the path where a PDF cannot be read at all.

### Fixed

- **`text_layer: null` now validates.** 0.14.1 made the four text-layer adapters
  record `null` when nothing could read a PDF — but `manifest_cag_state_account`,
  `manifest_doe_pay_allowances` and `manifest_ministry_ddg` declared
  `"type": "boolean"`, so the fix's own success path emitted records that
  `commoner-probe validate` rejects. The three schemas now carry
  `["boolean", "null"]`, as `manifest_niti_annual_report` already did.
- **`ManifestRenderedPageRecord.dry_run` moved to the end of the field list.**
  0.14.2 inserted it in the middle, which shifts the position of every field
  after it for anyone constructing the record positionally.

### Documentation

- **The 0.14.0 entry named the wrong affected range.** `questions-list` first
  shipped in **0.8.0**, not 0.10.0, so bled corpora go back that far. The wrong
  range came from `git tag --contains | head -1`, which sorts lexically —
  "v0.10.0" precedes "v0.8.0" as a string.

## 0.14.2 (2026-08-04)

**A patch for readers, not crawlers.** Nothing about acquisition changes. If you
do not import `Corpus` or the record dataclasses, 0.14.2 is identical to 0.14.1
for you.

### Fixed

- **`ManifestRenderedPageRecord` was missing `dry_run`.** The schema calls that
  field load-bearing — "a mode must never overwrite a verdict" — but the
  dataclass that reads `rendered_page` records back did not carry it, so a
  consumer using `Corpus` could not tell a preview run from a real capture.

### Added

- A guard that walks every `kind` -> record pairing in `corpus.py` and fails
  when a dataclass lacks a field its schema declares. The two representations
  are maintained by hand in different files and nothing connected them; the gap
  above was the only one in 38 pairs, found by measuring rather than assuming.

## 0.14.1 (2026-08-04)

**A patch: one regression fix and one internal dedupe, no contract change.**
Nothing a working caller does changes behaviour, which is what keeps this out
of the minor slot.

### Fixed

- **A missing PDF text backend no longer kills a crawl mid-download.** 0.14.0
  made `extract_pdf_text` raise where it had returned `""`, and the `cag`,
  `ministry-ddg` and `doe-pay-allowances` adapters called it unguarded inside
  their download path — so on a machine with neither poppler nor pdfminer the
  exception escaped a download that had already written a good file, losing the
  record and ending the run. The four adapters that record a text layer now
  share one helper, which treats extraction as advisory and acquisition as not:
  the file is kept, its hash recorded, and `text_layer` is `None` — unknown,
  which is not the same claim as `False`.

### Changed

- One PDF text chain instead of two. `academia`'s private
  `pdftotext -> pdfminer` fallback now delegates to `textparse`, which the rest
  of the package already used; the two had drifted, and only the shared one had
  the OCR rung. `extract_text` and `extract_text_flow` are unchanged for
  callers. `has_pdftotext` and `_PDFTOTEXT`, which nothing outside that module
  used, are gone. The pdftotext timeout for academia moves 120s -> the shared
  60s, so a slow PDF falls through to pdfminer rather than failing.

## 0.14.0 (2026-08-04)

**A minor on the breaking changes alone — no new acquisition surface.** The
subcommand count is unchanged at 36, no new adapter module ships, and the only
schema touched was edited rather than added. Everything here is a fix or a
contract change to a surface that already existed. The precedent is 0.10.0 and
0.13.0, each of which took the minor slot for one contract change.

The headline is a data defect: every question-list row acquired by **0.8.0
through 0.13.0** carries the *next* question's subject heading at the end of its
text — 97.9% of adjacent pairs on a live seven-day corpus. The range is the
adapter's whole released life: `questions-list` first shipped in **0.8.0**, and
the body has run to the next question's *head* since the file was written.
**Corpora acquired with any of those versions need a re-parse; see Changed.**

*(This entry first said 0.10.0. That came from `git tag --contains | head -1`,
which sorts lexically — "v0.10.0" precedes "v0.8.0" as a string. Sorted by date,
the first tag carrying the adapter is v0.8.0. Corrected in 0.14.3.)*

### Breaking

- **`extract_pdf_text` raises when no PDF text backend is available.** It
  returned `""` for both "poppler and pdfminer are missing" and "this PDF has
  no words", so a crawl over a thousand PDFs wrote a thousand empty text files
  and reported success. A missing toolchain now raises `PdfTextUnavailable`; a
  working backend that finds nothing still returns `""`. The OCR rung the
  package documents is wired in behind `ocr=True` and, when asked for and
  unavailable, raises rather than returning nothing.
- **A response body has a ceiling.** `MAX_RESPONSE_BYTES` (512 MB) applies at
  15 call sites through `iter_capped` / `read_capped_response` / `get_capped`,
  and `StdlibSession` bounds its own read since it cannot stream. A body past
  the ceiling raises `ResponseTooLarge` instead of being truncated into a short
  file that looks complete. The downloads that feed it now request
  `stream=True`, without which requests buffers the whole body before the cap
  sees a chunk, and the ceiling resolves at call time so raising or lowering
  the constant actually changes it.
- **`RetrySession.head()` no longer follows redirects by default.** It was
  routed through `request()`, whose default is the opposite of
  `requests.Session.head`, so a HEAD size check fetched the redirect target.

### Fixed

- **`questions-list` row text no longer carries the next question's subject
  heading.** The body ran to the next question's *head*; the subject printed
  between the two rows was swallowed as the previous row's last limb. Measured
  on the 27 Jul – 4 Aug 2026 lists, both Houses, 2,975 rows: **2,913 of 2,974
  adjacent pairs (97.9%) bled before the fix, 0 after** — same PDFs, both
  parsers. RS 2026-07-30 Q.1308 ("Public libraries") ended with Q.1309's subject
  and now ends at its own last limb. The body stops at the next row's subject
  line, which is the same line the next row's `subject` is read from: a line
  cannot be both.
- **A count match alone no longer reports `reconciled`.** Every affected
  document reconciled at 250 of 250 for months, because the check compared row
  counts and the defect was in every row's tail. `parse_status` gains
  `boundary_bleed`, records carry `question_rows_bleeding`, and a bled document
  stays retryable instead of reading as terminal.

- **The zero-dependency session re-checks redirect targets and asks robots
  under the User-Agent it will send.** `urllib` follows redirects inside
  `urlopen`, so the SSRF guard saw only the first URL; a public host answering
  302 to `http://169.254.169.254/` reached the metadata service. A per-request
  UA override also made the probe ask permission as one identity and fetch as
  another.
- **Two writers sharing an output directory no longer share a temp path.**
  Both wrote `<name>.tmp`, so one truncated the other's partial download and
  the loser's cleanup deleted work already renamed away.
- **`validate` survives a non-string `kind`** (a list or object raised
  `TypeError: unhashable type` and aborted the run) **and reports the file's
  real size after truncation** — the `read` count stopped at the error limit,
  turning a 40-record file into "0 of 3".
- **A domain error is a message and an exit code, not a traceback.** `main()`
  maps known failures to exit 1 and Ctrl-C to 130, printing the explanation the
  exception carries instead of a stack and the operator's local paths.
  `--traceback` opts back in.
- **`make verify-release` no longer cries wolf during propagation.** The index
  read was retried six times and the install once, but pip reads the simple
  index, which lags the JSON route the retry loop watched. The install now gets
  the same patience; a genuine install failure still fails at once.
- `academia`'s PDF downloads keep their existing filenames: the shared
  sanitiser's `collapse` no longer implies trimming, so `_report_.pdf` stays
  `_report_.pdf` rather than being renamed and re-downloaded.
- `docs/CLI.md` and `docs/GOV_SITE_PLATFORMS.md` are packaged, so the README's
  links do not dangle in the sdist, and the README's subcommand count (36) is
  now asserted against the parser.
- The packaging guard scans every tracked file in the package, not only `*.py`
  — the shipped JSON schemas were outside the scan it exists to perform.

### Changed

- `extractor` on `question_list_row` records is now
  `commoner_probe.questions_list.parse_question_rows.v3`. **Corpora acquired
  with v2 need a re-parse**: delete `manifest.jsonl` and `questions_list.jsonl`
  (or pass `--reset`) and re-run the same `questions-list` command. The PDFs on
  disk are reused — the re-parse re-reads them and downloads nothing.

## 0.13.0 (2026-08-01)

**A minor, on the breaking changes alone — no new acquisition surface.** The
precedent is 0.10.0, which took the minor slot for exactly one contract change:
`sansad` exiting non-zero where it exited 0. `validate` now does the same thing,
and three smaller changes are also visible to a working caller. Everything here
came out of a whole-package review; each defect below was demonstrated failing
before it was fixed.

### Breaking

- **`validate` fails closed.** A record whose `kind` had no schema was skipped,
  `file_ok` stayed true, and the command exited 0 having validated nothing. On a
  one-record corpus with an unregistered kind: 0.12.1 printed `1 records — ok`
  and exited 0; 0.13.0 names the kind and the line and exits 1. If you run
  `commoner-probe validate` in CI over a corpus containing an unregistered kind,
  it will now go red — that is the fix working.
- **The `validate` summary line changed** from `N records — ok` to
  `N of M records validated — ok`. The old count counted non-blank *lines*, so a
  file whose records were every one of them skipped printed the same number as a
  file that was fully checked. Anything parsing that line needs updating.
- **`census.KEY_HINT` is removed.** See the credential change below.
- **The zero-dependency session now refuses and throttles.** With no extras
  installed it applies the SSRF guard, the robots check and the per-domain rate
  limit, so a URL that previously fetched may now raise `ValueError` or
  `PermissionError`, and requests to one host are spaced.

### Security

- **The published package no longer names a private path or hunts credentials
  outside its own tree.** `resolve_api_key` walked *every* parent of `__file__`
  looking for one fixed relative path; installed into site-packages that walk
  covers `/usr/lib`, `/usr` and `/`, reading and parsing whatever it found there.
  Resolution is now three sources and no search: an explicit argument,
  `DATA_GOV_IN_KEY`, then `COMMONER_PROBE_KEY_FILE` — a full path you name.
  No credential was ever in the artefact; the disclosure was a directory name.
- **The default install is guarded.** `dependencies = []` means a plain
  `pip install commoner-probe` gets the stdlib session, which every probe uses
  and which applied none of the guards this package documents.
  `StdlibSession().get("file:///etc/hosts")` returned the file. None of the
  guards needed a dependency — `url_safety` is stdlib-only and the robots and
  rate-limit helpers are defined above the `import requests` — they were simply
  never wired in.
- **Every request verb is guarded.** `head`, `put`, `patch`, `delete` and
  `request` fell through to the bare `requests.Session`, skipping the SSRF
  guard, robots, rate limit and backoff. Unwrapped verbs are now refused rather
  than forwarded silently.
- **Zip members are capped on bytes read, not on the size the archive claims.**
  The bomb guard trusted `file_size` from the archive's own directory — a value
  an attacker writes — then called `ZipFile.read`, which expands the member
  fully before checking anything. A forged file declaring 1000 bytes grew peak
  RSS by 432 MB before raising. Reading is now incremental, at all three sites.
- **One filename sanitiser instead of eight.** Only the shared one carried the
  leading-dot and empty-input defences, so `nada._slug("..")` returned `".."`
  and `_slug("///")` returned `""`. Filenames already on disk are unchanged —
  verified on real names before the switch.

### Fixed

- **An interrupted PDF download is no longer treated as complete.** `write_pdf`
  streamed to its final path and resumed on `size > 1000`, so a dropped
  connection left a truncated PDF that every later run accepted — permanently.
  It now writes a temp file and renames. Fixed in `sansad.py`'s override too,
  which had the identical defect.
- **robots.txt is checked under the identity that fetches.** `NevaProbe` set
  the User-Agent after construction, so robots was evaluated and cached under
  the default while pages went out as `NEVA_UA`.
- **`validate`'s kind → schema map is derived from the schemas** rather than a
  60-line `if` chain a new kind could be left out of — which had already shipped
  twice. Proven equivalent before switching: 32 kinds, zero disagreements.

### Removed from the public tree

- **Internal request ids.** `REQ-NNNN` is work-coordination vocabulary that
  resolves only in a private ledger: meaningless to an outsider, and it
  advertises that the tracker exists. 81 occurrences across 42 files — docs,
  ~60 code comments, four schema descriptions, a dozen test docstrings. The
  reason each comment gives is kept; only the id is dropped.
- **Implementation plans and specs.** 2,334 lines under `docs/superpowers/`
  and `docs/plans/` were session artefacts, not product documentation, and
  were tracked so they were public. `docs/` now holds only what a user of the
  package needs.
- **A private sibling repo's name**, which appeared 23 times across the
  academia package and `http_client`, and a second one twice — one of those
  pointing at an internal research note. Provenance is preserved, the names
  are not.

### Added

- **`commoner-probe --version`**, which did not exist.
- **`docs/CLI.md`.** The README was 1,284 lines and the command reference was
  822 of them. It moved verbatim; the README is 468 and points at it.
- **A release gate.** The publish workflow ran no tests, no lint, and no check
  that the tag matched `pyproject.toml`, so a `v0.13.0` tag on a 0.12.1 tree
  would have published 0.12.1 mislabelled, permanently. Publishing now depends
  on a verify job.
- **A bare-install CI job.** `dependencies = []` is this package's core
  constraint and nothing tested it; every matrix entry installed the extras.
- **A packaging guard** covering the shipped package and tracked markdown, so
  an internal reference fails a test instead of reaching PyPI.


## 0.12.1 (2026-08-01)

A patch, not a minor. `ROADMAP.md`'s pre-1.0 rule reserves the minor slot for a
new acquisition surface or a breaking change, and this is neither: `dchb-town`
already existed, no subcommand was added (40 before, 40 after), nothing was
removed, and the only changed behaviour on an existing path is a `.zip` branch
where the previous code would have errored. The functions whose signatures moved
were never released.

**Closes the last coverage gap in the urban half.** 0.12.0 read the
state-level `DH_2011_DCHB_Town_Release_<code>.xlsx` that 34 of 35 states publish.
Sikkim publishes four per-district ZIPs of `Town Statement-V_<district>.xls`
instead — same facts, older format — and those are now read too, so
`commoner-probe dchb-town` covers all 35 states.

Two source traps are encoded, both found in review rather than by the first cut:

- **A Statement V cell holds either a count or the nearest town and its
  distance.** `GANGTOK(67)` means none here and the nearest is 67 km away — a
  count of *zero with a location*, not a missing value. Parsing the cell as an
  integer silently drops exactly the towns that lack the facility.
- **The ZIP filename carries ORGI's ordinal, not the census district code.**
  `DH_2011_1101-North_District.zip` is state 11 plus district counter 01; the
  2011 Census district code the corpus joins on is **241**, and it appears only
  in the sibling `Appendix_I` header. The reader therefore takes the ZIP, and
  refuses one without that header rather than writing a key that fails to join.

**New optional extra: `commoner-probe[xls]`** (xlrd). This is the strongest
argument anyone could make for calling this a minor, so it is stated plainly
rather than buried: it is additive, no existing install changes, the core keeps
`dependencies = []`, and 34 of 35 states need no extra at all. It is in `dev` so
CI exercises the path instead of skipping it.

Rows from both input shapes emit the same `dchb_town_amenity` kind, the same
schema and the same `measure: "count"` guard, so a consumer never has to know
which format a state happened to publish.

Live: Sikkim's four districts — census codes 241-244, 9 towns, 4 public
libraries, 9 being its actual 2011 town count — mixed with an xlsx state in one
corpus, 35 towns, all schema-valid.

## 0.12.0 (2026-07-31)

A new acquisition surface, so a minor bump under the pre-1.0 rule in
`ROADMAP.md` — and a Layer 0 fix that every existing adapter inherits.

**`commoner-probe nada`** reaches the survey *instruments* the statistics APIs
do not carry: NSS questionnaires, the written sample design, technical reports.
NADA is World Bank software, so one adapter parameterised by `--base-url` serves
both `microdata.gov.in/NADA` (187 studies) and `censusindia.gov.in/nada`
(40,254) — the second being the acquisition surface the urban half was
waiting on.

**`commoner-probe dchb-town` closes the urban half — without the PDF
route.** The urban public-library COUNT was believed to live only inside ~640
District Census Handbook PDFs at ~18 MB each, which is why a Statement V PDF
parser was attempted and removed at 4-of-24 town recall. It does not: every DCHB
record in ORGI's NADA catalogue ships a **state-level**
`DH_2011_DCHB_Town_Release_<statecode>00.xlsx` carrying the counts as ordinary
columns. Present in 10 of 10 sampled records across 8 states. ~36 files instead
of ~640, and no table extraction.

Rows declare `measure: "count"`, pinned by a schema `const`, because the rural
Village Amenities equivalent is an availability FLAG per village. Adding the two
gives the widely-cited and wrong "~75,000 public libraries" — the rural figure
counts villages that HAVE a library, not libraries. Reading rooms are a separate
facility and get no combined field at all.

**A key collision found by running it, not by the suite:** Greater Mumbai is one
municipal corporation split across Mumbai and Mumbai Suburban, sharing town code
802794. Keying on state+town collapsed them — the parser produced 535 Maharashtra
towns and the corpus held 534, losing 11 libraries and 9.36 million people, while
the CLI printed the correct total because it summed the in-memory list rather
than what it had written. The district is now in the key, and `ingest()` refuses
to persist fewer rows than it parsed, which catches the next collision whatever
causes it.

**Two states, two conventions for "no facility here"**, both handled: Maharashtra
writes an explicit `0` (535/535 towns carry a number), Nagaland leaves the cell
empty and marks the status column `not_available` (21/26 towns). Reading only the
count column would have made Nagaland's 21 look unrecorded when the source
plainly says the facility is absent. The status column is the signal; an empty
cell with no status at all stays null, because that one genuinely is unknown.

Census codes are stored as numbers in these sheets, so a raw read drops the
leading zero — Punjab's state code `03` arrives as `3`. Nine of the 35 states
have a code below 10 and the rural corpus is zero-padded, so codes are padded to
their census widths (state 2, district 3, subdistrict 5, town 6, verified
uniform across both files) or the join would silently miss a quarter of India.

XLSX parsing is stdlib `zipfile` + `ElementTree` (this package declares
`dependencies = []`, so `defusedxml` is unavailable). Since these files arrive
over the network from a government portal, parts declaring a DTD are refused and
uncompressed size is capped before reading — the entity-expansion and
decompression-bomb vectors.

**The HTTP client had been ignoring 429 entirely** — the one status that means
*slow down*, returned to the caller as though the portal had said yes. That is
the more consequential half of this release; it was found while building the
adapter, not by looking for it.

**Verified against the live source, not only fixtures.** Three NSS studies
acquired from microdata.gov.in: four `Questionnaires` PDFs with hashes matching
disk, `sampling_procedure_chars` 7306 on NSS 68, extraction reading 76,469
characters, 32 records validating. Both NADA hosts serve an incomplete TLS chain
(leaf without intermediate) — documented, with the fix, and verification is
never disabled.

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
  prints how many remain plus the next-step command that continues. Microdata
  files are login-gated and deliberately out of scope.

  Three defects were found by running it live rather than by the mocked suite,
  which was green throughout: a re-run appended a second row for every study and
  document, so a consumer streaming the corpus counted each artefact twice (the
  manifest is now one row per artefact, upserted by key, following the
  `load_seen()` convention seven other adapters here use); against a
  40,254-study catalogue the brake helpfully suggested `--max-studies 40254`,
  i.e. the bound recommending the unbounded run it exists to prevent (it now
  suggests the next step and names the total separately); and a TLS failure
  printed a urllib3 traceback instead of telling the operator what to do.

  `censusindia.gov.in` serves its leaf certificate without the intermediate, so
  Python cannot verify the chain even though curl can — curl chases the AIA
  extension, Python does not. The CLI now says so and names the fix
  (`REQUESTS_CA_BUNDLE` with the missing intermediate) rather than suggesting
  verification be turned off.

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

**Two source families the org covered nowhere:** ORGI / Census of India and NITI Aayog Annual Reports (an earlier request's residual). Both
live-verified end to end, and both shipped with the traps encoded rather than
described: the Census adapter refuses to let a rural availability flag be summed
with an urban count, and the NITI adapter reads its fiscal year from the filename
because the upload directory `/2025-02/` matches a year pattern.

**One request is deliberately NOT closed.** The request asked for all libraries.
The rural half is fully served and the urban public-library count is not on the
API, so it stays in-progress with the remaining route named. Reporting it as
delivered would hand the requester coverage they do not have.

**NeVA extraction now checkpoints**, after three consecutive corpus passes were
lost whole — at 14 minutes, at 100 minutes, and at 2h34m on the final write. The
first run under the new code completed and added **1,325 Q/A records**, inside
the 961-1,744 band predicted from a 60-document sample before the run.

### Added

- **`commoner-probe niti-annual-report`** — NITI Aayog Annual Reports (an
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

- **`commoner-probe census` — ORGI / Census of India acquisition**. A source family the org covered nowhere. Four surfaces from
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
  life. The RS CSV diverges from the LS CSV on 13 of 27 column names,
  and the adapter was built and fixtured against LS only.

The rest close five Codex findings, each reproduced by execution before being
touched. Four of the seven fixes in this release are corrections to earlier
fixes in the same session — recorded plainly because the pattern is the point:
in this cycle a fix drew a finding of its own seven rounds running, in two
recurring shapes (*a label asserting more than was verified*, and *a guard that
cannot see its own edge case*).

### Fixed

- **`prs --surface mp-track --house rs` wrote nothing and exited 0.**
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
  the zero-hour case: a run that reached nothing and a genuinely quiet source
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

A downstream repo also reported that `sansad --member` silently caps at 25 rows per
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
  500; the guard raises before any request.
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
