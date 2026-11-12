"""Municipal index parsers on the captured fixtures.

See `tests/fixtures/amc/README.md`, `tests/fixtures/delhi_finance/README.md`, and
`tests/fixtures/mcd/README.md` for the counts asserted here. No test touches the
network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from commoner_probe.municipal_budget_index import (
    fiscal_year_from_text,
    parse_anchor_index,
    parse_detail_page_links,
    parse_drupal_file_links,
    parse_mcd_documents,
    parse_mcd_menu_guides,
)

FIXTURES = Path(__file__).parent / "fixtures"
AMC = FIXTURES / "amc"
DELHI = FIXTURES / "delhi_finance"
MCD = FIXTURES / "mcd"

AMC_BUDGET_URL = "https://ahmedabadcity.gov.in/SP/Budget"
AMC_BALANCE_URL = "https://ahmedabadcity.gov.in/SP/BalanceSheet"
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
