# SPDX-License-Identifier: MIT
"""Offline tests for the NIC Government Orders Issue Register probe.

No network. Every test names the silent failure it prevents.

One session measured the live behaviour against goir.ap.gov.in on 2026-08-14.
It retrieved 51 orders. GO 84 of 24-12-2021 served as gid 511027. The
archive.org mirror holds 53 items for the same date.
"""
from __future__ import annotations

import datetime
import json

import pytest

from commoner_probe.go_issue_register import (
    AP_GOIR,
    ControlRequired,
    GoIssueRegister,
    GoQuery,
    GoRow,
    archive_document_url,
    archive_items,
    document_kind,
    document_url,
    format_go_date,
    orders_for_day,
    reachable,
    result_rows,
    search_fields,
)
from commoner_probe.invariants import ControlFailed

SEARCH_PAGE = """
<html><body>
<form method="post" action="./Default.aspx" id="aspnetForm">
<input type="hidden" name="__VIEWSTATE" value="VS0" />
<input type="hidden" name="__VIEWSTATEGENERATOR" value="G0" />
<select name="ctl00$ContentPlaceHolder1$DDLDeptname">
  <option value="0">--Select--</option>
  <option value="SE">School Education</option>
</select>
<select name="ctl00$ContentPlaceHolder1$DDLGoType">
  <option value="-1">All</option>
  <option value="1">MS</option>
</select>
</form></body></html>
"""

RESULT_PAGE = """
<table id="FileMoveList2">
<tr><th><a href="Default.aspx?sort=GoNo">Go No</a></th><th>Date</th><th>Abstract</th></tr>
<tr><td>MS-84</td><td>24/12/2021</td><td>Schools rationalisation</td>
    <td><img onclick="downloadFile(&#39;511027&#39;,&#39;E&#39;)" />
        <img onclick="downloadFile('511027','T')" /></td></tr>
<tr><td>MS-85</td><td>24/12/2021</td><td>Anganwadi mapping</td>
    <td><img onclick="downloadFile('511028','E')" /></td></tr>
</table>
"""

SORT_ONLY_PAGE = """
<table id="FileMoveList2">
<tr><th><a href="Default.aspx?sort=GoNo">Go No</a></th><th>Date</th></tr>
</table>
"""

ARCHIVE_JSON = json.dumps({"response": {"numFound": 2, "docs": [
    {"identifier": "in.gov.andhra.goir.2021-12-24.E-511027",
     "title": "G.O.Ms.No.84", "subject": ["School Education"]},
    {"identifier": "in.gov.andhra.goir.2021-12-24.E-511028",
     "title": "G.O.Ms.No.85", "subject": "School Education"},
]}})

QUERY = GoQuery(department="SE", from_date="01-12-2021", to_date="31-01-2022",
                go_no="84", go_type="1")


class _Resp:
    def __init__(self, text="", content=b"", status_code=200):
        self.text = text
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        return None


class _Session:
    """Serves canned pages, records requests, and dies on a HEAD."""

    def __init__(self, gets=(), posts=()):
        self.gets = list(gets)
        self.posts = list(posts)
        self.urls = []
        self.bodies = []

    def get(self, url, **kwargs):
        self.urls.append(("GET", url))
        return self.gets.pop(0)

    def post(self, url, **kwargs):
        self.urls.append(("POST", url))
        self.bodies.append(kwargs.get("data"))
        return self.posts.pop(0)

    def head(self, url, **kwargs):
        raise AssertionError("a HEAD on this host 404s on paths that serve")


def _body(session):
    return session.bodies[-1].decode()


def test_slashed_dates_are_refused_before_the_server_answers_the_blank_form():
    """Slashes cost an hour on 2026-08-14.

    The server answers a slashed date with the blank search form and no
    error. That page is indistinguishable from a genuine no-results page.
    """
    with pytest.raises(ValueError, match="dd-mm-yyyy"):
        format_go_date("01/12/2021")


def test_iso_strings_and_date_objects_become_hyphenated_dd_mm_yyyy():
    assert format_go_date("2021-12-24") == "24-12-2021"
    assert format_go_date(datetime.date(2021, 12, 24)) == "24-12-2021"
    assert format_go_date("24-12-2021") == "24-12-2021"


def test_the_query_normalises_its_dates_so_no_caller_can_post_slashes():
    query = GoQuery(department="SE", from_date="2021-12-01", to_date="2022-01-31")
    assert query.from_date == "01-12-2021"
    assert query.to_date == "31-01-2022"


def test_absent_eventvalidation_is_not_posted_back_as_an_empty_field():
    """This page carries no __EVENTVALIDATION.

    aspnet.py records that omitting the token is a 500. That holds for one
    Bihar portal, not for this host. An empty value is a wrong value, so
    the field is posted only when the page carries it.
    """
    fields = search_fields(SEARCH_PAGE, QUERY)
    assert "__EVENTVALIDATION" not in fields
    assert fields["__VIEWSTATE"] == "VS0"
    assert fields["__VIEWSTATEGENERATOR"] == "G0"


def test_present_eventvalidation_is_posted_back():
    page = SEARCH_PAGE.replace(
        '<input type="hidden" name="__VIEWSTATE" value="VS0" />',
        '<input type="hidden" name="__VIEWSTATE" value="VS0" />'
        '<input type="hidden" name="__EVENTVALIDATION" value="EV0" />')
    assert search_fields(page, QUERY)["__EVENTVALIDATION"] == "EV0"


def test_no_select_is_posted_empty_because_an_empty_select_answers_500():
    fields = search_fields(SEARCH_PAGE, QUERY)
    assert fields["ctl00$ContentPlaceHolder1$DDLDeptname"] == "SE"
    assert fields["ctl00$ContentPlaceHolder1$DDLGoType"] == "1"
    assert all(v != "" for k, v in fields.items()
               if k.endswith(("DDLDeptname", "DDLGoType", "sectddl", "DdlGo_cat")))


def test_the_search_button_posts_its_own_name_and_no_event_target():
    fields = search_fields(SEARCH_PAGE, QUERY)
    assert fields["ctl00$ContentPlaceHolder1$BtnSearch"] == "Search"
    assert fields["__EVENTTARGET"] == ""


def test_search_posts_hyphenated_dates():
    session = _Session(gets=[_Resp(text=SEARCH_PAGE)], posts=[_Resp(text=RESULT_PAGE)])
    register = GoIssueRegister(session=session)
    register.search(QUERY)
    assert "txtfrmdate=01-12-2021" in _body(session)
    assert "%2F" not in _body(session)


def test_rows_come_from_the_javascript_action_not_the_href():
    """An href scrape finds only the column-sort links.

    Each row calls downloadFile(goSeqId, fileType). The page's own
    JavaScript rewrites that to dgo.ashx. A row with no anchor still
    carries two documents.
    """
    rows = result_rows(RESULT_PAGE)
    assert [r.go_no for r in rows] == ["MS-84", "MS-85"]
    assert rows[0].files == (("511027", "E"), ("511027", "T"))
    assert rows[0].order_date == "2021-12-24"


def test_a_page_of_sort_links_alone_yields_no_rows():
    assert result_rows(SORT_ONLY_PAGE) == []


def test_document_url_is_the_endpoint_the_javascript_builds():
    assert document_url(AP_GOIR, "511027", "E") == (
        "https://goir.ap.gov.in/dgo.ashx?gid=511027&fileType=E")


def test_document_name_carries_the_go_identity_not_the_gid():
    """40 files named gid511027 are unreadable, and the row holds the identity."""
    row = result_rows(RESULT_PAGE)[0]
    assert row.document_name("SE", "511027", "E", suffix="pdf") == "GO-SE-MS-84-2021-12-24-EN.pdf"
    bare = GoRow(cells=("no identity",), files=(("511027", "T"),))
    assert bare.document_name("SE", "511027", "T", suffix="pdf") == "GO-SE-gid511027-TE.pdf"


def test_an_empty_result_without_a_control_raises_rather_than_reporting_absence():
    """A wrong date format and a genuine absence return the same page.

    So an empty result is a claim the probe may not make until a record
    already held has come back in the same session.
    """
    session = _Session(gets=[_Resp(text=SEARCH_PAGE)], posts=[_Resp(text=SORT_ONLY_PAGE)])
    with pytest.raises(ControlRequired, match="positive control"):
        GoIssueRegister(session=session).search(QUERY)


def test_an_empty_result_is_returned_once_a_control_has_passed():
    session = _Session(gets=[_Resp(text=SEARCH_PAGE)] * 2,
                       posts=[_Resp(text=RESULT_PAGE), _Resp(text=SORT_ONLY_PAGE)])
    register = GoIssueRegister(session=session)
    register.run_control()
    assert register.search(QUERY) == []


def test_a_control_that_returns_nothing_raises_control_failed():
    session = _Session(gets=[_Resp(text=SEARCH_PAGE)], posts=[_Resp(text=SORT_ONLY_PAGE)])
    register = GoIssueRegister(session=session)
    with pytest.raises(ControlFailed):
        register.run_control()
    assert register.control_passed is False


def test_the_probe_never_issues_a_head_request():
    """curl -I reported this host dead while a GET returned the register.

    The fake session raises on head(), so any HEAD fails the test.
    """
    session = _Session(gets=[_Resp(text=SEARCH_PAGE), _Resp(text=SEARCH_PAGE)],
                       posts=[_Resp(text=RESULT_PAGE)])
    register = GoIssueRegister(session=session)
    register.search(QUERY)
    assert reachable(AP_GOIR, session=session)["method"] == "GET"
    assert all(method != "HEAD" for method, _ in session.urls)


def test_reachability_reads_the_body_because_a_status_alone_lies():
    session = _Session(gets=[_Resp(text=SEARCH_PAGE)])
    report = reachable(AP_GOIR, session=session)
    assert report["ok"] is True
    assert report["bytes"] == len(SEARCH_PAGE)


def test_a_non_document_body_raises_instead_of_being_saved():
    """The endpoint answers a bad gid with an HTML error page, not a 404."""
    session = _Session(gets=[_Resp(content=b"<html>error</html>")])
    with pytest.raises(RuntimeError, match="not a document"):
        GoIssueRegister(session=session).document("999", "E")


def test_a_word_order_is_recognised_rather_than_discarded():
    """One order in a 26-order series is a Word file from the same endpoint."""
    assert document_kind(b"%PDF-1.4 ...") == "pdf"
    assert document_kind(b"\xd0\xcf\x11\xe0rest") == "msword"
    assert document_kind(b"PK\x03\x04rest") == "ooxml"
    assert document_kind(b"<html>") == "unknown"


def test_archive_download_url_matches_the_measured_go_84_url():
    assert archive_document_url("in.gov.andhra.goir.2021-12-24.E-511027") == (
        "https://archive.org/download/in.gov.andhra.goir.2021-12-24.E-511027/"
        "E-511027.pdf")


def test_archive_items_are_department_tagged_and_queried_by_identifier_prefix():
    session = _Session(gets=[_Resp(text=ARCHIVE_JSON)])
    items = archive_items("2021-12-24", session=session)
    assert [i.title for i in items] == ["G.O.Ms.No.84", "G.O.Ms.No.85"]
    assert items[0].subject == ("School Education",)
    assert items[1].subject == ("School Education",)
    assert "identifier%3Ain.gov.andhra.goir.2021-12-24%2A" in session.urls[0][1]


def test_orders_for_day_tries_the_mirror_before_the_live_register():
    """The mirror needs no Indian egress. The live host does."""
    mirror = _Session(gets=[_Resp(text=ARCHIVE_JSON)])
    live_session = _Session()
    live = GoIssueRegister(session=live_session)
    source, items = orders_for_day("2021-12-24", session=mirror, live=live, query=QUERY)
    assert source == "archive.org"
    assert len(items) == 2
    assert live_session.urls == []


def test_orders_for_day_falls_back_to_the_live_register_when_the_mirror_is_empty():
    empty = json.dumps({"response": {"numFound": 0, "docs": []}})
    mirror = _Session(gets=[_Resp(text=empty)])
    live_session = _Session(gets=[_Resp(text=SEARCH_PAGE)], posts=[_Resp(text=RESULT_PAGE)])
    live = GoIssueRegister(session=live_session)
    source, rows = orders_for_day("2021-12-24", session=mirror, live=live, query=QUERY)
    assert source == AP_GOIR
    assert len(rows) == 2


def test_orders_for_day_raises_when_the_mirror_is_empty_and_no_register_is_given():
    empty = json.dumps({"response": {"numFound": 0, "docs": []}})
    mirror = _Session(gets=[_Resp(text=empty)])
    with pytest.raises(LookupError, match="mirror"):
        orders_for_day("2021-12-24", session=mirror)


# --- review findings, 2026-08-17 -------------------------------------------
# Ten paths that answered with an empty grid, an empty list, or a plausible
# name, where the truth was a refusal or a collision. Each test states what the
# old code returned.


@pytest.mark.parametrize("status,body", [
    (429, "<html>Too many requests</html>"),
    (500, "<html>Server Error in '/' Application.</html>"),
    (403, "<html>Request blocked</html>"),
])
def test_a_refusal_is_never_read_as_an_absence(status, body):
    """On a default install this package runs the stdlib session, which RETURNS
    an HTTPError's body as the response. A throttle, an ASP.NET error page and a
    WAF challenge all reached the grid parser and came out as zero rows. The
    control cannot cover it: the control passes at minute one and the portal
    throttles at minute forty."""
    session = _Session(gets=[_Resp(text=SEARCH_PAGE)],
                       posts=[_Resp(text=body, status_code=status)])
    register = GoIssueRegister(session=session)
    register.control_passed = True
    with pytest.raises(RuntimeError, match=str(status)):
        register.search(QUERY)


def test_a_page_with_no_search_form_is_not_the_register():
    """The old fallback posted to the base URL when no form was found, so a
    maintenance page still produced a grid-less "result"."""
    session = _Session(gets=[_Resp(text="<html>Site under maintenance.</html>")],
                       posts=[_Resp(text=RESULT_PAGE)])
    register = GoIssueRegister(session=session)
    register.control_passed = True
    with pytest.raises(RuntimeError, match="no <form action>"):
        register.search(QUERY)


def test_reachable_does_not_call_a_throttled_page_a_serving_host():
    """`ok` was decided by body length alone, so a 429 page reported ok True with
    status 429 in the same dict — the true signal present and unused."""
    session = _Session(gets=[_Resp(text="<html>Too many requests</html>", status_code=429)])
    out = reachable("https://goir.ap.gov.in/", session=session)
    assert out["ok"] is False
    assert out["status"] == 429
    assert out["error"]


def test_an_impossible_date_is_refused():
    """05-13-2025 matches dd-mm-yyyy exactly. The server answers an impossible
    date the way it answers a slashed one: the blank form, HTTP 200."""
    with pytest.raises(ValueError):
        format_go_date("05-13-2025")
    with pytest.raises(ValueError):
        format_go_date("32-12-2025")


def test_a_reversed_date_range_is_refused():
    with pytest.raises(ValueError, match="after"):
        GoQuery(department="SE", from_date="31-01-2022", to_date="01-12-2021")


def test_a_word_body_is_not_named_pdf():
    """The record carried document_kind msword while the filename said .pdf."""
    row = GoRow(cells=("MS-84", "24/12/2021"), files=(("511027", "E"),))
    kind = document_kind(b"\xd0\xcf\x11\xe0somebytes")
    assert row.document_name("SE", "511027", "E", suffix=kind).endswith(".msword")


def test_two_rows_without_a_go_number_get_distinct_names():
    """The gid joined the name only when the number AND the date were missing, so
    two orders of one day took one filename: a 51-order day wrote 1 file."""
    a = GoRow(cells=("24/12/2021", "Schools"), files=(("511027", "E"),))
    b = GoRow(cells=("24/12/2021", "Anganwadi"), files=(("511028", "E"),))
    assert (a.document_name("SE", "511027", "E", suffix="pdf")
            != b.document_name("SE", "511028", "E", suffix="pdf"))


def test_an_unparseable_grid_date_does_not_become_a_fake_iso_date():
    """Reversing any date-shaped cell turned 05/13/2021 into 2021-13-05 —
    syntactically ISO, and no such date."""
    row = GoRow(cells=("MS-84", "05/13/2021"), files=(("511027", "E"),))
    assert row.order_date == ""


def test_an_unregistered_department_is_refused_against_the_pages_own_options():
    """An unregistered code posts fine and returns the empty grid, which after a
    passed control reads as "this department issued no such order"."""
    with pytest.raises(ValueError, match="offers no department"):
        search_fields(SEARCH_PAGE, GoQuery(department="SEE", from_date="01-12-2021",
                                           to_date="31-01-2022"))


def test_an_archive_error_object_is_not_an_empty_mirror():
    """A refusal is a JSON object too. `{"error": ...}` has no `response` key, and
    reading it as zero docs made the caller assert the mirror holds nothing."""
    session = _Session(gets=[_Resp(text=json.dumps({"error": "Rate limit exceeded"}))])
    with pytest.raises(RuntimeError, match="response"):
        archive_items("2021-12-24", session=session)


def test_a_truncated_mirror_page_is_not_reported_as_the_whole_day():
    """numFound 731 with 500 docs returned 500 items and no warning — a floor
    presented as a total."""
    payload = json.dumps({"response": {"numFound": 731, "docs": [
        {"identifier": f"in.gov.andhra.goir.2021-12-24.E-{i}", "title": "", "subject": []}
        for i in range(500)]}})
    session = _Session(gets=[_Resp(text=payload)])
    with pytest.raises(RuntimeError, match="731"):
        archive_items("2021-12-24", session=session, rows=500)
