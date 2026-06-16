# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cross-source evidence bundles.

Evidence bundles keep source families side by side instead of forcing unlike
records into one table. For DMFT, this means Ministry of Mines disclosure
snapshots stay separate from Sansad Q/A oversight records.
"""

from __future__ import annotations

import csv
import json
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from .corpus import Corpus

DMFT_TERMS = (
    "district mineral foundation",
    "dmf",
    "dmft",
    "pradhan mantri khanij kshetra kalyan yojana",
    "pmkkky",
    "mining affected",
)

_MOM_RECORD_TYPES = {
    "DMF_Collection.csv": "dmf_collection",
    "Project_Fund_Status_Detail.csv": "project_fund_status",
    "Sector_Wise_Project_Fund_Allocation.csv": "sector_wise_project_fund_allocation",
    "State_wise_Project_Details.csv": "state_wise_project_details",
}


def _iso_from_http_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return value
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_jsonl_by_filename(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            filename = record.get("filename")
            if filename:
                out[str(filename)] = record
    return out


def _clean_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return [
        {str(k): (v or "").strip() for k, v in row.items() if k is not None}
        for row in rows
        if any((v or "").strip() for v in row.values())
    ]


def _mom_dmft_records(mom_dir: Path) -> list[dict[str, Any]]:
    manifest = _load_jsonl_by_filename(mom_dir / "manifest.jsonl")
    records: list[dict[str, Any]] = []
    for filename, record_type in _MOM_RECORD_TYPES.items():
        path = mom_dir / filename
        if not path.exists():
            continue
        meta = manifest.get(filename, {})
        source_last_modified = _iso_from_http_date(
            meta.get("source_last_modified") or meta.get("last_modified")
        )
        for row_number, row in enumerate(_clean_csv_rows(path), start=1):
            records.append(
                {
                    "source": "mines.gov.in",
                    "record_type": record_type,
                    "filename": filename,
                    "row_number": row_number,
                    "url": meta.get("url"),
                    "source_last_modified": source_last_modified,
                    "sha256": meta.get("sha256"),
                    "period_kind": "cumulative_snapshot",
                    "data_period": None,
                    "row": row,
                }
            )
    return records


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    lower = text.lower()
    return any(term in lower for term in terms)


def _sansad_dmft_records(
    sansad_dir: Path,
    *,
    ministry: str,
    terms: tuple[str, ...],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    ministry_needle = ministry.upper()
    for pair in Corpus(sansad_dir).join_qa():
        manifest = pair.manifest
        if ministry_needle not in (manifest.ministry or "").upper():
            continue
        answer_payloads = [
            {
                "source_pdf": answer.source_pdf,
                "extracted_at": answer.extracted_at,
                "question_text": answer.question_text,
                "answer_text": answer.answer_text,
                "confidence": answer.confidence,
                "extractor": answer.extractor,
            }
            for answer in pair.answers
        ]
        haystack = " ".join(
            [
                manifest.title or "",
                manifest.ministry or "",
                *(a["question_text"] for a in answer_payloads),
                *(a["answer_text"] for a in answer_payloads),
            ]
        )
        if not _contains_any(haystack, terms):
            continue
        records.append(
            {
                "source": "sansad.in",
                "key": manifest.key,
                "house": manifest.house,
                "date": manifest.date,
                "qtype": manifest.qtype,
                "qno": manifest.qno,
                "title": manifest.title,
                "ministry": manifest.ministry,
                "askers": manifest.askers,
                "pdf_url": manifest.pdf_url,
                "answers": answer_payloads,
            }
        )
    return records


def build_dmft_evidence_bundle(
    *,
    mom_dir: str | Path,
    sansad_dir: str | Path | None = None,
    ministry: str = "MINES",
    terms: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Build a DMFT evidence bundle from MoM disclosure and Sansad Q/A corpora."""
    mom_path = Path(mom_dir)
    query_terms = terms or DMFT_TERMS
    sansad_records: list[dict[str, Any]] = []
    if sansad_dir is not None:
        sansad_records = _sansad_dmft_records(
            Path(sansad_dir),
            ministry=ministry,
            terms=tuple(term.lower() for term in query_terms),
        )
    return {
        "query": {
            "topic": "dmft",
            "ministry": ministry,
            "terms": list(query_terms),
        },
        "executive_disclosure": {
            "source": "mines.gov.in",
            "period_kind": "cumulative_snapshot",
            "data_period": None,
            "records": _mom_dmft_records(mom_path),
        },
        "parliamentary_oversight": {
            "source": "sansad.in",
            "ministry": ministry,
            "records": sansad_records,
        },
    }
