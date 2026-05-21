"""High-level corpus loader.

Provides :class:`Corpus` — a single class that wraps a corpus directory
produced by ``sansad-crawl`` and exposes typed streaming iterators and
join helpers.

All iterators are **streaming**: they read one line at a time and never
load the entire JSONL file into memory.  This means they can safely be
used on large corpora without exhausting RAM.

Usage::

    from sansad_crawler import Corpus

    c = Corpus("data/libraries")

    # Iterate manifest records
    for r in c.manifest_qa():
        print(r.house, r.title)

    for r in c.manifest_committee_reports():
        print(r.committee_slug, r.report_type, r.date)

    # Join manifest + extracted answers
    for pair in c.join_qa():
        print(pair.manifest.key, pair.answer.question_text[:80])

    # ATR lifecycle chain
    for chain in c.join_atr_chain():
        print(chain.atr.title, "->", chain.original.title)

    # Pandas (requires pip install sansad-crawler[pandas])
    df = c.to_dataframe("manifest_committee_reports")
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Generator, Iterator

from .records import (
    AtrLinkageRecord,
    AnswerAtrResponse,
    AnswerDfgRecommendation,
    AnswerQaResponse,
    ManifestCommitteeReportRecord,
    ManifestQaRecord,
    RunRecord,
)

if TYPE_CHECKING:
    from .entities import EntityStore


def _iter_jsonl(path: Path):
    """Yield parsed dicts from a JSONL file, skipping blank/malformed lines."""
    if not path.exists():
        return
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


# ---------------------------------------------------------------------------
# Join result types
# ---------------------------------------------------------------------------

@dataclass
class QaPair:
    """A manifest Q/A record joined with its extracted answers."""
    manifest: ManifestQaRecord
    answers: list[AnswerQaResponse] = field(default_factory=list)


@dataclass
class AtrChain:
    """An ATR manifest record with its linkage, original report, and observations."""
    atr: ManifestCommitteeReportRecord
    linkage: AtrLinkageRecord | None = None
    original: ManifestCommitteeReportRecord | None = None
    atr_answers: list[AnswerAtrResponse] = field(default_factory=list)
    original_observations: list[AnswerDfgRecommendation] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Corpus class
# ---------------------------------------------------------------------------

class Corpus:
    """Load and explore a sansad-crawler corpus directory.

    Parameters
    ----------
    out_dir:
        Path to the corpus root (the directory that contains
        ``manifest.jsonl``, ``_runs.jsonl``, etc.).
    """

    def __init__(self, out_dir: str | Path) -> None:
        self.out_dir = Path(out_dir)
        self._entity_store: "EntityStore | None" = None

    # --- Manifest ---

    def manifest_qa(self) -> Iterator[ManifestQaRecord]:
        """Stream Q/A records from manifest.jsonl."""
        for d in _iter_jsonl(self.out_dir / "manifest.jsonl"):
            if d.get("kind") == "qa":
                yield ManifestQaRecord.from_dict(d)

    def manifest_committee_reports(self) -> Iterator[ManifestCommitteeReportRecord]:
        """Stream committee report records from manifest.jsonl."""
        for d in _iter_jsonl(self.out_dir / "manifest.jsonl"):
            if d.get("kind") == "committee_report":
                yield ManifestCommitteeReportRecord.from_dict(d)

    # --- Answers ---

    def answers_qa(self) -> Iterator[AnswerQaResponse]:
        """Stream qa_response records from answers.jsonl."""
        for d in _iter_jsonl(self.out_dir / "answers.jsonl"):
            if d.get("kind") == "qa_response":
                yield AnswerQaResponse.from_dict(d)

    def answers_atr(self) -> Iterator[AnswerAtrResponse]:
        """Stream atr_response records from answers.jsonl."""
        for d in _iter_jsonl(self.out_dir / "answers.jsonl"):
            if d.get("kind") == "atr_response":
                yield AnswerAtrResponse.from_dict(d)

    def answers_dfg(self) -> Iterator[AnswerDfgRecommendation]:
        """Stream dfg_recommendation records from answers.jsonl."""
        for d in _iter_jsonl(self.out_dir / "answers.jsonl"):
            if d.get("kind") == "dfg_recommendation":
                yield AnswerDfgRecommendation.from_dict(d)

    # --- Other streams ---

    def atr_linkages(self) -> Iterator[AtrLinkageRecord]:
        """Stream records from atr_linkage.jsonl."""
        for d in _iter_jsonl(self.out_dir / "atr_linkage.jsonl"):
            yield AtrLinkageRecord.from_dict(d)

    def runs(self) -> Iterator[RunRecord]:
        """Stream records from _runs.jsonl."""
        for d in _iter_jsonl(self.out_dir / "_runs.jsonl"):
            yield RunRecord.from_dict(d)

    def entities(self) -> "EntityStore":
        """Load (and cache) the entity store from entities/.

        Returns an :class:`~sansad_crawler.entities.EntityStore` with the
        in-memory index populated from disk.  If the ``entities/`` directory
        does not exist the store will be empty.
        """
        if self._entity_store is None:
            from .entities import EntityStore
            store = EntityStore(self.out_dir)
            store.load()
            self._entity_store = store
        return self._entity_store

    # --- Joins ---

    def join_qa(self) -> Iterator[QaPair]:
        """Join manifest Q/A records with their extracted answers.

        Groups ``answers.jsonl`` ``qa_response`` records by ``key`` and
        attaches them to their corresponding manifest record.  Records in
        the manifest that have no extracted answers (e.g. no PDF was
        downloaded) produce a :class:`QaPair` with an empty ``answers``
        list.

        This method loads the answers index into memory (one dict keyed by
        ``key``) before iterating manifest records, so it is O(answers)
        in memory.
        """
        # Build index: key → list of AnswerQaResponse
        idx: dict[str, list[AnswerQaResponse]] = {}
        for ans in self.answers_qa():
            idx.setdefault(ans.key, []).append(ans)

        for rec in self.manifest_qa():
            yield QaPair(manifest=rec, answers=idx.get(rec.key, []))

    def join_atr_chain(self) -> Iterator[AtrChain]:
        """Build ATR life-cycle chains.

        For each ``action_taken`` committee report in the manifest:

        1. Look up the :class:`AtrLinkageRecord` (if the linkage was extracted).
        2. Resolve ``references_report_key`` to the original report record.
        3. Attach extracted ``atr_response`` answers for the ATR.
        4. Attach ``dfg_recommendation`` answers for the original report.

        Builds three in-memory indexes (linkage, answers_atr, answers_dfg)
        and then streams through the manifest.  Memory is O(answers +
        linkages + committee-report-records), not O(total-manifest).
        """
        # Indexes
        linkage_idx: dict[str, AtrLinkageRecord] = {}
        for lnk in self.atr_linkages():
            linkage_idx[lnk.atr_key] = lnk

        atr_ans_idx: dict[str, list[AnswerAtrResponse]] = {}
        for ans in self.answers_atr():
            atr_ans_idx.setdefault(ans.key, []).append(ans)

        dfg_ans_idx: dict[str, list[AnswerDfgRecommendation]] = {}
        for ans in self.answers_dfg():
            dfg_ans_idx.setdefault(ans.key, []).append(ans)

        # Build lookup of original reports by key
        original_idx: dict[str, ManifestCommitteeReportRecord] = {}
        for rec in self.manifest_committee_reports():
            if rec.report_type != "action_taken":
                original_idx[rec.key] = rec

        for rec in self.manifest_committee_reports():
            if rec.report_type != "action_taken":
                continue
            lnk = linkage_idx.get(rec.key)
            original = None
            if lnk and lnk.references_report_key:
                original = original_idx.get(lnk.references_report_key)
            yield AtrChain(
                atr=rec,
                linkage=lnk,
                original=original,
                atr_answers=atr_ans_idx.get(rec.key, []),
                original_observations=dfg_ans_idx.get(
                    lnk.references_report_key, []
                ) if lnk and lnk.references_report_key else [],
            )

    # --- DataFrame helper ---

    _STREAM_MAP = {
        "manifest_qa": "manifest_qa",
        "manifest_committee_reports": "manifest_committee_reports",
        "answers_qa": "answers_qa",
        "answers_atr": "answers_atr",
        "answers_dfg": "answers_dfg",
        "atr_linkages": "atr_linkages",
        "runs": "runs",
    }

    def to_dataframe(self, stream: str):
        """Convert a stream to a ``pandas.DataFrame``.

        Parameters
        ----------
        stream:
            One of ``"manifest_qa"``, ``"manifest_committee_reports"``,
            ``"answers_qa"``, ``"answers_atr"``, ``"answers_dfg"``,
            ``"atr_linkages"``, ``"runs"``.

        Raises
        ------
        ImportError
            When ``pandas`` is not installed.
        KeyError
            When ``stream`` is not one of the supported names.
        """
        try:
            import pandas as pd  # type: ignore
        except ImportError:
            raise ImportError(
                "pandas is not installed. "
                "Run: pip install 'sansad-crawler[pandas]'"
            ) from None

        if stream not in self._STREAM_MAP:
            raise KeyError(
                f"Unknown stream {stream!r}. "
                f"Available: {sorted(self._STREAM_MAP)}"
            )
        method = getattr(self, self._STREAM_MAP[stream])
        from dataclasses import asdict
        records = [asdict(r) for r in method()]
        return pd.DataFrame(records)

    # --- repr ---

    def __repr__(self) -> str:
        return f"Corpus({str(self.out_dir)!r})"
