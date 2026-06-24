# SPDX-License-Identifier: MIT
"""Indian HEI faculty-recruitment acquisition.

Consolidates the academiaindia scraping core into commoner-probe's Layer 0: an
institution registry, per-institution parsers, and PDF text extraction. Topic-
less, dmft-style — see :class:`AcademicJobsProbe`.

Migration status: ``generic`` + ``iim_recruit`` parsers are ported (covering the
bulk of the 79-institution registry); the remaining institution-specific parsers
fall back to ``generic`` until migrated (see ``parsers.UNMIGRATED_PARSERS``).
"""

from .probe import AcademicJobsProbe
from .registry import load_registry, select_institutions

__all__ = ["AcademicJobsProbe", "load_registry", "select_institutions"]
