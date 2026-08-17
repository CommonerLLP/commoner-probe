# SPDX-License-Identifier: MIT
"""Search a NIC Government Orders Issue Register, and fetch the order documents.

The stack is ASP.NET WebForms with a GridView result grid, in front of a NIC
"Government Orders Issue Register". A state government publishes every G.O. it
issues here, by department, order number, order type and date. The form drives
through `aspnet`, so the WebForms traps that module documents apply first.

Host, state and vintage: ``goir.ap.gov.in``, Andhra Pradesh, measured
2026-08-14 and 2026-08-15. One run retrieved 51 School Education orders.

**GENERALITY IS NOT VERIFIED.** NIC appears to run this application per state,
so a second state may need only a new base URL. Nothing here demonstrates that.
Nobody has run this against Telangana, Karnataka or Odisha. Treat the code as
one deployment's client until a second deployment answers it.

FIVE SILENT FAILURES, EACH WITH THE MEASUREMENT THAT PROVED IT
==============================================================
**1. A HEAD request answers 404 for every path.** It answers 404 for the paths
that serve too. ``curl -I https://goir.ap.gov.in/`` returned 404. A GET on the
same URL returned the register. A HEAD says nothing about this host.
:func:`reachable` therefore asks with a GET and reports the body size.

**2. The date format is dd-mm-yyyy with HYPHENS.** With slashes the server
returns the blank search form and HTTP 200. That page is near-identical to a
genuine no-results page, so a wrong date format reads as "no such order". One
session read it that way for an hour on 2026-08-14. :func:`format_go_date`
refuses a slashed date, and :class:`GoQuery` normalises every date it holds.

**3. An absence needs a positive control.** Because of failure 2, an empty grid
is not evidence. :meth:`GoIssueRegister.search` refuses to return an empty list
until :meth:`GoIssueRegister.run_control` has retrieved a record already held in
the same session. :data:`AP_SCHOOL_EDUCATION_CONTROL` is that record for Andhra.

**4. The grid's document links are not hrefs.** Each row calls
``downloadFile(goSeqId, fileType)``. The page's own JavaScript rewrites the call
to ``dgo.ashx?gid=<id>&fileType=E|T``. E is English and T is Telugu. An anchor
scrape finds only the column-sort links and reports nothing found.
:func:`result_rows` reads the JavaScript call.

**5. ``__EVENTVALIDATION`` is ABSENT from this page.** `aspnet` records that
omitting the token is another 500. That holds for one Bihar portal, and not for
this host. :func:`search_fields` posts each state token only when the page
carries it. An empty value is a wrong value.

The document endpoint also serves Microsoft Word files from the same parameters
as the PDFs, and an HTML error page instead of a 404. :func:`document_kind`
names what arrived, and :meth:`GoIssueRegister.document` raises on a body that
is no document at all.

THE ARCHIVE.ORG MIRROR, WHICH NEEDS NO INDIAN EGRESS
====================================================
The live host returns 000 from Canadian egress and serves from ap-south-1, so a
caller outside India needs an Indian vantage point. The Internet Archive holds
the same orders and answers from anywhere. Identifiers follow
``in.gov.<state>.<register>.<YYYY-MM-DD>.<E|T>-<gid>``: 53 items exist for
2021-12-24, department-tagged, and GO 84 is at
``archive.org/download/in.gov.andhra.goir.2021-12-24.E-511027/E-511027.pdf``.
:func:`orders_for_day` asks the mirror first for that reason. Whether the
identifier convention holds for other states is unverified.
"""

from __future__ import annotations

import html as _html
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode, urljoin

from . import aspnet
from .http_client import make_session
from .invariants import assert_finds

__all__ = [
    "AP_GOIR",
    "AP_SCHOOL_EDUCATION_CONTROL",
    "ArchiveItem",
    "ControlRequired",
    "GoIssueRegister",
    "GoQuery",
    "GoRow",
    "archive_document_url",
    "archive_items",
    "document_kind",
    "document_url",
    "format_go_date",
    "orders_for_day",
    "reachable",
    "result_rows",
    "search_fields",
]

AP_GOIR = "https://goir.ap.gov.in/"
FIELD_PREFIX = "ctl00$ContentPlaceHolder1$"
DOCUMENT_ENDPOINT = "dgo.ashx"

#: The register's file-type codes, and the language tag each one carries.
LANGUAGES = {"E": "EN", "T": "TE"}

ARCHIVE_SEARCH = "https://archive.org/advancedsearch.php"
ARCHIVE_DOWNLOAD = "https://archive.org/download"

_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
# downloadFile('511027','E'), after the row's HTML entities are decoded.
_DOWNLOAD = re.compile(
    r"""downloadFile\(\s*['"]?(\d{3,})['"]?\s*,\s*['"]?([ET])['"]?""")
_FORM_ACTION = re.compile(r'<form[^>]*action="([^"]*)"', re.I)
_GO_NO = re.compile(r"(?:MS|RT)-\d+")
_GRID_DATE = re.compile(r"\d{2}/\d{2}/\d{4}")


class ControlRequired(RuntimeError):
    """An empty grid was returned before any positive control had passed."""


def format_go_date(value: date | str) -> str:
    """The dd-mm-yyyy form the register accepts.

    A ``date``, an ISO string and a hyphenated string all pass. A slashed
    string raises. The server answers a slashed date with the blank search
    form and no error message, which reads as a genuine absence.
    """
    if isinstance(value, date):
        return value.strftime("%d-%m-%Y")
    text = value.strip()
    # The VALUE is parsed, not only the shape. A caller with mm-dd-yyyy habits
    # writes 05-13-2025, which matches dd-mm-yyyy exactly, and the server answers
    # an impossible date the way it answers a slashed one: the blank form, HTTP
    # 200. That is the single failure this function exists to block.
    for pattern in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern).strftime("%d-%m-%Y")
        except ValueError:
            continue
    raise ValueError(
        f"{value!r} is not a register date. Use dd-mm-yyyy with hyphens, a "
        "date, or an ISO date. A slashed date returns the blank search form "
        "with HTTP 200, which is indistinguishable from no such order.")


@dataclass(frozen=True)
class GoQuery:
    """One search of the register.

    ``go_type`` is the register's own code: 1 is MS, 2 is RT, -1 is every
    type. ``go_no`` and ``text`` may be empty, and the date range then carries
    the search.
    """

    department: str
    from_date: date | str
    to_date: date | str
    go_no: str = ""
    go_type: str = "-1"
    text: str = ""

    def __post_init__(self) -> None:
        if not self.department:
            raise ValueError("department is required. The register needs a "
                             "registered DDLDeptname code, such as SE")
        object.__setattr__(self, "from_date", format_go_date(self.from_date))
        object.__setattr__(self, "to_date", format_go_date(self.to_date))
        # A reversed range returns the empty grid, which after a passed control
        # reads as "no such order" rather than "you asked for nothing".
        first = datetime.strptime(self.from_date, "%d-%m-%Y")
        last = datetime.strptime(self.to_date, "%d-%m-%Y")
        if first > last:
            raise ValueError(
                f"from_date {self.from_date} is after to_date {self.to_date}. The "
                "register answers a reversed range with the empty grid, which is "
                "indistinguishable from an absence.")


#: A School Education order Andhra's register is known to hold, verified
#: 2026-08-14. Use it as the positive control before reporting any absence.
AP_SCHOOL_EDUCATION_CONTROL = GoQuery(
    department="SE", from_date="13-05-2025", to_date="13-05-2025",
    go_no="19", go_type="1")


@dataclass(frozen=True)
class GoRow:
    """One order in the result grid, and the documents its row offers.

    ``files`` holds (gid, file_type) pairs read out of the row's JavaScript
    call. A row with no such call is not an order row.
    """

    cells: tuple[str, ...]
    files: tuple[tuple[str, str], ...]

    @property
    def go_no(self) -> str:
        """The order number as the grid prints it, such as ``MS-84``."""
        return next((c for c in self.cells if _GO_NO.fullmatch(c)), "")

    @property
    def order_date(self) -> str:
        """The order date as ISO, or empty when the grid prints no readable date.

        The printed cell is PARSED, not reversed. Reversing any date-shaped cell
        turned ``05/13/2021`` into ``2021-13-05`` — syntactically ISO, and no such
        date — which then travelled into the record as the order's date.
        """
        for cell in self.cells:
            if not _GRID_DATE.fullmatch(cell):
                continue
            try:
                return datetime.strptime(cell, "%d/%m/%Y").strftime("%Y-%m-%d")
            except ValueError:
                continue
        return ""

    def document_name(self, department: str, gid: str, file_type: str,
                      *, suffix: str) -> str:
        """A filename carrying the order's identity, not the gid.

        One run saved 40 files named after the gid, and no reader could tell
        them apart. The row already prints the order number and the date, so
        the gid is the fallback rather than the name.

        The gid joins the name whenever the NUMBER is missing, not only when the
        date is missing too. Two orders issued the same day by one department
        print no number in some grids, and both then took one filename: a
        51-order day wrote one file and reported 51.

        ``suffix`` is REQUIRED and comes from :func:`document_kind`. This endpoint
        serves a Word file for some orders under the same parameters as the PDFs,
        so a default of ``pdf`` let the filename contradict the record beside it.
        A caller that has not looked at the bytes cannot name the file.
        """
        identity = "-".join(p for p in (self.go_no, self.order_date) if p)
        if not self.go_no:
            identity = "-".join(p for p in (identity, f"gid{gid}") if p)
        return "-".join(("GO", department, identity, LANGUAGES[file_type])) + f".{suffix}"


def document_url(base: str, gid: str, file_type: str) -> str:
    """The URL the row's JavaScript builds for one order document."""
    if file_type not in LANGUAGES:
        raise ValueError(f"file_type must be one of {sorted(LANGUAGES)}, not {file_type!r}")
    return urljoin(_root(base), f"{DOCUMENT_ENDPOINT}?gid={gid}&fileType={file_type}")


def document_kind(data: bytes) -> str:
    """Name the format that arrived: pdf, msword, ooxml or unknown.

    The endpoint serves a Word file for some orders under the same parameters
    as the PDFs, and an HTML error page instead of a 404. An extension check
    calls all three a PDF.
    """
    if data.startswith(b"%PDF"):
        return "pdf"
    if data.startswith(b"\xd0\xcf\x11\xe0"):
        return "msword"
    if data.startswith(b"PK\x03\x04"):
        return "ooxml"
    return "unknown"


def search_fields(page: str, query: GoQuery) -> dict[str, str]:
    """A POST body the register accepts, from the page it served.

    Every ``<select>`` carries a registered value, because an empty select is
    the commonest cause of an unexplained 500. Each ASP.NET state token is
    posted only when the page carries it: this page has no
    ``__EVENTVALIDATION``, and posting an empty one is posting a wrong value.
    The Search button posts its own name and no ``__EVENTTARGET``.
    """
    prefix = FIELD_PREFIX
    # The department code is checked against the page's own options. An
    # unregistered code posts fine and returns the empty grid, which after a
    # passed control reads as "this department issued no such order".
    offered = {value for value, _ in aspnet.selects(page).get(prefix + "DDLDeptname", [])}
    if offered and query.department not in offered:
        raise ValueError(
            f"the page offers no department {query.department!r}. It offers "
            f"{sorted(offered)}. An unregistered code returns the empty grid, "
            "which is indistinguishable from an absence.")
    selected = {
        prefix + "DDLDeptname": query.department,
        prefix + "sectddl": "-1",
        prefix + "DDLGoType": query.go_type,
        prefix + "DdlGo_cat": "-1",
    }
    fields = {name: value
              for name, value in aspnet.form_fields(page, selected=selected).items()
              if value or not name.startswith("__")}
    fields.update({
        "__EVENTTARGET": "",
        "__EVENTARGUMENT": "",
        "__LASTFOCUS": "",
        prefix + "txtGoNo": query.go_no,
        prefix + "txtfrmdate": query.from_date,
        prefix + "txttodate": query.to_date,
        prefix + "fAmount": "",
        prefix + "tAmount": "",
        prefix + "txtSearchText": query.text,
        prefix + "BtnSearch": "Search",
    })
    return fields


def _refuse_non_2xx(resp: Any, url: str, what: str) -> None:
    """Raise unless the response is a 2xx, naming the status and the first bytes.

    Nothing here may read a status as an absence. On a default install this
    package runs the stdlib session, which RETURNS an ``HTTPError``'s body as the
    response, so a 429 throttle page, a 500 ASP.NET error page and a 403 WAF
    challenge all reached the grid parser and came out as zero rows. The
    positive control cannot cover it either: the control passes at minute one and
    the portal throttles at minute forty, which is exactly when a caller trusts
    the empty list.
    """
    status = getattr(resp, "status_code", None)
    if status is None or 200 <= int(status) < 300:
        return
    body = (getattr(resp, "text", "") or "")[:160]
    raise RuntimeError(
        f"{url} answered HTTP {status} for {what}. This is a refusal, not an "
        f"absence, and no empty result may be read from it. Body starts {body!r}")


def result_rows(page: str) -> list[GoRow]:
    """The order rows of the result grid, read from their JavaScript calls.

    ``gridview.parse_grid`` anchors on a header cell and returns cells only.
    This grid identifies an order row by the ``downloadFile`` call it carries,
    and the document ids live in that call rather than in an href. An anchor
    scrape returns the column-sort links and no orders.
    """
    rows = []
    for row_html in _ROW.findall(page):
        row = _html.unescape(row_html)
        files = tuple(dict.fromkeys(_DOWNLOAD.findall(row)))
        if not files:
            continue
        cells = tuple(c for c in (_text(cell) for cell in _CELL.findall(row)) if c)
        rows.append(GoRow(cells=cells, files=files))
    return rows


def reachable(base: str, *, session: Any = None, timeout: float = 60) -> dict[str, Any]:
    """Report whether the host serves, asking with a GET.

    Never a HEAD. This host answers 404 to a HEAD on every path, including
    the paths that serve the register, so a HEAD reports a dead site.
    """
    sess = session if session is not None else make_session()
    try:
        resp = sess.get(base, timeout=timeout)
    except Exception as exc:  # network shape varies by session backend
        return {"ok": False, "method": "GET", "bytes": 0, "status": None,
                "error": f"{type(exc).__name__}: {exc}"}
    body = resp.text or ""
    status = getattr(resp, "status_code", None)
    # A body alone lies as loudly as a status alone. A 429 throttle page and a
    # 503 maintenance page both carry bytes, and reporting `ok` beside the
    # contradicting status is the shape a reader skims past.
    served = bool(body) and (status is None or 200 <= int(status) < 300)
    out = {"ok": served, "method": "GET", "bytes": len(body), "status": status,
           "error": None}
    if not served:
        out["error"] = (f"HTTP {status} with {len(body)} bytes"
                        if body else f"HTTP {status} with an empty body")
    return out


class GoIssueRegister:
    """A client for one deployment of the Government Orders Issue Register."""

    def __init__(self, base: str = AP_GOIR, *, session: Any = None,
                 timeout: float = 120, control: GoQuery = AP_SCHOOL_EDUCATION_CONTROL):
        self.base = _root(base)
        self.control = control
        self.control_passed = False
        self._session = session if session is not None else make_session()
        self._timeout = timeout

    def run_control(self, control: GoQuery | None = None) -> None:
        """Retrieve a record already held, and raise if it does not come back.

        A failed control means the query is broken, so any empty result from
        it says nothing about what the register holds.
        """
        assert_finds(self._search, control or self.control, describe=self.base)
        self.control_passed = True

    def search(self, query: GoQuery) -> list[GoRow]:
        """The orders the register returns for *query*.

        An empty grid raises :class:`ControlRequired` until a control has
        passed in this session. A wrong date format returns the same page as a
        genuine absence.
        """
        rows = self._search(query)
        if rows or self.control_passed:
            return rows
        raise ControlRequired(
            f"{self.base} returned no rows for {query!r}, and no positive control "
            "has passed in this session. A wrong date format returns the blank "
            "search form with HTTP 200, so this result does not establish an "
            "absence. Call run_control() first.")

    def document(self, gid: str, file_type: str = "E") -> bytes:
        """One order document, as bytes.

        Raises when the body is no document. The endpoint answers a bad gid
        with an HTML error page rather than a 404.
        """
        url = document_url(self.base, gid, file_type)
        data = self._session.get(url, timeout=self._timeout).content
        kind = document_kind(data)
        if kind == "unknown":
            raise RuntimeError(
                f"{url} returned {len(data)} bytes that are not a document "
                f"(starts {data[:40]!r}) — the endpoint serves an HTML error page "
                "instead of a 404")
        return data

    def _search(self, query: GoQuery) -> list[GoRow]:
        got = self._session.get(self.base, timeout=self._timeout)
        _refuse_non_2xx(got, self.base, "the search page")
        page = got.text
        # A page carrying no form at all is a maintenance or challenge page. The
        # old fallback posted to the base URL instead, which produced a grid-less
        # response that read as "no orders".
        actions = _FORM_ACTION.findall(page)
        if not actions:
            raise RuntimeError(
                f"{self.base} served {len(page)} bytes carrying no <form action>. "
                f"That is not the register (starts {page[:120]!r}), so no absence "
                "can be read from what a POST to it returns.")
        action = urljoin(self.base, actions[0])
        body = urlencode(search_fields(page, query)).encode()
        resp = self._session.post(
            action or self.base, data=body, timeout=self._timeout,
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Referer": self.base})
        _refuse_non_2xx(resp, action, "the result grid")
        return result_rows(resp.text)


@dataclass(frozen=True)
class ArchiveItem:
    """One mirrored order on archive.org, with its department tags."""

    identifier: str
    title: str
    subject: tuple[str, ...]

    @property
    def url(self) -> str:
        return archive_document_url(self.identifier)


def archive_document_url(identifier: str) -> str:
    """The download URL for a mirrored order.

    The file is named after the identifier's last segment, which is the
    file type and the register's own gid.
    """
    return f"{ARCHIVE_DOWNLOAD}/{identifier}/{identifier.rsplit('.', 1)[-1]}.pdf"


def archive_items(day: date | str, *, state: str = "andhra", register: str = "goir",
                  session: Any = None, rows: int = 500,
                  timeout: float = 60) -> list[ArchiveItem]:
    """Every mirrored order of one day, from archive.org's search API.

    ``day`` is a date or an ISO date. The identifier convention is
    ``in.gov.<state>.<register>.<YYYY-MM-DD>``; 53 items answered
    2021-12-24 for Andhra. Whether the convention holds for another state is
    unverified, so an empty list means "the mirror answered nothing", not
    "the state publishes nothing".

    A refusal never becomes an empty list. A non-2xx, a body with no ``response``
    key, and a page shorter than the day's own ``numFound`` each raise.
    """
    sess = session if session is not None else make_session()
    prefix = f"in.gov.{state}.{register}.{_iso(day)}"
    params = [("q", f"identifier:{prefix}*"), ("fl[]", "identifier"),
              ("fl[]", "title"), ("fl[]", "subject"), ("rows", str(rows)),
              ("page", "1"), ("output", "json")]
    url = f"{ARCHIVE_SEARCH}?{urlencode(params)}"
    resp = sess.get(url, timeout=timeout)
    _refuse_non_2xx(resp, url, "the mirror index")
    payload = json.loads(resp.text)
    # A refusal is a JSON object too. `{"error": "Rate limit exceeded"}` has no
    # `response` key, and reading it as zero docs made the caller assert that the
    # mirror holds nothing for the day.
    if "response" not in payload:
        raise RuntimeError(
            f"the mirror answered without a `response` key: {str(payload)[:160]!r}. "
            "That is a refusal or an error object, and no absence follows from it.")
    found = payload["response"].get("numFound")
    docs = payload["response"].get("docs", [])
    # A page shorter than the day is a floor presented as a total. 731 items with
    # rows=500 returned 500 and no warning.
    if found is not None and int(found) > len(docs):
        raise RuntimeError(
            f"the mirror holds {found} item(s) for {prefix} and this page carries "
            f"{len(docs)}. Raise `rows` above {found}; a truncated page reported as "
            "the whole day is a count that is really a floor.")
    return [ArchiveItem(identifier=doc.get("identifier", ""),
                        title=doc.get("title", ""),
                        subject=_tags(doc.get("subject")))
            for doc in docs]


def orders_for_day(day: date | str, *, session: Any = None,
                   live: GoIssueRegister | None = None,
                   query: GoQuery | None = None, state: str = "andhra",
                   register: str = "goir") -> tuple[str, list]:
    """The orders of one day, from the mirror first and the live host second.

    Returns (source, orders). The archive.org mirror answers from any
    vantage point. The live register returns 000 outside India, so asking it
    first costs a caller an Indian host it may not need.
    """
    items = archive_items(day, state=state, register=register, session=session)
    if items:
        return "archive.org", items
    if live is None or query is None:
        raise LookupError(
            f"the archive.org mirror holds no {register} item for {_iso(day)}, and "
            "no live register and query were given — so nothing is established "
            "about what the register holds")
    return live.base, live.search(query)


def _root(base: str) -> str:
    return base if base.endswith("/") else base + "/"


def _iso(day: date | str) -> str:
    return day.isoformat() if isinstance(day, date) else str(day).strip()


def _text(cell_html: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", cell_html))).strip()


def _tags(subject: Any) -> tuple[str, ...]:
    """archive.org returns one subject as a string and several as a list."""
    if not subject:
        return ()
    if isinstance(subject, str):
        return (subject,)
    return tuple(str(s) for s in subject)
