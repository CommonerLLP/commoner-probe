# Roadmap — commoner-probe

Forward-looking release sequencing. `CHANGELOG.md` is the per-version
record; this file says what ships next and what is deliberately deferred.

## Shipped

| Release | Date | Highlights |
|---|---|---|
| 0.4.1 | 2026-06-24 | CommonerLLP org package metadata |
| 0.5.0 | 2026-06-25 | `budget`, `academic-jobs`, `bills`, `debates`; robots.txt bounded timeout |
| 0.5.1 | 2026-06-25 | `record_filter_fn`; academic-jobs fetch resilience |
| 0.6.x | 2026-07-03 | HEI parser expansion (`parser_utils`, IIT Gandhinagar, IIT Hyderabad) |
| 0.7.0 | 2026-07-03 | `indiacode`; NeVA portal registry + `state-assembly-probe` |
| 0.8.0 | 2026-07-19 | 9 adapters (`questions-list`, `prs`, `mospi`, `ministry-ddg`, `legacy-dspace`, `doe-pay-allowances`, `attendance`, `myneta`, `dpe-csr`); 5 extraction modules; `sansad` tabled / `--all` / `--mp-code` |
| 0.9.0 | 2026-07-28 | `wayback`, `abhilekh-patal`, `render`, `courts`, `cag`; PRS completed; Wayback provenance on acquisition; the ten-finding Codex sweep |
| 0.10.0 | 2026-07-29 | OCR fallback (`ocr_pdf_text`, `extract-answers --ocr`); run-level `status` on `_runs.jsonl` + non-zero exit on a failed crawl; corpus-truncation audit recipe; two post-merge Codex waves |
| 0.10.1 | 2026-07-29 | Fixes only. Two live breakages on shipped code: `mca-csr` dead at the TLS layer since 2026-07-02 (cert SAN vs a hardcoded `www.`), and `prs --surface mp-track --house rs` writing nothing and exiting 0 for its whole life. Plus the `--ocr` acceptance gate (16/60 -> 26/60 recovery, no longer destroys records) and five Codex findings |
| 0.11.0 | 2026-07-30 | Two new source families: `census` (ORGI/Census of India — PCA, village/town amenities, town directory, via the data.gov.in API instead of ~11.5 GB of DCHB PDFs) and `niti-annual-report` (NITI Annual Reports, the residual). NeVA extraction checkpoints, so an interrupted corpus pass resumes instead of restarting |

| 0.12.x | 2026-07-31 | `nada` (NADA platform, MoSPI + ORGI from one `--base-url`), `dchb-town`; the shared HTTP client had been ignoring 429 entirely |
| 0.13.0 | 2026-08-01 | Eleven review findings, each demonstrated failing first; public surface cleaned |
| 0.14.x | 2026-08-04 → 08-16 | The questions-list subject bleed (97.9% of adjacent pairs, now 0); `parse_status` gains `boundary_bleed` |
| 0.15.x | 2026-08-18 → 08-19 | Every bill date as ISO with the repair reaching rows on disk; a `doctor` pin reader for every form the org uses; the Python floor at 3.11 |
| 0.16.0 | 2026-08-20 | `mirror`, `udise-docs`, `supervisor`, `dopo_catalogue`; a bills resume that skips recorded outcomes |
| 0.17.0 | 2026-08-23 | A per-institution `user_agent` for `academic-jobs` |
| 0.18.0 | 2026-08-23 | `closing_date_status` on `academic_job_posting`; `iit_hyderabad` reads the advertisement PDF; `textparse.term_pattern` |

## On master, unreleased

Nothing. 0.18.0 was cut from master with no open PRs and no open issues.

**Next gate: 1.0.0 — deferred deliberately, and gated on Phase 2.** Declaring
1.0 promises interface stability, and the package rename to `probe`/`compose`
on PyPI (below) would break that promise the moment it lands. Do the rename
first, or accept that 1.0 means a 2.0 shortly after.

Version-bump checklist: bump `pyproject.toml` (the single source of truth —
`__version__` reads it), CHANGELOG entry, `pip install -e .` in the repo
`.venv` (refreshes the `dist-info` that `_resolve_version()` reads), branch +
PR, tag, **`make verify-release VERSION=x.y.z`**, then move consumer pins.

**The verify step is not optional, and a green workflow does not replace it.**
Neither does one read of PyPI's top-level JSON. That index has now misled a
release twice, in both directions: at 0.10.0 a cached read showed the PREVIOUS
version and made an unfinished publish look done; at 0.12.0, twenty seconds
after a successful upload, it still reported 0.11.0 and made a finished publish
look failed. `make verify-release` therefore retries the version-specific route
and then **installs the exact version from PyPI into a throwaway venv and
imports it** — the only check a stale index cannot fake, and the only one a
consumer actually depends on.

**Pre-1.0 versioning rule in force here:** a new acquisition surface or any
breaking change is a minor bump; only backwards-compatible fixes take a patch.
0.9.0 carried both — five new subcommands and four behaviour changes (`render`
exit codes, `rendered_page` manifest keys for query-string URLs, a dropped
status enum value, 4xx reclassified as `error`). 0.10.0 added no subcommand but
took the minor slot anyway on the breaking change alone: `sansad` exits
non-zero where it exited 0. **0.10.1 is the patch case done properly**: seven
fixes, no new surface, and no contract change — the two new `ValueError`s it
raises fire only where the previous behaviour was to write nothing and exit 0,
so no working caller changes behaviour.

## Queued (requested source adapters, not started)

- BPRD *Data on Police Organisations* acquisition + extraction. **The host is
  unreachable, and a proxy does not fix it** (verified 2026-07-28):
  `bprd.nic.in` resolves but every connection times out, and `bprd.gov.in` does
  not resolve. Two egress points were tried, so the timeout is a fact about the
  host rather than about one network. Needs a new host or a mirror before any
  adapter work; the shared OCR decision is not the binding constraint.
- MPLADS fund-release / works / utilisation-certificate records — source
  recon from an India vantage point first.
- Finance document-disclosure adapters beyond the current budget set.

## Deferred / parked

- OCR machinery for scanned PDFs (NeVA low-quality backlog; police-statistics
  scans): build once, shared, when a second consumer needs it.
- India Code Wayback snapshot-on-fetch — no second adapter needs it;
  revisit as a shared capability if that changes.
- Making `requests_cache` a required default — the cache is optional-install
  today (when present it already runs with 6h TTL + stale-if-error, and the
  one-second per-domain rate limit is always on); evaluate against all
  existing corpora before requiring it.
- Package rename to `probe` / `compose` on PyPI (Phase 2) — kept separate
  from code movement by decision.

## Explicit non-builds

- CPPP Award-of-Contract scraping — CAPTCHA-gated; this repo does not
  build around access controls.
- GeM procurement — India-geo-fenced JS application with no reachable
  terms; needs a scoping decision before any build.
