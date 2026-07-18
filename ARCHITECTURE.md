# Architecture — commoner-probe

System contracts and data flow. Repo boundaries live in `SCOPE.md`;
release sequencing in `ROADMAP.md`; user-facing usage in `README.md`.

## Pipeline shape

```
disclosure portal
  → source adapter (one module per source family)
  → manifest.jsonl            one schema-validated record per item
  → files on disk             PDFs/CSVs, sha256 recorded per file
  → extract-answers / extract-debates
  → answers.jsonl + typed rows (vacancy, outsourcing, district tables)
  → Corpus read API → downstream consumers
```

Instrumented adapters also append `_runs.jsonl` (run id, scope, counts,
errors) — the append-only audit trail for the corpus. Not every adapter is
instrumented yet: lighter ones (bills, attendance, academic-jobs) write
manifest records only.

## Layers

- **`http_client.py` + `url_safety.py`** — the outbound HTTP surface for
  every adapter built on `make_session()`: SSRF guard (rejects
  private/loopback/link-local/reserved targets), robots.txt checked per
  (domain, User-Agent), per-domain rate limit, 5xx backoff, optional
  `requests_cache` with stale-if-error, and a User-Agent override path for
  WAF false-positives. With `requests` absent, a zero-dependency stdlib
  fallback preserves the `requests.Response` interface
  (`text`/`content`/`json`/`iter_content`/`raise_for_status`) but carries
  only the honest User-Agent — the SSRF guard, robots check, rate limit,
  backoff, and cache require the `requests` install.
- **`base.py`** — `BaseProbe`: manifest append, seen-set resume (only
  terminal statuses skipped), `write_pdf`, run logging.
- **Adapters** — one module per source family: `sansad.py`,
  `committees.py`, `debates.py`, `bills.py`, `neva.py` +
  `neva_portals.py`, `indiacode.py`, `dspace.py`, `ddg.py`, `doe.py`,
  `mospi.py`, `attendance.py`, `myneta.py`, `csr/`, `dmft/`, `budget/`,
  `academia/`. Portal/ministry registries grow one live-verified entry at
  a time.
- **Contracts** — `schemas/*.schema.json` (37 schemas), the `validate`
  command, `records.py` dataclasses, `corpus.py` typed readers.
- **Extraction** — `answers.py`, `textparse.py`, `vacancy.py`,
  `outsourcing.py`, `neva_text.py`, `extract_debates.py`: deterministic
  text extraction over downloaded files. Honest quality markers
  (`text_layer`, NeVA `quality`, `layout: "evasive"`) instead of guessed
  content.
- **Cross-record structure** — `atr_linkage.py` (ATR → original report
  chains), `entities.py` / `resolver.py` (asker name → stable entity),
  `evidence.py`, `stats.py`.
- **CLI** — `cli.py`, 25 subcommands; `init-topic` scaffolds a topic
  profile; `validate` checks a corpus against its schemas.

## Invariants

1. **Provenance**: every acquired item has a manifest record with source
   URL and fetch timestamp; downloaded files carry sha256.
2. **Schemas describe source reality**: when the source and the schema
   disagree, widen the schema to match the source — do not normalize at
   acquisition.
3. **Resume safety**: reruns skip only terminal statuses
   (`downloaded`, `skipped_exists`, `no_pdf_found`); a metadata-only pass
   must never block a later download pass. This is the target contract —
   held by indiacode, dspace, and sansad tabled; the debates adapter still
   marks every appended row seen regardless of status (known gap, tracked
   in the repo TODO).
4. **Access posture**: fail open on robots.txt fetch failure, fail closed
   on SSRF, and never work around a CAPTCHA or an access control.
5. **Live verification**: unit tests pin behaviour, and adapters are also
   exercised against the real portal before a merge.
6. **Geo-fences are network-path-dependent**: detect and message them
   (explicit geo-fence exit), never hardcode "needs India egress" as a
   universal claim.
