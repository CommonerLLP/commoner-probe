# SPDX-License-Identifier: MIT
"""Typed-row extraction of vacancy-disclosure tables from Q/A answers.

Three layout families, live-verified against real answer PDFs on
2026-07-08:

* ``in_answer_summary`` — LS US Q5305 (25.03.2026): services as columns
  in the answer body itself. A "Service IAS IPS IFS" header line is
  followed by a "Sanctioned strength" row and an "Officers in position"
  row; pdftotext wraps the two-word labels so the numbers sit on the
  line where the label starts.

* ``annexure_cadre_matrix`` — LS US Q5491 (25.03.2026) Annexure-A:
  cadre × (authorized strength, in position) matrix. The measure header
  line ("Total Authorized Strength ... No. of officers in position") is
  followed by a sub-header repeating the service names ("IAS IPS IAS
  IPS"), then one row per cadre. The "as on" date lives in the annexure
  title line above the header, not in the main answer.

* ``evasive`` — RS US Q2529 (10.08.2023): a vacancy question answered
  with boilerplate and zero numbers. The refusal is itself data, so it
  gets its own layout label rather than ``unknown``.

``unknown`` is reserved for a genuine parse miss: sanctioned-strength /
in-position labels co-occur with numbers but no family parser could
structure them. A layout is never guessed.

``vacant`` is emitted as stated by the source when a vacancy row exists;
otherwise it is derived as ``sanctioned - in_position`` and flagged with
``vacant_stated: false`` so downstream consumers can tell disclosure
from arithmetic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

EXTRACTOR_VERSION = "vacancy_regex_v1"

LAYOUT_IN_ANSWER_SUMMARY = "in_answer_summary"
LAYOUT_ANNEXURE_CADRE_MATRIX = "annexure_cadre_matrix"
LAYOUT_EVASIVE = "evasive"
LAYOUT_UNKNOWN = "unknown"

_VACANCY_QUESTION_RE = re.compile(
    r"vacan|sanctioned\s+(?:strength|posts?)|men[\s-]+in[\s-]+position"
    r"|backlog|lying\s+vacant|posts?\s+abolished|abolition\s+of\s+posts?"
    r"|shortage\s+of\s+(?:staff|officers|manpower)"
    r"|रिक्त|मंज़ूर\s*पद|मंजूर\s*पद",
    re.IGNORECASE,
)

_INT_RE = re.compile(r"\d[\d,]*")

# Family 1: header line listing services as columns.
_SERVICE_HEADER_RE = re.compile(
    r"^\s*(?:name\s+of\s+)?services?\b[:.\s]*(?P<rest>\S.*?)\s*$",
    re.IGNORECASE,
)
_SERVICE_TOKEN_RE = re.compile(r"^[A-Z][A-Za-z&/().-]*$")
_SANCTIONED_LABEL_RE = re.compile(r"sanction|authori[sz]ed", re.IGNORECASE)
_IN_POSITION_LABEL_RE = re.compile(
    r"in[\s-]*position|(?:officers|men|persons|staff)\s+in\b", re.IGNORECASE
)
_VACANT_LABEL_RE = re.compile(r"vacan", re.IGNORECASE)

# Family 2: measure header + optional service sub-header + cadre rows.
_MATRIX_HEADER_RE = re.compile(
    r"(?:authori[sz]ed|sanctioned)\s+strength", re.IGNORECASE
)
_MATRIX_ROW_RE = re.compile(
    r"^\s*(?:\d{1,3}\.?\s+)?(?P<name>[A-Za-z][A-Za-z .&()'/-]*?)\s+"
    r"(?P<nums>\d[\d,]*(?:\s+\d[\d,]*)+)\s*$"
)
_ALLCAPS_TOKEN_RE = re.compile(r"^[A-Z]{2,10}$")
_ANNEXURE_LINE_RE = re.compile(r"^\s*annexure\b[\s'\"–-]*[A-Z0-9]*\s*$", re.IGNORECASE)

_AS_ON_DATE_RE = re.compile(
    r"as\s+on\s+(\d{1,2})[./-](\d{1,2})[./-](\d{4})", re.IGNORECASE
)

_GROUP_NAME_RE = re.compile(r"^group\s*[–-]?\s*['\"]?([A-D])['\"]?$", re.IGNORECASE)
_CATEGORY_NAMES = {
    "SC": "SC",
    "ST": "ST",
    "OBC": "OBC",
    "UR": "UR",
    "EWS": "EWS",
    "GENERAL": "UR",
}


@dataclass
class VacancyRow:
    layout: str
    org_unit: str | None = None
    service: str | None = None
    group: str | None = None
    category: str | None = None
    sanctioned: int | None = None
    in_position: int | None = None
    vacant: int | None = None
    vacant_stated: bool = False
    date_of_data: str | None = None
    extractor: str = EXTRACTOR_VERSION

    @property
    def confidence(self) -> float:
        if self.sanctioned is not None:
            return 0.85
        return 0.8 if self.layout == LAYOUT_EVASIVE else 0.3

    def to_record(self) -> dict:
        return {
            "kind": "vacancy_row",
            "layout": self.layout,
            "org_unit": self.org_unit,
            "service": self.service,
            "group": self.group,
            "category": self.category,
            "sanctioned": self.sanctioned,
            "in_position": self.in_position,
            "vacant": self.vacant,
            "vacant_stated": self.vacant_stated,
            "date_of_data": self.date_of_data,
            "confidence": self.confidence,
            "extractor": self.extractor,
        }


def is_vacancy_question(question_text: str) -> bool:
    return bool(_VACANCY_QUESTION_RE.search(question_text or ""))


def _ints(text: str) -> list[int]:
    return [int(m.group(0).replace(",", "")) for m in _INT_RE.finditer(text)]


def _as_on_date(lines: list[str], center: int, before: int = 4, after: int = 1) -> str | None:
    window = lines[max(0, center - before): center + after]
    for line in window:
        m = _AS_ON_DATE_RE.search(line)
        if m:
            day, month, year = (int(x) for x in m.groups())
            return f"{year}-{month:02d}-{day:02d}"
    return None


def _make_row(
    *,
    layout: str,
    org_unit: str | None = None,
    service: str | None = None,
    sanctioned: int | None = None,
    in_position: int | None = None,
    vacant: int | None = None,
    date_of_data: str | None = None,
) -> VacancyRow:
    vacant_stated = vacant is not None
    if vacant is None and sanctioned is not None and in_position is not None:
        vacant = sanctioned - in_position
    group = None
    category = "ALL"
    if org_unit:
        gm = _GROUP_NAME_RE.match(org_unit)
        if gm:
            group = gm.group(1).upper()
        category = _CATEGORY_NAMES.get(org_unit.upper(), "ALL")
    return VacancyRow(
        layout=layout,
        org_unit=org_unit,
        service=service,
        group=group,
        category=category,
        sanctioned=sanctioned,
        in_position=in_position,
        vacant=vacant,
        vacant_stated=vacant_stated,
        date_of_data=date_of_data,
    )


def _parse_in_answer_summary(lines: list[str]) -> list[VacancyRow]:
    rows: list[VacancyRow] = []
    for i, line in enumerate(lines):
        m = _SERVICE_HEADER_RE.match(line)
        if not m:
            continue
        tokens = m.group("rest").split()
        if len(tokens) < 2 or not all(_SERVICE_TOKEN_RE.match(t) for t in tokens):
            continue
        n = len(tokens)
        sanctioned = in_position = vacant = None
        for follow in lines[i + 1: i + 10]:
            nums = _ints(follow)
            if len(nums) != n:
                continue
            if sanctioned is None and _SANCTIONED_LABEL_RE.search(follow):
                sanctioned = nums
            elif in_position is None and _IN_POSITION_LABEL_RE.search(follow):
                in_position = nums
            elif vacant is None and _VACANT_LABEL_RE.search(follow):
                vacant = nums
        if sanctioned is None or in_position is None:
            continue
        date_of_data = _as_on_date(lines, i)
        for j, service in enumerate(tokens):
            rows.append(_make_row(
                layout=LAYOUT_IN_ANSWER_SUMMARY,
                service=service,
                sanctioned=sanctioned[j],
                in_position=in_position[j],
                vacant=vacant[j] if vacant is not None else None,
                date_of_data=date_of_data,
            ))
    return rows


def _find_service_subheader(lines: list[str], header_idx: int) -> tuple[list[str], int]:
    """Look just below the measure header for a line repeating the service
    names once per measure block ("IAS IPS IAS IPS" → ["IAS", "IPS"]).
    Returns (services, first_data_line_idx); without a sub-header the
    table has one unnamed service and rows carry exactly two numbers.
    """
    for j in range(header_idx + 1, min(header_idx + 4, len(lines))):
        tokens = lines[j].split()
        if (
            len(tokens) >= 2
            and len(tokens) % 2 == 0
            and all(_ALLCAPS_TOKEN_RE.match(t) for t in tokens)
            and tokens[: len(tokens) // 2] == tokens[len(tokens) // 2:]
        ):
            return tokens[: len(tokens) // 2], j + 1
    return [""], header_idx + 1


def _parse_cadre_matrix(lines: list[str]) -> list[VacancyRow]:
    rows: list[VacancyRow] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not (_MATRIX_HEADER_RE.search(line) and _IN_POSITION_LABEL_RE.search(line)):
            i += 1
            continue
        services, start = _find_service_subheader(lines, i)
        k = len(services)
        date_of_data = _as_on_date(lines, i)
        parsed: list[VacancyRow] = []
        last_consumed = i
        for j in range(start, len(lines)):
            if _ANNEXURE_LINE_RE.match(lines[j]):
                break
            m = _MATRIX_ROW_RE.match(lines[j])
            if not m:
                continue
            nums = _ints(m.group("nums"))
            if len(nums) != 2 * k:
                continue
            name = m.group("name").strip().rstrip(".")
            last_consumed = j
            for col, service in enumerate(services):
                parsed.append(_make_row(
                    layout=LAYOUT_ANNEXURE_CADRE_MATRIX,
                    org_unit=name,
                    service=service or None,
                    sanctioned=nums[col],
                    in_position=nums[k + col],
                    date_of_data=date_of_data,
                ))
        if parsed:
            rows.extend(parsed)
            i = last_consumed + 1
        else:
            i += 1
    return rows


def _has_numeric_supply_evidence(lines: list[str]) -> bool:
    """True when a sanctioned-strength / in-position label co-occurs with a
    number — the anchor a real vacancy table cannot avoid. Vacancy *talk*
    with recruitment aggregates but no sanctioned figures (the Railways
    pattern) stays evasive, not unknown.
    """
    for line in lines:
        if re.search(r"\d{2,}", line) and (
            _SANCTIONED_LABEL_RE.search(line) or _IN_POSITION_LABEL_RE.search(line)
        ):
            return True
    return False


def extract_vacancy_rows(question_text: str, answer_text: str) -> list[VacancyRow] | None:
    """Extract typed vacancy rows from a Q/A pair.

    Returns ``None`` when the question is not a vacancy-disclosure
    question. Otherwise returns parsed rows, or a single layout-only
    marker row (``evasive`` / ``unknown``) so the refusal series is
    first-class data.
    """
    if not is_vacancy_question(question_text):
        return None
    lines = (answer_text or "").splitlines()
    rows = _parse_cadre_matrix(lines) + _parse_in_answer_summary(lines)
    if rows:
        return rows
    layout = LAYOUT_UNKNOWN if _has_numeric_supply_evidence(lines) else LAYOUT_EVASIVE
    return [VacancyRow(layout=layout)]
