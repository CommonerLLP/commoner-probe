# SPDX-License-Identifier: MIT
"""Karnataka RERA project listing.

The Karnataka Real Estate Regulatory Authority publishes every registered and
applied-for project at ``https://rera.karnataka.gov.in``. The contract below
was verified live on November 11, 2026 and is recorded, with the exact header
strings, in ``tests/fixtures/rera_karnataka/README.md``.

* The search form lives on ``GET /viewAllProjects`` (``<form id="loginFormID">``).
  A bare ``GET /projectViewDetails`` answers HTTP 405.
* The result is ``POST /projectViewDetails`` with
  ``project=&firm=&appNo=&regNo=&district=<option value>&subdistrict=0&btn1=Search``.
  There is no token, no cookie, and no CAPTCHA. ``robots.txt`` answers 404.
* The ``district`` value is the option's ``value`` attribute exactly.
  ``Bengaluru  Rural`` has two spaces.
* Every response carries about 6.5 MB of page chrome, and Bengaluru Urban's is
  about 33 MB. The adapter defaults to Bengaluru Urban, waits at least five
  seconds between requests, and reads the district list (a 6.5 MB GET) only for
  ``--all-districts``, ``--list-districts``, or a district name it doesn't know.

The result table is ``<table id="approvedTable">``. The parser anchors every
column on its exact header string, not on position, so a portal redesign that
adds, drops, or reorders a column raises :class:`ReraError` instead of
returning mislabelled rows. See ``gridview.py`` for the failure mode.

Each row keeps every column in ``fields``, keyed by the portal's header string,
and adds the typed dates. Three conventions are the portal's, not ours:

* ``APPROVED ON`` and ``PROPOSED COMPLETION DATE`` are ``dd/mm/yyyy``. The
  registration-time date and the extension ranges are ``dd-mm-yyyy``.
* An extension cell is a range, ``<start> - <end>``. The portal prints
  ``<start> -`` for 47 Bengaluru Urban COVID-19 extensions, so ``_end`` is null.
* ``STATUS`` holds a status ``<p>`` and, for a non-approved project, a second
  ``<p>`` with the authority's order text. ``fields["STATUS"]`` is the first and
  ``fields["STATUS ORDER TEXT"]`` is the second, a key the portal has no header
  for.

Whitespace inside a cell: a run that contains a line break or a tab becomes one
space, and a run of plain spaces stays as published (``Bengaluru  Rural``). Link
columns hold the link, not text: the ``id`` for the project and complaints
icons, the ``href`` for the four certificate and order icons. The adapter never
follows them.
"""

from __future__ import annotations

import hashlib
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

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
    slugify,
    upsert_jsonl,
    write_bytes_atomic,
)

BASE_URL = "https://rera.karnataka.gov.in"
FORM_URL = f"{BASE_URL}/viewAllProjects"
LISTING_URL = f"{BASE_URL}/projectViewDetails"
STATE = "IN-KA"
SOURCE = "rera-karnataka"
DEFAULT_DISTRICT = "Bengaluru Urban"

#: Ceiling for one listing response. Bengaluru Urban is about 33 MB.
MAX_LISTING_BYTES = 128 * 1024 * 1024

#: The option values of the ``district`` select on November 11, 2026, used to
#: resolve a name without fetching the 6.5 MB form page. ``--list-districts``
#: reads the live list.
KNOWN_DISTRICTS: tuple[str, ...] = (
    "Bagalkot", "Ballari", "Belagavi", "Bengaluru  Rural", "Bengaluru Urban", "Bidar",
    "Chamarajanagar", "Chikkaballapura", "Chikkamagaluru", "Chitradurga",
    "Dakshina Kannada", "Davangere", "Dharwad", "Gadag", "Hassan", "Haveri",
    "Kalaburagi", "Kodagu", "Kolar", "Koppal", "Mandya", "Mysore", "Raichur",
    "Ramanagara", "Shivamogga", "Tumakuru", "Udupi", "Uttara Kannada",
    "Vijayanagara", "Vijayapura", "Yadgir",
)

#: The 21 header strings, in the order the portal prints them.
HEADERS: tuple[str, ...] = (
    "S.No",
    "ACKNOWLEDGEMENT NO",
    "REGISTRATION NO",
    "View Project Details",
    "PROMOTER NAME",
    "PROJECT NAME",
    "STATUS",
    "DISTRICT",
    "TALUK",
    "PROJECT TYPE",
    "APPROVED ON",
    "PROPOSED COMPLETION DATE",
    "PROPOSED COMPLETION DATE AT THE TIME OF REGISTRATION",
    "COVID-19 EXTENSION DATE",
    "SECTION 6 EXTENSION DATE",
    "FURTHER EXTENSION DATE",
    "Certificate",
    "Covid Certificate",
    "Renewed Certificate",
    "FURTHER EXTENSION ORDER",
    "COMPLAINTS / LITIGATION",
)

#: Columns whose value is a link, not text, and the attribute that holds it.
LINK_COLUMNS: dict[str, str] = {
    "View Project Details": "id",
    "Certificate": "href",
    "Covid Certificate": "href",
    "Renewed Certificate": "href",
    "FURTHER EXTENSION ORDER": "href",
    "COMPLAINTS / LITIGATION": "id",
}

STATUS_ORDER_KEY = "STATUS ORDER TEXT"

_DATE = r"\d{2}-\d{2}-\d{4}"
_RANGE = re.compile(rf"\s*({_DATE})\s*(?:-\s*({_DATE})?)?\s*")
_TABLE_OPEN = re.compile(r"<table\b[^>]*\bid\s*=\s*[\"']approvedTable[\"'][^>]*>", re.I)
_TABLE_TAG = re.compile(r"<(/?)table\b", re.I)
_BREAKING_SPACE = re.compile(r"\s*[\r\n\t\f\v]\s*")


def _clean(text: str) -> str:
    """Strip a cell, turning any run of whitespace that has a line break or tab into one space."""
    return _BREAKING_SPACE.sub(" ", text).strip()


def _extract_table(html: str) -> str:
    """Return the ``approvedTable`` element, including any tables nested in it."""
    opening = _TABLE_OPEN.search(html)
    if opening is None:
        raise ReraError(
            'no <table id="approvedTable"> in the response; the portal answered with '
            "something other than the project listing"
        )
    depth = 0
    for tag in _TABLE_TAG.finditer(html, opening.start()):
        depth += -1 if tag.group(1) else 1
        if depth == 0:
            return html[opening.start():html.index(">", tag.end()) + 1]
    raise ReraError('<table id="approvedTable"> is never closed; the response is truncated')


class _Cell:
    __slots__ = ("pieces", "paragraphs", "attrs")

    def __init__(self) -> None:
        self.pieces: list[str] = []
        self.paragraphs: list[str] = []
        self.attrs: dict[str, str | None] = {}


class _TableParser(HTMLParser):
    """Collect every ``<tr>`` of one table as a list of cells."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[list[_Cell], bool]] = []  # (cells, header row)
        self._row: list[_Cell] | None = None
        self._header = False
        self._cell: _Cell | None = None
        self._para: list[str] | None = None

    def _end_para(self) -> None:
        if self._cell is not None and self._para is not None:
            text = _clean("".join(self._para))
            if text:
                self._cell.paragraphs.append(text)
        self._para = None

    def _end_cell(self) -> None:
        if self._cell is None:
            return
        self._end_para()
        if self._row is not None:
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
            if tag in ("p", "br"):
                self._end_para()
            elif tag == "a":
                for name, value in attrs:
                    self._cell.attrs.setdefault(name, value)

    def handle_endtag(self, tag: str) -> None:
        if tag == "tr":
            self._end_row()
        elif tag in ("td", "th"):
            self._end_cell()
        elif tag == "p":
            self._end_para()

    def handle_data(self, data: str) -> None:
        if self._cell is None:
            return
        self._cell.pieces.append(data)
        if self._para is None:
            self._para = []
        self._para.append(data)

    def close(self) -> None:
        super().close()
        self._end_row()


def _cell_text(cell: _Cell) -> str | None:
    return _clean("".join(cell.pieces)) or None


def _extension(text: str | None) -> tuple[str | None, str | None]:
    """Split ``dd-mm-yyyy - dd-mm-yyyy`` into ISO ``(start, end)``.

    ``<start> -`` gives ``(start, None)``, as does a lone start date.
    """
    if not text:
        return None, None
    m = _RANGE.fullmatch(text)
    if not m:
        return None, None
    return iso_date(m.group(1), "-"), iso_date(m.group(2), "-")


def parse_listing(
    html: str,
    *,
    listing_url: str = LISTING_URL,
    source_sha256: str | None = None,
    extracted_at: str | None = None,
) -> list[dict]:
    """Parse the ``approvedTable`` of a Karnataka listing into project rows.

    Raises :class:`ReraError` when the table is missing, when any of the 21
    expected header strings is missing or repeated, when a data row's width
    differs from the header's, or when a row has no acknowledgement number.
    Never returns a row it could not label.

    ``source_sha256`` is the digest of the stored response. When omitted it is
    the digest of *html* encoded as UTF-8.
    """
    parser = _TableParser()
    parser.feed(_extract_table(html))
    parser.close()

    header_cells = next((cells for cells, is_header in parser.rows if is_header), None)
    if header_cells is None:
        raise ReraError("the project table has no header row")
    header = [_cell_text(c) or "" for c in header_cells]
    missing = [h for h in HEADERS if h not in header]
    repeated = sorted({h for h in HEADERS if header.count(h) > 1})
    if missing or repeated:
        raise ReraError(
            "the project table's headers changed. "
            f"Missing: {missing or 'none'}. Repeated: {repeated or 'none'}. "
            f"Found: {header}"
        )
    index = {name: header.index(name) for name in HEADERS}
    # Headers beyond the 21 stay in `fields` under their own string.
    extra = [(i, h) for i, h in enumerate(header) if h and h not in index]

    sha = source_sha256 or hashlib.sha256(html.encode("utf-8")).hexdigest()
    stamp = extracted_at or now_iso()
    rows: list[dict] = []
    for cells, is_header in parser.rows:
        if is_header:
            continue
        if len(cells) != len(header):
            raise ReraError(
                f"a data row has {len(cells)} cells but the header has {len(header)}; "
                f"row starts {[_cell_text(c) for c in cells[:3]]}"
            )

        def text(name: str, _cells: list[_Cell] = cells) -> str | None:
            return _cell_text(_cells[index[name]])

        ack = text("ACKNOWLEDGEMENT NO")
        if not ack:
            raise ReraError(f"row S.No {text('S.No')!r} has no acknowledgement number")

        fields: dict[str, str | None] = {}
        for name in HEADERS:
            cell = cells[index[name]]
            if name in LINK_COLUMNS:
                fields[name] = cell.attrs.get(LINK_COLUMNS[name]) or None
            elif name == "STATUS":
                fields[name] = cell.paragraphs[0] if cell.paragraphs else None
                fields[STATUS_ORDER_KEY] = (
                    " ".join(cell.paragraphs[1:]) if len(cell.paragraphs) > 1 else None
                )
            else:
                fields[name] = _cell_text(cell)
        for i, name in extra:
            fields[name] = _cell_text(cells[i])

        covid = _extension(text("COVID-19 EXTENSION DATE"))
        section6 = _extension(text("SECTION 6 EXTENSION DATE"))
        further = _extension(text("FURTHER EXTENSION DATE"))
        rows.append({
            "key": f"RERA|{STATE}|{ack}",
            "kind": "rera_project_row",
            "record_type": "rera_project_row",
            "source": SOURCE,
            "state": STATE,
            "registration_no": text("REGISTRATION NO"),
            "promoter": text("PROMOTER NAME"),
            "project_name": text("PROJECT NAME"),
            "district": text("DISTRICT"),
            "approved_on": iso_date(text("APPROVED ON"), "/"),
            "proposed_completion_date_at_registration": iso_date(
                text("PROPOSED COMPLETION DATE AT THE TIME OF REGISTRATION"), "-"
            ),
            "proposed_completion_date_current": iso_date(
                text("PROPOSED COMPLETION DATE"), "/"
            ),
            "covid_extension_start": covid[0],
            "covid_extension_end": covid[1],
            "section6_extension_start": section6[0],
            "section6_extension_end": section6[1],
            "further_extension_start": further[0],
            "further_extension_end": further[1],
            "fields": fields,
            "listing_url": listing_url,
            "source_sha256": sha,
            "extracted_at": stamp,
        })
    return rows


def parse_districts(html: str) -> list[str]:
    """Return the ``district`` option values from the ``/viewAllProjects`` form.

    Values are returned exactly as the ``value`` attribute has them (the portal
    has ``Bengaluru  Rural`` with two spaces), without the ``0`` placeholder.
    """
    select = re.search(
        r"<select\b[^>]*\bname\s*=\s*[\"']district[\"'][^>]*>(.*?)</select>", html, re.S | re.I
    )
    if select is None:
        raise ReraError("no <select name=\"district\"> in the form page")
    values = re.findall(
        r"<option\b[^>]*\bvalue\s*=\s*(?:'([^']*)'|\"([^\"]*)\")", select.group(1), re.I
    )
    districts = [a or b for a, b in values if (a or b) != "0"]
    if not districts:
        raise ReraError("the district select has no options")
    return districts


def resolve_district(name: str, options: tuple[str, ...] | list[str]) -> str | None:
    """Match *name* to an option value, ignoring case and runs of spaces."""
    def norm(s: str) -> str:
        return " ".join(s.split()).casefold()

    wanted = norm(name)
    return next((o for o in options if norm(o) == wanted), None)


def listing_form(district: str) -> dict[str, str]:
    """The form values the portal's own search form sends."""
    return {
        "project": "",
        "firm": "",
        "appNo": "",
        "regNo": "",
        "district": district,
        "subdistrict": "0",
        "btn1": "Search",
    }


class KarnatakaReraProbe:
    """Fetch Karnataka RERA project listings, one POST per district."""

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
        self._pause_before_next_request = False
        self.manifest_path = self.out_dir / MANIFEST_FILE
        self.rows_path = self.out_dir / ROWS_FILE

    @property
    def session(self) -> Any:
        if self._session is None:
            self._session = make_session()
        return self._session

    def list_districts(self) -> list[str]:
        """Read the district option values from the live form page."""
        resp = self.session.get(FORM_URL, stream=True, timeout=120)
        self._pause_before_next_request = True
        resp.raise_for_status()
        body = read_capped_response(resp, max_bytes=MAX_LISTING_BYTES)
        return parse_districts(body.decode("utf-8", errors="replace"))

    def resolve_districts(self, names: list[str], *, live: bool = True) -> list[str]:
        """Map each name to the exact option value.

        Reads the live list only for a name the built-in list doesn't know, and
        with ``live=False`` never reads it: an unknown name then raises.
        """
        resolved: list[str] = []
        fetched: list[str] | None = None
        for name in names:
            value = resolve_district(name, KNOWN_DISTRICTS)
            if value is None and live:
                if fetched is None:
                    fetched = self.list_districts()
                value = resolve_district(name, fetched)
            if value is None:
                raise ReraError(
                    f"{name!r} is not a Karnataka district. "
                    "Run `commoner-probe rera --state KA --list-districts` for the list."
                )
            resolved.append(value)
        return resolved

    def _record(self, district: str, fetched_at: str, *, status: str, **extra: Any) -> dict:
        record: dict[str, Any] = {
            "key": f"RERA|{STATE}|{district}|{fetched_at[:10]}",
            "kind": "rera_listing",
            "record_type": "rera_listing",
            "source": SOURCE,
            "state": STATE,
            "url": LISTING_URL,
            "method": "POST",
            "form": listing_form(district),
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

    def fetch_district(self, district: str) -> dict:
        """POST one district, store the response, and write its rows and manifest record.

        Returns the manifest record. A failure (an HTTP error, a response
        without the table, a changed header) returns a ``status: "error"``
        record and writes no rows and no file.
        """
        fetched_at = now_iso()
        http_status: int | None = None
        try:
            resp = self.session.post(
                LISTING_URL,
                data=urlencode(listing_form(district)),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                stream=True,
                timeout=300,
            )
            http_status = resp.status_code
            if http_status != 200:
                return self._record(
                    district, fetched_at, status="error", http_status=http_status,
                    error=f"HTTP {http_status} from {LISTING_URL}",
                )
            refuse_challenge(resp, LISTING_URL, expect_body=True)
            body = read_capped_response(resp, max_bytes=MAX_LISTING_BYTES)
            sha = hashlib.sha256(body).hexdigest()
            rows = parse_listing(
                body.decode("utf-8", errors="replace"),
                source_sha256=sha,
                extracted_at=fetched_at,
            )
        except (ReraError, ChallengeDetected) as exc:
            return self._record(
                district, fetched_at, status="error", http_status=http_status,
                error=str(exc),
            )
        except Exception as exc:  # network, robots, size ceiling: report, keep going
            return self._record(
                district, fetched_at, status="error", error=f"{type(exc).__name__}: {exc}"
            )

        keys = [r["key"] for r in rows]
        if len(set(keys)) != len(keys):
            return self._record(
                district, fetched_at, status="error", http_status=http_status,
                error=(
                    f"{len(keys)} rows collapse to {len(set(keys))} acknowledgement numbers; "
                    "refusing to store fewer rows than the portal listed"
                ),
            )
        relative = Path("listings") / "IN-KA" / f"{slugify(district)}-{fetched_at[:10]}.html"
        write_bytes_atomic(self.out_dir / relative, body)
        upsert_jsonl(self.rows_path, rows)
        return self._record(
            district, fetched_at, status="downloaded", http_status=http_status,
            path=relative.as_posix(), sha256=sha, bytes=len(body), row_count=len(rows),
        )

    def run(self, districts: list[str] | None = None, *, dry_run: bool = False) -> list[dict]:
        """Fetch each district in turn and return one manifest record per request.

        With ``dry_run`` nothing is requested or written: the records say what
        would be sent, with ``status: "dry_run"``.
        """
        names = districts or [DEFAULT_DISTRICT]
        if dry_run:
            return [self._record(d, now_iso(), status="dry_run") for d in names]
        records: list[dict] = []
        for i, district in enumerate(names):
            if i or self._pause_before_next_request:
                self._sleep_fn(self.sleep)
            self._pause_before_next_request = False
            record = self.fetch_district(district)
            upsert_jsonl(self.manifest_path, [record])
            records.append(record)
        return records
