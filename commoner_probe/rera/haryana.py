# SPDX-License-Identifier: MIT
"""Haryana RERA registered-projects listing.

The Haryana Real Estate Regulatory Authority publishes its registered projects
at ``https://haryanarera.gov.in``. The contract below was verified live on
November 11, 2026 and is recorded, with the exact header strings, in
``tests/fixtures/rera_haryana/README.md``.

* Two listings, each a plain ``GET`` that returns the whole table (the page's
  DataTable paginates in the browser): ``/admincontrol/registered_projects/2``
  holds the Gurugram authority, and ``/admincontrol/registered_projects/1``
  holds the Panchkula authority, which covers every other Haryana district.
* No token, cookie, login, or CAPTCHA. ``robots.txt`` answers 404.
* Each listing is about 2.6 MB. The adapter waits at least five seconds between
  requests.

The table is ``<table id="compliant_hearing">`` with 14 columns. The parser
anchors every column on its exact header string, so a portal redesign that adds,
drops, or reorders a column raises :class:`ReraError` instead of returning
mislabelled rows. Each row keeps every column in ``fields``, keyed by the
portal's header string. These conventions are the portal's, not ours:

* The registration certificate number and its date share one cell
  (``GGM/415/147/2020/31 DATED 09.10.2020``). ``registration_no`` is the number
  and ``approved_on`` is the date. A cell can also carry a supersession note in
  parentheses, which stays in ``fields``, or a ``Lapsed Project`` flag, which
  becomes ``lapsed: true``. Some certificate numbers have no date at all
  (Panchkula lists ``HRERA-PKL-AMB-812-2025``), so ``approved_on`` is null there.
* ``Project ID`` is ``NA`` for 37 Gurugram projects, so the row key is
  ``RERA|IN-HR|<registration certificate number>``, which is unique across both
  listings. The adapter refuses a listing where it isn't.
* ``Registration Up-to`` is ``dd/mm/yyyy`` and becomes ``registration_valid_until``.
  The portal prints ``01/01/1970``, the Unix epoch, in the few rows that have no
  date (4 Gurugram rows and 3 Panchkula rows on November 11, 2026). That is a
  placeholder, so the typed date is null and ``fields`` keeps the text.
* A link column holds the link, not the label: the ``href``, or for the
  columns that open a pop-up the URL inside ``window.open('...')``. When the
  cell has no link, the column holds the cell text instead (``N/A``,
  ``QPR not Uploaded``, ``NA``, ``--``), which is itself a published fact. A
  value that starts with ``https://`` is a link.
* The pop-up for ``View Quarterly Progress`` lists the project's quarters. The
  adapter records its URL and never fetches it, and it never fetches any other
  link in a row.
* Two keys have no header of their own: ``Project ID URL`` (the link around the
  project ID) and ``Corrigendum ID`` and ``OC/CC/PCC ID`` (the number the page
  passes to its ``view_corrigendums()`` script, which this adapter doesn't call).
* A project location can wrap with ``<br>`` in the middle of a word
  (``G<br>URUGRAM``). The text joins the pieces without a separator.
"""

from __future__ import annotations

import hashlib
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

from commoner_probe.http_client import (
    ChallengeDetected,
    make_session,
    read_capped_response,
    refuse_challenge,
)

from .common import (
    MANIFEST_FILE,
    MIN_SLEEP_SEC,
    ROWS_FILE,
    ReraError,
    iso_date,
    now_iso,
    upsert_jsonl,
    write_bytes_atomic,
)

BASE_URL = "https://haryanarera.gov.in"
STATE = "IN-HR"
SOURCE = "rera-haryana"
DEFAULT_AUTHORITY = "gurugram"

#: The listing number for each authority, as it appears in the URL.
AUTHORITIES: dict[str, int] = {"gurugram": 2, "panchkula": 1}

#: Ceiling for one listing response. Each is about 2.6 MB.
MAX_LISTING_BYTES = 32 * 1024 * 1024

#: The 14 header strings, in the order the portal prints them.
HEADERS: tuple[str, ...] = (
    "Serial No.",
    "Registration Certificate Number",
    "Project ID",
    "Project Name",
    "Builder",
    "Project Location",
    "Project District",
    "Registered With",
    "Details of Project(Form A-H)",
    "Registration Up-to",
    "View Certificate",
    "View Quarterly Progress",
    "Monitoring Orders",
    "View OC/CC/PCC",
)

#: Columns that hold a link when the cell has one, and the cell text otherwise.
LINK_COLUMNS: tuple[str, ...] = (
    "Details of Project(Form A-H)",
    "View Certificate",
    "View Quarterly Progress",
    "Monitoring Orders",
)

PROJECT_ID_URL_KEY = "Project ID URL"
CORRIGENDUM_ID_KEY = "Corrigendum ID"
OC_ID_KEY = "OC/CC/PCC ID"

LAPSED_TEXT = "Lapsed Project"

_TABLE_OPEN = re.compile(r"<table\b[^>]*\bid\s*=\s*[\"']compliant_hearing[\"'][^>]*>", re.I)
_TABLE_TAG = re.compile(r"<(/?)table\b", re.I)
_BREAKING_SPACE = re.compile(r"\s*[\r\n\t\f\v]\s*")
_OPEN_URL = re.compile(r"window\.open\(\s*'([^']+)'")
_CORRIGENDUM = re.compile(r"view_corrigendums\(\s*(\d+)\s*,")
_DATED = re.compile(r"\bDATED\W{0,3}(\d{1,2})\.(\d{1,2})\.(\d{4})", re.I)
# A supersession note is a parenthesis after a space. A parenthesis inside the
# number is part of it: `160(A) OF 2017`, `GGM/441/173/2021/08(A)`.
_NOTE = re.compile(r"\s\(")
_NUMBER = re.compile(r"^(.*?)(?:\s+\(|\s*\bDATED\b|\s*$)", re.I)


def listing_url(authority: str) -> str:
    return f"{BASE_URL}/admincontrol/registered_projects/{AUTHORITIES[authority]}"


def _clean(text: str) -> str:
    """Strip a cell, turning any run of whitespace that has a line break or tab into one space."""
    return _BREAKING_SPACE.sub(" ", text).strip()


def _extract_table(html: str) -> str:
    """Return the ``compliant_hearing`` element, including any tables nested in it."""
    opening = _TABLE_OPEN.search(html)
    if opening is None:
        raise ReraError(
            'no <table id="compliant_hearing"> in the response; the portal answered with '
            "something other than the registered-projects listing"
        )
    depth = 0
    for tag in _TABLE_TAG.finditer(html, opening.start()):
        depth += -1 if tag.group(1) else 1
        if depth == 0:
            return html[opening.start():html.index(">", tag.end()) + 1]
    raise ReraError('<table id="compliant_hearing"> is never closed; the response is truncated')


class _Cell:
    __slots__ = ("pieces", "spaced", "anchors")

    def __init__(self) -> None:
        self.pieces: list[str] = []  # text, <br> dropped
        self.spaced: list[str] = []  # text, <br> as a line break
        self.anchors: list[dict[str, str | None]] = []


class _TableParser(HTMLParser):
    """Collect every ``<tr>`` of one table as a list of cells. Comments are ignored."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[list[_Cell], bool]] = []  # (cells, header row)
        self._row: list[_Cell] | None = None
        self._header = False
        self._cell: _Cell | None = None

    def _end_cell(self) -> None:
        if self._cell is not None and self._row is not None:
            self._row.append(self._cell)
        self._cell = None

    def _end_row(self) -> None:
        self._end_cell()
        if self._row is not None:
            self.rows.append((self._row, self._header))
        self._row = None
        self._header = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._end_row()
            self._row = []
        elif tag in ("td", "th"):
            self._end_cell()
            if self._row is None:
                self._row = []
            self._header = self._header or tag == "th"
            self._cell = _Cell()
        elif self._cell is not None:
            if tag == "br":
                self._cell.spaced.append("\n")
            elif tag == "a":
                self._cell.anchors.append(dict(attrs))

    def handle_endtag(self, tag: str) -> None:
        if tag == "tr":
            self._end_row()
        elif tag in ("td", "th"):
            self._end_cell()

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.pieces.append(data)
            self._cell.spaced.append(data)

    def close(self) -> None:
        super().close()
        self._end_row()


def _text(cell: _Cell) -> str | None:
    return _clean("".join(cell.pieces)) or None


def _anchor_url(cell: _Cell) -> str | None:
    """The first link in the cell: an ``href``, or the URL a ``window.open`` call opens."""
    for anchor in cell.anchors:
        href = anchor.get("href")
        if href and href.startswith("http"):
            return href
        opened = _OPEN_URL.search(anchor.get("onclick") or "")
        if opened:
            return opened.group(1)
    return None


def _corrigendum_id(cell: _Cell) -> str | None:
    for anchor in cell.anchors:
        found = _CORRIGENDUM.search(anchor.get("onclick") or "")
        if found:
            return found.group(1)
    return None


#: Printed by the portal for a row with no real date.
_EPOCH = "1970-01-01"


def _dmy_dots(day: str, month: str, year: str) -> str | None:
    """ISO date from the dotted day, month, year the certificate cell prints, or None."""
    return iso_date(f"{int(day):02d}/{int(month):02d}/{year}", "/")


def _certificate(cell: _Cell) -> tuple[str, str | None, bool]:
    """Split the certificate cell into ``(registration_no, approved_on, lapsed)``.

    ``registration_no`` is the text before ``DATED`` or a supersession note in parentheses,
    and ``approved_on`` is the date after ``DATED``, read from the text before
    any parenthesis so a superseded certificate's date isn't taken by mistake.
    """
    spaced = " ".join(_clean("".join(cell.spaced)).split())
    lapsed = LAPSED_TEXT in spaced
    own = spaced.replace(LAPSED_TEXT, "").strip()
    number = _NUMBER.match(own).group(1).strip()  # always matches: the tail is optional
    dated = _DATED.search(_NOTE.split(own, maxsplit=1)[0])
    approved_on = _dmy_dots(*dated.groups()) if dated else None
    return number, approved_on, lapsed


def parse_listing(
    html: str,
    *,
    url: str | None = None,
    source_sha256: str | None = None,
    extracted_at: str | None = None,
) -> list[dict]:
    """Parse the ``compliant_hearing`` table of a Haryana listing into project rows.

    Raises :class:`ReraError` when the table is missing, when any of the 14
    expected header strings is missing or repeated, when a data row's width
    differs from the header's, when a row has no certificate number, or when two
    rows share one. Never returns a row it could not label.

    ``source_sha256`` is the digest of the stored response. When omitted it is
    the digest of *html* encoded as UTF-8.
    """
    parser = _TableParser()
    parser.feed(_extract_table(html))
    parser.close()

    header_cells = next((cells for cells, is_header in parser.rows if is_header), None)
    if header_cells is None:
        raise ReraError("the project table has no header row")
    header = [_text(c) or "" for c in header_cells]
    missing = [h for h in HEADERS if h not in header]
    repeated = sorted({h for h in HEADERS if header.count(h) > 1})
    if missing or repeated:
        raise ReraError(
            "the project table's headers changed. "
            f"Missing: {missing or 'none'}. Repeated: {repeated or 'none'}. "
            f"Found: {header}"
        )
    index = {name: header.index(name) for name in HEADERS}
    # Headers beyond the 14 stay in `fields` under their own string.
    extra = [(i, h) for i, h in enumerate(header) if h and h not in index]

    sha = source_sha256 or hashlib.sha256(html.encode("utf-8")).hexdigest()
    stamp = extracted_at or now_iso()
    source_url = url or listing_url(DEFAULT_AUTHORITY)
    rows: list[dict] = []
    seen: dict[str, str | None] = {}
    for cells, is_header in parser.rows:
        if is_header:
            continue
        if len(cells) != len(header):
            raise ReraError(
                f"a data row has {len(cells)} cells but the header has {len(header)}; "
                f"row starts {[_text(c) for c in cells[:3]]}"
            )

        def cell(name: str, _cells: list[_Cell] = cells) -> _Cell:
            return _cells[index[name]]

        serial = _text(cell("Serial No."))
        number, approved_on, lapsed = _certificate(cell("Registration Certificate Number"))
        if not number:
            raise ReraError(f"row Serial No. {serial!r} has no registration certificate number")
        if number in seen:
            raise ReraError(
                f"registration certificate number {number!r} appears in rows "
                f"{seen[number]!r} and {serial!r}; refusing to store fewer rows than the "
                "portal listed"
            )
        seen[number] = serial

        valid_until = iso_date(_text(cell("Registration Up-to")), "/")
        if valid_until == _EPOCH:
            valid_until = None

        fields: dict[str, str | None] = {}
        for name in HEADERS:
            c = cell(name)
            if name in LINK_COLUMNS:
                fields[name] = _anchor_url(c) or _text(c)
            else:
                fields[name] = _text(c)
        fields[PROJECT_ID_URL_KEY] = _anchor_url(cell("Project ID"))
        fields[CORRIGENDUM_ID_KEY] = _corrigendum_id(cell("View Certificate"))
        fields[OC_ID_KEY] = _corrigendum_id(cell("View OC/CC/PCC"))
        for i, name in extra:
            fields[name] = _text(cells[i])

        rows.append({
            "key": f"RERA|{STATE}|{number}",
            "kind": "rera_project_row",
            "record_type": "rera_project_row",
            "source": SOURCE,
            "state": STATE,
            "registration_no": number,
            "promoter": _text(cell("Builder")),
            "project_name": _text(cell("Project Name")),
            "district": _text(cell("Project District")),
            "approved_on": approved_on,
            "registration_valid_until": valid_until,
            "lapsed": lapsed,
            "fields": fields,
            "listing_url": source_url,
            "source_sha256": sha,
            "extracted_at": stamp,
        })
    return rows


def resolve_authority(name: str) -> str:
    """Match *name* to an authority key, ignoring case."""
    key = name.strip().casefold()
    if key not in AUTHORITIES:
        raise ReraError(
            f"{name!r} is not a Haryana RERA authority. Choose from: {', '.join(AUTHORITIES)}."
        )
    return key


class HaryanaReraProbe:
    """Fetch Haryana RERA registered-project listings, one GET per authority."""

    state = STATE

    def __init__(
        self,
        out_dir: Path | str,
        *,
        sleep: float = MIN_SLEEP_SEC,
        session: Any | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.sleep = sleep
        self._session = session
        self._sleep_fn = sleep_fn
        self.manifest_path = self.out_dir / MANIFEST_FILE
        self.rows_path = self.out_dir / ROWS_FILE

    @property
    def session(self) -> Any:
        if self._session is None:
            self._session = make_session()
        return self._session

    def _record(self, authority: str, fetched_at: str, *, status: str, **extra: Any) -> dict:
        record: dict[str, Any] = {
            "key": f"RERA|{STATE}|{authority}|{fetched_at[:10]}",
            "kind": "rera_listing",
            "record_type": "rera_listing",
            "source": SOURCE,
            "state": STATE,
            "url": listing_url(authority),
            "method": "GET",
            "form": {},
            "http_status": None,
            "path": None,
            "sha256": None,
            "bytes": None,
            "row_count": None,
            "fetched_at": fetched_at,
            "status": status,
            "error": None,
        }
        record.update(extra)
        return record

    def fetch_authority(self, authority: str) -> dict:
        """GET one listing, store the response, and write its rows and manifest record.

        Returns the manifest record. A failure (an HTTP error, a response
        without the table, a changed header, a repeated certificate number)
        returns a ``status: "error"`` record and writes no rows and no file.
        """
        url = listing_url(authority)
        fetched_at = now_iso()
        http_status: int | None = None
        try:
            resp = self.session.get(url, stream=True, timeout=120)
            http_status = resp.status_code
            if http_status != 200:
                return self._record(
                    authority, fetched_at, status="error", http_status=http_status,
                    error=f"HTTP {http_status} from {url}",
                )
            refuse_challenge(resp, url, expect_body=True)
            body = read_capped_response(resp, max_bytes=MAX_LISTING_BYTES)
            sha = hashlib.sha256(body).hexdigest()
            rows = parse_listing(
                body.decode("utf-8", errors="replace"),
                url=url,
                source_sha256=sha,
                extracted_at=fetched_at,
            )
        except (ReraError, ChallengeDetected) as exc:
            return self._record(
                authority, fetched_at, status="error", http_status=http_status,
                error=str(exc),
            )
        except Exception as exc:  # network, robots, size ceiling: report, keep going
            return self._record(
                authority, fetched_at, status="error", error=f"{type(exc).__name__}: {exc}"
            )

        relative = Path("listings") / "IN-HR" / f"{authority}-{fetched_at[:10]}.html"
        write_bytes_atomic(self.out_dir / relative, body)
        upsert_jsonl(self.rows_path, rows)
        return self._record(
            authority, fetched_at, status="downloaded", http_status=http_status,
            path=relative.as_posix(), sha256=sha, bytes=len(body), row_count=len(rows),
        )

    def run(self, authorities: list[str] | None = None, *, dry_run: bool = False) -> list[dict]:
        """Fetch each authority in turn and return one manifest record per request.

        With ``dry_run`` nothing is requested or written: the records say what
        would be requested, with ``status: "dry_run"``.
        """
        names = authorities or [DEFAULT_AUTHORITY]
        if dry_run:
            return [self._record(a, now_iso(), status="dry_run") for a in names]
        records: list[dict] = []
        for i, authority in enumerate(names):
            if i:
                self._sleep_fn(self.sleep)
            record = self.fetch_authority(authority)
            upsert_jsonl(self.manifest_path, [record])
            records.append(record)
        return records
