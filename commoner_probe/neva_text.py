# SPDX-License-Identifier: MIT
"""Deterministic text extraction for Gujarati NeVA question/answer PDFs.

NeVA question PDFs (Gujarat Vidhan Sabha) differ from Sansad Q&A PDFs in
three ways that break ``answers.split_qa``:

1. **Language and layout.** The boundary is a two-column header
   ``પ્રશ્ન | જવાબ`` (question | answer) with the halves typeset side by
   side, so the columns interleave in flat text unless the layout's
   whitespace is used to split them. ``extract_pdf_text`` uses
   ``pdftotext -layout``, which preserves that whitespace.
2. **Gujarati numerals.** Figures arrive as ૦૧૨૩૪૫૬૭૮૯.
3. **Broken embedded fonts.** A share of the PDFs carries a damaged
   ToUnicode cmap, so the text layer comes out glyph-corrupted (e.g.
   બ→ફ, પ→઩, લ→઱, and doubled aa-matras ``ાાં``). The corruption is
   per-document and sometimes many-to-one (જ and થ both extract as િ),
   so it is NOT fully invertible: this module repairs what it can prove
   against a known-clean reference line (the portal's own metadata
   subject) and honestly reports quality — ``clean``, ``repaired``, or
   ``low`` — instead of emitting fabricated text. ``low`` documents are
   OCR-fallback candidates for downstream consumers.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

EXTRACTOR_VERSION = "neva-gu-v1"

#: The 33 districts of Gujarat, clean Unicode.
GUJARAT_DISTRICTS = (
    "અમદાવાદ", "અમરેલી", "આણંદ", "અરવલ્લી", "બનાસકાંઠા", "ભરૂચ", "ભાવનગર", "બોટાદ",
    "છોટાઉદેપુર", "દાહોદ", "ડાંગ", "દેવભૂમિ દ્વારકા", "ગાંધીનગર", "ગીર સોમનાથ", "જામનગર",
    "જૂનાગઢ", "કચ્છ", "ખેડા", "મહીસાગર", "મહેસાણા", "મોરબી", "નર્મદા", "નવસારી",
    "પંચમહાલ", "પાટણ", "પોરબંદર", "રાજકોટ", "સાબરકાંઠા", "સુરત", "સુરેન્દ્રનગર",
    "તાપી", "વડોદરા", "વલસાડ",
)

_GJ_DIGITS = str.maketrans("૦૧૨૩૪૫૬૭૮૯", "0123456789")

# The two-column header: પ્રશ્ન, a column gap, then જવાબ — whose જ (and બ)
# frequently extract corrupted (િવાબ, િવાફ, જવાફ).
_QA_HEADER_RE = re.compile(r"પ્રશ્ન\s{2,}\S{0,2}વા[બફ]")

# A question/answer item marker: (૧) / (1), possibly multi-digit.
_ITEM_MARKER_RE = re.compile(r"\((\d{1,2})\)")

# Appendix statement header (પત્રક-૧ etc.) on a line of its own.
_APPENDIX_RE = re.compile(r"^\s*પત્રક\s*[-–]?\s*\d*\s*$")

# The starred-question reference on the asker line, e.g. *15/8/3863.
_QREF_RE = re.compile(r"\*\s*(\d+(?:/\d+)+)")

_NUM_TOKEN_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

# Non-figure numerics that must not count as table cells: item markers
# ("(1)"), dates ("31/12/2025", "તા.31/12/2025ની"), question refs
# ("*15/8/3875"), and statement references ("પત્રક-1 મુજબ.").
_NON_FIGURE_RE = re.compile(r"\(\d{1,2}\)|\*?\d{1,4}(?:/\d{1,4})+|પત્રક\s*[-–]?\s*\d+")


def gujarati_digits_to_ascii(text: str) -> str:
    return text.translate(_GJ_DIGITS)


def normalize_gujarati_text(text: str) -> str:
    """Safe, unconditional normalizations for extracted NeVA text.

    - Gujarati digits → ASCII.
    - Collapse doubled aa-matras (``ાા`` never occurs in legitimate
      Gujarati; broken cmaps emit it for the ાં ligature's pieces).
    """
    text = gujarati_digits_to_ascii(text)
    while "ાા" in text:
        text = text.replace("ાા", "ા")
    return text


def derive_glyph_repair(reference: str, garbled: str) -> dict[str, str]:
    """Char-substitution map inferred by aligning a known-clean reference
    line against its garbled extraction.

    Only 1:1 same-length replacements are kept, and only when the same
    garbled character maps to a single clean character across the whole
    alignment — conflicting or lossy (many-to-one) corruption is left
    alone rather than guessed at.
    """
    mapping: dict[str, str] = {}
    conflicted: set[str] = set()
    sm = difflib.SequenceMatcher(None, garbled, reference, autojunk=False)
    for op, g1, g2, r1, r2 in sm.get_opcodes():
        if op != "replace" or (g2 - g1) != (r2 - r1):
            continue
        for gc, rc in zip(garbled[g1:g2], reference[r1:r2]):
            if gc == rc or gc in conflicted:
                continue
            if gc in mapping and mapping[gc] != rc:
                del mapping[gc]
                conflicted.add(gc)
                continue
            mapping[gc] = rc
    return mapping


def apply_glyph_repair(text: str, mapping: dict[str, str]) -> str:
    if not mapping:
        return text
    return text.translate(str.maketrans(mapping))


def _best_matching_line(reference: str, text: str) -> str | None:
    """The line of *text* most similar to *reference* (the garbled subject)."""
    best, best_ratio = None, 0.0
    for line in text.splitlines()[:8]:
        line = line.strip()
        if not line or len(line) < 4:
            continue
        ratio = difflib.SequenceMatcher(None, line, reference, autojunk=False).ratio()
        if ratio > best_ratio:
            best, best_ratio = line, ratio
    return best if best_ratio >= 0.5 else None


def repair_text(text: str, reference: str | None) -> tuple[str, str, dict[str, str]]:
    """Normalize and (where provable) glyph-repair extracted NeVA text.

    *reference* is a known-clean line that must appear in the document —
    in practice the portal metadata's ``subject``, which is printed as
    the question's title line.

    Returns ``(text, quality, mapping)`` where quality is:

    - ``clean``    — reference found verbatim after safe normalization
    - ``repaired`` — reference found only after applying a glyph map
      derived from the reference alignment itself (map applied doc-wide)
    - ``low``      — reference still absent; the text layer cannot be
      trusted for Gujarati content (OCR-fallback candidate). The
      normalized text is still returned; no repair map is applied.
    """
    text = normalize_gujarati_text(text)
    if not reference:
        return text, "unknown", {}
    reference = normalize_gujarati_text(reference).strip()
    if reference in text:
        return text, "clean", {}
    candidate = _best_matching_line(reference, text)
    if candidate:
        mapping = derive_glyph_repair(reference, candidate)
        if mapping and reference in apply_glyph_repair(text, mapping):
            return apply_glyph_repair(text, mapping), "repaired", mapping
    return text, "low", {}


@dataclass
class NevaQaExtraction:
    question_text: str
    answer_text: str
    confidence: float
    quality: str = "unknown"
    subject: str = ""
    question_ref: str = ""
    boundary_marker: str = ""
    extractor: str = EXTRACTOR_VERSION

    def to_record(self) -> dict:
        rec = {
            "kind": "neva_qa_response",
            "question_text": self.question_text,
            "answer_text": self.answer_text,
            "confidence": self.confidence,
            "quality": self.quality,
            "extractor": self.extractor,
            "boundary_marker": self.boundary_marker,
        }
        if self.subject:
            rec["question_subject"] = self.subject
        if self.question_ref:
            rec["question_ref"] = self.question_ref
        return rec


def split_qa_neva(text: str) -> NevaQaExtraction | None:
    """Split a Gujarati NeVA question PDF's ``-layout`` text into question
    and answer halves.

    The layout is two-column below a ``પ્રશ્ન | જવાબ`` header: lines
    carrying both a question item and an answer item are split at the
    second ``(n)`` marker; continuation lines are assigned by indent
    relative to the answer column; ``પત્રક-N`` appendix statements (the
    tabled data) and everything after them belong to the answer.

    Returns ``None`` when no header is found — callers decide the
    fallback, mirroring ``answers.split_qa``. Run :func:`repair_text`
    first when a clean reference line is available.
    """
    text = normalize_gujarati_text(text)
    lines = text.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        if _QA_HEADER_RE.search(line):
            header_idx = i
            break
    if header_idx is None:
        return None

    subject = ""
    question_ref = ""
    preamble: list[str] = []
    for line in lines[:header_idx]:
        stripped = line.strip()
        if not stripped:
            continue
        if not subject and not stripped.isdigit():
            subject = stripped
            continue
        if stripped.isdigit():
            continue
        preamble.append(stripped)
        m = _QREF_RE.search(stripped)
        if m:
            question_ref = m.group(1)

    q_parts: list[str] = []
    a_parts: list[str] = []
    answer_col: int | None = None
    in_appendix = False
    for line in lines[header_idx + 1:]:
        if not line.strip():
            continue
        if _APPENDIX_RE.match(line):
            in_appendix = True
            a_parts.append(line.strip())
            continue
        if in_appendix:
            a_parts.append(line.rstrip())
            continue
        markers = list(_ITEM_MARKER_RE.finditer(line))
        # A line carrying both the question item and its answer item:
        # split at the second marker (the answer column's).
        if len(markers) >= 2:
            split_at = markers[1].start()
            answer_col = split_at if answer_col is None else min(answer_col, split_at)
            q_parts.append(line[:split_at].rstrip())
            a_parts.append(line[split_at:].rstrip())
            continue
        indent = len(line) - len(line.lstrip())
        if answer_col is not None and indent >= answer_col - 2:
            a_parts.append(line.strip())
        else:
            q_parts.append(line.rstrip())

    question = "\n".join(p for p in ([subject] + preamble + q_parts) if p).strip()
    answer = "\n".join(p for p in a_parts if p).strip()
    if not question or not answer:
        return None
    return NevaQaExtraction(
        question_text=question,
        answer_text=answer,
        confidence=0.8 if answer_col is not None or in_appendix else 0.5,
        subject=subject,
        question_ref=question_ref,
        boundary_marker=lines[header_idx].strip(),
    )


@dataclass
class NevaDistrictRow:
    district: str
    area: str            # "" for the district row, "શહેર" for the city row
    figures: list = field(default_factory=list)
    primary_figure: float | int | None = None
    raw_line: str = ""
    line_no: int = 0

    def to_record(self) -> dict:
        return {
            "kind": "neva_district_row",
            "district": self.district,
            "area": self.area,
            "figures": self.figures,
            "primary_figure": self.primary_figure,
            "raw_line": self.raw_line,
            "line_no": self.line_no,
            "extractor": EXTRACTOR_VERSION,
        }


def _district_pattern(districts: tuple[str, ...]) -> re.Pattern:
    # pdftotext splits glyph clusters with stray spaces (શહેર → "શહે ર"),
    # so every district is matched with optional whitespace between its
    # characters. Longest names first so "ગીર સોમનાથ" beats a bare prefix.
    alts = []
    for d in sorted(districts, key=len, reverse=True):
        alts.append(r"\s*".join(re.escape(c) for c in d.replace(" ", "")))
    city = r"\s*".join(re.escape(c) for c in "શહેર")
    return re.compile(rf"({'|'.join(alts)})(\s*(?:{city}))?")


def _parse_number(token: str) -> float | int:
    token = token.replace(",", "")
    return float(token) if "." in token else int(token)


def extract_district_rows(
    text: str,
    *,
    districts: tuple[str, ...] = GUJARAT_DISTRICTS,
    repair_map: dict[str, str] | None = None,
    max_gap: int = 40,
) -> list[NevaDistrictRow]:
    """Deterministic district→figures rows from a NeVA answer's tables.

    A district (optionally suffixed શહેર for the city row) followed within
    *max_gap* characters by a numeric token yields one row carrying every
    numeric token to the end of the line, in order. Column semantics
    (permits vs seizures vs revenue) stay with the consumer — this
    extracts the printed numbers, it does not interpret them. Prose
    mentions of a district with no adjacent figure yield nothing.
    """
    text = normalize_gujarati_text(text)
    if repair_map:
        text = apply_glyph_repair(text, repair_map)
    pattern = _district_pattern(districts)
    canonical = {d.replace(" ", ""): d for d in districts}
    rows: list[NevaDistrictRow] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for m in pattern.finditer(line):
            rest = _NON_FIGURE_RE.sub(" ", line[m.end():])
            first_num = _NUM_TOKEN_RE.search(rest)
            if not first_num or first_num.start() > max_gap:
                continue
            figures = [_parse_number(t) for t in _NUM_TOKEN_RE.findall(rest)]
            rows.append(NevaDistrictRow(
                district=canonical[re.sub(r"\s+", "", m.group(1))],
                area="શહેર" if m.group(2) else "",
                figures=figures,
                primary_figure=figures[0],
                raw_line=line.strip(),
                line_no=line_no,
            ))
    return rows


@dataclass
class NevaExtractionStats:
    questions_processed: int = 0
    qa_records: int = 0
    district_rows: int = 0
    quality_counts: dict = field(default_factory=dict)
    skipped_no_pdf: int = 0
    skipped_no_text: int = 0
    skipped_no_split: int = 0
    errors: list = field(default_factory=list)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def extract_neva_answers(
    out_dir: Path, *, log_fn=print
) -> NevaExtractionStats:
    """Walk a NeVA corpus's ``questions.jsonl``, split each question PDF's
    Gujarati text into Q/A halves, and extract district-table rows.

    Writes ``answers.jsonl`` (kind ``neva_qa_response``) and
    ``neva_district_rows.jsonl``. Each record carries the document's
    text-layer ``quality`` (clean / repaired / low). District rows are
    emitted from every document that splits, because a row only exists
    where the district name matched the clean gazetteer verbatim on that
    line — that per-row match is the integrity condition; a corrupted
    district name never matches and never yields a row. The doc-level
    quality rides along so consumers can filter harder if they choose.
    ``low``-quality documents remain the OCR backlog for their prose.
    """
    from .textparse import extract_pdf_text, read_jsonl

    stats = NevaExtractionStats()
    questions = read_jsonl(out_dir / "questions.jsonl")
    out_records: list[dict] = []
    row_records: list[dict] = []
    for rec in questions:
        stats.questions_processed += 1
        pdf_rel = rec.get("pdf_path")
        pdf = (out_dir / pdf_rel) if pdf_rel else None
        if not pdf or not pdf.exists():
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
        repaired, quality, mapping = repair_text(text, rec.get("subject"))
        stats.quality_counts[quality] = stats.quality_counts.get(quality, 0) + 1
        common = {
            "key": rec.get("key"),
            "source_pdf": str(pdf.relative_to(out_dir)),
            "extracted_at": _now(),
            "language_classified": ["gu"],
        }
        qa = split_qa_neva(repaired)
        if qa is None:
            stats.skipped_no_split += 1
            continue
        qa.quality = quality
        out_records.append({**common, **qa.to_record()})
        stats.qa_records += 1
        # Scan only the answer half: the question prose can mention a
        # district next to an incidental number ("અમદાવાદ ... છેલ્લા 2
        # વર્ષમાં"), and the tabled figures always live in the answer
        # column / appendix statements. line_no on these rows indexes
        # into answer_text.
        for row in extract_district_rows(qa.answer_text):
            row_records.append({**common, "quality": quality, **row.to_record()})
            stats.district_rows += 1

    for path, records in (
        (out_dir / "answers.jsonl", out_records),
        (out_dir / "neva_district_rows.jsonl", row_records),
    ):
        tmp = path.with_name(path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        tmp.replace(path)

    log_fn(
        f"NeVA extraction: {stats.qa_records} qa records, "
        f"{stats.district_rows} district rows, quality={stats.quality_counts}, "
        f"skipped: no_pdf={stats.skipped_no_pdf} no_text={stats.skipped_no_text} "
        f"no_split={stats.skipped_no_split}, errors={len(stats.errors)}"
    )
    return stats
