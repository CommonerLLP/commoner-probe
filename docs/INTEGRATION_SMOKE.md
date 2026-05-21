# Integration smoke tests

These checks prove the crawler and extractor work against real API
responses. The corpus in `examples/corpora/committees-smoke/` is
frozen for offline regression testing.

## Standing-committee crawler — frozen fixture

`examples/corpora/committees-smoke/` carries frozen LS/RS API responses
and the canonical manifest they produce.
`tests/test_smoke_fixture.py` runs the crawler against these with a
fake HTTP session and asserts the manifest matches byte-for-byte.

```bash
.venv/bin/python -m pytest tests/test_smoke_fixture.py -q
```

To refresh the fixture after a confirmed upstream API change:

```bash
# 1. Pull the raw API responses again.
curl -sS \
  -H 'User-Agent: Mozilla/5.0 sansad-crawler' \
  'https://sansad.in/api_ls/committee/lsRSAllReports?house=L&committeeCode=12&lsNo=18&page=1&size=2&sortOn=reportNo&sortBy=desc' \
  -o examples/corpora/committees-smoke/raw/ls_finance_p1.json

curl -sS \
  -H 'User-Agent: Mozilla/5.0 sansad-crawler' \
  -H 'Referer: https://sansad.in/rs/committees' \
  'https://sansad.in/api_rs/committee/committee-reports?mstCommId=14&departmentId=&presentationYear=&search=&page=1&size=2&sortOn=reportNo&sortBy=desc&locale=en' \
  -o examples/corpora/committees-smoke/raw/rs_health_p1.json

# 2. Regenerate the canonical manifest.
SANSAD_REGENERATE_FIXTURE=1 .venv/bin/python -m pytest tests/test_smoke_fixture.py -q

# 3. Inspect diff and commit only intentional changes.
git diff examples/corpora/committees-smoke/
```

## End-to-end smoke crawl (requires network)

```bash
pip install -e ".[pdf,http]"

sansad-crawl crawl \
  --topic examples/topics/libraries.json \
  --out /tmp/smoke \
  --house ls \
  --max-buckets 1 --max-records 1 --no-download

sansad-crawl extract-answers --out /tmp/smoke
sansad-crawl crawl-committees \
  --topic examples/topics/libraries.json \
  --out /tmp/smoke-cc \
  --house ls --committees finance --max-records 1 --no-download
sansad-crawl extract-atr-linkage --out /tmp/smoke-cc
```

All four commands should exit 0. Manifest records should not contain
`matches`, `tags`, `score`, or `classifier` fields — classification
is handled by downstream consumers.
