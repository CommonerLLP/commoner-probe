"""Schema validation for sansad-crawler corpus directories.

Used by the ``sansad-crawl validate`` CLI subcommand.  Walks a corpus
``out_dir`` and validates every present JSONL file against the matching
JSON Schema.  Requires the optional ``jsonschema`` package
(``pip install sansad-crawler[dev]``).

Exit behaviour:
- 0  all present files validated cleanly (or no files present)
- 1  one or more records failed validation
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable

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
            "run: pip install sansad-crawler[dev]",
            file=sys.stderr,
        )
        sys.exit(2)


def _pick_schema_name(rec: dict) -> str | None:
    """Choose the schema name for a manifest record based on kind + house."""
    kind = rec.get("kind")
    if kind == "qa":
        return "manifest_qa"
    if kind == "committee_report":
        return "manifest_committee_report"
    return None


def _schema_for_answers_kind(kind: str) -> str | None:
    return {
        "qa_response": "answers_qa_response",
        "atr_response": "answers_atr_response",
        "dfg_recommendation": "answers_dfg_recommendation",
    }.get(kind)


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
    from sansad_crawler import schemas as sc

    Validator = _load_jsonschema()

    any_error = False

    def _validate_file(
        path: Path,
        schema_name_for: Callable[[dict], str | None],
    ) -> bool:
        """Validate a JSONL file; return True if all records are clean."""
        if not path.exists():
            return True
        schema_cache: dict[str, dict] = {}
        file_ok = True
        error_count = 0

        with path.open(encoding="utf-8") as f:
            for lineno, raw_line in enumerate(f, start=1):
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    rec = json.loads(raw_line)
                except json.JSONDecodeError as exc:
                    log(f"  line {lineno}: JSON parse error — {exc}")
                    file_ok = False
                    error_count += 1
                    if error_count >= max_errors:
                        log(f"  (truncated after {max_errors} errors)")
                        break
                    continue

                sname = schema_name_for(rec)
                if sname is None:
                    continue  # unknown kind — skip, don't fail

                if sname not in schema_cache:
                    try:
                        schema_cache[sname] = sc.load(sname)
                    except KeyError:
                        continue  # schema not found — skip

                schema = schema_cache[sname]
                validator = Validator(schema)
                errors = list(validator.iter_errors(rec))
                if errors:
                    file_ok = False
                    for err in errors[:3]:
                        path_str = " -> ".join(str(p) for p in err.absolute_path) or "(root)"
                        log(f"  line {lineno} [{sname}] {path_str}: {err.message}")
                    error_count += len(errors)
                    if error_count >= max_errors:
                        log(f"  (truncated after {max_errors} errors)")
                        break

        return file_ok

    # --- manifest.jsonl ---
    manifest = out_dir / "manifest.jsonl"
    if manifest.exists():
        log(f"Validating {manifest.relative_to(out_dir)} ...")
        ok = _validate_file(manifest, _pick_schema_name)
        status = "ok" if ok else "FAILED"
        n = sum(1 for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip())
        log(f"  {n} records — {status}")
        any_error = any_error or (not ok)
    else:
        log("manifest.jsonl not found — skipping")

    # --- _runs.jsonl ---
    runs_path = out_dir / "_runs.jsonl"
    if runs_path.exists():
        log(f"Validating {runs_path.relative_to(out_dir)} ...")
        ok = _validate_file(runs_path, lambda _: "runs")
        n = sum(1 for line in runs_path.read_text(encoding="utf-8").splitlines() if line.strip())
        log(f"  {n} records — {'ok' if ok else 'FAILED'}")
        any_error = any_error or (not ok)

    # --- answers.jsonl ---
    answers_path = out_dir / "answers.jsonl"
    if answers_path.exists():
        log(f"Validating {answers_path.relative_to(out_dir)} ...")
        ok = _validate_file(answers_path, lambda r: _schema_for_answers_kind(r.get("kind", "")))
        n = sum(1 for line in answers_path.read_text(encoding="utf-8").splitlines() if line.strip())
        log(f"  {n} records — {'ok' if ok else 'FAILED'}")
        any_error = any_error or (not ok)

    # --- atr_linkage.jsonl ---
    atr_path = out_dir / "atr_linkage.jsonl"
    if atr_path.exists():
        log(f"Validating {atr_path.relative_to(out_dir)} ...")
        ok = _validate_file(atr_path, lambda _: "atr_linkage")
        n = sum(1 for line in atr_path.read_text(encoding="utf-8").splitlines() if line.strip())
        log(f"  {n} records — {'ok' if ok else 'FAILED'}")
        any_error = any_error or (not ok)

    # --- entities/*.jsonl ---
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
                log(f"Validating entities/{fname} ...")
                ok = _validate_file(ep, lambda _, s=sname: s)
                n = sum(1 for line in ep.read_text(encoding="utf-8").splitlines() if line.strip())
                log(f"  {n} records — {'ok' if ok else 'FAILED'}")
                any_error = any_error or (not ok)

    return not any_error
