# SPDX-License-Identifier: MIT
"""Schema validation for commoner-probe corpus directories.

Used by the ``commoner-probe validate`` CLI subcommand.  Walks a corpus
``out_dir`` and validates every present JSONL file against the matching
JSON Schema.  Requires the optional ``jsonschema`` package
(``pip install commoner-probe[dev]``).

Exit behaviour:
- 0  all present files validated cleanly (or no files present)
- 1  one or more records failed validation
"""

from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

# Maximum number of individual errors to print per file before truncating.
_MAX_ERRORS_PER_FILE = 10


def _load_jsonschema():
    """Lazy import so the zero-dep install path stays clean."""
    try:
        from jsonschema import Draft202012Validator  # type: ignore
        return Draft202012Validator
    except ImportError:
        print(
            "Error: schema validation requires jsonschema — "
            "run: pip install commoner-probe[dev]",
            file=sys.stderr,
        )
        sys.exit(2)


@lru_cache(maxsize=1)
def manifest_schema_by_kind() -> dict[str, str]:
    """Map each manifest ``kind`` to its schema, read from the schemas.

    Derived, not hand-written. Every ``manifest_*`` schema pins the kinds it
    describes in ``properties.kind`` as a ``const`` or an ``enum``, so the
    mapping is already stated once, in the schema, and can be read back.

    The version this replaced was a 60-line ``if kind == ...`` chain, and a
    kind missing from it was silently skipped rather than reported. That
    combination shipped unvalidated records at least twice — commit dc06d85 is
    literally "one more unvalidated manifest kind". Each fix added one more
    branch; none removed the possibility of forgetting the next one. Derivation
    does, and ``test_every_manifest_schema_is_reachable`` proves the derivation
    finds them all.
    """
    from commoner_probe import schemas as sc

    mapping: dict[str, str] = {}
    for name in sc.list_all():
        if not name.startswith("manifest_"):
            continue
        spec = sc.load(name).get("properties", {}).get("kind") or {}
        kinds = [spec["const"]] if "const" in spec else list(spec.get("enum") or [])
        for kind in kinds:
            mapping.setdefault(kind, name)
    return mapping


def _pick_schema_name(rec: dict) -> str | None:
    """Choose the schema name for a manifest record based on kind + house."""
    return _schema_by_kind(manifest_schema_by_kind(), rec.get("kind"))


def _schema_by_kind(mapping: dict[str, str], kind: object) -> str | None:
    """Look a kind up without trusting it to be hashable.

    ``kind`` is read off disk. A record carrying a list or an object for it
    made this lookup raise ``TypeError: unhashable type`` and abort the whole
    run; an unusable kind is an unvalidatable record, which is a reported
    failure, not a crash.
    """
    return mapping.get(kind) if isinstance(kind, str) else None


def _schema_for_answers_kind(kind: object) -> str | None:
    return _schema_by_kind({
        "qa_response": "answers_qa_response",
        "atr_response": "answers_atr_response",
        "dfg_recommendation": "answers_dfg_recommendation",
        "neva_qa_response": "answers_neva_qa_response",
    }, kind)


def validate_corpus(
    out_dir: Path,
    *,
    log: Callable[[str], None] = print,
    max_errors: int = _MAX_ERRORS_PER_FILE,
) -> bool:
    """Validate all JSONL files in ``out_dir`` against their schemas.

    Returns ``True`` if everything is valid, ``False`` if any record failed.
    Missing optional files are silently skipped.
    """
    from commoner_probe import schemas as sc

    Validator = _load_jsonschema()

    any_error = False

    def _validate_file(
        path: Path,
        schema_name_for: Callable[[dict], str | None],
    ) -> tuple[bool, int, int]:
        """Validate a JSONL file.

        Returns ``(ok, validated, read)``. The caller reports both counts,
        because they used to be assumed equal: the summary line counted
        non-blank LINES, so a file whose records were every one of them
        skipped printed the same "N records — ok" as a file that was fully
        checked.

        A record whose kind has no schema is a FAILURE, not a skip. It is by
        definition unvalidated, and reporting it as validated is the exact
        confusion this function exists to prevent.
        """
        if not path.exists():
            return True, 0, 0
        validator_cache: dict[str, Any] = {}
        file_ok = True
        error_count = 0
        read = 0
        validated = 0
        truncated = False

        with path.open(encoding="utf-8") as f:
            for lineno, raw_line in enumerate(f, start=1):
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                read += 1
                try:
                    rec = json.loads(raw_line)
                except json.JSONDecodeError as exc:
                    log(f"  line {lineno}: JSON parse error — {exc}")
                    file_ok = False
                    error_count += 1
                    if error_count >= max_errors:
                        log(f"  (truncated after {max_errors} errors)")
                        truncated = True
                        break
                    continue

                sname = schema_name_for(rec)
                if sname is None:
                    log(
                        f"  line {lineno}: no schema for kind {rec.get('kind')!r} — "
                        "the record cannot be validated. Add the kind to its "
                        "manifest schema's `properties.kind`."
                    )
                    file_ok = False
                    error_count += 1
                    if error_count >= max_errors:
                        log(f"  (truncated after {max_errors} errors)")
                        truncated = True
                        break
                    continue

                if sname not in validator_cache:
                    try:
                        # Built once per schema, not once per record. Schema
                        # compilation and $ref resolution are not free, and
                        # this loop runs per line of a corpus manifest.
                        validator_cache[sname] = Validator(sc.load(sname))
                    except KeyError:
                        log(f"  line {lineno}: schema {sname!r} is not installed")
                        file_ok = False
                        error_count += 1
                        if error_count >= max_errors:
                            log(f"  (truncated after {max_errors} errors)")
                            truncated = True
                            break
                        continue

                errors = list(validator_cache[sname].iter_errors(rec))
                validated += 1
                if errors:
                    file_ok = False
                    for err in errors[:3]:
                        path_str = " -> ".join(str(p) for p in err.absolute_path) or "(root)"
                        log(f"  line {lineno} [{sname}] {path_str}: {err.message}")
                    error_count += len(errors)
                    if error_count >= max_errors:
                        log(f"  (truncated after {max_errors} errors)")
                        truncated = True
                        break

            if truncated:
                # `read` is the summary's denominator. Stopping it at the error
                # limit turned a 40-record file into "0 of 3", which reads like
                # a small clean file rather than a truncated bad one.
                read += sum(1 for line in f if line.strip())

        return file_ok, validated, read

    def _report(path: Path, schema_name_for: Callable[[dict], str | None]) -> bool:
        """Validate one file and print its one-line summary."""
        log(f"Validating {path.relative_to(out_dir)} ...")
        ok, validated, read = _validate_file(path, schema_name_for)
        log(f"  {validated} of {read} records validated — {'ok' if ok else 'FAILED'}")
        return ok

    # One file, one schema. The exceptions come first because they are the
    # only two that are not a fixed name: manifest.jsonl picks per record, and
    # answers.jsonl picks per record from a small dict.
    #
    # Everything after that used to be twelve copies of the same six lines,
    # each hardcoding a path and a schema, each accumulating `any_error`
    # separately. Adding one output file meant copying the block and editing
    # four call sites inside it correctly — the same copy-paste shape that
    # produced the forgotten-kind bug this commit also fixes. The file already
    # demonstrated the table form twice, at the bottom, for state-assembly and
    # entity outputs.
    manifest = out_dir / "manifest.jsonl"
    if manifest.exists():
        any_error |= not _report(manifest, _pick_schema_name)
    else:
        log("manifest.jsonl not found — skipping")

    answers_path = out_dir / "answers.jsonl"
    if answers_path.exists():
        any_error |= not _report(
            answers_path, lambda r: _schema_for_answers_kind(r.get("kind", ""))
        )

    fixed_schema_files = {
        "_runs.jsonl": "runs",
        "_windows.jsonl": "windows",
        "questions_list.jsonl": "question_list_row",
        "outsourcing_rows.jsonl": "outsourcing_row",
        "neva_district_rows.jsonl": "neva_district_row",
        "town_amenity_rows.jsonl": "dchb_town_amenity",
        "vacancy_rows.jsonl": "vacancy_row",
        "atr_linkage.jsonl": "atr_linkage",
        "committee_members.jsonl": "committee_members",
        # state-assembly outputs
        "questions.jsonl": "state_assembly_question",
        "questions_unlisted.jsonl": "state_assembly_question_unlisted",
        "members.jsonl": "state_assembly_member",
        "papers_laid.jsonl": "state_assembly_paper_laid",
    }
    for fname, sname in fixed_schema_files.items():
        fpath = out_dir / fname
        if fpath.exists():
            any_error |= not _report(fpath, lambda _, s=sname: s)

    entity_map = {
        "people.jsonl": "entities_person",
        "mp_memberships.jsonl": "entities_mp_membership",
        "committee_memberships.jsonl": "entities_committee_membership",
        "ministerial_appointments.jsonl": "entities_ministerial_appointment",
        "bureaucratic_postings.jsonl": "entities_bureaucratic_posting",
    }
    entities_dir = out_dir / "entities"
    if entities_dir.is_dir():
        for fname, sname in entity_map.items():
            ep = entities_dir / fname
            if ep.exists():
                any_error |= not _report(ep, lambda _, s=sname: s)

    return not any_error
