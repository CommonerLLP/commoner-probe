# SPDX-License-Identifier: MIT
"""Tamil Nadu RERA registered-projects listings (buildings and layouts).

The Tamil Nadu Real Estate Regulatory Authority publishes its online
registrations at ``https://rera.tn.gov.in``. The contract below was verified
live on November 11, 2026 and is recorded, with the exact header strings, in
``tests/fixtures/rera_tamil_nadu/README.md``.

* ``GET /registered-building/tn`` and ``GET /registered-layout/tn`` return the
  whole table for the current year (the page's DataTable paginates in the
  browser). The page carries a ``<form method="POST">`` with a Laravel
  ``_token`` and a ``year`` select (2026, 2025, and 2024).
* An older year is the same URL with ``POST _token=<token>&year=<year>``. The
  token must come from a ``GET`` of that page in the same session, because
  Laravel checks it against the session cookie. The adapter raises
  :class:`ReraError` when the page has no token, and never posts an empty one.
  The default ``pip install commoner-probe`` session keeps no cookies, so an
  older year needs ``commoner-probe[http]``.
* No login or CAPTCHA. The building list is about 1.4 MB (402 rows on
  November 11, 2026) and the layout list is about 11 MB (3,945 rows). The
  adapter waits at least five seconds between requests.

``robots.txt`` has two lines: ``Disallow: /uploads/`` and ``Disallow: /*.pdf$``.
``make_session`` reads robots.txt with :mod:`urllib.robotparser`, which matches
path prefixes and ignores the ``*`` wildcard, so it enforces the first line and
permits every PDF. The adapter therefore refuses both itself, before any
request: :func:`refuse_disallowed_url` raises for a path under ``/uploads/`` and
for any path ending in ``.pdf``. Every request goes through
:meth:`TamilNaduReraProbe._request`, which calls it first. The listing rows link
to PDFs (the current-status column of some rows links to a ``.pdf`` under
``/public/storage/``). The adapter records those links in ``fields`` and never
fetches them or any other link in a row.

The parser anchors all 9 columns on their exact header strings, so a portal
redesign raises :class:`ReraError` instead of returning mislabelled rows. These
conventions are the portal's, not ours:

* ``Project Registration No.`` holds the number and ``dated dd-mm-yyyy``.
  ``registration_no`` is the number and ``approved_on`` is the date.
* ``Name and Address of the Promoter`` is one cell, so ``promoter`` is the whole
  text. The adapter doesn't guess where the name ends.
* ``Project Details and Address`` starts with ``Project Name: <name>`` on its own
  line. The portal has no district column, so ``district`` is null.
* ``Approval Details`` is free text typed by the portal's staff. The typed
  ``cmda_permission_no`` is the CMDA planning-permission number (``OL-PP/...``)
  and, when the text gives only the letter, the CMDA letter number
  (``CMDA/PP/...``). The typed ``gcc_permission_no`` is the Greater Chennai
  Corporation building-permission number (``CEBA/...``). Both are null when the
  text has no number in that shape, and the full text stays in ``fields``.
* The coordinates in ``Other Details`` are typed by promoters, and most are not
  decimal degrees (``130503.6`` for 13 degrees 05 minutes 03.6 seconds, or the
  same number in both). ``latitude`` and ``longitude`` are set only when both
  are decimal degrees with at least three decimal places inside Tamil Nadu's
  bounding box and are not equal. Otherwise both are null and ``fields`` keeps
  the text.
* ``Project Completion Date`` is ``dd.mm.yyyy`` or ``Completed``. A revised date
  appears only in free text in the status column, so it isn't typed.
"""

from __future__ import annotations

import hashlib
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlencode, urlsplit

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

BASE_URL = "https://rera.tn.gov.in"
STATE = "IN-TN"
SOURCE = "rera-tamil-nadu"
DEFAULT_KIND = "building"

#: The listing path for each kind.
KINDS: dict[str, str] = {
    "building": "/registered-building/tn",
    "layout": "/registered-layout/tn",
}

#: Ceiling for one listing response. The layout list is about 11 MB.
MAX_LISTING_BYTES = 64 * 1024 * 1024

#: The 9 header strings, in the order the portal prints them.
HEADERS: tuple[str, ...] = (
    "S. No",
    "Project Registration No.",
    "Name and Address of the Promoter",
    "Project Details and Address",
    "Approval Details",
    "Project Completion Date",
    "Other Details",
    "Form-c",
    "Current Status of the Project",
)

PROMOTER_URL_KEY = "Promoter Details URL"
PROJECT_URL_KEY = "Project Details URL"
STATUS_URL_KEY = "Current Status URL"

#: Tamil Nadu's bounding box, padded. Coordinates outside it are typing errors.
_LAT_RANGE = (7.5, 14.0)
_LON_RANGE = (76.0, 81.0)

_TABLE_OPEN = re.compile(r"<table\b[^>]*\bid\s*=\s*[\"']example1[\"'][^>]*>", re.I)
_TABLE_TAG = re.compile(r"<(/?)table\b", re.I)
_FORM_TOKEN = re.compile(
    r"<input\b[^>]*\bname\s*=\s*[\"']_token[\"'][^>]*\bvalue\s*=\s*[\"']([^\"']+)[\"']", re.I
)
_YEAR_OPTION = re.compile(r"<option\b[^>]*\bvalue\s*=\s*[\"'](\d{4})[\"']([^>]*)>", re.I)
_BREAKING_SPACE = re.compile(r"\s*[\r\n\t\f\v]\s*")
_REGISTRATION = re.compile(r"^(\S+)\s+dated\s+(\d{2}-\d{2}-\d{4})$", re.I)
_PROJECT_NAME = re.compile(r"^Project Name:\s*(.*)$", re.I)
_DECIMAL = re.compile(r"^\d{1,3}\.\d{3,}$")
_COORDINATES = re.compile(
    r"Latitude\s*-\s*([^;]*?)\s*;\s*Longitude\s*-\s*([^;]*?)\s*;", re.I
)
_OL_PP = re.compile(r"\b(OL-PP/[A-Z]+/\d+/\d{4})\b")
_CMDA_LETTER = re.compile(r"\b((?:CMDA/)?PP/[A-Z]+/[A-Z]/\d+/\d{4})\b")
_CMDA_WORD = re.compile(r"\bC\s?M\s?D\s?A\b")
_GCC_WORD = re.compile(r"Greater\s+Chennai\s+Corporation|\bGCC\b", re.I)
_GCC_PERMIT = re.compile(r"\b(CEBA/[A-Z0-9]+/\d+/\d{4})")


def listing_url(kind: str) -> str:
    return BASE_URL + KINDS[kind]


def refuse_disallowed_url(url: str) -> None:
    """Raise :class:`ReraError` for a URL that TNRERA's robots.txt disallows.

    robots.txt disallows ``/uploads/`` and ``/*.pdf$``. ``make_session`` enforces
    only the first, so this refuses both. A ``.pdf`` path is refused in any case,
    and after percent-decoding, so ``%2Epdf`` doesn't slip through.
    """
    path = unquote(urlsplit(url).path).lower()
    if path.startswith("/uploads/") or path == "/uploads" or path.endswith(".pdf"):
        raise ReraError(
            f"refusing {url}: TNRERA's robots.txt disallows /uploads/ and *.pdf. "
            "The adapter records such links and never fetches them."
        )


def resolve_kind(name: str) -> str:
    """Match *name* to a listing kind, ignoring case."""
    key = name.strip().casefold()
    if key not in KINDS:
        raise ReraError(
            f"{name!r} is not a Tamil Nadu RERA listing kind. Choose from: {', '.join(KINDS)}."
        )
    return key


def _clean(text: str) -> str:
    """Strip a cell, turning any run of whitespace that has a line break or tab into one space."""
    return _BREAKING_SPACE.sub(" ", text).strip()


def _extract_table(html: str) -> str:
    """Return the ``example1`` element, including any tables nested in it."""
    opening = _TABLE_OPEN.search(html)
    if opening is None:
        raise ReraError(
            'no <table id="example1"> in the response; the portal answered with '
            "something other than a registered-projects listing"
        )
    depth = 0
    for tag in _TABLE_TAG.finditer(html, opening.start()):
        depth += -1 if tag.group(1) else 1
        if depth == 0:
            return html[opening.start():html.index(">", tag.end()) + 1]
    raise ReraError('<table id="example1"> is never closed; the response is truncated')


def parse_form(html: str) -> tuple[str | None, list[str], str | None]:
    """Read ``(token, years, selected_year)`` from a listing page's year form.

    ``token`` is None when the page has no ``_token`` input. ``years`` are the
    ``year`` select's option values in page order. ``selected_year`` is the
    option marked ``selected``, or None (a GET page marks none, and shows the
    first year).
    """
    token = _FORM_TOKEN.search(html)
    years: list[str] = []
    selected: str | None = None
    for m in _YEAR_OPTION.finditer(html):
        years.append(m.group(1))
        if re.search(r"\bselected\b", m.group(2), re.I):
            selected = m.group(1)
    return (token.group(1) if token else None, years, selected)


class _Cell:
    __slots__ = ("spaced", "anchors", "_anchor")

    def __init__(self) -> None:
        self.spaced: list[str] = []  # text, <br> as a line break
        self.anchors: list[dict[str, Any]] = []
        self._anchor: dict[str, Any] | None = None


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
                self._cell._anchor = {"href": dict(attrs).get("href"), "text": []}
                self._cell.anchors.append(self._cell._anchor)

    def handle_endtag(self, tag: str) -> None:
        if tag == "tr":
            self._end_row()
        elif tag in ("td", "th"):
            self._end_cell()
        elif tag == "a" and self._cell is not None:
            self._cell._anchor = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.spaced.append(data)
            if self._cell._anchor is not None:
                self._cell._anchor["text"].append(data)

    def close(self) -> None:
        super().close()
        self._end_row()


def _text(cell: _Cell) -> str | None:
    """The cell text, with a line break as one space."""
    return _clean("".join(cell.spaced)) or None


def _lines(cell: _Cell) -> list[str]:
    """The cell's text split at each ``<br>``, empty lines dropped."""
    joined = "".join(cell.spaced)
    return [line for line in (_clean(p) for p in joined.split("\n")) if line]


def _href(cell: _Cell) -> str | None:
    for anchor in cell.anchors:
        if anchor["href"]:
            return anchor["href"].strip()
    return None


def _coordinate(text: str, low_high: tuple[float, float]) -> float | None:
    if not _DECIMAL.match(text):
        return None
    value = float(text)
    return value if low_high[0] <= value <= low_high[1] else None


def parse_coordinates(text: str | None) -> tuple[float | None, float | None]:
    """Read ``(latitude, longitude)`` from ``Latitude-..; Longitude-..;``.

    Both are None unless both are decimal degrees inside Tamil Nadu's bounding
    box and differ from each other. One bad value voids the pair, because a
    pair that was typed wrong is wrong in both.
    """
    if not text:
        return None, None
    m = _COORDINATES.search(text)
    if not m:
        return None, None
    lat = _coordinate(m.group(1), _LAT_RANGE)
    lon = _coordinate(m.group(2), _LON_RANGE)
    if lat is None or lon is None or m.group(1) == m.group(2):
        return None, None
    return lat, lon


def permit_numbers(approval: str | None) -> tuple[str | None, str | None]:
    """Read ``(cmda_permission_no, gcc_permission_no)`` from the Approval Details text.

    CMDA is the planning-permission number (``OL-PP/...``), or the CMDA letter
    number when the text names no planning-permission number. GCC is the Greater
    Chennai Corporation building-permission number (``CEBA/...``), read only
    after the text names the Corporation. Each is None when the text has none
    in that shape.
    """
    if not approval:
        return None, None
    # The portal's staff sometimes type a space around a slash (`OL-PP/ IND/ 0028/ 2024`).
    text = re.sub(r"\s*/\s*", "/", " ".join(approval.split()))
    cmda = None
    if _CMDA_WORD.search(text):
        found = _OL_PP.search(text) or _CMDA_LETTER.search(text)
        cmda = found.group(1) if found else None
    gcc = None
    mention = _GCC_WORD.search(text)
    if mention:
        found = _GCC_PERMIT.search(text[mention.start():])
        gcc = found.group(1) if found else None
    return cmda, gcc


def parse_listing(
    html: str,
    *,
    kind: str = DEFAULT_KIND,
    url: str | None = None,
    source_sha256: str | None = None,
    extracted_at: str | None = None,
) -> list[dict]:
    """Parse the ``example1`` table of a Tamil Nadu listing into project rows.

    Raises :class:`ReraError` when the table is missing, when any of the 9
    expected header strings is missing or repeated, when a data row's width
    differs from the header's, when a row has no registration number, or when
    two rows share one. Never returns a row it could not label.

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
    extra = [(i, h) for i, h in enumerate(header) if h and h not in index]

    sha = source_sha256 or hashlib.sha256(html.encode("utf-8")).hexdigest()
    stamp = extracted_at or now_iso()
    source_url = url or listing_url(kind)
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

        serial = _text(cell("S. No"))
        registration = _text(cell("Project Registration No."))
        match = _REGISTRATION.match(registration or "")
        if match is None:
            raise ReraError(
                f"row S. No {serial!r} has no 'number dated dd-mm-yyyy' registration: "
                f"{registration!r}"
            )
        number = match.group(1)
        if number in seen:
            raise ReraError(
                f"registration number {number!r} appears in rows {seen[number]!r} and "
                f"{serial!r}; refusing to store fewer rows than the portal listed"
            )
        seen[number] = serial

        detail_lines = _lines(cell("Project Details and Address"))
        named = _PROJECT_NAME.match(detail_lines[0]) if detail_lines else None
        project_name = (named.group(1).strip() or None) if named else None

        other = cell("Other Details")
        lat, lon = parse_coordinates(" ".join(_lines(other)))
        approval = _text(cell("Approval Details"))
        cmda, gcc = permit_numbers(approval)

        fields: dict[str, str | None] = {name: _text(cell(name)) for name in HEADERS}
        fields["Form-c"] = _href(cell("Form-c")) or fields["Form-c"]
        fields[PROMOTER_URL_KEY] = None
        fields[PROJECT_URL_KEY] = None
        for anchor in other.anchors:
            label = _clean("".join(anchor["text"]))
            if anchor["href"] and label:
                fields[f"{label} URL"] = anchor["href"].strip()
        fields[STATUS_URL_KEY] = _href(cell("Current Status of the Project"))
        for i, name in extra:
            fields[name] = _text(cells[i])

        rows.append({
            "key": f"RERA|{STATE}|{number}",
            "kind": "rera_project_row",
            "record_type": "rera_project_row",
            "source": SOURCE,
            "state": STATE,
            "registration_no": number,
            "promoter": _text(cell("Name and Address of the Promoter")),
            "project_name": project_name,
            "district": None,
            "approved_on": iso_date(match.group(2), "-"),
            "cmda_permission_no": cmda,
            "gcc_permission_no": gcc,
            "latitude": lat,
            "longitude": lon,
            "fields": fields,
            "listing_url": source_url,
            "source_sha256": sha,
            "extracted_at": stamp,
        })
    return rows


class TamilNaduReraProbe:
    """Fetch Tamil Nadu RERA listings: one GET, plus one POST for an older year."""

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

    def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        """Send one request, after refusing any URL robots.txt disallows."""
        refuse_disallowed_url(url)
        return getattr(self.session, method.lower())(url, **kwargs)

    def _record(
        self, kind: str, year: str | None, fetched_at: str, *, status: str, **extra: Any
    ) -> dict:
        record: dict[str, Any] = {
            "key": f"RERA|{STATE}|{kind}|{year or 'current'}|{fetched_at[:10]}",
            "kind": "rera_listing",
            "record_type": "rera_listing",
            "source": SOURCE,
            "state": STATE,
            "url": listing_url(kind),
            "method": "POST" if year else "GET",
            "form": {"year": year} if year else {},
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

    def _read(self, resp: Any, url: str) -> bytes:
        refuse_challenge(resp, url, expect_body=True)
        return read_capped_response(resp, max_bytes=MAX_LISTING_BYTES)

    def fetch_listing(self, kind: str, year: str | None = None) -> dict:
        """GET one listing, POST the token back for an older year, and write the rows.

        Returns the manifest record. A failure (an HTTP error, a missing token,
        a year the form doesn't offer, a changed header, a repeated registration
        number) returns a ``status: "error"`` record and writes no rows and no
        file. With no *year*, the GET page is the current year and no POST is
        sent.
        """
        url = listing_url(kind)
        fetched_at = now_iso()
        http_status: int | None = None
        try:
            resp = self._request("GET", url, stream=True, timeout=300)
            http_status = resp.status_code
            if http_status != 200:
                return self._record(
                    kind, year, fetched_at, status="error", http_status=http_status,
                    error=f"HTTP {http_status} from {url}",
                )
            body = self._read(resp, url)
            token, years, selected = parse_form(body.decode("utf-8", errors="replace"))
            page_year = selected or (years[0] if years else None)
            if year is not None and year != page_year:
                if year not in years:
                    raise ReraError(
                        f"the {kind} form offers years {years or 'none'}, not {year!r}"
                    )
                if not token:
                    raise ReraError(
                        f"the {kind} page at {url} has no _token input; "
                        "refusing to POST an empty token"
                    )
                if getattr(self.session, "cookies", None) is None:
                    raise ReraError(
                        "an older year needs a session that keeps cookies, because the "
                        "portal checks the token against the session. "
                        "Install commoner-probe[http]."
                    )
                self._sleep_fn(self.sleep)
                resp = self._request(
                    "POST", url,
                    data=urlencode({"_token": token, "year": year}),
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    stream=True, timeout=300,
                )
                http_status = resp.status_code
                if http_status != 200:
                    return self._record(
                        kind, year, fetched_at, status="error", http_status=http_status,
                        error=f"HTTP {http_status} from POST {url} (year {year})",
                    )
                body = self._read(resp, url)
                request_year: str | None = year
            else:
                request_year = None
                year = page_year
            sha = hashlib.sha256(body).hexdigest()
            rows = parse_listing(
                body.decode("utf-8", errors="replace"),
                kind=kind, url=url, source_sha256=sha, extracted_at=fetched_at,
            )
        except (ReraError, ChallengeDetected) as exc:
            return self._record(
                kind, year, fetched_at, status="error", http_status=http_status,
                error=str(exc),
            )
        except Exception as exc:  # network, robots, size ceiling: report, keep going
            return self._record(
                kind, year, fetched_at, status="error", error=f"{type(exc).__name__}: {exc}"
            )

        relative = Path("listings") / "IN-TN" / f"{kind}-{year or 'current'}-{fetched_at[:10]}.html"
        write_bytes_atomic(self.out_dir / relative, body)
        upsert_jsonl(self.rows_path, rows)
        return self._record(
            kind, request_year, fetched_at, status="downloaded", http_status=http_status,
            path=relative.as_posix(), sha256=sha, bytes=len(body), row_count=len(rows),
        )

    def run(
        self,
        kinds: list[str] | None = None,
        *,
        year: str | None = None,
        dry_run: bool = False,
    ) -> list[dict]:
        """Fetch each kind in turn and return one manifest record per listing.

        With ``dry_run`` nothing is requested or written: the records say what
        would be requested, with ``status: "dry_run"``.
        """
        names = kinds or [DEFAULT_KIND]
        if dry_run:
            return [self._record(k, year, now_iso(), status="dry_run") for k in names]
        records: list[dict] = []
        for i, kind in enumerate(names):
            if i:
                self._sleep_fn(self.sleep)
            record = self.fetch_listing(kind, year)
            upsert_jsonl(self.manifest_path, [record])
            records.append(record)
        return records
