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
    raw_text_excerpt: str | None = None
    parse_confidence: float | None = None
    pdf_path: str | None = None
    pdf_parsed: bool = False
    snapshot_fetched_at: str | None = None
    error: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "ManifestAcademicJobRecord":
        return _from_dict(cls, d)


@dataclass
class ManifestFloorDebateRecord:
    """One Lok Sabha floor-debate record (kind='floor_debate').

    NOTE: the live sansad.in contract is provisional — most fields are
    nullable until a real response is captured (bead sansad-crawler-5ht).
    """

    key: str
    kind: str
    house: str
    source: str
    probed_at: str
    run_id: str | None = None
    ls_no: int | None = None
    date: str | None = None
    business_type: str | None = None
    member_name: str | None = None
    member_party: str | None = None
    constituency: str | None = None
    debate_title: str | None = None
    verbatim_text: str | None = None
    language_classified: list = field(default_factory=list)
    pdf_url: str | None = None
    pdf_path: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "ManifestFloorDebateRecord":
        return _from_dict(cls, d)


@dataclass
class ManifestBillRecord:
    """One sansad.in bill record (kind='bill_record').

    NOTE: the live sansad.in contract is provisional — most fields are
    nullable until a real response is captured (bead sansad-crawler-4xd).
    """

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
    ministry: str | None = None
    introduced_date: str | None = None
    introduced_house: str | None = None
    passed_ls_date: str | None = None
    passed_rs_date: str | None = None
    assent_date: str | None = None
    current_stage: str | None = None
    status: str | None = None
    pdf_url: str | None = None
    pdf_path: str | None = None
    api_url: str | None = None
    fetched_at: str | None = None
    error: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "ManifestBillRecord":
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
    "AnswerQaResponse",
    "AnswerAtrResponse",
    "AnswerDfgRecommendation",
    "AtrLinkageRecord",
    "RunRecord",
    # Re-exported from entities
    "Person",
    "MpMembership",
    "CommitteeMembership",
    "MinisterialAppointment",
    "BureaucraticPosting",
]
