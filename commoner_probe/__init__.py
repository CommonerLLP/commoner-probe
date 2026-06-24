# SPDX-License-Identifier: MIT
"""Data-pulling crawler for Indian Parliament question corpora."""

import re
from pathlib import Path

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
    ManifestMinesDmftRecord,
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
    "ManifestMinesDmftRecord",
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
def _version_from_pyproject() -> str | None:
    """Fallback for source-tree runs before the package is installed."""
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if not pyproject.exists():
        return None
    match = re.search(r'^version = "([^"]+)"$', pyproject.read_text(encoding="utf-8"), re.MULTILINE)
    return match.group(1) if match else None


def _resolve_version() -> str:
    try:
        from importlib.metadata import version as _dist_version
        return _dist_version("commoner-probe")
    except Exception:
        return _version_from_pyproject() or "0.0.0"


__version__ = _resolve_version()
