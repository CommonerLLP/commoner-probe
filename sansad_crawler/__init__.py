"""Data-pulling crawler for Indian Parliament question corpora."""

from . import schemas as schemas
from .corpus import AtrChain, Corpus, QaPair  # noqa: F401
from .records import (  # noqa: F401
    AnswerAtrResponse,
    AnswerDfgRecommendation,
    AnswerQaResponse,
    AtrLinkageRecord,
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
    "Corpus",
    "QaPair",
    "AtrChain",
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
__version__ = "0.2.0"
