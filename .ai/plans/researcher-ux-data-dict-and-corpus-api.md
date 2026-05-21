# Plan: Researcher UX — data dictionary + load-corpus Python API

## Context

The crawler is feature-complete for v0.1.0 (acquisition, structural extraction, ATR linkage, optional entity resolution). The next bottleneck is the gap between a corpus on disk and a researcher in a notebook. Two complementary deliverables close that gap:

1. **Data dictionary + JSON Schemas** for every output stream (`manifest.jsonl`, `_runs.jsonl`, `answers.jsonl`, `atr_linkage.jsonl`, `entities/*.jsonl`). Today the only schema documentation is a comma-separated list in README.md L122-127, several streams have nothing, and field nullability lives in docstrings. We commit to a single source of truth: a Markdown reference (`docs/SCHEMAS.md`), machine-readable JSON Schemas shipped as package data, and a sync test that fails when docs and schemas drift.

2. **`Corpus` loader API** so consumers do `from sansad_crawler import Corpus` instead of writing 50 lines of glob-and-json-loads. Includes typed dataclass records, streaming iterators, sensible joins (manifest+answers, ATR chains), and an opt-in pandas frame helper. Plus a `sansad-crawl stats` subcommand that exercises the API and prints corpus health.

These do not change emit-side behaviour — no crawler or extractor code is touched. Existing on-disk corpora must validate cleanly against the schemas they describe.

**Scope limits**: no schema versioning yet (current shape is v1; future-bumps land when we change emitter output); no `pyarrow`/`duckdb` integration; no remote-corpus fetch; no breaking changes to the four existing CLI subcommands.

## Phased plan (8 beads)

Each phase below maps to one `bd create`. Dependencies are wired with `bd dep add`. Each phase ends with a single focused commit (`feat(phase-N): <title>`) referencing this plan, and a `bd close`.

### Phase 0 — Save plan into the repo
Land this plan as a checked-in reference so every subsequent bead can cite it.
**Acceptance**: `.ai/plans/researcher-ux-data-dict-and-corpus-api.md` exists and is committed.

### Phase 1 — Inventory + draft `docs/SCHEMAS.md`
Walk every emit site (`sansad_crawler/sansad.py:256`, `:399`; `committees.py:343`, `:462`; `runlog.py:181`; `answers.py:540`, `:589`; `atr_linkage.py:250`; `entities.py:282-293`) and produce one Markdown reference. For each output stream document: file path, one-record-per-what, list of fields with `name | type | required | enum/format | provenance` columns. Cover every record kind separately:

- `manifest.jsonl` — three kinds: LS Q/A, RS Q/A, LS committee report, RS committee report (note shape divergence between LS and RS Q/A: LS has `uuid`, `handle`, `session`, `loksabhanumber`, `uri`, `found_via_group`; RS has `qslno`, `ses_no`, `question_text`, `answer_text`, `pdf_url_hindi`, `status`).
- `_runs.jsonl` — one record per crawl invocation. Schema = `Run` dataclass (runlog.py:91) plus `elapsed_ms`, with `bucket_attempts[]` free-form per crawler kind (document conventional keys for ls_qa, rs_qa, committee_report).
- `answers.jsonl` — three kinds discriminated by `kind`: `qa_response`, `atr_response`, `dfg_recommendation`. Document the common header (`key`, `run_id`, `source_pdf`, `extracted_at`, `language_classified`, `source_report_type`) once and the kind-specific tail per kind. Note the "only emit when parsed" rule for `question_subject`/`question_stem`/`question_body`/`answer_minister_name`/`answer_body` (answers.py:112-121).
- `atr_linkage.jsonl` — `AtrLinkage` dataclass (atr_linkage.py:172).
- `entities/people.jsonl`, `mp_memberships.jsonl`, `committee_memberships.jsonl`, `ministerial_appointments.jsonl`, `bureaucratic_postings.jsonl` — one record per dataclass in entities.py:164-221.

Add a "Controlled vocabularies" section covering legal values for: `kind`, `house`, `qtype` (`STARRED`/`UNSTARRED`/empty), `report_type`, `presented_via` (`ls_only`/`rs_only`/`both`/`none`), `source` (the four valid sources), `extractor` (`answers_regex_v1`, `atr_linkage_v1`), `primary_kind` (`politician`/`bureaucrat`/`expert_witness`/`unknown`). Add a "Joins" section showing key relationships: `manifest.key` ↔ `answers.key`, `manifest.run_id` ↔ `_runs.run_id`, `atr_linkage.atr_key` ↔ `manifest.key`, `atr_linkage.references_report_key` ↔ another `manifest.key`, `manifest.asker_entity_ids[]` ↔ `entities/people.entity_id`.

Cross-link from `README.md` "Output files" section to `docs/SCHEMAS.md`.

**Acceptance**: `docs/SCHEMAS.md` exists; every stream listed in README's "Output files" table has a section; every field documented has all five columns filled; README "Output files" table links to `docs/SCHEMAS.md`.

### Phase 2 — Ship JSON Schemas as package data
Encode the same content as JSON Schema (Draft 2020-12) under `sansad_crawler/schemas/`. Twelve schemas:

- `manifest_qa.schema.json` — with `oneOf` branches for LS vs RS subschema (discriminated by `house` enum); both share a base requiring `key`, `run_id`, `kind == "qa"`, `house`, `title`, `date`, `qtype`, `qno`, `ministry`, `askers`, `source`, `crawled_at`.
- `manifest_committee_report.schema.json` — with `oneOf` for LS vs RS (LS has `loksabha_no`, `date_presented_ls`, `date_laid_rs`, `date_presented_speaker`; RS has only `date_presentation`).
- `runs.schema.json`
- `answers_qa_response.schema.json`, `answers_atr_response.schema.json`, `answers_dfg_recommendation.schema.json`
- `atr_linkage.schema.json`
- `entities_person.schema.json`, `entities_mp_membership.schema.json`, `entities_committee_membership.schema.json`, `entities_ministerial_appointment.schema.json`, `entities_bureaucratic_posting.schema.json`

Add helper `sansad_crawler.schemas.load(name: str) -> dict` using `importlib.resources` so consumers can do `from sansad_crawler import schemas; schemas.load("manifest_qa")`. Register `*.json` under `[tool.setuptools.package-data]` in `pyproject.toml` so the schemas ship in the wheel. Each schema has `$id` namespaced as `https://github.com/sreeramramasubramanian/sansad-crawler/schemas/v1/<name>.json` and `$schema` set to Draft 2020-12.

Add `[project.optional-dependencies] dev = ["jsonschema>=4.20", "pytest>=7"]` (or extend an existing dev extra).

**Acceptance**: `python -c "from sansad_crawler import schemas; print(sorted(schemas.list_all()))"` prints all twelve names; each schema parses as valid JSON Schema (the test in Phase 3 enforces this); `python -m build` produces a wheel that contains `sansad_crawler/schemas/manifest_qa.schema.json`.
**Depends on**: Phase 1.

### Phase 3 — Validation tests + docs/schema sync
Add `tests/test_schemas.py` enforcing three invariants:

1. **Self-validity**: every shipped schema validates against the JSON Schema 2020-12 metaschema via `jsonschema.Draft202012Validator.check_schema(...)`.
2. **Fixture validation**: every record in `examples/corpora/committees-smoke/manifest.jsonl` validates against `manifest_committee_report.schema.json`. Add a synthetic LS Q/A and RS Q/A fixture (one record each) under `tests/fixtures/manifest_qa_samples.jsonl` and validate them too. Same for `_runs.jsonl`, `answers.jsonl`, `atr_linkage.jsonl` — generate a one-record golden fixture per stream from a synthetic crawl in conftest if no smoke artefact exists.
3. **Docs ⊆ schemas**: parse `docs/SCHEMAS.md` with a tiny header-table parser; for every documented field assert it is present in the schema's `properties` (or in one of the `oneOf` branches). For every schema property assert it is documented. Failure message points to the drift.

Skip `tests/test_schemas.py` cleanly (don't fail) when `jsonschema` is not importable, but emit a `pytest.skip(...)` so CI can require it to be installed.

**Acceptance**: `pytest tests/test_schemas.py -q` passes; intentionally renaming a field in one of the schemas without updating docs causes the sync test to fail with a clear message.
**Depends on**: Phase 2.

### Phase 4 — `sansad-crawl validate` subcommand
Add a new CLI subcommand `sansad-crawl validate --out <corpus_dir>` that walks the corpus and validates every present JSONL file against the appropriate schema, printing a summary `<file>: validated N, errors M` and listing the first-N failing line numbers and JSON pointers. Exit code 0 on full success, 1 on any failure. Treat missing optional files (`atr_linkage.jsonl`, `answers.jsonl`, `entities/`) as skipped, not failed. For `manifest.jsonl` records, dispatch on `kind` and `house` to choose the right subschema.

Document the subcommand in README under "Commands". Add an integration test in `tests/test_validate_cli.py` that runs the smoke fixture through it and asserts exit 0.

**Acceptance**: `sansad-crawl validate --out examples/corpora/committees-smoke/` exits 0; corrupting one record (e.g. `qno: null` in LS Q/A) causes exit 1 with a clear pointer to the offending line.
**Depends on**: Phase 3.

### Phase 5 — Typed records (`sansad_crawler/records.py`)
Add frozen dataclasses for every record kind:

- `ManifestQaRecord` (with `house: Literal["Lok Sabha", "Rajya Sabha"]`, all LS+RS fields with `Optional[...]` for the divergent ones)
- `ManifestCommitteeReportRecord`
- `AnswerQaResponse`, `AnswerAtrResponse`, `AnswerDfgRecommendation`
- `AtrLinkageRecord`
- `RunRecord`

Each class gets `@classmethod from_dict(cls, d: dict)` that tolerates unknown keys (drops them) and missing optional keys (uses dataclass defaults). Re-export entity dataclasses (`Person`, `MpMembership`, `CommitteeMembership`, `MinisterialAppointment`, `BureaucraticPosting`) from `entities.py` for symmetry.

Schemas remain the source of truth for validation; dataclasses are convenience for typed iteration.

**Acceptance**: `from sansad_crawler.records import ManifestQaRecord; r = ManifestQaRecord.from_dict({"key":"LS|U|1|2024-01-01", ...}); r.house` works; `mypy --strict sansad_crawler/records.py` passes (best-effort, not a CI gate yet).
**Depends on**: Phase 2.

### Phase 6 — `Corpus` loader API (`sansad_crawler/corpus.py`)
Single class `Corpus(out_dir: str | Path)` exposing streaming iterators and joins:

- `.manifest_qa() -> Iterator[ManifestQaRecord]`
- `.manifest_committee_reports() -> Iterator[ManifestCommitteeReportRecord]`
- `.answers_qa() -> Iterator[AnswerQaResponse]`
- `.answers_atr() -> Iterator[AnswerAtrResponse]`
- `.answers_dfg() -> Iterator[AnswerDfgRecommendation]`
- `.atr_linkages() -> Iterator[AtrLinkageRecord]`
- `.runs() -> Iterator[RunRecord]`
- `.entities() -> EntityStore` — wraps the existing `EntityStore.load()` lazily.
- `.join_qa() -> Iterator[QaPair]` — joins `manifest_qa` + `answers_qa` on `key`, attaches resolved `Person` records for askers when entities/ exists.
- `.join_atr_chain() -> Iterator[AtrChain]` — for each ATR record, attach the referenced original report (via `atr_linkage.references_report_key`) and that report's `dfg_recommendation` answers.
- `.to_dataframe(stream: str)` — only available when `pandas` is importable; raises a clear `ImportError("pip install sansad-crawler[pandas]")` otherwise. `stream` ∈ `{"manifest_qa", "manifest_committee_reports", "answers_qa", ..., "runs"}`.

All iterators are streaming (`for line in open(...)` style) — never load whole files into memory. Add `[project.optional-dependencies] pandas = ["pandas>=2.0"]`.

Re-export from `sansad_crawler/__init__.py`: `Corpus`, all record dataclasses, `schemas`. Keep the public API list in `__all__`.

Add `tests/test_corpus_loader.py` covering: streaming iteration counts on the smoke fixture; `from_dict` tolerates an extra unknown key; `join_qa` and `join_atr_chain` produce the expected number of joined rows on a hand-crafted fixture; pandas-gated path raises a clear ImportError when pandas is absent (skipif when present).

**Acceptance**: `python -c "from sansad_crawler import Corpus; c = Corpus('examples/corpora/committees-smoke'); print(sum(1 for _ in c.manifest_committee_reports()))"` prints 5; `tests/test_corpus_loader.py` passes.
**Depends on**: Phase 5.

### Phase 7 — `sansad-crawl stats` + README quickstart + version bump
Add a `sansad-crawl stats --out <corpus_dir>` subcommand that uses the `Corpus` API to print:

- Total records by stream (manifest_qa, manifest_committee_reports, runs, answers_*, atr_linkage, entities/*).
- Manifest Q/A: counts per house, per year (from `date[:4]`), per ministry top-10.
- Manifest committee reports: counts per house, per committee_slug, per report_type.
- Answers extraction coverage: `extracted / has_pdf_path` per kind.
- Entity resolution rate: `non_null(asker_entity_ids[]) / total_askers`.
- Newest and oldest record date per stream.

Plain-text output by default; `--json` flag emits a single JSON object suitable for dashboards.

Add a "Quickstart for researchers" section to README between "Install" and "Commands":

- A six-line snippet showing `Corpus` iteration.
- A pointer to `docs/SCHEMAS.md`.
- A pointer to `examples/usage.py` (a 30-line script demonstrating manifest iteration, ATR chain join, and `to_dataframe`).

Bump `sansad_crawler/__init__.py` and `pyproject.toml` to `0.2.0`. Add a `CHANGELOG.md` with sections "Added" (schemas, Corpus, stats, validate) and "Unchanged" (crawler/extractor behaviour, manifest field set).

**Acceptance**: `sansad-crawl stats --out examples/corpora/committees-smoke/` prints a sane summary and exits 0; README quickstart snippet copy-pastes and runs against the smoke fixture; `examples/usage.py` runs without error; `__version__ == "0.2.0"`.
**Depends on**: Phase 6.

## Cross-cutting rules

1. **No emit-side changes.** This plan does not touch `sansad.py`, `committees.py`, `answers.py`, `atr_linkage.py`, `entities.py` emit code. Tests must demonstrate that the existing smoke fixture validates and loads cleanly without modification.
2. **Zero runtime deps stay zero.** Schema validation uses `jsonschema` from a `dev` extra; pandas integration is a separate `[pandas]` extra. The base install (`pip install sansad-crawler`) must continue to work with no third-party deps.
3. **Schemas are the source of truth.** `docs/SCHEMAS.md`, `records.py` dataclasses, and `Corpus` iterators all describe a subset of what the schemas allow. The Phase 3 sync test enforces docs ⊆ schemas; the Phase 5/6 tests exercise the dataclasses against schema-valid records.
4. **Every phase ends with one commit + `bd close`.** Commit messages: `feat(phase-N): <title>`, body referencing `.ai/plans/researcher-ux-data-dict-and-corpus-api.md`. Per project rules, commit locally only; do not push without explicit user request.
5. **Backward compat to v0.1.0 corpora.** Any record produced by current code (committed smoke fixture, plus a fresh smoke crawl run during Phase 7 verification) must validate. If a real-world record fails validation, treat the schema as wrong, not the crawler.
6. **Version bump only at Phase 7.** Phases 1-6 leave `__version__ == "0.1.0"`. The version flips to `0.2.0` together with CHANGELOG, README quickstart, and the `stats` subcommand.

## Manifest schema impact

None. No fields added or removed from any output stream. We only describe what is already emitted.

## Verification (run after Phase 7)

```
cd /Users/sreeramramasubramanian/Learning/sansad-crawler
.venv/bin/pip install -e ".[pdf,http,dev,pandas]"
.venv/bin/pytest -q

.venv/bin/sansad-crawl validate --out examples/corpora/committees-smoke/
.venv/bin/sansad-crawl stats    --out examples/corpora/committees-smoke/

.venv/bin/python - <<'PY'
from sansad_crawler import Corpus
c = Corpus("examples/corpora/committees-smoke")
print("manifest:", sum(1 for _ in c.manifest_committee_reports()))
print("schemas:", sorted(__import__("sansad_crawler.schemas", fromlist=["list_all"]).list_all()))
PY

.venv/bin/python examples/usage.py

# Fresh smoke crawl validation (network)
.venv/bin/sansad-crawl crawl --topic examples/topics/libraries.json --out /tmp/v02-smoke \
  --house ls --max-buckets 1 --max-records 1 --no-download
.venv/bin/sansad-crawl validate --out /tmp/v02-smoke
```

All green → tag `v0.2.0`.

## Risks / open items

1. **Field divergence between LS and RS Q/A is wider than the README implies** (LS has `uuid`/`handle`/`session`/`loksabhanumber`/`uri`/`found_via_group`; RS has `qslno`/`ses_no`/`question_text`/`answer_text`/`pdf_url_hindi`/`status`). Phase 1 must document this clearly; Phase 2 schemas use `oneOf` branches per `house` rather than a single permissive shape.
2. **`bucket_attempts[]` in `_runs.jsonl` is intentionally free-form** (runlog.py:106-111). The schema accepts `additionalProperties: true` here; documentation lists the conventional keys per crawler kind without making them required.
3. **Optional `answers.jsonl` sub-fields** (`question_subject`, `question_stem`, `question_body`, `answer_minister_name`, `answer_body`) are emitted only when parsing succeeded (answers.py:112-121). Phase 1 docs and Phase 2 schemas mark these as optional; Phase 3 sync test must not require them.
4. **`pdf_path` is relative to `out_dir`** (committees.py:375, sansad.py:287, etc.). Schema documents it as such; the Corpus loader exposes a `.absolute_pdf_path()` helper that joins it with the corpus root.
5. **`pandas` opt-in surfaces a new dependency**. We isolate it behind `[pandas]` extras and a runtime guard. Researchers who want zero-deps load via the streaming iterators.
6. **Wheel size**: shipping schemas adds ~30-50 KB. Acceptable.

## Beads bootstrap (executed during plan creation)

```
PLAN=".ai/plans/researcher-ux-data-dict-and-corpus-api.md"

P0 = bd create --type=task --priority=1 \
  --title "researcher-ux phase 0: save plan into .ai/plans/" \
  --description "Land the plan as a checked-in reference. See $PLAN." \
  --design "Create .ai/plans/researcher-ux-data-dict-and-corpus-api.md with the plan body. Commit on master." \
  --acceptance "File exists and is committed."

P1 = bd create --type=task --priority=1 \
  --title "researcher-ux phase 1: docs/SCHEMAS.md data dictionary" \
  --description "Walk every emit site and produce a single Markdown reference covering manifest.jsonl, _runs.jsonl, answers.jsonl, atr_linkage.jsonl, and entities/*.jsonl. See $PLAN section Phase 1." \
  --design "Sections per stream with a name|type|required|enum/format|provenance table. Cover four manifest record kinds (LS Q/A, RS Q/A, LS committee, RS committee) separately because LS and RS Q/A diverge in fields. Document conventional bucket_attempts[] keys per crawler kind. Add Controlled Vocabularies and Joins sections. Cross-link from README Output files table." \
  --acceptance "docs/SCHEMAS.md exists; every stream from README's Output files table has a section; every documented field has all five columns; README links to it." \
  --notes "Field provenance lives at sansad.py:256/399, committees.py:343/462, runlog.py:181, answers.py:540/589, atr_linkage.py:250, entities.py:282-293."

P2 = bd create --type=task --priority=1 \
  --title "researcher-ux phase 2: ship JSON Schemas as package data" \
  --description "Encode the data dictionary as twelve JSON Schema (Draft 2020-12) files under sansad_crawler/schemas/, register as package-data, expose via importlib.resources helper. See $PLAN section Phase 2." \
  --design "Twelve schemas: manifest_qa, manifest_committee_report (each with oneOf per house), runs, three answers_* (qa_response/atr_response/dfg_recommendation), atr_linkage, five entities_*. Each has $id and $schema. Add sansad_crawler.schemas.load(name) and list_all() helpers. Register schemas/*.json in pyproject [tool.setuptools.package-data]. Add jsonschema to dev extras." \
  --acceptance "from sansad_crawler import schemas; schemas.list_all() returns all twelve; python -m build wheel includes schemas/*.json." \
  --notes "Depends on P1. No emit-side code changes."

P3 = bd create --type=task --priority=1 \
  --title "researcher-ux phase 3: schema validation + docs sync tests" \
  --description "Add tests/test_schemas.py enforcing self-validity, fixture validation, and docs-schemas sync. See $PLAN section Phase 3." \
  --design "Three invariants: (a) Draft202012Validator.check_schema for every shipped schema; (b) every record in examples/corpora/committees-smoke/manifest.jsonl validates against manifest_committee_report; add tests/fixtures/ samples for the other streams; (c) parse docs/SCHEMAS.md tables and assert documented fields == schema properties (per oneOf branch). Skip cleanly when jsonschema is unavailable." \
  --acceptance "pytest tests/test_schemas.py -q passes; intentionally renaming a schema field without updating docs fails the sync test with a pointed message." \
  --notes "Depends on P2."

P4 = bd create --type=task --priority=2 \
  --title "researcher-ux phase 4: sansad-crawl validate CLI subcommand" \
  --description "Add a CLI subcommand that walks a corpus dir and validates every JSONL against its schema. See $PLAN section Phase 4." \
  --design "sansad-crawl validate --out <dir>. Dispatch manifest records on kind+house. Skip optional missing files. Print per-file summary; list first-N failures with line numbers and JSON pointers. Exit 1 on any failure. Document in README. Add tests/test_validate_cli.py covering smoke fixture and a corrupted record." \
  --acceptance "sansad-crawl validate --out examples/corpora/committees-smoke/ exits 0; corrupting one record produces exit 1 with a pointer." \
  --notes "Depends on P3."

P5 = bd create --type=task --priority=1 \
  --title "researcher-ux phase 5: typed records (sansad_crawler/records.py)" \
  --description "Add frozen dataclasses for every record kind so the loader API can return typed objects. See $PLAN section Phase 5." \
  --design "Dataclasses: ManifestQaRecord, ManifestCommitteeReportRecord, AnswerQaResponse/Atr/Dfg, AtrLinkageRecord, RunRecord. Each has from_dict() that tolerates unknown keys and missing optionals. Re-export entity dataclasses from entities.py for symmetry. Schemas remain the validation source of truth." \
  --acceptance "from sansad_crawler.records import ManifestQaRecord; ManifestQaRecord.from_dict({...}) works; round-trip through asdict matches input on a clean record." \
  --notes "Depends on P2 (schemas inform field lists). Independent of P3/P4."

P6 = bd create --type=task --priority=1 \
  --title "researcher-ux phase 6: Corpus loader API + tests" \
  --description "Add sansad_crawler/corpus.py with Corpus class exposing streaming iterators and join helpers. See $PLAN section Phase 6." \
  --design "Corpus(out_dir) with: manifest_qa(), manifest_committee_reports(), answers_qa()/atr()/dfg(), atr_linkages(), runs(), entities() (lazy EntityStore wrap), join_qa() (manifest+answers+askers), join_atr_chain() (atr->original->dfg observations), to_dataframe(stream) gated on pandas. Streaming, never load entire JSONL into memory. Add [pandas] extras. Re-export from __init__.py with __all__. Tests: streaming counts on smoke fixture; from_dict tolerates extras; join correctness on hand-crafted fixture; pandas-gated ImportError path." \
  --acceptance "python -c 'from sansad_crawler import Corpus; print(sum(1 for _ in Corpus(\"examples/corpora/committees-smoke\").manifest_committee_reports()))' prints 5; tests/test_corpus_loader.py passes." \
  --notes "Depends on P5."

P7 = bd create --type=task --priority=2 \
  --title "researcher-ux phase 7: stats CLI + README quickstart + v0.2.0" \
  --description "Add sansad-crawl stats subcommand, README researcher quickstart, examples/usage.py, CHANGELOG, and bump to 0.2.0. See $PLAN section Phase 7." \
  --design "stats subcommand prints counts by stream/house/year/ministry/committee/report_type, answers extraction coverage, entity resolution rate, newest+oldest dates. --json flag for machine output. README gets a Quickstart for researchers section between Install and Commands. examples/usage.py demonstrates manifest iteration, ATR chain join, to_dataframe. CHANGELOG.md added with v0.2.0 entry. __version__ and pyproject version bumped." \
  --acceptance "sansad-crawl stats --out examples/corpora/committees-smoke/ exits 0 with sane summary; README snippet copy-pastes and runs; __version__ == 0.2.0." \
  --notes "Depends on P6. No tag push without explicit user request."
```

After running the bd create block and wiring deps, `bd ready` should surface only P0; closing it unblocks P1 and P5 (after P2). Each phase ends with one focused commit and `bd close`.

## Stop point — wait for user approval before Phase 1

After the bd creates above are run and dependencies wired, do not start Phase 1. Pause and report:

1. The eight bead ids (P0…P7).
2. `bd list --status=open` showing titles, ids, and priorities.
3. `bd ready` (should be P0 only).
4. `bd show $P0` so the user can verify shape on a representative bead.

Then wait for explicit approval before claiming P0 and beginning work. Do not proceed past this checkpoint without confirmation, even if the beads look obviously fine.
