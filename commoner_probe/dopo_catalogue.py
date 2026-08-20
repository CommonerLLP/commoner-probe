"""Data on Police Organisations (BPRD): where the editions are, and three traps.

BPRD publishes DOPO yearly — sanctioned strength, actual strength and the
vacancy between them, by state and by rank. **The host is dead.**
``bprd.nic.in`` resolves to 164.100.252.214 and neither port 80 nor port 443
answers; ``bprd.gov.in`` does not resolve at all. Verified from two continents
on 2026-07-28 and again from one on 2026-08-20.

**The editions survive in the Internet Archive, and no new fetcher is needed
to get them.** ``commoner-probe wayback-recover`` already does it. The one
thing that makes it usable is scoping the index walk to a path::

    commoner-probe wayback-recover --out data/dopo \\
        --host bprd.nic.in/uploads/dopo --verify pdf

Bare ``--host bprd.nic.in`` walks the whole capture history of a large
government domain and did not finish in seven minutes. The path-scoped form
returned in about a second.

TRAP 1 — THE CATALOGUE CANNOT BE GUESSED FROM THE PATTERN
=========================================================
Most editions are ``dopo<year>.pdf``. Two are not: 2014 is ``dopoFile2014.pdf``
and 2017 is ``databook2017.pdf``. A directory listing of the archive finds
eleven files; the archived index page names **thirteen**. Generating URLs from
the obvious pattern misses 2014, 2017, 2021 and 2022 — and 2014 and 2017 are
the two editions the first consumer of this data actually used.

So the catalogue below is read from the source's own page and pinned, not
derived. Re-read the page when a new edition appears.

TRAP 2 — THE LIGATURE, AND IT IS WORSE THAN A TYPO
==================================================
The 2016 edition's fonts drop the ``ti`` ligature on extraction. Its tables say
``Sanc oned``, not ``Sanctioned``. Counted case-insensitively over the first 90
pages, 2026-08-20:

===========  ==============  =============
edition      "Sanctioned"    "Sanc oned"
===========  ==============  =============
dopo2011      162             0
dopo2016      20              172
===========  ==============  =============

**It is per edition, so a working extractor proves nothing about the next
year.** Code keyed on the correct spelling reads 2011 perfectly and finds 20
hits in 2016 — the prose, not the tables — missing the 172 that carry the
numbers. The result is not an error. It is a plausible, quiet "this edition has
no sanctioned-strength data".

:func:`term_pattern` fixes the query rather than the document. On dopo2016 it
finds 192 where the same search spelled correctly and case-sensitively finds 2,
which is the size of what a correct-looking extractor drops. Repairing the text
instead would mean writing characters into a primary source that nobody could
then tell from the original.

TRAP 3 — THESE ARE NOT SCANS
============================
The request that asked for this work described DOPO as scanned PDFs with no
text layer, needing OCR. That is not true of the editions checked here.
``dopo2011`` yields 329,702 characters over 90 pages and ``dopo2016`` yields
364,706, tables included, through ``textparse.extract_pdf_text(layout=True)``.
Use OCR only after measuring a specific edition and finding it empty.
"""

from __future__ import annotations

import re
from typing import NamedTuple

__all__ = [
    "DOPO_BASE", "DOPO_EDITIONS", "RECOVERY_HOST_PREFIX",
    "Edition", "recovery_urls", "term_pattern",
]

DOPO_BASE = "https://bprd.nic.in"

#: What to pass to ``wayback-recover --host``. The path scope is the difference
#: between a second and a walk that does not finish.
RECOVERY_HOST_PREFIX = "bprd.nic.in/uploads/dopo"


class Edition(NamedTuple):
    """One DOPO edition and whether the archive can actually serve it."""

    year: int
    path: str
    archived: bool
    note: str = ""

    @property
    def url(self) -> str:
        return f"{DOPO_BASE}/{self.path}"


#: Read from the archived index page at ``bprd.nic.in/page/dopo``
#: (capture 20241008112922) on 2026-08-20, then each entry re-checked against
#: the CDX index the same day. ``archived`` records what the index answered,
#: not what the page claimed.
DOPO_EDITIONS: tuple[Edition, ...] = (
    Edition(2010, "uploads/dopo/dopo2010.pdf", True),
    Edition(2011, "uploads/dopo/dopo2011.pdf", True),
    Edition(2012, "uploads/dopo/dopo2012.pdf", True),
    Edition(2013, "uploads/dopo/dopo2013.pdf", True),
    Edition(2014, "uploads/dopo/dopoFile2014.pdf", True,
            "the filename breaks the pattern, and this is one of the two "
            "editions the first consumer used"),
    Edition(2015, "uploads/dopo/dopo2015.pdf", True),
    Edition(2016, "uploads/dopo/dopo2016.pdf", True, "the ligature edition; see TRAP 2"),
    Edition(2017, "uploads/dopo/databook2017.pdf", True,
            "the filename breaks the pattern; 29 MB, the largest of the series "
            "apart from 2014"),
    Edition(2018, "uploads/dopo/dopo2018.pdf", True),
    Edition(2019, "uploads/dopo/dopo2019.pdf", True),
    Edition(2020, "uploads/dopo/dopo2020.pdf", True),
    Edition(2021, "uploads/pdf/DoPO 2021.pdf", False,
            "named on the index page, and the archive holds no capture of it. "
            "The space in the filename is the source's, not a typo here"),
    Edition(2022, "uploads/pdf/202301110504030641146DataonPoliceOrganizations.pdf",
            False,
            "named on the index page; the only capture is a 302, so the bytes "
            "were never archived. The year is inferred from the 2023-01-11 "
            "upload stamp and is NOT confirmed by the document"),
)


def recovery_urls(*, archived_only: bool = True) -> list[str]:
    """The edition URLs, for ``wayback-recover --urls``.

    ``archived_only`` keeps the eleven the index can serve. Pass ``False`` to
    get all thirteen — useful for re-checking whether 2021 and 2022 have since
    been captured, and useless for a recovery run today.
    """
    return [e.url for e in DOPO_EDITIONS if e.archived or not archived_only]


#: The ``ti`` that extraction drops. A term is searched with the ligature
#: allowed to be either itself or the space it becomes.
_LIGATURE = re.compile(r"ti", re.IGNORECASE)


def term_pattern(term: str, *, flags: int = re.IGNORECASE) -> re.Pattern[str]:
    """A regex for ``term`` that survives the dropped ligature.

    ``term_pattern("Sanctioned")`` matches both ``Sanctioned`` and
    ``Sanc oned``. The document is left exactly as the source wrote it, because
    repairing extracted text means writing characters into a primary source
    that nobody can then tell from the original.
    """
    parts = [re.escape(piece) for piece in _LIGATURE.split(term)]
    return re.compile(r"(?:ti|\s)".join(parts), flags)
