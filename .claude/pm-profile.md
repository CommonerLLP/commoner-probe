---
repo: commoner-probe
cockpit: docs/STATUS.md
roadmap: TODO.md
unit_of_progress: public acquisition/evidence source families with CLI commands, schemas, corpus streams, docs, and tests
---

## Stage discipline

source-proven -> adapter -> manifest schema -> corpus stream -> CLI -> evidence bundle -> live corpus refresh.

Do not add schema or CLI until the live portal/source contract is proven.

## 1. Count commands

```bash
find commoner_probe/schemas -maxdepth 1 -name '*.schema.json' | wc -l | tr -d ' '
/opt/homebrew/bin/rg -n "sub.add_parser\\(" commoner_probe/cli.py | wc -l | tr -d ' '
find commoner_probe -path '*/__pycache__' -prune -o -name '*.py' -print | wc -l | tr -d ' '
find tests -name 'test_*.py' | wc -l | tr -d ' '
```

## 1a. Integrity check

```bash
pytest tests/test_dmft_mines.py tests/test_evidence_dmft.py tests/test_init_topic_cli.py tests/test_docs_sync.py
pytest -k 'not test_mca_csr_manifest_schema_is_bundled_and_validates_record and not test_mines_dmft_manifest_schema_is_bundled_and_validates_record'
git diff --check
```

Schema-validation tests require `jsonschema`; ruff must be installed before treating lint as available.

## 2. Freshness gates

- MCA CSR: MCA CDM CSR export endpoint is the source of truth; refresh years before comparison work.
- Mines DMFT: Ministry CSVs are cumulative/current snapshots; use source `Last-Modified` as the timestamp, not FY.
- Odisha DMF: district/state JSON and report endpoints are proven first; Chhattisgarh and Jharkhand remain discovery targets.
- MoF/DPE CSR: `/cms/wp-json` proves document disclosure only until spend/project fields are proven.

## 3. Live-ops check

No daemon is expected for normal repo work. Live acquisition is explicit via CLI commands into ignored data paths.

## 4. Roadmap source

Use `TODO.md`. Mark items complete only when implementation and verification artifacts exist.

## 5. Report template

Report: integrity, source-family stages, stale/freshness risks, blocked owners, commit gap, and next command.

## Discipline

Use `/opt/homebrew/bin/rg` for all ripgrep searches. Keep public names clear: `mines-dmft`, not `mom-dmft`.
