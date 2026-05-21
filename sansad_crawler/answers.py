"""Phase 1: structured text extraction from parliamentary PDFs.

Three shapes of source PDF, three extractors:

* **Q/A PDFs** (parliamentary questions): split a single
  question+answer into ``(question_text, answer_text)`` on the
  "Reply by ..." / "Answer" boundary. Output: one record per source.

* **ATR PDFs** (Action-Taken Reports — government's response to a
  prior committee report): split into
  ``[(recommendation_no, recommendation_text, response_text), ...]``
  on "Recommendation No. X" / "Reply of the Government" boundaries.
  One source PDF → many records.

* **DFG / original committee reports**: find the
  ``OBSERVATIONS/RECOMMENDATIONS OF THE COMMITTEE`` section, split on
  numbered paragraphs. Output: ``[(recommendation_no,
  recommendation_text), ...]`` — no response text because the
  executive hasn't replied yet (that arrives in a future ATR).

This module is **extraction only**. Classification (counterinsurgency
labels) is Phase 2 in ``discourse.py``.

Schema commitments for ``answers.jsonl`` are documented in the file's
header comments and in `notes/PLAN_v0.5.0_SCOPE.md`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .textparse import extract_pdf_text, read_jsonl

EXTRACTOR_VERSION = "answers_regex_v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(text: str) -> str:
    """Normalise PDF whitespace artefacts: collapse runs of spaces, strip
    page-number boilerplate, but preserve paragraph boundaries.
    """
    if not text:
        return ""
    # Strip page numbers that often appear as standalone numeric lines.
    text = re.sub(r"^\s*\d+\s*$", "", text, flags=re.MULTILINE)
    # Drop "(Para 2.15)" style cross-references — they're metadata, not text.
    text = re.sub(r"\(Para\s+\d+(?:\.\d+)*\)", "", text)
    # Collapse spaces within lines but keep newlines (paragraph structure).
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# -------------------------------------------------------------------------
# Q/A extractor
# -------------------------------------------------------------------------

# Markers indicating the boundary between question text and answer text in
# parliamentary Q/A PDFs. Order matters — earlier patterns are more
# specific and tried first.
_QA_REPLY_PATTERNS = [
    r"^\s*ANSWER\s*$",                          # Bare "ANSWER" header line
    r"^\s*REPLY\s*$",                           # Bare "REPLY"
    r"\bTO\s+BE\s+ANSWERED\s+ON\b",            # Often followed by date, then answer
    r"^\s*Reply\s+by\b.{0,200}?:",             # "Reply by [Minister name]:"
    r"^\s*Answer\s+by\b.{0,200}?:",
    r"\bSHRI\b.{0,60}\b(?:MINISTER|MOS)\b",    # "SHRI X, MINISTER OF Y"
]
_QA_REPLY_RE = re.compile(
    "|".join(f"({p})" for p in _QA_REPLY_PATTERNS),
    re.IGNORECASE | re.MULTILINE,
)


@dataclass
class QaExtraction:
    question_text: str       # Full raw question half (incl. boilerplate)
    answer_text: str         # Full raw answer half (incl. minister preamble)
    confidence: float
    extractor: str = EXTRACTOR_VERSION
    boundary_marker: str = ""
    # v0.6.5 — structured sub-fields derived from question_text/answer_text.
    # Additive: legacy consumers reading question_text / answer_text are
    # unaffected. Designed for the v0.7.0 mp-draft bridge feature, which
    # needs clean substrings (subject, body) for semantic indexing rather
    # than the noisy full PDF prelude.
    question_subject: str = ""    # e.g. "ANNUAL INCOME OF SHGS"
    question_stem: str = ""       # e.g. "Will the Minister of RURAL DEVELOPMENT be pleased to state:"
    question_body: str = ""       # The actual (a)/(b)/(c)/(d) sub-questions
    answer_minister_name: str = ""
    answer_body: str = ""         # Answer text with minister-name preamble stripped

    def to_record(self) -> dict:
        rec = {
            "kind": "qa_response",
            "question_text": self.question_text,
            "answer_text": self.answer_text,
            "confidence": self.confidence,
            "extractor": self.extractor,
            "boundary_marker": self.boundary_marker,
        }
        # Only emit structured fields when we actually parsed them, to
        # avoid lying with empty-string defaults on legacy/edge records.
        if self.question_subject:
            rec["question_subject"] = self.question_subject
        if self.question_stem:
            rec["question_stem"] = self.question_stem
        if self.question_body:
            rec["question_body"] = self.question_body
        if self.answer_minister_name:
            rec["answer_minister_name"] = self.answer_minister_name
        if self.answer_body:
            rec["answer_body"] = self.answer_body
        return rec


# -------------------------------------------------------------------------
# v0.6.5 — structured sub-extraction within Q/A halves
# -------------------------------------------------------------------------

# The "ANSWERED ON" date line is the most reliable boundary marker between
# the boilerplate header (GOVERNMENT OF INDIA / MINISTRY / DEPT / LOK SABHA
# / QUESTION NO. / ANSWERED ON DATE) and the substantive question content
# (subject line, asker, stem, body).
_ANSWERED_ON_RE = re.compile(
    r"\bANSWERED\s+ON\b\s*[:.,]?\s*\d.*?$",
    re.IGNORECASE | re.MULTILINE,
)

# The asker line: "1147. SHRI. <NAME>:" or
# "*123. SHRIMATI <NAME>:". Number + honorific + name + colon.
_ASKER_LINE_RE = re.compile(
    r"^\s*\*?\s*\d+\.?\s+(?:SHRI|SHRIMATI|SMT|DR|KUMARI|PROF)"
    r"[A-Z\.\s,]+:\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# The question stem: "Will the Minister of X be pleased to state:" or
# variants like "be pleased to refer to the answer given...". Anchored on
# "Will the Minister" so it doesn't catch other "Will" phrases.
_QUESTION_STEM_RE = re.compile(
    r"\bWill\s+the\s+Minister\b.*?:",
    re.IGNORECASE | re.DOTALL,
)

# Minister-name preamble in the answer half: "MINISTER OF STATE IN THE
# MINISTRY OF X (NAME)" or "THE MINISTER OF X: (NAME)". The name is in
# parentheses; we capture it.
#
# The bridge between "MINISTER OF X" and the captured "(NAME)" is bounded
# at ~250 chars so we don't accidentally walk into the answer body and
# capture a sub-item paren like "(a) The Ministry...". The captured name
# itself must be ≥4 chars and contain at least one space (so single-letter
# false positives like "(a)" are excluded — a real minister name is
# always at least "FIRST LAST").
_MINISTER_NAME_RE = re.compile(
    r"\b(?:THE\s+)?MINISTER\s+(?:OF\s+STATE\s+(?:IN\s+THE\s+MINISTRY\s+OF|FOR)\b|OF\s+(?!STATE)|FOR)"
    r"[^()]{0,250}?\((?P<name>[^()]{4,}?\s[^()]{1,})\)",
    re.IGNORECASE | re.DOTALL,
)


def _parse_question_subject(question_text: str) -> str:
    """Extract the all-caps subject line that appears between the
    "ANSWERED ON" header and the asker line. Returns "" if not found.
    """
    after_date = _ANSWERED_ON_RE.search(question_text)
    if not after_date:
        return ""
    body_after_date = question_text[after_date.end():]
    # Find the asker line — subject sits between ANSWERED ON and asker.
    asker_m = _ASKER_LINE_RE.search(body_after_date)
    candidate = body_after_date[: asker_m.start()] if asker_m else body_after_date[:300]
    # Subject lines are usually one all-caps phrase, possibly multi-line.
    # Strip whitespace, drop empty lines, take the longest contiguous
    # non-empty caps-or-mixed line block.
    lines = [l.strip() for l in candidate.splitlines() if l.strip()]
    if not lines:
        return ""
    # Heuristic: the subject is the line(s) before any line starting with
    # a digit (which would be the asker). Already handled by asker_m, so
    # join all surviving lines.
    return " ".join(lines).strip()[:200]


def _parse_question_stem_and_body(question_text: str) -> tuple[str, str]:
    """Split the substantive question into stem ("Will the Minister of X
    be pleased to state:") and body (the (a)/(b)/(c)/(d) sub-questions).
    Returns ("", "") if no stem found.
    """
    m = _QUESTION_STEM_RE.search(question_text)
    if not m:
        return "", ""
    stem = m.group(0).strip()
    # Body is everything after the stem.
    body = question_text[m.end():].strip()
    # Strip the trailing question-mark of the (d) sub-question only if
    # the body already ends with one — preserve question-marks within.
    return stem, body


def _parse_answer_minister_and_body(answer_text: str) -> tuple[str, str]:
    """Pull the minister's name out of the answer prelude and return the
    cleaned answer body. Returns ("", answer_text) if no preamble found.
    """
    m = _MINISTER_NAME_RE.search(answer_text)
    if not m:
        return "", answer_text
    name = (m.group("name") or "").strip()
    body = answer_text[m.end():]
    # Strip leftover punctuation immediately after the captured "(NAME)" —
    # PDFs often have ":" and/or whitespace separating the prelude from
    # the answer body. Without this, `body` starts with stray ":\n".
    body = re.sub(r"^[\s:]+", "", body)
    # Edge case: answers occasionally have *both* a State minister and a
    # Cabinet minister listed in the prelude. The regex catches the first
    # occurrence; that's fine — the second name and the answer text both
    # remain in `body`, where they're still searchable.
    return name, body


def split_qa(text: str) -> QaExtraction | None:
    """Split a Q/A PDF's full text into question + answer halves, plus
    structured sub-fields for semantic indexing.

    Returns ``None`` when no recognisable boundary marker is found. Caller
    decides what to do (skip; fall back to whole-text classification with
    lower confidence).

    The four structured sub-fields (``question_subject``, ``question_stem``,
    ``question_body``, ``answer_minister_name``, ``answer_body``) are
    best-effort: each parser returns an empty string when its anchor
    isn't found, in which case ``to_record()`` omits that field rather
    than emitting an empty placeholder. Legacy ``question_text`` and
    ``answer_text`` are always populated when the function returns a
    non-None result.
    """
    cleaned = _clean(text)
    if not cleaned:
        return None
    m = _QA_REPLY_RE.search(cleaned)
    if not m:
        return None
    question = cleaned[: m.start()].strip()
    answer = cleaned[m.end():].strip()
    if not question or not answer:
        return None
    subject = _parse_question_subject(question)
    stem, body = _parse_question_stem_and_body(question)
    minister, answer_body = _parse_answer_minister_and_body(answer)
    return QaExtraction(
        question_text=question,
        answer_text=answer,
        confidence=0.85 if len(answer) > 50 else 0.5,
        boundary_marker=m.group(0).strip(),
        question_subject=subject,
        question_stem=stem,
        question_body=body,
        answer_minister_name=minister,
        answer_body=answer_body,
    )


# -------------------------------------------------------------------------
# ATR extractor
# -------------------------------------------------------------------------

# Recommendation markers — "Recommendation No. X", "Recommendation (Sl. No. X)",
# "Observation/Recommendation No. X", or "Recommendation \n 1.".
_ATR_REC_RE = re.compile(
    r"(?:Observation\s*/\s*)?Recommendation\s*(?:\n|\s+)(?:No\.?|Sl\.?\s*No\.?|Serial\s*No\.?)\s*(\d+)"
    r"|(?:\n|^)\s*Recommendation\s*\n\s*(\d+)\.",
    re.IGNORECASE | re.MULTILINE,
)

# Reply markers — "Reply of the Government", "Action Taken by the Government",
# "Ministry's Reply", "Action Taken".
_ATR_REPLY_RE = re.compile(
    r"(?:Reply\s+of\s+the\s+Government"
    r"|Action\s+Taken\s+by\s+the\s+Government"
    r"|Action\s+Taken"
    r"|Ministry'?s\s+Reply"
    r"|Comments\s+of\s+the\s+(?:Ministry|Government))",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass
class AtrExtraction:
    recommendation_no: int
    recommendation_text: str
    response_text: str
    confidence: float
    extractor: str = EXTRACTOR_VERSION

    def to_record(self) -> dict:
        return {
            "kind": "atr_response",
            "recommendation_no": self.recommendation_no,
            "recommendation_text": self.recommendation_text,
            "response_text": self.response_text,
            "confidence": self.confidence,
            "extractor": self.extractor,
        }


def split_atr(text: str) -> list[AtrExtraction]:
    """Split an ATR PDF's text into (rec_no, rec_text, response_text) triples.

    Returns an empty list if no recommendation markers are found.
    """
    cleaned = _clean(text)
    if not cleaned:
        return []
    chunks = _ATR_REC_RE.split(cleaned)
    # split() with N capture groups: [pre, g1, g2, ..., gn, body1, g1, g2, ..., gn, body2, ...]
    num_groups = _ATR_REC_RE.groups
    stride = num_groups + 1
    if len(chunks) < stride + 1:
        return []
    out: list[AtrExtraction] = []
    i = 1
    while i < len(chunks) - stride + 1:
        rec_no_raw = next((c for c in chunks[i:i + num_groups] if c), None)
        try:
            rec_no = int(rec_no_raw) if rec_no_raw else None
        except (ValueError, TypeError):
            rec_no = None

        if rec_no is None:
            i += stride
            continue

        body = chunks[i + num_groups] or ""
        # Within the body, find the "Reply ..." boundary; everything before
        # is the recommendation, everything after is the response.
        reply_m = _ATR_REPLY_RE.search(body)
        if reply_m:
            rec_text = body[: reply_m.start()].strip()
            resp_text = body[reply_m.end():].strip()
            confidence = 0.9 if (rec_text and resp_text) else 0.5
        else:
            # Whole body becomes the recommendation; no reply found.
            rec_text = body.strip()
            resp_text = ""
            confidence = 0.4
        if rec_text:
            out.append(AtrExtraction(
                recommendation_no=rec_no,
                recommendation_text=rec_text,
                response_text=resp_text,
                confidence=confidence,
            ))
        i += stride
    return out


# -------------------------------------------------------------------------
# DFG / original committee report extractor
# -------------------------------------------------------------------------

# The recommendations section header. PDFs vary: "OBSERVATIONS/RECOMMENDATIONS
# OF THE COMMITTEE", "OBSERVATIONS / RECOMMENDATIONS", etc.
_DFG_SECTION_RE = re.compile(
    r"OBSERVATIONS\s*/\s*RECOMMENDATIONS(?:\s+OF\s+THE\s+COMMITTEE)?",
    re.IGNORECASE,
)

# Numbered paragraph: line starting with "<digit>." followed by whitespace.
# Non-line-anchored use is too greedy (matches in body text); we anchor to
# line start (after newline) and require the number-period-whitespace to
# start a new paragraph.
_DFG_PARA_RE = re.compile(r"(?:^|\n)\s*(\d+)\.\s+", re.MULTILINE)


@dataclass
class DfgExtraction:
    recommendation_no: int
    recommendation_text: str
    confidence: float
    extractor: str = EXTRACTOR_VERSION

    def to_record(self) -> dict:
        return {
            "kind": "dfg_recommendation",
            "recommendation_no": self.recommendation_no,
            "recommendation_text": self.recommendation_text,
            "confidence": self.confidence,
            "extractor": self.extractor,
        }


def split_dfg(text: str) -> list[DfgExtraction]:
    """Find the recommendations section and split into numbered paragraphs.

    DFG (Demands for Grants) reports list committee observations and
    recommendations as numbered paragraphs in a dedicated section. Returns
    an empty list if the section header isn't found.
    """
    if not text:
        return []
    # Find the LAST occurrence of the section header — the first occurrence
    # is typically a TOC entry; the actual section appears later.
    matches = list(_DFG_SECTION_RE.finditer(text))
    if not matches:
        return []
    section_start = matches[-1].end()
    section_text = text[section_start:]
    # Cap at next major section heading or end of document. Common boundaries:
    # "ANNEXURE", "MINUTES OF THE", "APPENDIX", "ADDENDUM".
    end_match = re.search(
        r"\n\s*(?:ANNEXURE|MINUTES\s+OF\s+THE|APPENDIX|ADDENDUM)\b",
        section_text,
        re.IGNORECASE,
    )
    if end_match:
        section_text = section_text[: end_match.start()]

    # Split on numbered-paragraph markers.
    chunks = _DFG_PARA_RE.split(section_text)
    if len(chunks) < 3:
        return []
    out: list[DfgExtraction] = []
    i = 1
    while i < len(chunks) - 1:
        try:
            rec_no = int(chunks[i])
        except (ValueError, TypeError):
            i += 2
            continue
        body = _clean(chunks[i + 1] or "")
        if body:
            out.append(DfgExtraction(
                recommendation_no=rec_no,
                recommendation_text=body,
                confidence=0.8 if len(body) > 80 else 0.5,
            ))
        i += 2
    return out


# -------------------------------------------------------------------------
# Corpus dispatcher
# -------------------------------------------------------------------------


@dataclass
class ExtractionStats:
    qa_records: int = 0
    atr_records: int = 0
    dfg_records: int = 0
    skipped_no_pdf: int = 0
    skipped_no_text: int = 0
    skipped_no_split: int = 0
    sources_processed: int = 0
    errors: list[dict] = field(default_factory=list)


def _classify_source(rec: dict) -> str:
    """Decide which extractor applies to a manifest record.

    Returns ``'qa'`` | ``'atr'`` | ``'observations'`` | ``'skip'``.

    The ``'observations'`` bucket covers any committee report that
    contains numbered Observations/Recommendations text — that's
    Demands for Grants reports, Bill examinations, and Subject (own-
    initiative) reports. They share a textual structure (numbered
    paragraphs in a section heading variant of OBSERVATIONS /
    RECOMMENDATIONS) so they share an extractor; the *source*
    ``report_type`` from the manifest is preserved on each output
    record as ``source_report_type`` so downstream filters can
    distinguish them.

    Prior to v0.6.3 this function returned ``'dfg'`` for everything
    non-ATR, which mislabelled subject and bill reports as DFG.
    """
    kind = rec.get("kind") or ""
    report_type = rec.get("report_type") or ""
    if kind == "qa":
        return "qa"
    if kind == "committee_report":
        if report_type == "action_taken":
            return "atr"
        # demands_for_grants | bill | subject | other (legacy "original")
        # all dispatch to the observations extractor.
        return "observations"
    return "skip"


def _pdf_for_record(rec: dict, out_dir: Path) -> Path | None:
    rel = rec.get("pdf_path")
    if not rel:
        return None
    p = out_dir / rel
    return p if p.exists() and p.stat().st_size > 1000 else None


def extract_answers(
    out_dir: Path, *, refresh: bool = False, log_fn=print
) -> ExtractionStats:
    """Walk ``manifest.jsonl``, run the right extractor per record, write
    ``answers.jsonl``. Returns stats for telemetry / CLI output.

    Idempotent: ``answers.jsonl`` is overwritten, but the input
    (``manifest.jsonl`` + downloaded PDFs) is unchanged. ``refresh=True``
    forces re-extraction; otherwise existing ``answers.jsonl`` is replaced
    with current parser output.
    """
    stats = ExtractionStats()
    manifest_path = out_dir / "manifest.jsonl"
    out_path = out_dir / "answers.jsonl"
    records = read_jsonl(manifest_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    out_records: list[dict] = []
    for rec in records:
        kind = _classify_source(rec)
        if kind == "skip":
            continue
        stats.sources_processed += 1
        pdf = _pdf_for_record(rec, out_dir)
        if not pdf:
            stats.skipped_no_pdf += 1
            continue
        try:
            text = extract_pdf_text(pdf)
        except Exception as exc:  # noqa: BLE001
            stats.errors.append({"key": rec.get("key"), "where": "pdftotext", "error": repr(exc)})
            continue
        if not text or not text.strip():
            stats.skipped_no_text += 1
            continue

        common = {
            "key": rec.get("key"),
            "run_id": rec.get("run_id"),
            "source_pdf": str(pdf.relative_to(out_dir)),
            "extracted_at": _now(),
            "language_classified": ["en"],
            # Carry the manifest's report_type forward so downstream
            # consumers can distinguish numbered observations that came
            # from a Demands-for-Grants vs Bill vs Subject report. Prior
            # to v0.6.3 this distinction was not surfaced and all non-ATR
            # records were tagged 'dfg_recommendation' regardless of source.
            "source_report_type": rec.get("report_type"),
        }

        try:
            if kind == "qa":
                qa = split_qa(text)
                if qa is None:
                    stats.skipped_no_split += 1
                    continue
                out_records.append({**common, **qa.to_record()})
                stats.qa_records += 1
            elif kind == "atr":
                items = split_atr(text)
                if not items:
                    stats.skipped_no_split += 1
                    continue
                for item in items:
                    out_records.append({**common, **item.to_record()})
                stats.atr_records += len(items)
            elif kind == "observations":
                items = split_dfg(text)
                if not items:
                    stats.skipped_no_split += 1
                    continue
                for item in items:
                    out_records.append({**common, **item.to_record()})
                stats.dfg_records += len(items)
        except Exception as exc:  # noqa: BLE001
            stats.errors.append({"key": rec.get("key"), "where": kind, "error": repr(exc)})

    # Write atomically: write to a sibling temp then rename. Use ``with_name``
    # rather than ``with_suffix`` because ``Path("answers.jsonl").with_suffix(
    # ".jsonl.tmp")`` is ambiguous across pathlib versions.
    tmp = out_path.with_name(out_path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for row in out_records:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(out_path)

    log_fn(
        f"answers.jsonl: qa={stats.qa_records} atr={stats.atr_records} "
        f"dfg={stats.dfg_records} skipped_no_pdf={stats.skipped_no_pdf} "
        f"skipped_no_text={stats.skipped_no_text} skipped_no_split={stats.skipped_no_split} "
        f"errors={len(stats.errors)}"
    )
    return stats
