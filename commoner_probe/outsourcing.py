# SPDX-License-Identifier: MIT
"""Typed-row extraction of outsourcing/consultancy signals from committee
report text.

DRSC Demands-for-Grants reports carry the outsourcing story of each
ministry's bodies in prose and small tables: contractual/outsourced
staff counts, consultancy and professional-services spend, Project
Management Units, and vacancy-driven contractualisation ("34 of the 36
sanctioned posts are lying vacant... functions with 102 contractual
staff"). This module extracts those as typed rows; interpretation
(which body, which scheme, whether it evidences hollowing-out) stays
downstream.

Signal kinds:

* ``headcount`` — a number adjacent to a workforce term ("102
  contractual staff", "engaged 45 consultants").
* ``spend``     — a rupee amount on the same line as a consultancy /
  professional-services / outsourcing term; crore/lakh multipliers are
  normalised into ``value_inr``.
* ``vacancy``   — the "X out of Y posts ... vacant" prose pair; both
  numbers are kept as stated.
* ``mention``   — a term present with no adjacent number (e.g. a PMU
  named without headcount). The mention is data: it locates the
  outsourcing surface even when the report withholds figures.

Every row carries the matched term, the full source line as context,
and the line number for citation back into the PDF text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

EXTRACTOR_VERSION = "outsourcing_regex_v1"

KIND_HEADCOUNT = "headcount"
KIND_SPEND = "spend"
KIND_VACANCY = "vacancy"
KIND_MENTION = "mention"

#: Workforce/engagement terms. Matched case-insensitively; the matched
#: surface form is preserved on the row.
_TERM_RE = re.compile(
    r"(?P<term>"
    r"contractual(?:\s+(?:staff|employees?|workers?|basis|manpower|posts?|appointments?))?"
    r"|out[\s-]?sourc(?:ed|ing|e)"
    r"|consultanc(?:y|ies)"
    r"|consultants?"
    r"|professional\s+services"
    r"|project\s+management\s+unit|\bPMUs?\b"
    r"|young\s+professionals?"
    r"|manpower\s+(?:agenc|supply)\w*"
    r"|daily\s+wage[rs]*"
    r"|ad[\s-]?hoc\s+(?:staff|basis|appointments?)"
    r")",
    re.IGNORECASE,
)

_INT_RE = re.compile(r"\b\d[\d,]*\b")

#: A number directly adjacent to the term (within a few words either
#: side): "102 contractual staff", "consultants (45)", "engaged 45
#: consultants".
_NEAR_NUMBER_WINDOW = 32

_RUPEE_RE = re.compile(
    r"(?:Rs\.?|₹|INR)\s*(?P<amount>\d[\d,]*(?:\.\d+)?)\s*(?P<scale>crores?|crs?\.?|lakhs?|lacs?)?",
    re.IGNORECASE,
)

_SCALES = {"crore": 1e7, "cr": 1e7, "lakh": 1e5, "lac": 1e5}

_VACANCY_PAIR_RE = re.compile(
    r"(?P<vacant>\d[\d,]*)\s+(?:out\s+of|of\s+the|of)\s+(?:the\s+)?(?P<sanctioned>\d[\d,]*)\s+"
    r"(?:sanctioned\s+)?posts?\b[^.]{0,120}?vacan",
    re.IGNORECASE,
)


def _to_int(token: str) -> int:
    return int(token.replace(",", ""))


def _scale_factor(scale: str | None) -> float:
    if not scale:
        return 1.0
    key = scale.lower().rstrip("s.").replace("crore", "cr").replace("lakh", "lakh")
    for name, factor in _SCALES.items():
        if key.startswith(name[:2]):
            return factor
    return 1.0


@dataclass
class OutsourcingSignal:
    kind: str
    term: str
    context: str
    line_no: int
    value: float | int | None = None
    unit: str | None = None          # "persons" | "inr"
    sanctioned: int | None = None    # vacancy kind only
    vacant: int | None = None        # vacancy kind only

    def to_record(self) -> dict:
        rec = {
            "kind": "outsourcing_signal",
            "signal": self.kind,
            "term": self.term,
            "context": self.context,
            "line_no": self.line_no,
            "extractor": EXTRACTOR_VERSION,
        }
        if self.value is not None:
            rec["value"] = self.value
            rec["unit"] = self.unit
        if self.sanctioned is not None:
            rec["sanctioned"] = self.sanctioned
            rec["vacant"] = self.vacant
        return rec


def _headcount_candidates(segment: str) -> list[tuple[int, int, int]]:
    """(start, end, value) integer tokens in *segment* that can be
    headcounts.

    Excludes decimals/percentages (a token touching ``.`` or ``%``) and
    bare 4-digit years 1900–2100 ("Rules, 2006", "since 2021") — a
    genuine four-digit headcount in that range is sacrificed for not
    emitting years as staff counts; the full line stays in context.
    """
    out: list[tuple[int, int, int]] = []
    for m in _INT_RE.finditer(segment):
        before = segment[m.start() - 1] if m.start() > 0 else ""
        after = segment[m.end()] if m.end() < len(segment) else ""
        # ``-``/``–`` exclusions keep financial-year suffixes out
        # ("2025-26" must not yield a headcount of 26).
        if before in ".%-–" or after in ".%-–":
            continue
        value = _to_int(m.group(0))
        if len(m.group(0)) == 4 and 1900 <= value <= 2100:
            continue
        # Durations ("more than 10 years on contractual basis") are not
        # headcounts.
        if re.match(r"\s*(?:years?|months?|yrs?)\b", segment[m.end():], re.IGNORECASE):
            continue
        out.append((m.start(), m.end(), value))
    return out


def _headcount_near(line: str, start: int, end: int) -> int | None:
    """The plausible integer nearest to the term within the window."""
    before = line[max(0, start - _NEAR_NUMBER_WINDOW):start]
    after = line[end:end + _NEAR_NUMBER_WINDOW]
    candidates: list[tuple[int, int]] = []  # (distance, value)
    for _, tok_end, value in _headcount_candidates(before):
        candidates.append((len(before) - tok_end, value))
    for tok_start, _, value in _headcount_candidates(after):
        candidates.append((tok_start, value))
    if not candidates:
        return None
    return min(candidates)[1]


def extract_outsourcing_signals(text: str) -> list[OutsourcingSignal]:
    """Scan report text line by line for outsourcing/consultancy signals.

    Deterministic and conservative: a ``headcount`` needs a number
    adjacent to the term; a ``spend`` needs a rupee amount on the term's
    own line; anything else with a term is a ``mention``. Rupee-amount
    years ("2025-26") never parse as amounts because the ₹/Rs anchor is
    required.
    """
    signals: list[OutsourcingSignal] = []
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    for line_no, line in enumerate(lines, start=1):
        if not line:
            continue
        prev_line = lines[line_no - 2] if line_no >= 2 else ""
        for m in _VACANCY_PAIR_RE.finditer(line):
            signals.append(OutsourcingSignal(
                kind=KIND_VACANCY,
                term="posts vacant",
                context=line[:400],
                line_no=line_no,
                sanctioned=_to_int(m.group("sanctioned")),
                vacant=_to_int(m.group("vacant")),
            ))
        seen_on_line: set[tuple[str, str]] = set()
        # Rupee amounts are masked before headcount adjacency so an
        # amount's digits never count as a staff count; offsets are
        # preserved.
        masked = _RUPEE_RE.sub(lambda m: " " * len(m.group(0)), line)
        for m in _TERM_RE.finditer(line):
            term = re.sub(r"\s+", " ", m.group("term")).strip()
            emitted = False
            # pdftotext wraps prose mid-sentence, so the rupee amount for
            # a term is often on the line above ("Rs. 4.56 crore ... on\n
            # consultancy services"). Prose wraps carry no terminal
            # punctuation; a line ending a sentence is not a wrap source.
            rupee = _RUPEE_RE.search(line)
            wrapped = None
            if not rupee and prev_line and not prev_line.endswith((".", ":", ";", "?")):
                wrapped = _RUPEE_RE.search(prev_line)
                rupee = wrapped
            if rupee:
                emitted = True
                key = (term.lower(), "spend")
                if key not in seen_on_line:
                    seen_on_line.add(key)
                    amount = float(rupee.group("amount").replace(",", ""))
                    context = f"{prev_line} {line}" if wrapped else line
                    signals.append(OutsourcingSignal(
                        kind=KIND_SPEND,
                        term=term,
                        context=context[:400],
                        line_no=line_no,
                        value=round(amount * _scale_factor(rupee.group("scale")), 2),
                        unit="inr",
                    ))
            # A line can carry BOTH a spend and a headcount ("engaged 102
            # consultants at a cost of Rs. 4.56 crore") — spend must not
            # swallow the count.
            count = _headcount_near(masked, m.start(), m.end())
            if count is not None:
                emitted = True
                key = (term.lower(), "headcount")
                if key not in seen_on_line:
                    seen_on_line.add(key)
                    signals.append(OutsourcingSignal(
                        kind=KIND_HEADCOUNT,
                        term=term,
                        context=line[:400],
                        line_no=line_no,
                        value=count,
                        unit="persons",
                    ))
            if emitted:
                continue
            key = (term.lower(), "mention")
            if key in seen_on_line:
                continue
            seen_on_line.add(key)
            signals.append(OutsourcingSignal(
                kind=KIND_MENTION,
                term=term,
                context=line[:400],
                line_no=line_no,
            ))
    return signals
