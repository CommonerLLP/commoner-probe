"""Schema validation and docs/schemas sync tests.

Three invariants:

1. Self-validity: every shipped schema is itself a valid JSON Schema
   (Draft 2020-12 metaschema).
2. Fixture validation: synthetic one-record samples for each stream
   validate against the corresponding schema.  Also drives the
   committee crawler against the frozen raw fixtures (which produces
   records WITH run_id, unlike the scrubbed smoke manifest.jsonl) and
   validates those.
3. Docs ⊆ schemas sync: for each schema, every field documented in
   docs/SCHEMAS.md is present in the schema's properties (or in a
   oneOf/allOf branch), and vice-versa.  Fails loudly when they drift.

Skip cleanly when ``jsonschema`` is not installed.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_DOC = ROOT / "docs" / "SCHEMAS.md"

try:
    import jsonschema
    from jsonschema import Draft202012Validator
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

pytestmark = pytest.mark.skipif(
    not HAS_JSONSCHEMA,
    reason="jsonschema not installed — pip install sansad-crawler[dev]",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _all_properties(schema: dict) -> set[str]:
    """Collect all property keys from a schema, including oneOf / allOf branches."""
    props: set[str] = set(schema.get("properties", {}).keys())
    for branch_key in ("oneOf", "anyOf", "allOf"):
        for branch in schema.get(branch_key, []):
            props |= _all_properties(branch)
    return props


def _parse_docs_fields(schema_name: str) -> set[str]:
    """Parse docs/SCHEMAS.md and return the set of field names documented
    for the given schema.

    Only collects from field tables (those whose header row has "Field" as
    the first column), skipping sub-tables (bucket_attempts conventions use
    "Key" as first column) and vocabulary tables (enum cells start with
    a double-quote character).
    """
    text = SCHEMAS_DOC.read_text(encoding="utf-8")

    # Map schema name → keyword(s) to look for in section headings.
    _HEADING_KEYWORDS: dict[str, list[str]] = {
        "manifest_qa": ["Shape A", "Shape B"],
        "manifest_committee_report": ["Shape C", "Shape D"],
        "runs": ["_runs.jsonl"],
        "answers_qa_response": ['kind = "qa_response"'],
        "answers_atr_response": ['kind = "atr_response"'],
        "answers_dfg_recommendation": ['kind = "dfg_recommendation"'],
        "atr_linkage": ["atr_linkage.jsonl"],
        "entities_person": ["entities/people.jsonl"],
        "entities_mp_membership": ["entities/mp_memberships.jsonl"],
        "entities_committee_membership": ["entities/committee_memberships.jsonl"],
        "entities_ministerial_appointment": ["entities/ministerial_appointments.jsonl"],
        "entities_bureaucratic_posting": ["entities/bureaucratic_postings.jsonl"],
    }
    keywords = _HEADING_KEYWORDS.get(schema_name, [schema_name])

    # Split into sections at heading boundaries.
    sections = re.split(r"\n(?=#{1,4} )", text)

    fields: set[str] = set()
    for section in sections:
        heading_line = section.split("\n", 1)[0]
        if not any(kw.lower() in heading_line.lower() for kw in keywords):
            continue

        # Walk the section line by line. Identify field tables as those
        # whose header row first column is "Field" (plain text, no backticks)
        # — that is the 5-column schema table. Sub-tables (bucket_attempts
        # conventions) use "Key" as their first column header. We detect
        # the header row by looking at all cells, not just the first one.
        in_field_table = False
        for line in section.splitlines():
            if not line.startswith("|"):
                in_field_table = False
                continue
            cols = [c.strip() for c in line.split("|")]
            if len(cols) < 3:
                continue
            first = cols[1].strip()
            second = cols[2].strip() if len(cols) > 2 else ""
            # Skip separator rows like |---|---|
            if re.fullmatch(r"[-: ]+", first):
                continue
            # Detect table header row: first col is literally "Field" and
            # second col is "Type".  Must be exact (no backticks) to avoid
            # matching data rows like `| \`field\` | ... |`.
            if first == "Field" and second == "Type":
                in_field_table = True
                continue
            # Any other plain-text first column that reads "Key", "Column",
            # "Name", or "Value" flags a different (non-field) table.
            if first in ("Key", "Column", "Name", "Value") and second == "Type":
                in_field_table = False
                continue
            if not in_field_table:
                continue
            # Skip vocabulary enum rows whose first column starts with "
            raw_first = re.sub(r"^`+", "", first)
            if raw_first.startswith('"'):
                continue
            # Extract the field name from the first backtick-wrapped token.
            m = re.search(r"`([^`]+)`", first)
            if m:
                fields.add(m.group(1))
    return fields


def _load_schema(name: str) -> dict:
    from sansad_crawler import schemas
    return schemas.load(name)


def _validate(instance: Any, schema: dict) -> None:
    Draft202012Validator(schema).validate(instance)


# ---------------------------------------------------------------------------
# Invariant 1 — self-validity
# ---------------------------------------------------------------------------

def test_all_schemas_are_valid_json_schema():
    from sansad_crawler import schemas
    meta = Draft202012Validator.META_SCHEMA
    for name in schemas.list_all():
        s = schemas.load(name)
        Draft202012Validator.check_schema(s)  # raises SchemaError on failure


# ---------------------------------------------------------------------------
# Synthetic one-record fixtures
# ---------------------------------------------------------------------------

# Minimal valid instances for each schema.
_LS_QA = {
    "key": "LS|S|1|2024-01-15",
    "run_id": "abcdef1234567890abcdef1234567890",
    "kind": "qa",
    "house": "Lok Sabha",
    "uuid": "uuid-1234",
    "handle": "123456789/1",
    "title": "Status of Libraries",
    "date": "2024-01-15",
    "qtype": "STARRED",
    "qno": "1",
    "session": "260",
    "loksabhanumber": "18",
    "ministry": "EDUCATION",
    "askers": ["Shri Test MP"],
    "asker_details": [{"name": "Test MP", "party": "INC", "party_name": "Indian National Congress", "house": "Lok Sabha"}],
    "asker_entity_ids": [None],
    "responder_entity_id": None,
    "responder_role_at_event": None,
    "uri": "http://hdl.handle.net/123456789/1",
    "source": "elibrary.sansad.in",
    "found_via_group": "public_libraries",
    "found_via_query": "public library",
    "crawled_at": "2024-01-15T10:00:00",
    "language_classified": ["en"],
}

_RS_QA = {
    "key": "RS|U|42|2024-01-15",
    "run_id": "abcdef1234567890abcdef1234567890",
    "kind": "qa",
    "house": "Rajya Sabha",
    "qslno": "42",
    "ses_no": 260,
    "title": "Status of Rural Libraries",
    "date": "2024-01-15",
    "qtype": "UNSTARRED",
    "qno": "42",
    "ministry": "EDUCATION",
    "askers": ["Shri RS MP"],
    "asker_details": [{"name": "RS MP", "party": "BJP"}],
    "asker_entity_ids": [None],
    "responder_entity_id": None,
    "responder_role_at_event": None,
    "question_text": "Will the Minister state...",
    "answer_text": "The Minister states...",
    "pdf_url": None,
    "pdf_url_hindi": None,
    "source": "rsdoc.nic.in",
    "found_via_query": "EDUCATION",
    "status": "answered",
    "crawled_at": "2024-01-15T10:00:00",
    "language_classified": ["en"],
}

_LS_COMMITTEE = {
    "key": "LS|finance|35|18",
    "run_id": "abcdef1234567890abcdef1234567890",
    "kind": "committee_report",
    "house": "Lok Sabha",
    "report_type": "demands_for_grants",
    "presented_via": "both_houses",
    "committee_slug": "finance",
    "committee_name": "Finance",
    "report_no": 35,
    "loksabha_no": 18,
    "title": "Demands for Grants 2026-27",
    "title_hindi": None,
    "language_classified": ["en"],
    "date": "2026-03-17",
    "date_presented_ls": "2026-03-17",
    "date_laid_rs": "2026-03-17",
    "date_presented_speaker": "",
    "date_adoption": "",
    "pdf_url": "https://sansad.in/getFile/app/lsscommittee/Finance/18_Finance_35.pdf",
    "pdf_url_hindi": None,
    "source": "sansad.in/api_ls/committee",
    "crawled_at": "2026-03-17T12:00:00",
}

_RS_COMMITTEE = {
    "key": "RS|health|174",
    "run_id": "abcdef1234567890abcdef1234567890",
    "kind": "committee_report",
    "house": "Rajya Sabha",
    "report_type": "demands_for_grants",
    "presented_via": "rs_only",
    "committee_slug": "health",
    "committee_name": "Health and Family Welfare",
    "report_no": 174,
    "title": "174th Report on Demands for Grants 2026-27",
    "title_hindi": None,
    "language_classified": ["en"],
    "date": "2026-03-18",
    "date_presentation": "2026-03-18",
    "date_adoption": "2026-03-18",
    "pdf_url": "https://sansad.in/getFile/rsnew/report.pdf",
    "pdf_url_hindi": None,
    "source": "sansad.in/api_rs/committee",
    "crawled_at": "2026-03-18T12:00:00",
}

_RUN = {
    "run_id": "abcdef1234567890abcdef1234567890",
    "kind": "committee_report",
    "scope": {"house": "ls", "from_date": None, "to_date": None},
    "topic_name": "libraries",
    "topic_path": "examples/topics/libraries.json",
    "topic_hash": "sha256:" + "a" * 64,
    "classifier_mode": "",
    "classifier_config_redacted": {},
    "tool_version": "0.1.0",
    "started_at": "2024-01-15T10:00:00",
    "ended_at": "2024-01-15T10:01:00",
    "added": 2,
    "errors": [],
    "bucket_attempts": [],
    "elapsed_ms": 1234.5,
}

_ANSWERS_QA = {
    "key": "LS|S|1|2024-01-15",
    "run_id": "abcdef1234567890abcdef1234567890",
    "source_pdf": "pdfs/ls/S1_abc123.pdf",
    "extracted_at": "2024-01-15T10:00:00Z",
    "language_classified": ["en"],
    "source_report_type": None,
    "kind": "qa_response",
    "question_text": "What is the status of public libraries?",
    "answer_text": "The Minister states that libraries are well-funded.",
    "confidence": 0.85,
    "extractor": "answers_regex_v1",
    "boundary_marker": "REPLY",
}

_ANSWERS_ATR = {
    "key": "LS|finance|35|18",
    "run_id": "abcdef1234567890abcdef1234567890",
    "source_pdf": "pdfs/ls/finance_18_35.pdf",
    "extracted_at": "2024-01-15T10:00:00Z",
    "language_classified": ["en"],
    "source_report_type": "action_taken",
    "kind": "atr_response",
    "recommendation_no": 1,
    "recommendation_text": "The committee recommends increased funding.",
    "response_text": "The Ministry has allocated funds.",
    "confidence": 0.9,
    "extractor": "answers_regex_v1",
}

_ANSWERS_DFG = {
    "key": "LS|finance|35|18",
    "run_id": "abcdef1234567890abcdef1234567890",
    "source_pdf": "pdfs/ls/finance_18_35.pdf",
    "extracted_at": "2024-01-15T10:00:00Z",
    "language_classified": ["en"],
    "source_report_type": "demands_for_grants",
    "kind": "dfg_recommendation",
    "recommendation_no": 1,
    "recommendation_text": "The committee notes the budgetary allocation is insufficient.",
    "confidence": 0.8,
    "extractor": "answers_regex_v1",
}

_ATR_LINKAGE = {
    "atr_key": "LS|finance|35|18",
    "atr_no": 35,
    "house": "Lok Sabha",
    "committee_slug": "finance",
    "atr_title": "Action Taken Report on the 24th Report of the Finance Committee",
    "references_report_no": 24,
    "references_report_key": "LS|finance|24|18",
    "extracted_at": "2024-01-15T10:00:00",
    "extractor": "atr_linkage_v1",
}

_ENTITY_PERSON = {
    "entity_id": "PERSON_abcdef01_test_person",
    "canonical_name": "test person",
    "alt_names": ["Test Person", "Shri Test Person"],
    "primary_kind": "politician",
    "first_seen_at": "2024-01-15T10:00:00",
    "last_updated_at": "2024-01-15T10:00:00",
}

_ENTITY_MP = {
    "entity_id": "PERSON_abcdef01_test_person",
    "house": "ls",
    "term": 18,
    "party": "BJP",
    "party_name": "Bharatiya Janata Party",
    "state": "UP",
    "constituency": "Varanasi",
    "start": "2024-06-01",
    "end": None,
    "fetched_at": "2024-01-15T10:00:00",
}

_ENTITY_COMMITTEE_MEM = {
    "entity_id": "PERSON_abcdef01_test_person",
    "committee_slug": "finance",
    "house": "ls",
    "role": "member",
    "term": 18,
    "start": "2024-06-01",
    "end": None,
    "fetched_at": "2024-01-15T10:00:00",
}

_ENTITY_MINISTERIAL = {
    "entity_id": "PERSON_abcdef01_test_person",
    "ministry": "Finance",
    "rank": "cabinet",
    "start": "2024-06-01",
    "end": None,
    "govt_period": "Modi-3",
    "fetched_at": "2024-01-15T10:00:00",
}

_ENTITY_BUREAU = {
    "entity_id": "PERSON_abcdef01_test_person",
    "designation": "Additional Secretary",
    "ministry": "Finance",
    "department": "Revenue",
    "cadre": "UP",
    "batch": 1995,
    "start": "2020-01-01",
    "end": None,
    "fetched_at": "2024-01-15T10:00:00",
}

_SCHEMA_FIXTURES: dict[str, list[dict]] = {
    "manifest_qa": [_LS_QA, _RS_QA],
    "manifest_committee_report": [_LS_COMMITTEE, _RS_COMMITTEE],
    "runs": [_RUN],
    "answers_qa_response": [_ANSWERS_QA],
    "answers_atr_response": [_ANSWERS_ATR],
    "answers_dfg_recommendation": [_ANSWERS_DFG],
    "atr_linkage": [_ATR_LINKAGE],
    "entities_person": [_ENTITY_PERSON],
    "entities_mp_membership": [_ENTITY_MP],
    "entities_committee_membership": [_ENTITY_COMMITTEE_MEM],
    "entities_ministerial_appointment": [_ENTITY_MINISTERIAL],
    "entities_bureaucratic_posting": [_ENTITY_BUREAU],
}


# ---------------------------------------------------------------------------
# Invariant 2 — fixture validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("schema_name,fixture", [
    (name, instance)
    for name, instances in _SCHEMA_FIXTURES.items()
    for instance in instances
])
def test_synthetic_fixture_validates(schema_name, fixture):
    schema = _load_schema(schema_name)
    _validate(fixture, schema)


def test_crawled_committee_records_validate():
    """Drive the committee crawler in-memory and validate each record."""
    import json as _json
    from sansad_crawler.committees import CommitteeCrawler
    from sansad_crawler.topics import load_topic

    FIXTURE_DIR = ROOT / "examples" / "corpora" / "committees-smoke"
    RAW = FIXTURE_DIR / "raw"
    TOPIC = ROOT / "examples" / "topics" / "libraries.json"
    schema = _load_schema("manifest_committee_report")

    ls_payload = _json.loads((RAW / "ls_finance_p1.json").read_text(encoding="utf-8"))
    rs_payload = _json.loads((RAW / "rs_health_p1.json").read_text(encoding="utf-8"))

    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200
        def json(self):
            return self._payload
        def raise_for_status(self):
            return None

    class _FakeSession:
        def __init__(self, routes):
            self.routes = routes
        def get(self, url, **_):
            for needle, payload in self.routes.items():
                if needle in url:
                    return _FakeResponse(payload)
            raise AssertionError(f"No route for {url}")

    routes = {"api_ls/committee": ls_payload, "api_rs/committee": rs_payload}
    topic = load_topic(TOPIC)
    records_seen = 0
    for slug, fn_name in [("finance", "crawl_ls"), ("health", "crawl_rs")]:
        with tempfile.TemporaryDirectory() as tmp:
            crawler = CommitteeCrawler(topic, Path(tmp), sleep=0, lok_sabha_no=18, topic_path=TOPIC)
            crawler.session = _FakeSession(routes)
            getattr(crawler, fn_name)(
                set(), committees=[slug],
                from_date=None, to_date=None,
                max_records=None, download=False,
            )
            for line in (Path(tmp) / "manifest.jsonl").read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = _json.loads(line)
                    _validate(rec, schema)
                    records_seen += 1
    assert records_seen > 0, "crawler produced no records — fixture may be stale"


# ---------------------------------------------------------------------------
# Invariant 3 — docs ⊆ schemas sync
# ---------------------------------------------------------------------------

# Schemas where the docs have extra context fields (common answer header)
# documented once but not in the per-kind schema — we merge those manually.
_ANSWERS_COMMON_FIELDS = {
    "key", "run_id", "source_pdf", "extracted_at",
    "language_classified", "source_report_type",
}

@pytest.mark.parametrize("schema_name", [
    "manifest_qa",
    "manifest_committee_report",
    "runs",
    "answers_qa_response",
    "answers_atr_response",
    "answers_dfg_recommendation",
    "atr_linkage",
    "entities_person",
    "entities_mp_membership",
    "entities_committee_membership",
    "entities_ministerial_appointment",
    "entities_bureaucratic_posting",
])
def test_docs_fields_match_schema_properties(schema_name):
    """Every field documented in SCHEMAS.md exists in the schema, and vice-versa."""
    doc_fields = _parse_docs_fields(schema_name)
    # For answers schemas, also include the common header fields.
    if schema_name.startswith("answers_"):
        doc_fields |= _ANSWERS_COMMON_FIELDS

    schema = _load_schema(schema_name)
    schema_props = _all_properties(schema)

    missing_from_schema = doc_fields - schema_props
    missing_from_docs = schema_props - doc_fields

    errors = []
    if missing_from_schema:
        errors.append(
            f"Fields in docs/SCHEMAS.md but NOT in schema '{schema_name}': "
            f"{sorted(missing_from_schema)}\n"
            f"  → Add them to sansad_crawler/schemas/{schema_name}.schema.json "
            f"or remove them from docs/SCHEMAS.md"
        )
    if missing_from_docs:
        errors.append(
            f"Properties in schema '{schema_name}' but NOT in docs/SCHEMAS.md: "
            f"{sorted(missing_from_docs)}\n"
            f"  → Document them in docs/SCHEMAS.md or remove from the schema"
        )
    assert not errors, "\n\n".join(errors)
