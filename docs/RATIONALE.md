# commoner-probe: Rationale and Domain Case

*Draft for JOSS submission / README anchor / funder pitch.*

---

## What this library does

`commoner-probe` is a Python library for acquiring Indian legislative records from mandatory-disclosure portals. It currently covers three source families:

- **Lok Sabha and Rajya Sabha parliamentary questions** — starred and unstarred questions, with PDF attachments, from sansad.in
- **Standing committee reports** — Lok Sabha and Rajya Sabha department-related committees, with Action Taken Report linkage
- **NeVA state assembly portals** — questions, unlisted questions, member details, and papers to be laid across state legislative assemblies running the National e-Vidhan Application

The library produces structured JSONL corpora and downloaded PDFs. It is designed to be consumed by downstream analytics layers that index, classify, and search the acquired records.

---

## Why this library needs to exist

### The problem: mandatory data that is practically inaccessible

The Right to Information Act (2005), Section 4, imposes a *proactive disclosure* obligation on every public authority in India. Parliamentary questions and committee reports are among the most important categories of this mandated disclosure: they are the formal record of how the legislature exercises oversight over the executive.

The data exists. It is published. But it is published in ways that make systematic, programmatic access extremely difficult:

- sansad.in exposes no documented API. The endpoints used by the website's own JavaScript are undocumented, version without announcement, and differ structurally between the Lok Sabha and Rajya Sabha sections.
- The NeVA platform serves 28+ state assemblies. Each has its own portal subdomain and state code. None publish endpoint documentation.
- PDFs are the primary record format. Metadata in the web interface is sparse; full text requires extraction.
- Portal uptime is unreliable. Downtime during sessions (when queries are most active) is common.

Any researcher or journalist who wants to work systematically with this data currently faces a choice: spend weeks reverse-engineering the web interface, or restrict their inquiry to what they can manually download. Neither is acceptable for public interest research at scale.

### The gap in existing tools

General-purpose scraping libraries (Scrapy, Playwright, httpx) provide excellent HTTP infrastructure but no domain knowledge. They do not know:

- that sansad.in's Lok Sabha question API takes a ministry name as a bucket parameter, while the Rajya Sabha API takes a session number
- that committee report PDFs are located by constructing a path from `reportNo` and `uuid` fields in the search response, not from a direct download link
- that NeVA's member detail pages are behind a separate CMS endpoint requiring a `memberId` parameter extracted from the roster response
- that any of these fields, when interpolated into filesystem paths, require sanitisation against path traversal

No existing Python package on PyPI covers Indian legislative data acquisition. The closest analogs — Open States (US), TheyWorkForYou (UK/mySociety), ParlTrack (EU) — are jurisdiction-specific and not applicable here. There is no Indian equivalent.

---

## Domain knowledge: what we have reverse-engineered

### sansad.in — Lok Sabha questions

The LS question search operates via a POST endpoint that accepts a JSON body specifying ministry, date range, question type (starred/unstarred), and pagination parameters. Key findings:

- The ministry list is not exposed in the API; it must be scraped from the search form and kept current across Lok Sabha numbers, as ministry names and jurisdictions change at government formation.
- Pagination is record-offset based, not page-based. The API silently returns empty results (not an error) when the offset exceeds the result set — callers must detect end-of-results by comparing returned count to requested limit.
- The `qslno` (question serial number) field in the response is the canonical identifier. It is stable across re-crawls of the same session.
- PDF URLs are constructed from a base path plus `qslno` — they are not returned as full URLs in the search response.

### sansad.in — Rajya Sabha questions

The RS question API shares a domain but differs structurally:

- Search is session-number-based, not date-based. Session numbers must be enumerated independently.
- The ministry filter uses a `LIKE`-style string match against a ministry name field, not an exact match against a controlled vocabulary.
- The response schema differs from the LS response. Several fields present in LS records are absent in RS records and vice versa.
- RS PDF construction follows a different path convention from LS.

### sansad.in — standing committees

Committee reports use a third endpoint family, distinct from both LS and RS question APIs:

- Committee slugs (e.g. `Rural Development and Panchayati Raj`) appear literally in URL paths, requiring percent-encoding before the HTTP client sees them.
- The Lok Sabha committee list is semi-stable across Lok Sabha numbers but not identical. We maintain a curated slug registry.
- Action Taken Reports are identified by title parsing, not by a structured ATR flag in the API response. The linkage between a committee report and its ATR is reconstructed from title patterns.

### NeVA — state assembly portals

NeVA (National e-Vidhan Application) is a common CMS deployed across state assemblies. The portal structure is consistent across deployments but:

- Each state assembly has a distinct portal subdomain (e.g. `gujarat.neva.gov.in`) and a CMS state code (e.g. `GJ`). These are not published in a central registry.
- Assembly numbers, session numbers, and question serial numbers are all required to construct a full question record. The relationship between these identifiers differs from the sansad.in conventions.
- Member detail pages exist but require a separate request per member; they are not included in roster list responses.
- "Papers to be laid" (a category of legislative business distinct from questions) are available through a separate endpoint with different pagination behaviour.

---

## Community endpoint reference

Because no official API documentation exists for any of these portals, we publish our own — a community-reverse-engineered endpoint reference for each source family. This is standard practice in open government technology (see: mySociety's documentation of the UK Parliament API, Open States' documentation of state legislative interfaces, Sunlight Foundation's Capitol Words).

The endpoint reference is maintained in `docs/ENDPOINTS.md`. It describes:

- Base URLs and HTTP methods
- Request body / query parameter schemas
- Response field inventory
- Known instabilities and version history
- Rate limit behaviour observed in practice

Publishing this documentation serves the broader research community: anyone trying to access this data, regardless of whether they use this library, benefits from knowing these endpoints exist and how they behave.

The legal basis is clear: proactive disclosure under RTI Act 2005 Section 4 is a legal obligation, not a courtesy. Documenting how to access mandated public data is itself a public service. There is no terms-of-service prohibition on sansad.in or NeVA portals that would restrict this documentation.

---

## Production-grade HTTP behaviour for government portals

Indian government portals exhibit failure modes that generic scraping infrastructure does not handle gracefully:

- **Silent 429/503 without Retry-After headers.** sansad.in and NeVA portals throttle under load without advertising it. Callers must implement their own backoff.
- **Intermittent 5xx during sessions.** Portal load spikes when Parliament is in session — exactly when the data is most valuable. A stale cached copy is preferable to a failed crawl.
- **Unresolvable hosts.** Some state NeVA portals go offline between sessions. SSRF-style host resolution checking catches these before a crawl attempt.

The library's HTTP client addresses each of these: per-domain rate limiting, exponential backoff with cap, optional stale-if-error caching via `requests_cache`, SSRF guard, and robots.txt compliance.

---

## What the library is not

`commoner-probe` is Layer 0: acquisition. It does not:

- perform semantic search or topic classification (that is the responsibility of the compose layer, which injects a `filter_fn` into the topic profile)
- extract structured text from PDFs beyond answer/recommendation pair extraction for committee reports
- maintain a database or search index
- make normative judgements about the content of records

The library's output is a JSONL manifest and a `pdfs/` directory. What downstream consumers do with that corpus is out of scope.

---

## Relation to the RTI framework and sousveillance

The framing that organises this work is Steve Mann's concept of sousveillance — turning the tools of surveillance back on institutions of power. The Indian legislature's mandatory disclosure infrastructure is not a gift from the state; it is a concession extracted through the RTI movement. Making that disclosure systematically accessible — turning it into a corpus that can be queried, cross-referenced, and held up against executive action — is the continuation of that political project by technical means.

This framing has practical consequences for library design. It is why we preserve provenance metadata (session number, question number, ministry, date) alongside document content. It is why we audit classifier decisions in `_runs.jsonl`. It is why the HTTP client identifies itself honestly via User-Agent rather than spoofing a browser. The apparatus of acquisition must be as transparent as the data it acquires.

---

## Target users

- **Parliamentary researchers and journalists** who need structured access to question-answer records at scale, without reverse-engineering sansad.in themselves.
- **Policy researchers** working on specific ministries or domains (food security, environment, health) who want a filtered corpus from a topic profile.
- **Civic tech organizations** building dashboards, alerts, or public-facing tools on top of Indian legislative data.
- **Academics** studying legislative behaviour, question-asking patterns, or the relationship between parliamentary oversight and executive action.

---

## Citation target (JOSS)

The Journal of Open Source Software (JOSS) accepts submissions for research software with clear scholarly value. A JOSS paper for `commoner-probe` would establish:

- a citable DOI for the library
- peer review of the software design and documentation
- formal recognition of the domain reverse-engineering work as a scholarly contribution

JOSS papers are typically 250–1000 words and focus on the research contribution rather than technical implementation. The rationale in this document provides the raw material for that submission.
