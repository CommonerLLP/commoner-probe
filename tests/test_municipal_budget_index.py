"""Municipal index parsers on the captured fixtures.

See `tests/fixtures/amc/README.md`, `tests/fixtures/delhi_finance/README.md`, and
`tests/fixtures/mcd/README.md` for the counts asserted here. No test touches the
network.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from commoner_probe import records as R
from commoner_probe import schemas as sc
from commoner_probe.corpus import Corpus
from commoner_probe.municipal_budget_index import (
    MAX_MENU_GUIDES,
    MUNICIPAL_BUDGET_PORTALS,
    MunicipalBudgetPortal,
    MunicipalBudgetProbe,
    document_slug,
    fiscal_year_from_text,
    get_portal,
    mcd_menu_urls,
    parse_anchor_index,
    parse_detail_page_links,
    parse_drupal_file_links,
    parse_mcd_documents,
    parse_mcd_menu_guides,
    portals_for_city,
    respect_robots_for,
)
from commoner_probe.validate import validate_corpus

FIXTURES = Path(__file__).parent / "fixtures"
AMC = FIXTURES / "amc"
DELHI = FIXTURES / "delhi_finance"
MCD = FIXTURES / "mcd"

AMC_BUDGET_URL = "https://ahmedabadcity.gov.in/SP/Budget"
AMC_BALANCE_URL = "https://ahmedabadcity.gov.in/SP/BalanceSheet"

# Ahmedabad is not in the registry (its host needs legacy TLS renegotiation and
# omits its intermediate certificate; see commoner-probe-h7k). This test-local
# portal keeps the probe-level coverage of the anchor_index template and the
# AMC fixtures, and matches the entry the registry had.
AMC_PORTAL = MunicipalBudgetPortal(
    code="amc-budget",
    city="ahmedabad",
    body="Ahmedabad Municipal Corporation",
    host="ahmedabadcity.gov.in",
    template="anchor_index",
    document_type="budget_book",
    pages=(AMC_BUDGET_URL,),
    keywords=("budget",),
)
GNCTD = "https://finance.delhi.gov.in/finance"
MCD_BASE = "https://mcdonline.nic.in/portal"
BUDGET_GUIDE = "1b4066c6-73ae-417b-bc62-796f9f0b7c7e"
SUBMENU_URL = f"{MCD_BASE}/showSubMenu?menuguide=914bf40f-81d3-4ae4-91c0-202a64bfa925"
CONTENT_URL = f"{MCD_BASE}/showContent?menuguide={BUDGET_GUIDE}"
PDF_URL = f"{MCD_BASE}/showPDFContent?menuguide={BUDGET_GUIDE}"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Budget 2026-27 - A", "2026-27"),
        ("Budget_13_14.pdf", "2013-14"),
        ("Budget_12-13.pdf", "2012-13"),
        ("Budget_05-06.pdf", "2005-06"),
        ("RBE 22-23 BE 2023-24", "2022-23"),
        ("Budget Speech Hindi 2026-26", None),
        ("Final_Balance_Sheet_as_on_31-03-2023.pdf", "2022-23"),
        ("Balance Sheet 31-03-23", "2022-23"),
        ("Detailed Demands for Grants 2015-16 VOA", "2015-16"),
        ("Finance Department", None),
        ("2026-2027", "2026-27"),
        ("budget/2019/20", "2019-20"),
        ("Schedules_31_03_06.pdf", None),
        ("", None),
        (None, None),
    ],
)
def test_fiscal_year_from_text(text, expected):
    assert fiscal_year_from_text(text) == expected


def test_amc_budget_page_yields_27_links():
    docs = parse_anchor_index(
        _read(AMC / "budget_trimmed.html"), AMC_BUDGET_URL, keywords=("budget",)
    )
    assert len(docs) == 27
    assert docs[0]["fiscal_year"] == "2026-27" and docs[0]["title"] == "Budget 2026-27 - A"
    assert (
        docs[0]["url"] == "https://ahmedabadcity.gov.in/ViewFile/ViewFile?TYPE=FileRepository,2583"
    )
    assert {d["url"] for d in docs if d["fiscal_year"] == "2023-24"} == {
        "https://ahmedabadcity.gov.in/Uploads/Budget/AMC Budget 2023-24.pdf",
        "https://ahmedabadcity.gov.in/ViewFile/ViewFile?TYPE=FileRepository,2203",
    }
    assert any(
        d["url"].endswith("Budget_13_14.pdf") and d["fiscal_year"] == "2013-14" for d in docs
    )
    assert all(d["source_page"] == AMC_BUDGET_URL for d in docs)
    years = [d["fiscal_year"] for d in docs]
    assert years == sorted(years, reverse=True)


def test_amc_balance_sheet_page_yields_27_links():
    docs = parse_anchor_index(
        _read(AMC / "balance_sheet_trimmed.html"),
        AMC_BALANCE_URL,
        keywords=("balance sheet", "audit report", "account"),
    )
    assert len(docs) == 27
    sheet = next(d for d in docs if d["url"].endswith("Final_Balance_Sheet_as_on_31-03-2023.pdf"))
    assert sheet["fiscal_year"] == "2022-23"
    # The 2005-06 schedule files are named 31_03_06 and the year rules don't match
    # them. Only the consolidated sheet, whose label says 31-03-2006, carries a year.
    assert [d["title"] for d in docs if d["fiscal_year"] == "2005-06"] == [
        "Consolidated Balance Sheet as on 31-03-2006"
    ]


@pytest.mark.parametrize(
    "name,document_type,count,first_url_tail",
    [
        ("budget_glance_trimmed.html", "budget_at_a_glance", 20, "bag_compressed_1_2.pdf"),
        ("receipts_budget_trimmed.html", "receipts_budget", 20, "receipts_budget.pdf"),
        ("budget_speech_trimmed.html", "budget_speech", 20, "budget_speech_2026-27_hindi.pdf"),
        ("gender_budget_trimmed.html", "gender_budget", 17, "gender_budget.pdf"),
        # sevent4 parity: the demand-numbered volumes are dropped (bead commoner-probe-6e7).
        ("ddg_2026-27_trimmed.html", "detailed_demands_for_grants", 0, None),
    ],
)
def test_gnctd_pages(name, document_type, count, first_url_tail):
    docs = parse_drupal_file_links(
        _read(DELHI / name), f"{GNCTD}/page", document_type=document_type
    )
    assert len(docs) == count
    assert len({d["url"] for d in docs}) == count
    assert all(d["document_type"] == document_type for d in docs)
    assert all(d["source_page"] == f"{GNCTD}/page" for d in docs)
    assert all(d["url"].startswith("https://finance.delhi.gov.in/sites/") for d in docs)
    if first_url_tail:
        assert docs[0]["url"].endswith(first_url_tail)


def test_gnctd_glance_years_span_2007_08_to_2026_27():
    docs = parse_drupal_file_links(
        _read(DELHI / "budget_glance_trimmed.html"),
        f"{GNCTD}/budget-glance",
        document_type="budget_at_a_glance",
    )
    years = {d["fiscal_year"] for d in docs}
    assert min(years) == "2007-08" and max(years) == "2026-27"


def test_gnctd_malformed_speech_title_takes_year_from_url():
    docs = parse_drupal_file_links(
        _read(DELHI / "budget_speech_trimmed.html"),
        f"{GNCTD}/budget-speech",
        document_type="budget_speech",
    )
    hindi = next(d for d in docs if d["title"] == "Budget Speech Hindi 2026-26")
    assert hindi["fiscal_year"] == "2026-27"
    assert hindi["url"].endswith("budget_speech_2026-27_hindi.pdf")


def test_drupal_untitled_download_anchor_gets_a_synthesised_title():
    html = '<a href="/sites/x/budget_2020-21.pdf">Download</a>'
    docs = parse_drupal_file_links(html, f"{GNCTD}/p", document_type="budget_speech")
    assert [(d["title"], d["fiscal_year"]) for d in docs] == [("Budget Speech 2020-21", "2020-21")]


def test_drupal_drops_documents_that_are_not_budget_like():
    # The page URL's host is outside finance.delhi.gov.in, whose name alone matches "finance".
    html = '<a href="/sites/x/handbook.pdf" title="Handbook 2020-21">Download</a>'
    assert parse_drupal_file_links(html, "https://example.org/p", document_type="x") == []


def test_ddg_index_yields_22_detail_pages():
    pages = parse_detail_page_links(
        _read(DELHI / "ddg_index_trimmed.html"), f"{GNCTD}/detailed-demands-grants-0"
    )
    assert len(pages) == 22
    assert sum(p["title"].endswith(" VOA") for p in pages) == 2
    assert pages[0]["url"] == f"{GNCTD}/detailed-demands-grants-2026-27"
    assert pages[0]["title"] == "Detailed Demands for Grants 2026-27"
    assert len({p["url"] for p in pages}) == 22


def test_mcd_first_hop_has_one_budget_guide_and_no_documents():
    payload = _read(MCD / "showSubMenu_finance.json")
    assert parse_mcd_documents(payload, SUBMENU_URL) == []
    assert parse_mcd_menu_guides(payload) == [
        {"title": "Budget Documents", "menu_guide": BUDGET_GUIDE}
    ]


def test_mcd_content_hop_lists_22_documents():
    docs = parse_mcd_documents(_read(MCD / "showContent_budget_documents.json"), CONTENT_URL)
    assert len(docs) == 22
    assert all(d["url"].startswith("https://mcdonline.nic.in/portal/downloadFile/") for d in docs)
    assert all(d["source_page"] == CONTENT_URL for d in docs)
    be = next(
        d for d in docs if d["title"] == "Budget Estimate 2022-23 of Municipal Corporation of Delhi"
    )
    assert be["fiscal_year"] == "2022-23"
    assert be["url"].endswith("be_22-23_re-unified_mcd_aprroved_by_spl_officer_22071102160171.pdf")


def test_mcd_empty_hops_yield_nothing():
    assert parse_mcd_documents("{}", SUBMENU_URL) == [] and parse_mcd_menu_guides("{}") == []
    assert parse_mcd_documents(_read(MCD / "showPDFContent_budget_documents.json"), PDF_URL) == []
    assert parse_mcd_menu_guides(_read(MCD / "showSubMenu_budget_documents.json")) == []


def test_mcd_menu_guides_ignores_invalid_json():
    assert parse_mcd_menu_guides("not json") == []


# --- registry --------------------------------------------------------------


def test_registry_holds_only_live_verified_portals():
    assert {p.code for p in MUNICIPAL_BUDGET_PORTALS} == {
        "gnctd-budget-at-a-glance",
        "gnctd-receipts-budget",
        "gnctd-budget-speech",
        "gnctd-gender-budget",
        "gnctd-detailed-demands",
        "mcd-budget-documents",
    }
    assert len({p.code for p in MUNICIPAL_BUDGET_PORTALS}) == len(MUNICIPAL_BUDGET_PORTALS)
    delhi = portals_for_city("delhi")
    assert {p.city for p in delhi} == {"delhi"} and len(delhi) == 6
    assert len(MUNICIPAL_BUDGET_PORTALS) == 6
    assert portals_for_city("Ahmedabad") == [] and portals_for_city("pune") == []
    assert not [p for p in MUNICIPAL_BUDGET_PORTALS if "ahmedabadcity.gov.in" in (p.host, *p.pages)]


def test_get_portal_names_the_known_codes_on_a_miss():
    assert get_portal("gnctd-budget-speech").pages == (f"{GNCTD}/budget-speech",)
    with pytest.raises(ValueError, match="gnctd-budget-speech"):
        get_portal("nope")
    with pytest.raises(ValueError, match="unknown"):
        get_portal("amc-budget")


def test_registry_fields():
    glance = get_portal("gnctd-budget-at-a-glance")
    assert glance.pages == (f"{GNCTD}/budget-glance",) and glance.template == "drupal_files"
    assert get_portal("gnctd-detailed-demands").template == "drupal_detail_pages"
    mcd = get_portal("mcd-budget-documents")
    assert mcd.template == "mcd_menu_json" and len(mcd.menu_guides) == 2 and mcd.pages == ()
    assert MAX_MENU_GUIDES == 40


def test_respect_robots_only_relaxed_for_gnctd():
    assert respect_robots_for("https://finance.delhi.gov.in/finance/budget-glance") is False
    assert respect_robots_for("https://ahmedabadcity.gov.in/SP/Budget") is True
    assert respect_robots_for("https://mcdonline.nic.in/portal/showContent?menuguide=x") is True
    assert respect_robots_for("https://finance.delhi.gov.in.example.org/x") is True


def test_mcd_menu_urls_order():
    urls = mcd_menu_urls("g1")
    assert [urlparse(u).path.rsplit("/", 1)[-1] for u in urls] == [
        "showSubMenu",
        "showContent",
        "showPDFContent",
    ]
    assert all(u.endswith("?menuguide=g1") and u.startswith("https://mcdonline.nic.in/portal/") for u in urls)


@pytest.mark.parametrize(
    "title,url,expected",
    [
        ("Budget 2023-24", "https://h/Uploads/Budget/AMC Budget 2023-24.pdf", "budget-2023-24-amc-budget-2023-24"),
        ("Budget 2023-24", "https://h/ViewFile/ViewFile?TYPE=FileRepository,2203", "budget-2023-24-2203"),
        ("Budget", "https://h/x.PDF", "budget-x"),
        ("Budget", "https://h/page", "budget"),
        ("A", "https://h/f?a=1&b=22", "a-1-22"),
    ],
)
def test_document_slug(title, url, expected):
    assert document_slug(title, url) == expected


def test_document_slug_is_cut_to_80_characters():
    assert len(document_slug("word " * 40, "https://h/a.pdf")) <= 80


# --- the probe, on fixtures ---------------------------------------------------


class FakeResponse:
    def __init__(self, *, text="", content=b"", status=200, content_type=None):
        self.text = text
        self.content = content
        self.status_code = status
        self.headers = {"Content-Type": content_type} if content_type else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=16384):
        for i in range(0, len(self.content), chunk_size):
            yield self.content[i : i + chunk_size]


class RoutedSession:
    """Answers each GET from `routes`: url -> text, a callable(url) -> text, or an Exception."""

    def __init__(self, routes=None, default=None):
        self.routes = routes or {}
        self.default = default
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, **kwargs):
        self.calls.append((url, {k: kwargs[k] for k in ("respect_robots", "stream") if k in kwargs}))
        route = self.routes.get(url, self.default)
        if isinstance(route, Exception):
            raise route
        if route is None:
            return FakeResponse(status=404)
        if callable(route):
            route = route(url)
        if isinstance(route, FakeResponse):
            return route
        return FakeResponse(text=route)


def _probe(tmp_path, code, session, **kwargs):
    """`code` is a registry code, or a MunicipalBudgetPortal for a test-local portal."""
    portal = code if isinstance(code, MunicipalBudgetPortal) else get_portal(code)
    return MunicipalBudgetProbe(tmp_path, portal=portal, session=session, sleep=0, **kwargs)


def _amc_session():
    return RoutedSession({AMC_BUDGET_URL: _read(AMC / "budget_trimmed.html")})


def test_amc_discover_gives_distinct_keys_for_same_label(tmp_path):
    probe = _probe(tmp_path, AMC_PORTAL, _amc_session())
    records = probe.discover()
    assert len(records) == 27
    both = [r for r in records if r["fiscal_year"] == "2023-24"]
    assert len(both) == 2 and both[0]["title"] == both[1]["title"]
    assert both[0]["key"] != both[1]["key"] and both[0]["filename"] != both[1]["filename"]
    assert both[0]["dest"] != both[1]["dest"]
    assert len({r["key"] for r in records}) == 27 and len({r["filename"] for r in records}) == 27
    assert all(r["key"].startswith("MUNICIPAL_BUDGET|amc-budget|") for r in records)
    assert {r["status"] for r in records} == {"pending"}
    first = records[0]
    assert first["fiscal_year"] == "2026-27" and first["city"] == "ahmedabad"
    assert first["body"] == "Ahmedabad Municipal Corporation" and first["publisher"] == "ahmedabadcity.gov.in"
    assert first["document_type"] == "budget_book" and first["source_page"] == AMC_BUDGET_URL
    assert first["filename"] == f"amc-budget_2026-27_{document_slug(first['title'], first['url'])}.pdf"
    assert first["dest"] == str(tmp_path / "amc-budget" / first["filename"])


def test_two_urls_with_one_slug_both_survive(tmp_path):
    page = (
        '<a href="/a/x.pdf">Budget 2020-21 ' + "pad " * 30 + 'one</a>'
        '<a href="/b/x.pdf">Budget 2020-21 ' + "pad " * 30 + 'one</a>'
    )
    probe = _probe(tmp_path, AMC_PORTAL, RoutedSession({AMC_BUDGET_URL: page}))
    records = probe.discover()
    assert len(records) == 2 and len({r["key"] for r in records}) == 2
    assert len({r["dest"] for r in records}) == 2
    assert all(r["key"].count("|") == 3 for r in records)


def test_gnctd_files_page_discovers_with_robots_relaxed(tmp_path):
    session = RoutedSession({f"{GNCTD}/budget-speech": _read(DELHI / "budget_speech_trimmed.html")})
    records = _probe(tmp_path, "gnctd-budget-speech", session).discover()
    assert len(records) == 20 and {r["document_type"] for r in records} == {"budget_speech"}
    assert [kw["respect_robots"] for _, kw in session.calls] == [False]
    hindi = next(r for r in records if r["title"] == "Budget Speech Hindi 2026-26")
    assert hindi["fiscal_year"] == "2026-27"


def test_gnctd_detail_pages_are_followed(tmp_path):
    index_url = f"{GNCTD}/detailed-demands-grants-0"
    detail = _read(DELHI / "ddg_2026-27_trimmed.html")
    session = RoutedSession(
        {index_url: _read(DELHI / "ddg_index_trimmed.html")},
        default=detail,
    )
    probe = _probe(tmp_path, "gnctd-detailed-demands", session)
    records = probe.discover()
    assert len(session.calls) == 23  # 1 index + 22 detail pages
    assert records == []  # sevent4 parity: the demand-numbered volumes stay dropped
    assert all(kw.get("respect_robots") is False for _, kw in session.calls)


def test_gnctd_detail_pages_keep_documents_the_parser_accepts(tmp_path):
    index_url = f"{GNCTD}/detailed-demands-grants-0"
    index = '<a href="/finance/detailed-demands-grants-2026-27">DDG</a>'
    detail = '<a href="/sites/x/ddg_2026-27_summary.pdf" title="Detailed Demands for Grants 2026-27 Summary">x</a>'
    session = RoutedSession({index_url: index, f"{GNCTD}/detailed-demands-grants-2026-27": detail})
    records = _probe(tmp_path, "gnctd-detailed-demands", session).discover()
    assert [(r["fiscal_year"], r["document_type"]) for r in records] == [
        ("2026-27", "detailed_demands_for_grants")
    ]
    assert records[0]["source_page"] == f"{GNCTD}/detailed-demands-grants-2026-27"


def _mcd_routes():
    finance, other, child = (
        "914bf40f-81d3-4ae4-91c0-202a64bfa925",
        "561ea9dd-8ddb-46cf-b19f-a3eabd02903f",
        BUDGET_GUIDE,
    )
    routes = {u: "{}" for g in (finance, other) for u in mcd_menu_urls(g)}
    routes[mcd_menu_urls(finance)[0]] = _read(MCD / "showSubMenu_finance.json")
    for url, name in zip(
        mcd_menu_urls(child),
        ("showSubMenu", "showContent", "showPDFContent"),
        strict=True,
    ):
        routes[url] = _read(MCD / f"{name}_budget_documents.json")
    return routes


def _guides(session):
    return [parse_qs(urlparse(u).query)["menuguide"][0] for u, _ in session.calls]


def test_mcd_crawl_follows_child_guides_and_terminates(tmp_path):
    session = RoutedSession(_mcd_routes())
    records = _probe(tmp_path, "mcd-budget-documents", session).discover()
    assert len(records) == 22
    assert set(_guides(session)) == {
        "914bf40f-81d3-4ae4-91c0-202a64bfa925",
        "561ea9dd-8ddb-46cf-b19f-a3eabd02903f",
        BUDGET_GUIDE,
    }
    assert len(session.calls) == 9 and all(kw["respect_robots"] is True for _, kw in session.calls)
    assert {r["document_type"] for r in records} == {"mcd_budget"}
    assert len({r["key"] for r in records}) == 22


def test_mcd_child_that_points_back_to_its_parent_terminates(tmp_path):
    finance = "914bf40f-81d3-4ae4-91c0-202a64bfa925"
    loop = json.dumps({"a": [{"menuName": "Budget", "menuGuide": finance}]})
    routes = {u: "{}" for u in mcd_menu_urls(finance)}
    routes[mcd_menu_urls(finance)[0]] = loop
    routes.update({u: "{}" for u in mcd_menu_urls("561ea9dd-8ddb-46cf-b19f-a3eabd02903f")})
    session = RoutedSession(routes)
    assert _probe(tmp_path, "mcd-budget-documents", session).discover() == []
    assert len(session.calls) == 6


class _EndlessMenu:
    """Every guide lists itself and one fresh guide as budget children."""

    def __init__(self):
        self.count = 0

    def __call__(self, url):
        if "/showSubMenu" not in url:
            return "{}"
        guide = parse_qs(urlparse(url).query)["menuguide"][0]
        self.count += 1
        return json.dumps(
            [
                {"menuName": "Budget self", "menuGuide": guide},
                {"menuName": "Budget next", "menuGuide": f"fresh-{self.count}"},
            ]
        )


def test_mcd_crawl_stops_at_max_menu_guides(tmp_path):
    session = RoutedSession(default=_EndlessMenu())
    _probe(tmp_path, "mcd-budget-documents", session, max_menu_guides=5).discover()
    assert len(set(_guides(session))) == 5
    assert len(session.calls) == 15


def test_page_that_fails_is_one_error_record_and_the_rest_continue(tmp_path):
    finance = "914bf40f-81d3-4ae4-91c0-202a64bfa925"
    routes = _mcd_routes()
    routes[mcd_menu_urls(finance)[1]] = TimeoutError("timed out")
    records = _probe(tmp_path, "mcd-budget-documents", RoutedSession(routes)).discover()
    errors = [r for r in records if r["status"] == "error"]
    assert len(errors) == 1 and errors[0]["error"] == "timed out"
    assert errors[0]["source_page"] == errors[0]["url"] == mcd_menu_urls(finance)[1]
    assert len([r for r in records if r["status"] == "pending"]) == 22


def test_http_error_on_an_index_page_is_an_error_record(tmp_path):
    session = RoutedSession({AMC_BUDGET_URL: FakeResponse(status=503)})
    records = _probe(tmp_path, AMC_PORTAL, session).discover()
    assert len(records) == 1 and records[0]["status"] == "error" and "503" in records[0]["error"]
    assert records[0]["source_page"] == AMC_BUDGET_URL and records[0]["fiscal_year"] is None
    assert records[0]["key"].startswith("MUNICIPAL_BUDGET|amc-budget|unknown|")


# --- download ---------------------------------------------------------------


def _pending(tmp_path, session=None):
    probe = _probe(tmp_path, AMC_PORTAL, session or _amc_session())
    return probe, probe.discover()[0]


def _file_session(body=b"%PDF-1.4 not really a pdf", status=200, content_type="application/pdf"):
    class Files(RoutedSession):
        def get(self, url, **kwargs):
            self.calls.append((url, {k: kwargs[k] for k in ("respect_robots", "stream") if k in kwargs}))
            return FakeResponse(content=body, status=status, content_type=content_type)

    return Files()


def test_download_rejects_html_body_for_pdf(tmp_path):
    probe, pending = _pending(tmp_path)
    probe.session = _file_session(b"<html>login</html>", content_type="text/html; charset=utf-8")
    record = probe.download(pending, dry_run=False)
    assert record["status"] == "error" and record["error"] == "body isn't a PDF"
    assert record["http_status"] == 200
    assert not Path(record["dest"]).exists() and not list(tmp_path.rglob("*.tmp"))
    assert "sha256" not in record


def test_download_pdf_records_sha256_and_text_layer(tmp_path):
    body = b"%PDF-1.4 not really a pdf"
    probe, pending = _pending(tmp_path)
    probe.session = session = _file_session(body)
    record = probe.download(pending, dry_run=False)
    assert record["status"] == "downloaded" and record["media_type"] == "application/pdf"
    assert record["sha256"] == hashlib.sha256(body).hexdigest() and record["bytes"] == len(body)
    assert record["text_layer"] in (True, False, None)
    assert Path(record["dest"]).read_bytes() == body
    assert session.calls[0][1] == {"respect_robots": True, "stream": True}
    again = probe.download(pending, dry_run=False)
    assert again["status"] == "skipped_exists" and again["sha256"] == record["sha256"]
    assert len(session.calls) == 1


def test_download_http_error_is_an_error_record(tmp_path):
    probe, pending = _pending(tmp_path)
    probe.session = _file_session(status=503)
    record = probe.download(pending, dry_run=False)
    assert record["status"] == "error" and record["http_status"] == 503 and "503" in record["error"]
    assert not Path(record["dest"]).exists()


def test_download_keeps_robots_off_for_gnctd_only(tmp_path):
    session = RoutedSession({f"{GNCTD}/budget-speech": _read(DELHI / "budget_speech_trimmed.html")})
    probe = _probe(tmp_path, "gnctd-budget-speech", session)
    pending = probe.discover()[0]
    probe.session = files = _file_session()
    assert probe.download(pending, dry_run=False)["status"] == "downloaded"
    assert files.calls[0][1]["respect_robots"] is False


def test_dry_run_downloads_nothing_and_writes_no_manifest(tmp_path):
    session = _amc_session()
    records = _probe(tmp_path, AMC_PORTAL, session).probe(dry_run=True)
    assert len(records) == 27 and {r["status"] for r in records} == {"dry_run"}
    assert len(session.calls) == 1
    assert not (tmp_path / "manifest.jsonl").exists()


def test_dry_run_leaves_error_records_as_errors(tmp_path):
    session = RoutedSession({AMC_BUDGET_URL: FakeResponse(status=500)})
    records = _probe(tmp_path, AMC_PORTAL, session).probe(dry_run=True)
    assert [r["status"] for r in records] == ["error"]


def test_probe_downloads_and_appends_manifest(tmp_path):
    probe = _probe(tmp_path, AMC_PORTAL, _amc_session())
    probe.session = _SwitchSession(probe.session, _file_session())
    records = probe.probe()
    assert len(records) == 27 and {r["status"] for r in records} == {"downloaded"}
    lines = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert lines == records
    assert validate_corpus(tmp_path, log=lambda _: None)


class _SwitchSession:
    """Index pages from `pages`, every other URL from `files`."""

    def __init__(self, pages, files):
        self.pages, self.files = pages, files

    def get(self, url, **kwargs):
        return (self.pages if url == AMC_BUDGET_URL else self.files).get(url, **kwargs)


# --- schema, record, corpus ---------------------------------------------------


def _record(tmp_path, **kw):
    base = {
        "key": "MUNICIPAL_BUDGET|amc-budget|2023-24|budget-2023-24-2203",
        "kind": "municipal_budget_document",
        "record_type": "municipal_budget_document",
        "source_family": "municipal-budget",
        "portal_code": "amc-budget",
        "city": "ahmedabad",
        "body": "Ahmedabad Municipal Corporation",
        "publisher": "ahmedabadcity.gov.in",
        "document_type": "budget_book",
        "title": "Budget 2023-24",
        "fiscal_year": "2023-24",
        "url": "https://ahmedabadcity.gov.in/ViewFile/ViewFile?TYPE=FileRepository,2203",
        "source_page": AMC_BUDGET_URL,
        "filename": "amc-budget_2023-24_budget-2023-24-2203.pdf",
        "dest": str(tmp_path / "amc-budget" / "amc-budget_2023-24_budget-2023-24-2203.pdf"),
        "status": "downloaded",
        "media_type": "application/pdf",
        "fetched_at": "2026-11-13T00:00:00Z",
        "probed_at": "2026-11-13T00:00:00Z",
        "sha256": "a" * 64,
        "bytes": 12,
        "text_layer": None,
    }
    return {**base, **kw}


def test_record_validates_against_schema(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    schema = sc.load("manifest_municipal_budget")
    jsonschema.validate(_record(tmp_path), schema)
    jsonschema.validate(_record(tmp_path, fiscal_year=None, key="MUNICIPAL_BUDGET|amc-budget|unknown|x"), schema)
    jsonschema.validate(_record(tmp_path, status="error", error="body isn't a PDF", http_status=200), schema)
    for bad in (
        {"status": "not_found"},
        {"fiscal_year": "2023"},
        {"document_type": "other"},
        {"key": "MUNICIPAL_BUDGET|amc-budget|2023|x"},
        {"kind": "ckan_resource"},
    ):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(_record(tmp_path, **bad), schema)


def test_discovered_records_validate_against_schema(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    schema = sc.load("manifest_municipal_budget")
    for code, session in (
        (AMC_PORTAL, _amc_session()),
        ("mcd-budget-documents", RoutedSession(_mcd_routes())),
    ):
        for record in _probe(tmp_path, code, session).discover():
            jsonschema.validate(record, schema)
    failed = _probe(tmp_path, AMC_PORTAL, RoutedSession({AMC_BUDGET_URL: FakeResponse(status=500)})).discover()
    jsonschema.validate(failed[0], schema)


def test_corpus_streams_municipal_budget_records(tmp_path):
    other = {"key": "BUDGET|x|y|z", "kind": "budget_source_file"}
    lines = [
        _record(tmp_path),
        other,
        _record(tmp_path, key="MUNICIPAL_BUDGET|amc-budget|2023-24|b", text_layer=True, fiscal_year=None),
    ]
    (tmp_path / "manifest.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    records = list(Corpus(tmp_path).manifest_municipal_budget())
    assert len(records) == 2 and isinstance(records[0], R.ManifestMunicipalBudgetRecord)
    assert records[0].fiscal_year == "2023-24" and records[1].fiscal_year is None
    assert records[0].text_layer is None and records[1].text_layer is True


def test_every_schema_field_survives_the_typed_api():
    from dataclasses import fields

    declared = set(sc.load("manifest_municipal_budget")["properties"])
    assert declared <= {f.name for f in fields(R.ManifestMunicipalBudgetRecord)}


# --- CLI --------------------------------------------------------------------


def test_municipal_budget_cli_list_portals(capsys):
    from commoner_probe.cli import build_parser

    args = build_parser().parse_args(["municipal-budget", "--list-portals"])
    args.func(args)
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 6
    assert lines[0].split("\t") == [
        "gnctd-budget-at-a-glance",
        "delhi",
        "Government of NCT of Delhi",
        "drupal_files",
    ]
    assert not any("amc-" in line or "ahmedabad" in line for line in lines)


def test_municipal_budget_cli_dry_run_prints_records_without_manifest(tmp_path, capsys, monkeypatch):
    from commoner_probe.cli import build_parser

    html = _read(DELHI / "budget_speech_trimmed.html")
    monkeypatch.setattr(MunicipalBudgetProbe, "_fetch_text", lambda self, url: html)
    args = build_parser().parse_args(
        ["municipal-budget", "--out", str(tmp_path), "--portal", "gnctd-budget-speech", "--dry-run"]
    )
    assert args.sleep == 2.0 and args.max_menu_guides == 40
    args.func(args)
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 20 and json.loads(lines[0])["status"] == "dry_run"
    assert not (tmp_path / "manifest.jsonl").exists()


def test_municipal_budget_cli_city_runs_each_portal_of_the_city(tmp_path, capsys, monkeypatch):
    from commoner_probe.cli import build_parser

    monkeypatch.setattr(MunicipalBudgetProbe, "_fetch_text", lambda self, url: "<html></html>")
    args = build_parser().parse_args(
        ["municipal-budget", "--out", str(tmp_path), "--city", "delhi", "--dry-run"]
    )
    args.func(args)
    assert capsys.readouterr().out == ""


def test_municipal_budget_cli_rejects_unknown_portal_and_city(tmp_path):
    from commoner_probe.cli import build_parser

    parser = build_parser()
    for flags in (["--portal", "nope"], ["--city", "pune"]):
        args = parser.parse_args(["municipal-budget", "--out", str(tmp_path), *flags])
        with pytest.raises(SystemExit):
            args.func(args)
    with pytest.raises(SystemExit):
        parser.parse_args(["municipal-budget", "--out", str(tmp_path)])
    with pytest.raises(SystemExit):
        parser.parse_args(["municipal-budget", "--portal", "gnctd-budget-speech", "--city", "delhi"])
