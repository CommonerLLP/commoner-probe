"""Data-pulling crawler for Indian Parliament question corpora."""

from . import schemas as schemas
from .records import (  # noqa: F401
    AtrLinkageRecord,
    AnswerAtrResponse,
    AnswerDfgRecommendation,
    AnswerQaResponse,
    BureaucraticPosting,
    CommitteeMembership,
    ManifestCommitteeReportRecord,
    ManifestQaRecord,
    MinisterialAppointment,
    MpMembership,
    Person,
    RunRecord,
)

__all__ = [
    "__version__",
    "schemas",
    "ManifestQaRecord",
    "ManifestCommitteeReportRecord",
    "AnswerQaResponse",
    "AnswerAtrResponse",
    "AnswerDfgRecommendation",
    "AtrLinkageRecord",
    "RunRecord",
    "Person",
    "MpMembership",
    "CommitteeMembership",
    "MinisterialAppointment",
    "BureaucraticPosting",
]
__version__ = "0.1.0"
