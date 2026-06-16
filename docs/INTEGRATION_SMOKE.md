# Integration smoke tests

These checks prove the probe and extractor work against real API
responses. The corpus in `examples/corpora/committees-smoke/` is
frozen for offline regression testing.

## Standing-committee probe — frozen fixture

`examples/corpora/committees-smoke/` carries frozen LS/RS API responses
and the canonical manifest they produce.
`tests/test_smoke_fixture.py` runs the probe against these with a
fake HTTP session and asserts the manifest matches byte-for-byte.

```bash
.venv/bin/python -m pytest tests/test_smoke_fixture.py -q
```

To refresh the fixture after a confirmed upstream API change:

```bash
# 1. Pull the raw API responses again.
curl -sS \
  -H 'User-Agent: Mozilla/5.0 commoner-probe' \
  'https://sansad.in/api_ls/committee/lsRSAllReports?house=L&committeeCode=12&lsNo=18&page=1&size=2&sortOn=reportNo&sortBy=desc' \
  -o examples/corpora/committees-smoke/raw/ls_finance_p1.json

curl -sS \
  -H 'User-Agent: Mozilla/5.0 commoner-probe' \
  -H 'Referer: https://sansad.in/rs/committees' \
  'https://sansad.in/api_rs/committee/committee-reports?mstCommId=14&departmentId=&presentationYear=&search=&page=1&size=2&sortOn=reportNo&sortBy=desc&locale=en' \
  -o examples/corpora/committees-smoke/raw/rs_health_p1.json

# 2. Regenerate the canonical manifest.
COMMONER_REGENERATE_FIXTURE=1 .venv/bin/python -m pytest tests/test_smoke_fixture.py -q

# 3. Inspect diff and commit only intentional changes.
git diff examples/corpora/committees-smoke/
```

## End-to-end smoke probe (requires network)

```bash
pip install -e ".[pdf,http]"

commoner-probe sansad \
  --topic examples/topics/libraries.json \
  --out /tmp/smoke \
  --house ls \
  --max-buckets 1 --max-records 1 --no-download

commoner-probe extract-answers --out /tmp/smoke
commoner-probe committees \
  --topic examples/topics/libraries.json \
  --out /tmp/smoke-cc \
  --house ls --committees finance --max-records 1 --no-download
commoner-probe atr-linkage --out /tmp/smoke-cc
```

All four commands should exit 0. Manifest records should not contain
`matches`, `tags`, `score`, or `classifier` fields — classification
is handled by downstream consumers.

## DMFT evidence bundle

Use the bundled topic to probe Sansad Q/A records answered by the Ministry
of Mines, then bundle those parliamentary oversight records with Ministry
of Mines DMFT disclosure snapshots:

```bash
commoner-probe init-topic \
  --name mines_dmft_pmkkky \
  --out data/topics/mines_dmft_pmkkky.json

commoner-probe sansad \
  --topic data/topics/mines_dmft_pmkkky.json \
  --out data/sansad/mines-dmft-pmkkky \
  --house both \
  --from-date 2015-09-01 \
  --sessions 1-267

commoner-probe extract-answers \
  --out data/sansad/mines-dmft-pmkkky

commoner-probe mines-dmft \
  --out data/mines-dmft \
  --sources mines-gov-in

commoner-probe evidence dmft \
  --mines-dmft-dir data/mines-dmft \
  --sansad-dir data/sansad/mines-dmft-pmkkky \
  --out data/evidence/dmft.json
```

The evidence bundle keeps `executive_disclosure` and
`parliamentary_oversight` as separate arrays. The Ministry CSVs are
cumulative snapshots keyed by source `Last-Modified`; Sansad records are
dated legislative answers.
