# committees-smoke

Frozen smoke fixture for the standing-committee crawler.

## Contents

- `raw/ls_finance_p1.json` — page 1 of LS Finance Committee reports
  (committeeCode=12, lsNo=18), pulled from
  `https://sansad.in/api_ls/committee/lsRSAllReports`.
- `raw/rs_health_p1.json` — page 1 of RS Health Committee reports
  (mstCommId=14), pulled from
  `https://sansad.in/api_rs/committee/committee-reports`.
- `manifest.jsonl` — canonical crawler output produced from the raw
  payloads above using `examples/topics/libraries.json` and the regex
  classifier. Volatile fields (`run_id`, `crawled_at`, `elapsed_ms`)
  are stripped so the fixture is byte-stable.

## Why frozen

The live smoke (running the CLI against sansad.in) proves the crawler
works *today*. This fixture proves the parser still produces the
expected record shape *without* hitting the network — so a
sansad.in-side breakage and a parser regression are distinguishable.
If `tests/test_smoke_fixture.py` fails, the parser drifted; if a live
crawl fails but this fixture still passes, the upstream API changed.

## Refreshing

The frozen payloads should change rarely — only when the upstream API
shape changes in a way the crawler must now handle. Refresh procedure:

1. Pull the same two URLs again (commands in
   `docs/INTEGRATION_SMOKE.md`).
2. Save them under `raw/` overwriting the existing files.
3. Re-generate `manifest.jsonl` by running the regen one-liner kept
   in `tests/test_smoke_fixture.py` (set `SANSAD_REGENERATE_FIXTURE=1`).
4. Inspect the diff in both `raw/*.json` and `manifest.jsonl` carefully
   before committing.

The two-file structure (`raw/` + canonical `manifest.jsonl`) means the
fixture refresh is itself an audit event: the diff shows which upstream
changes flowed through which parser changes.
