"""Corpus health statistics for the `sansad-crawl stats` subcommand.

Walks all JSONL streams in a corpus directory and prints a structured
summary covering record counts, distribution by house/year/ministry/
committee/report_type, answers extraction coverage, and entity resolution
rate.  Uses the :class:`~sansad_crawler.corpus.Corpus` streaming iterators
so memory stays O(1) per record (counters are accumulated, not buffered).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Callable


def _top(counter: Counter, n: int = 10) -> list[tuple[str, int]]:
    return counter.most_common(n)


def compute_stats(out_dir: Path) -> dict[str, Any]:
    """Walk a corpus directory and return a stats dict."""
    from .corpus import Corpus
    c = Corpus(out_dir)

    stats: dict[str, Any] = {
        "corpus_dir": str(out_dir),
        "manifest_qa": {},
        "manifest_committee_reports": {},
        "runs": {},
        "answers": {},
        "atr_linkage": {},
        "entities": {},
    }

    # --- manifest Q/A ---
    qa_total = 0
    qa_by_house: Counter = Counter()
    qa_by_year: Counter = Counter()
    qa_by_ministry: Counter = Counter()
    qa_with_pdf = 0
    qa_askers_total = 0
    qa_askers_resolved = 0
    qa_dates: list[str] = []

    for r in c.manifest_qa():
        qa_total += 1
        qa_by_house[r.house] += 1
        year = (r.date or "")[:4]
        if year:
            qa_by_year[year] += 1
        if r.ministry:
            qa_by_ministry[r.ministry.upper()] += 1
        if r.pdf_path:
            qa_with_pdf += 1
        if r.askers:
            qa_askers_total += len(r.askers)
        if r.asker_entity_ids:
            qa_askers_resolved += sum(1 for eid in r.asker_entity_ids if eid is not None)
        if r.date:
            qa_dates.append(r.date)

    stats["manifest_qa"] = {
        "total": qa_total,
        "by_house": dict(qa_by_house),
        "by_year_top10": dict(_top(qa_by_year)),
        "by_ministry_top10": dict(_top(qa_by_ministry)),
        "with_pdf": qa_with_pdf,
        "oldest_date": min(qa_dates) if qa_dates else None,
        "newest_date": max(qa_dates) if qa_dates else None,
        "entity_resolution_rate": (
            round(qa_askers_resolved / qa_askers_total, 3) if qa_askers_total else None
        ),
    }

    # --- manifest committee reports ---
    cr_total = 0
    cr_by_house: Counter = Counter()
    cr_by_committee: Counter = Counter()
    cr_by_report_type: Counter = Counter()
    cr_by_year: Counter = Counter()
    cr_with_pdf = 0
    cr_dates: list[str] = []

    for r in c.manifest_committee_reports():
        cr_total += 1
        cr_by_house[r.house] += 1
        cr_by_committee[r.committee_slug] += 1
        cr_by_report_type[r.report_type or "unknown"] += 1
        year = (r.date or "")[:4]
        if year:
            cr_by_year[year] += 1
        if r.pdf_path:
            cr_with_pdf += 1
        if r.date:
            cr_dates.append(r.date)

    stats["manifest_committee_reports"] = {
        "total": cr_total,
        "by_house": dict(cr_by_house),
        "by_report_type": dict(cr_by_report_type),
        "by_committee_top10": dict(_top(cr_by_committee)),
        "by_year_top10": dict(_top(cr_by_year)),
        "with_pdf": cr_with_pdf,
        "oldest_date": min(cr_dates) if cr_dates else None,
        "newest_date": max(cr_dates) if cr_dates else None,
    }

    # --- runs ---
    runs_total = 0
    runs_added_total = 0
    runs_errors_total = 0
    for r in c.runs():
        runs_total += 1
        runs_added_total += r.added or 0
        runs_errors_total += len(r.errors or [])

    stats["runs"] = {
        "total": runs_total,
        "total_added": runs_added_total,
        "total_errors": runs_errors_total,
    }

    # --- answers ---
    qa_answers = 0
    atr_answers = 0
    dfg_answers = 0
    answers_path = out_dir / "answers.jsonl"
    if answers_path.exists():
        for d in _iter_jsonl(answers_path):
            k = d.get("kind")
            if k == "qa_response":
                qa_answers += 1
            elif k == "atr_response":
                atr_answers += 1
            elif k == "dfg_recommendation":
                dfg_answers += 1

    total_with_pdf = qa_with_pdf + cr_with_pdf
    total_answers = qa_answers + atr_answers + dfg_answers
    stats["answers"] = {
        "qa_response": qa_answers,
        "atr_response": atr_answers,
        "dfg_recommendation": dfg_answers,
        "total": total_answers,
        "extraction_coverage": (
            round(total_answers / total_with_pdf, 3) if total_with_pdf else None
        ),
    }

    # --- atr_linkage ---
    atr_total = 0
    atr_linked = 0
    for r in c.atr_linkages():
        atr_total += 1
        if r.references_report_key:
            atr_linked += 1

    stats["atr_linkage"] = {
        "total": atr_total,
        "with_resolved_key": atr_linked,
        "linkage_rate": round(atr_linked / atr_total, 3) if atr_total else None,
    }

    # --- entities ---
    entities_dir = out_dir / "entities"
    entity_counts: dict[str, int] = {}
    for fname in ["people.jsonl", "mp_memberships.jsonl",
                  "committee_memberships.jsonl", "ministerial_appointments.jsonl",
                  "bureaucratic_postings.jsonl"]:
        ep = entities_dir / fname
        if ep.exists():
            entity_counts[fname] = sum(1 for _ in _iter_jsonl(ep))
    stats["entities"] = entity_counts

    return stats


def _iter_jsonl(path: Path):
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def print_stats(stats: dict, *, json_output: bool = False) -> None:
    """Print stats in human-readable or JSON format."""
    if json_output:
        print(json.dumps(stats, indent=2, ensure_ascii=False))
        return

    print(f"Corpus: {stats['corpus_dir']}")
    print()

    def _section(title: str, d: dict) -> None:
        print(f"── {title}")
        for k, v in d.items():
            if isinstance(v, dict):
                print(f"   {k}:")
                for kk, vv in list(v.items())[:10]:
                    print(f"     {kk}: {vv}")
            else:
                print(f"   {k}: {v}")
        print()

    if stats["manifest_qa"]["total"]:
        _section("Q/A records (manifest)", stats["manifest_qa"])

    if stats["manifest_committee_reports"]["total"]:
        _section("Committee reports (manifest)", stats["manifest_committee_reports"])

    if stats["runs"]["total"] or stats["runs"]["total_added"]:
        _section("Crawl runs", stats["runs"])

    if stats["answers"]["total"]:
        _section("Extracted answers", stats["answers"])

    if stats["atr_linkage"]["total"]:
        _section("ATR linkages", stats["atr_linkage"])

    if stats["entities"]:
        _section("Entities", stats["entities"])

    if not any([
        stats["manifest_qa"]["total"],
        stats["manifest_committee_reports"]["total"],
    ]):
        print("(corpus is empty — no manifest.jsonl found or no records)")
