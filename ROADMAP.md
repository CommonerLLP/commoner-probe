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

## On master, unreleased (merged since 0.7.0)

- `sansad --all` full-corpus enumeration with suspect-marking and
  per-window resume; `--member` per-member retrieval; `sansad tabled`
  title-search mode.
- `attendance`, `myneta`, `legacy-dspace`, `mospi`, `ministry-ddg`
  (7-portal registry) adapters.
- NeVA Gujarati extraction (two-column geometry split, district tables,
  reference-calibrated glyph repair).
- Committee outsourcing/consultancy typed signals in `extract-answers`;
  vacancy rows; RS debates source contract.

**Next gate: 0.8.0** — cut from master once the in-flight review fixes
land. Version-bump checklist: bump `pyproject.toml`, CHANGELOG entry,
`pip install -e .` in the repo `.venv` (refreshes the `dist-info` that
`_resolve_version()` reads), tag.

## Queued (requested source adapters, not started)

- BPRD *Data on Police Organisations* acquisition + extraction (needs the
  shared OCR decision below).
- Abhilekh Patal (National Archives of India) — India-egress hard
  requirement verified; unscoped.
- PRS Legislative adapter — licensing posture decided (internal research
  use only, no text republication); not started.
- Identity-safe per-member retrieval by stable member ID.
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
