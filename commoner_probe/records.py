# SPDX-License-Identifier: MIT
"""Typed dataclass records for every commoner-probe output stream.

Each class has a :meth:`from_dict` classmethod that tolerates unknown keys
(drops them) and missing optional fields (uses dataclass defaults).  This
makes them safe to instantiate from records produced by any version of the
crawler without raising ``TypeError``.

Schema validation — whether a given dict *conforms to the JSON Schema* — is
handled by :mod:`commoner_probe.validate`, not here.  These dataclasses are
convenience wrappers for typed iteration; they are not a substitute for
schema validation.

Re-exports of entity dataclasses from :mod:`commoner_probe.entities` are
provided for symmetry, so downstream code can do::

    from commoner_probe.records import (
        ManifestQaRecord,
        ManifestCommitteeReportRecord,
        Person,
        MpMembership,
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from dataclasses import fields as dc_fields
from typing import Any

# Re-export entity dataclasses for API symmetry.
from .entities import (  # noqa: F401
    BureaucraticPosting,
    CommitteeMembership,
    MinisterialAppointment,
    MpMembership,
    Person,
)


def _from_dict(cls, d: dict) -> Any:
    """Generic from_dict factory: keep only known fields, fill missing optionals."""
    known = {f.name for f in dc_fields(cls)}
    filtered = {k: v for k, v in d.items() if k in known}
    # Supply defaults for any known fields not present in the dict so that
    # dataclass construction never raises TypeError for missing args.
    for f in dc_fields(cls):
        if f.name not in filtered:
            if f.default is not f.default_factory:  # type: ignore[misc]
                # Has a concrete default — dataclass will fill it in.
                pass
            elif f.default_factory is not field:  # type: ignore[misc]
                # Has a factory default — will be filled in.
                pass
            else:
                # No default — inject None so construction doesn't crash.
                filtered[f.name] = None
    # Rely on dataclass __init__ to handle the rest.
    try:
        return cls(**filtered)
    except TypeError:
        # Last-resort: inject None for every unfilled field.
        for f in dc_fields(cls):
            filtered.setdefault(f.name, None)
        return cls(**{k: filtered[k] for k in (f.name for f in dc_fields(cls))})


# ---------------------------------------------------------------------------
# Manifest records
# ---------------------------------------------------------------------------

@dataclass
class ManifestQaRecord:
    """One Q/A record from manifest.jsonl (kind='qa').

    Combines both LS and RS shapes. Fields exclusive to one house are
    ``None`` when absent.
    """

    key: str
    kind: str
    house: str
    title: str
    date: str
    qtype: str
    qno: str
    ministry: str
    askers: list
    source: str
    # Optional/conditional fields
    run_id: str | None = None
    probed_at: str | None = None
    language_classified: list = field(default_factory=list)
    asker_details: list = field(default_factory=list)
    asker_entity_ids: list = field(default_factory=list)
    responder_entity_id: str | None = None
    responder_role_at_event: str | None = None
    pdf_url: str | None = None
    pdf_path: str | None = None
    # LS-only
    uuid: str | None = None
    handle: str | None = None
    session: str | None = None
    loksabhanumber: str | None = None
    uri: str | None = None
    found_via_group: str | None = None
    found_via_query: str | None = None
    # RS-only
    qslno: Any = None
    ses_no: Any = None
    question_text: str | None = None
    answer_text: str | None = None
    pdf_url_hindi: str | None = None
    status: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "ManifestQaRecord":
        return _from_dict(cls, d)


@dataclass
class ManifestCommitteeReportRecord:
    """One committee report record from manifest.jsonl (kind='committee_report')."""

    key: str
    kind: str
    house: str
    report_type: str
    presented_via: str
    committee_slug: str
    committee_name: str
    title: str
    date: str
    source: str
    # Optional/conditional
    run_id: str | None = None
    probed_at: str | None = None
    report_no: Any = None
    title_hindi: str | None = None
    language_classified: list = field(default_factory=list)
    date_adoption: str | None = None
    pdf_url: str | None = None
    pdf_url_hindi: str | None = None
    pdf_path: str | None = None
    # LS-only
    loksabha_no: Any = None
    date_presented_ls: str | None = None
    date_laid_rs: str | None = None
    date_presented_speaker: str | None = None
    # RS-only
    date_presentation: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "ManifestCommitteeReportRecord":
        return _from_dict(cls, d)


@dataclass
class ManifestMcaCsrRecord:
    """One MCA CSR company-spend export record from manifest.jsonl."""

    key: str
    kind: str
    record_type: str
    year: str
    financial_year: str
    filename: str
    dest: str
    source_page: str
    url: str
    status: str
    timestamp_utc: str
    probed_at: str
    sha256: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "ManifestMcaCsrRecord":
        return _from_dict(cls, d)


@dataclass
class ManifestDpeCsrRecord:
    """One DPE CPSE CSR document record from manifest.jsonl."""

    key: str
    kind: str
    record_type: str
    id: int
    date: str
    title: str
    filename: str
    dest: str
    url: str
    status: str
    timestamp_utc: str
    probed_at: str
    sha256: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "ManifestDpeCsrRecord":
        return _from_dict(cls, d)


@dataclass
class ManifestMinesDmftRecord:
    """One Ministry of Mines / DMFT raw source-file record from manifest.jsonl."""

    key: str
    kind: str
    record_type: str
    source_family: str
    source_name: str
    publisher: str
    endpoint_kind: str
    filename: str
    dest: str
    url: str
    status: str
    media_type: str
    period_kind: str
    data_period: str | None
    fetched_at: str
    probed_at: str
    source_last_modified: str | None = None
    source_last_modified_raw: str | None = None
    sha256: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "ManifestMinesDmftRecord":
        return _from_dict(cls, d)


@dataclass
class ManifestDoePayAllowancesRecord:
    """One DoE Pay & Allowances annual-report record from manifest.jsonl."""

    key: str
    kind: str
    record_type: str
    source_family: str
    source_name: str
    publisher: str
    title: str
    year: str
    filename: str
    dest: str
    url: str
    listing_url: str
    status: str
    media_type: str
    fetched_at: str
    probed_at: str
    text_layer: bool | None = None
    error: str | None = None
    sha256: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "ManifestDoePayAllowancesRecord":
        return _from_dict(cls, d)


@dataclass
class ManifestBudgetRecord:
    """One Union Budget / RBI State-Finances raw source-file record."""

    key: str
    kind: str
    record_type: str
    source_family: str
    source_name: str
    publisher: str
    fiscal_year: str
    document_type: str
    filename: str
    dest: str
    url: str
    status: str
    media_type: str
    fetched_at: str
    probed_at: str
    demand_no: str | None = None
    section: str | None = None
    source_last_modified: str | None = None
    source_last_modified_raw: str | None = None
    sha256: str | None = None
    http_status: int | None = None
    error: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "ManifestBudgetRecord":
        return _from_dict(cls, d)


@dataclass
class ManifestAcademicJobRecord:
    """One Indian HEI faculty-recruitment advertisement / coverage record."""

    key: str
    kind: str
    record_type: str
    source_family: str
    institution_id: str
    title: str
    original_url: str
    fetch_status: str
    probed_at: str
    institution_name: str | None = None
    institution_short_name: str | None = None
    institution_type: str | None = None
    state: str | None = None
    parser: str | None = None
    ad_number: str | None = None
    department: str | None = None
    discipline: str | None = None
    post_type: str | None = None
    contract_status: str | None = None
    category_breakdown: dict | None = None
    number_of_posts: int | None = None
    pay_scale: str | None = None
    publication_date: str | None = None
    closing_date: str | None = None
    info_url: str | None = None
    apply_url: str | None = None
    publications_required: str | None = None
    unit_eligibility: str | None = None
    annexure_pdf_url: str | None = None
    reservation_note: str | None = None
    general_eligibility: str | None = None
    raw_text_excerpt: str | None = None
    parse_confidence: float | None = None
    pdf_path: str | None = None
    pdf_parsed: bool = False
    source_method: str | None = None
    snapshot_fetched_at: str | None = None
    error: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "ManifestAcademicJobRecord":
        return _from_dict(cls, d)


@dataclass
class ManifestFloorDebateRecord:
    """One sitting-day debate transcript (kind='floor_debate').

    The live sources serve PDF transcripts; per-speaker structured text is a
    downstream extraction concern.
    """

    key: str
    run_id: str
    kind: str
    record_type: str
    source: str
    house: str
    fetch_status: str
    probed_at: str
    loksabha: int | None = None
    session_no: Any = None
    date: str | None = None
    segment: str | None = None
    pdf_url: str | None = None
    pdf_path: str | None = None
    sha256: str | None = None
    fetched_at: str | None = None
    error: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "ManifestFloorDebateRecord":
        return _from_dict(cls, d)


@dataclass
class ManifestBillRecord:
    """One sansad.in bill record (kind='bill_record', api_rs/legislation/getBills)."""

    key: str
    kind: str
    record_type: str
    source: str
    house: str
    fetch_status: str
    probed_at: str
    bill_no: Any = None
    bill_name: str | None = None
    bill_type: str | None = None
    bill_category: str | None = None
    ministry: str | None = None
    bill_year: Any = None
    introduced_house: str | None = None
    introduced_by: str | None = None
    introduced_date: str | None = None
    introduced_file: str | None = None
    passed_ls_date: str | None = None
    passed_ls_file: str | None = None
    passed_rs_date: str | None = None
    passed_rs_file: str | None = None
    passed_both_houses_file: str | None = None
    referred_to_committee_date: str | None = None
    report_presented_date: str | None = None
    report_file: str | None = None
    act_no: Any = None
    act_year: Any = None
    assent_date: str | None = None
    gazetted_file: str | None = None
    synopsis_file: str | None = None
    errata_file: str | None = None
    status: str | None = None
    api_url: str | None = None
    fetched_at: str | None = None
    error: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "ManifestBillRecord":
        return _from_dict(cls, d)


@dataclass
class ManifestIndiaCodeRecord:
    """One India Code state Act / amendment / rule / notification instrument
    (kind='indiacode_instrument', indiacode.nic.in)."""

    key: str
    kind: str
    record_type: str
    source: str
    state: str
    status: str
    probed_at: str
    state_handle: str | None = None
    act_handle: str | None = None
    act_id: str | None = None
    act_no: str | None = None
    act_year: str | None = None
    short_title: str | None = None
    department: str | None = None
    act_type: str | None = None
    location: str | None = None
    instrument_type: str | None = None
    is_amendment: bool | None = None
    instrument_date: str | None = None
    description: str | None = None
    description_hi: str | None = None
    lang: str | None = None
    actid: str | None = None
    filename: str | None = None
    source_url: str | None = None
    dest: str | None = None
    sha256: str | None = None
    error: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "ManifestIndiaCodeRecord":
        return _from_dict(cls, d)


# ---------------------------------------------------------------------------
# answers.jsonl records
# ---------------------------------------------------------------------------

@dataclass
class AnswerQaResponse:
    """One Q/A extraction (kind='qa_response') from answers.jsonl."""

    key: str
    kind: str
    source_pdf: str
    extracted_at: str
    question_text: str
    answer_text: str
    confidence: float
    extractor: str
    boundary_marker: str
    run_id: str | None = None
    language_classified: list = field(default_factory=list)
    source_report_type: str | None = None
    question_subject: str | None = None
    question_stem: str | None = None
    question_body: str | None = None
    answer_minister_name: str | None = None
    answer_body: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "AnswerQaResponse":
        return _from_dict(cls, d)


@dataclass
class AnswerNevaQaResponse:
    """One NeVA Gujarati Q/A extraction (kind='neva_qa_response') from answers.jsonl."""

    key: str
    kind: str
    source_pdf: str
    extracted_at: str
    question_text: str
    answer_text: str
    confidence: float
    quality: str
    extractor: str
    boundary_marker: str = ""
    question_subject: str | None = None
    question_ref: str | None = None
    language_classified: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "AnswerNevaQaResponse":
        return _from_dict(cls, d)


@dataclass
class NevaDistrictRowRecord:
    """One district→figures table row from neva_district_rows.jsonl."""

    key: str
    kind: str
    source_pdf: str
    extracted_at: str
    district: str
    figures: list
    primary_figure: float
    raw_line: str
    quality: str
    extractor: str
    area: str = ""
    line_no: int | None = None
    language_classified: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "NevaDistrictRowRecord":
        return _from_dict(cls, d)


@dataclass
class AnswerAtrResponse:
    """One ATR recommendation/response pair (kind='atr_response') from answers.jsonl."""

    key: str
    kind: str
    source_pdf: str
    extracted_at: str
    recommendation_no: int
    recommendation_text: str
    response_text: str
    confidence: float
    extractor: str
    run_id: str | None = None
    language_classified: list = field(default_factory=list)
    source_report_type: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "AnswerAtrResponse":
        return _from_dict(cls, d)


@dataclass
class AnswerDfgRecommendation:
    """One DFG/committee recommendation (kind='dfg_recommendation') from answers.jsonl."""

    key: str
    kind: str
    source_pdf: str
    extracted_at: str
    recommendation_no: int
    recommendation_text: str
    confidence: float
    extractor: str
    run_id: str | None = None
    language_classified: list = field(default_factory=list)
    source_report_type: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "AnswerDfgRecommendation":
        return _from_dict(cls, d)


# ---------------------------------------------------------------------------
# manifest.jsonl record (legacy DSpace item)
# ---------------------------------------------------------------------------

@dataclass
class ManifestLegacyDspaceRecord:
    """One legacy-DSpace (XMLUI/JSPUI) item record from manifest.jsonl."""

    key: str
    kind: str
    record_type: str
    source: str
    portal_name: str
    handle_id: str
    handle_prefix: str
    status: str
    probed_at: str
    title: str | None = None
    issue_date_raw: str | None = None
    publisher: str | None = None
    type: str | None = None
    collection: str | None = None
    bitstream_paths: list = field(default_factory=list)
    downloads: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "ManifestLegacyDspaceRecord":
        return _from_dict(cls, d)


# ---------------------------------------------------------------------------
# manifest.jsonl record (attendance)
# ---------------------------------------------------------------------------

@dataclass
class ManifestAttendanceRecord:
    """One Lok Sabha member-wise attendance record from manifest.jsonl."""

    key: str
    kind: str
    record_type: str
    source: str
    house: str
    loksabha: int
    session_no: int
    probed_at: str
    mpsno: int | None = None
    member_name: str | None = None
    constituency: str | None = None
    state: str | None = None
    state_code: str | None = None
    signed_days_count: int | None = None
    division: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "ManifestAttendanceRecord":
        return _from_dict(cls, d)


# ---------------------------------------------------------------------------
# manifest.jsonl record (MyNeta candidate affidavit)
# ---------------------------------------------------------------------------

@dataclass
class ManifestMynetaRecord:
    """One ADR/MyNeta Lok Sabha 2024 candidate affidavit record from manifest.jsonl."""

    key: str
    kind: str
    record_type: str
    source: str
    election: str
    candidate_id: int
    source_url: str
    probed_at: str
    constituency_id: int | None = None
    constituency_name: str | None = None
    name: str | None = None
    winner_status: str | None = None
    party: str | None = None
    age: int | None = None
    self_profession: str | None = None
    spouse_profession: str | None = None
    education_category: str | None = None
    assets_rupees: int | None = None
    liabilities_rupees: int | None = None
    criminal_cases_declared: int | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "ManifestMynetaRecord":
        return _from_dict(cls, d)


# ---------------------------------------------------------------------------
# vacancy_rows.jsonl record
# ---------------------------------------------------------------------------

@dataclass
class VacancyRowRecord:
    """One typed vacancy-disclosure row (kind='vacancy_row') from vacancy_rows.jsonl."""

    key: str
    kind: str
    source_pdf: str
    extracted_at: str
    layout: str
    vacant_stated: bool
    confidence: float
    extractor: str
    run_id: str | None = None
    ministry: str | None = None
    org_unit: str | None = None
    service: str | None = None
    group: str | None = None
    category: str | None = None
    sanctioned: int | None = None
    in_position: int | None = None
    vacant: int | None = None
    date_of_data: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "VacancyRowRecord":
        return _from_dict(cls, d)


# ---------------------------------------------------------------------------
# atr_linkage.jsonl record
# ---------------------------------------------------------------------------

@dataclass
class AtrLinkageRecord:
    """One ATR linkage record from atr_linkage.jsonl."""

    atr_key: str
    atr_title: str
    references_report_no: int
    extracted_at: str
    extractor: str
    atr_no: Any = None
    house: str | None = None
    committee_slug: str | None = None
    references_report_key: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "AtrLinkageRecord":
        return _from_dict(cls, d)


# ---------------------------------------------------------------------------
# _runs.jsonl record
# ---------------------------------------------------------------------------

@dataclass
class RunRecord:
    """One run audit record from _runs.jsonl."""

    run_id: str
    kind: str
    scope: dict
    topic_name: str
    topic_path: str
    topic_hash: str
    classifier_mode: str
    classifier_config_redacted: dict
    tool_version: str
    started_at: str
    added: int
    errors: list = field(default_factory=list)
    bucket_attempts: list = field(default_factory=list)
    ended_at: str | None = None
    elapsed_ms: float | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "RunRecord":
        return _from_dict(cls, d)


__all__ = [
    "ManifestQaRecord",
    "ManifestCommitteeReportRecord",
    "ManifestDpeCsrRecord",
    "ManifestMcaCsrRecord",
    "ManifestMinesDmftRecord",
    "ManifestDoePayAllowancesRecord",
    "ManifestAttendanceRecord",
    "ManifestMynetaRecord",
    "ManifestLegacyDspaceRecord",
    "ManifestBudgetRecord",
    "ManifestAcademicJobRecord",
    "ManifestFloorDebateRecord",
    "ManifestBillRecord",
    "ManifestIndiaCodeRecord",
    "AnswerQaResponse",
    "AnswerNevaQaResponse",
    "AnswerAtrResponse",
    "AnswerDfgRecommendation",
    "NevaDistrictRowRecord",
    "VacancyRowRecord",
    "AtrLinkageRecord",
    "RunRecord",
    # Re-exported from entities
    "Person",
    "MpMembership",
    "CommitteeMembership",
    "MinisterialAppointment",
    "BureaucraticPosting",
]
