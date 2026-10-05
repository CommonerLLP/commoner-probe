"""Registry entries for state and legal sources: state assemblies, statutes, orders, audit, and elections."""
import functools
import itertools
import re
from datetime import date, timedelta

from check_sources import (
    CountFloor,
    ExpectedEdition,
    MaxAge,
    Sentinel,
    Source,
)

from commoner_probe.affidavit_pages import MyNetaProbe
from commoner_probe.assembly_portal import NEVA_UA, StateAssemblyCrawler
from commoner_probe.audit_pdf_index import CAGAccountsProbe, get_state
from commoner_probe.dmft.mines import MinesDmftProbe
from commoner_probe.dspace import LegacyDSpaceProbe, parse_browse_page
from commoner_probe.go_issue_register import AP_GOIR, GoIssueRegister, GoQuery, document_url
from commoner_probe.statute_dspace import HEADERS as INDIACODE_HEADERS
from commoner_probe.statute_dspace import IndiaCodeProbe
from commoner_probe.statute_dspace import parse_browse_page as parse_indiacode_browse

from ._shared import SCRATCH

# -- State assemblies (NeVA) ---------------------------------------------------

# One parser family serves every NeVA portal (see assembly_portal_registry.py),
# so one portal stands for all of them. Gujarat is the portal the adapter was
# written and tested against.
_NEVA_PORTAL, _NEVA_STATE = "gujarat", "GJ"

# NeVA prints a sitting date in the portal's own language, for example
# "શુક્રવાર, ૧૧ સપ્ટેમ્બર, ૨૦૨૬" (Friday, 11 September, 2026). Gujarati digits
# and month names are the only forms seen live; another portal that prints a
# month this table lacks yields no date, never a wrong one.
_GUJARATI_DIGITS = str.maketrans("૦૧૨૩૪૫૬૭૮૯", "0123456789")
_MONTHS = {
    "જાન્યુઆરી": 1, "ફેબ્રુઆરી": 2, "માર્ચ": 3, "એપ્રિલ": 4, "મે": 5, "જૂન": 6, "જુન": 6,
    "જુલાઈ": 7, "ઑગસ્ટ": 8, "ઓગસ્ટ": 8, "સપ્ટેમ્બર": 9, "ઑક્ટોબર": 10, "ઓક્ટોબર": 10,
    "નવેમ્બર": 11, "ડિસેમ્બર": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_SITTING = re.compile(r"(\d{1,2})\s+([^\s,\d]+)\s*,?\s+(\d{4})")


def _neva_date(label: str) -> str:
    """The ISO date in a NeVA sitting label, or "" when the label has none we can read.

    A label can carry a trailing sitting marker, for example "(બીજી બેઠક) 2nd sitting".
    """
    match = _SITTING.search((label or "").translate(_GUJARATI_DIGITS))
    if not match:
        return ""
    day, month_name, year = match.groups()
    month = _MONTHS.get(month_name.lower())
    if not month:
        return ""
    try:
        return date(int(year), month, int(day)).isoformat()
    except ValueError:
        return ""


@functools.cache
def _neva() -> StateAssemblyCrawler:
    crawler = StateAssemblyCrawler(_NEVA_PORTAL, _NEVA_STATE, SCRATCH / "state-assembly", sleep=0)
    crawler.bootstrap()
    return crawler


def _neva_latest_assembly(crawler: StateAssemblyCrawler, max_assembly: int = 20) -> tuple[int, list[dict]]:
    """Scan down from `max_assembly` to the first assembly with sessions, as probe_depth does."""
    for number in range(max_assembly, 0, -1):
        sessions = crawler.get_sessions(number)
        if sessions:
            return number, sessions
    return 0, []


def _neva_questions() -> list[dict]:
    crawler = _neva()
    assembly, sessions = _neva_latest_assembly(crawler)
    for session in sessions[:2]:  # NeVA lists the latest session first
        sittings = [(_neva_date(d.get("SessionDate")), d)
                    for d in crawler.get_dates(assembly, session["SessionCode"])]
        for sitting_date, sitting in sorted(sittings, key=lambda s: s[0], reverse=True)[:5]:
            rows = crawler.fetch_questions_for_date(
                assembly, session["SessionCode"], sitting["SessionDateId"], set())
            if rows:
                # The question table has no date column. Its rows are the questions
                # listed for this sitting, so the sitting's date is theirs.
                return [{**row, "sitting_date": sitting_date,
                         "pdf_url": (row.get("pdf_urls") or [""])[0]} for row in rows[:5]]
    return []


_NEVA_DEPTH_COUNTS = ("latest_assembly", "sessions_found", "dates_found", "members_count")


@functools.cache
def _neva_depth() -> dict:
    result = StateAssemblyCrawler(
        _NEVA_PORTAL, _NEVA_STATE, SCRATCH / "state-assembly-probe", sleep=0).probe_depth()
    # A zero count would pass the contract check, which only rejects empty values.
    # Report it as empty so a portal that returns no sessions or no members fails it.
    return {key: (value or "") if key in _NEVA_DEPTH_COUNTS else value
            for key, value in result.items()}



# -- India Code (state Acts) ---------------------------------------------------

# The West Bengal Public Libraries Act, 1979: the record that statute_dspace.py
# cites as its verified example.
_INDIACODE_STATE, _INDIACODE_STATE_HANDLE = "West Bengal", "2512"
_INDIACODE_SENTINEL_HANDLE = "14547"


@functools.cache
def _indiacode_probe() -> IndiaCodeProbe:
    return IndiaCodeProbe(SCRATCH / "indiacode", sleep=0, rpp=5)


@functools.cache
def _indiacode_act() -> list[dict]:
    return _indiacode_probe().probe_act(
        _INDIACODE_STATE, _INDIACODE_STATE_HANDLE, _INDIACODE_SENTINEL_HANDLE)


def _indiacode_records() -> list[dict]:
    return _indiacode_act()[:5]


def _indiacode_total() -> int:
    """How many Acts the West Bengal collection lists, from the browse banner."""
    text = _indiacode_probe()._get(
        f"/handle/123456789/{_INDIACODE_STATE_HANDLE}/browse?type=dateissued&rpp=5&offset=0")
    return parse_indiacode_browse(text)[2]


def _indiacode_sentinel() -> dict | None:
    return next((r for r in _indiacode_act() if r.get("instrument_type") == "act"), None)


# -- Government orders (Andhra Pradesh) ----------------------------------------

# The default control and the adapter's verified example are both School
# Education orders, so that department is the one this entry searches.
_GO_DEPARTMENT = "SE"
_GO_WINDOW_DAYS = 60


def _go_orders() -> list[dict]:
    register = GoIssueRegister(AP_GOIR, timeout=120)
    register.run_control()  # an empty grid proves nothing until the control has passed
    today = date.today()
    rows = register.search(GoQuery(
        department=_GO_DEPARTMENT, from_date=today - timedelta(days=_GO_WINDOW_DAYS), to_date=today))
    return [{"go_no": row.go_no, "order_date": row.order_date,
             "document_url": document_url(AP_GOIR, row.files[0][0], row.files[0][1])}
            for row in rows[:20]]


# -- CAG State Finance Accounts ------------------------------------------------

_CAG_STATE = "Gujarat"  # the state the adapter's tests use


def _cag_accounts() -> list[dict]:
    probe = CAGAccountsProbe(SCRATCH / "cag", sleep=0)
    records = probe.probe(get_state(_CAG_STATE), volumes=["II"], dry_run=True)
    return sorted(records, key=lambda r: r["year"], reverse=True)


def _cag_edition(cycle: int) -> str:
    """The fiscal year whose Finance Accounts are due in `cycle`, for example 2026 -> "2023-24"."""
    return f"{cycle - 3}-{(cycle - 2) % 100:02d}"


# -- MyNeta (Lok Sabha 2024 affidavits) ----------------------------------------

# Candidate 17 (Bishnu Pada Ray, Andaman and Nicobar Islands) is the record
# the adapter's tests use.
_MYNETA_SENTINEL_ID = 17


@functools.cache
def _myneta_probe() -> MyNetaProbe:
    return MyNetaProbe(SCRATCH / "myneta", sleep=0)


@functools.cache
def _myneta_constituencies() -> list[dict]:
    return _myneta_probe().discover_constituencies()


def _myneta_records() -> list[dict]:
    return [_myneta_probe().fetch_candidate(_MYNETA_SENTINEL_ID)]


def _myneta_sentinel() -> dict | None:
    return _myneta_records()[0]


# -- Legacy DSpace (Assam Legislative Assembly Digital Library) ----------------

# The instance the adapter's tests and docs/CLI.md use. DSpace XMLUI is one
# platform, so one instance stands for the parser.
_DSPACE_BASE, _DSPACE_NAME = "https://aladigitallibrary.in", "assam-ala"
_DSPACE_SENTINEL_HANDLE = "2263"  # "Economic Survey Assam 2023-24"


@functools.cache
def _dspace_probe() -> LegacyDSpaceProbe:
    return LegacyDSpaceProbe(SCRATCH / "legacy-dspace", base_url=_DSPACE_BASE,
                             portal_name=_DSPACE_NAME, sleep=0, rpp=5)


def _dspace_item(handle: str) -> dict:
    probe = _dspace_probe()
    item = probe.fetch_item(handle)
    paths = item.get("bitstream_paths") or []
    return {**item, "bitstream_url": probe.base_url + paths[0] if paths else ""}


@functools.cache
def _dspace_items() -> list[dict]:
    handles = [h for h, _ in itertools.islice(_dspace_probe().iter_handles(), 3)]
    return [_dspace_item(h) for h in handles]


def _dspace_sentinel() -> dict | None:
    return _dspace_item(_DSPACE_SENTINEL_HANDLE)


def _dspace_total() -> int:
    probe = _dspace_probe()
    text = probe._get("/browse?type=dateissued&order=ASC&rpp=5&offset=0")
    return parse_browse_page(text, probe.handle_prefix)[2]


# -- Mines DMFT source files ---------------------------------------------------

_LAST_MODIFIED = re.compile(r"\d{4}-\d{2}-\d{2}")


def _last_modified_date(r: dict) -> str:
    """The date the server says it last changed the file, as ISO, or "".

    This is the HTTP `Last-Modified` header, not a date inside the data. A job
    that rewrites the file without new figures keeps it looking current.
    """
    match = _LAST_MODIFIED.match(r.get("source_last_modified") or "")
    return match.group(0) if match else ""


def _dmft(source: str) -> list[dict]:
    probe = MinesDmftProbe(SCRATCH / f"mines-dmft-{source}", sleep=0)
    opener = probe._build_opener()
    # The files are 1 to 4 KB, and downloading them is the adapter's only path
    # to the `Last-Modified` header.
    return [probe.download_endpoint(opener, endpoint, dry_run=False)
            for endpoint in probe.endpoints_for([source])
            if endpoint.endpoint_kind != "report_page"]


SOURCES = [
    Source(id="state-assembly-neva", label="State assembly questions (NeVA, Gujarat)",
           host="gujarat.neva.gov.in",
           fetch=_neva_questions, required=("key", "question_number", "sitting_date", "pdf_url"),
           record_date=lambda r: r["sitting_date"],
           document=lambda r: r["pdf_url"], document_headers={"User-Agent": NEVA_UA},
           # Assemblies sit a few times a year, and a session can be 6 months from the last.
           freshness=(MaxAge(365),)),
    Source(id="state-assembly-probe-neva", label="State assembly depth probe (NeVA, Gujarat)",
           host="gujarat.neva.gov.in",
           fetch=lambda: [_neva_depth()],
           required=("latest_assembly", "sessions_found", "dates_found", "members_count"),
           # The probe result carries counts and no date, so this entry can't see a
           # portal that stopped adding sittings. It sees a portal that lost its
           # sessions or its member roster.
           total=lambda: _neva_depth()["members_count"] or 0,
           freshness=(CountFloor(),)),
    Source(id="indiacode", label="India Code state Acts (West Bengal)", host="indiacode.nic.in",
           fetch=_indiacode_records,
           required=("key", "short_title", "instrument_type", "source_url"),
           document=lambda r: r["source_url"], document_headers=INDIACODE_HEADERS,
           total=_indiacode_total,
           freshness=(Sentinel(_indiacode_sentinel, lambda r: r["short_title"] or "", "Public Libraries"),
                      CountFloor())),
    Source(id="go-register-ap", label="Andhra Pradesh government orders (School Education)",
           host="goir.ap.gov.in",
           fetch=_go_orders, required=("go_no", "order_date", "document_url"),
           record_date=lambda r: r["order_date"],
           # Msword orders are served from the same endpoint as PDFs.
           document=lambda r: r["document_url"], document_kind="any-non-html",
           freshness=(MaxAge(30),)),
    Source(id="cag-finance-accounts", label="CAG State Finance Accounts Vol-II (Gujarat)",
           host="cag.gov.in",
           fetch=_cag_accounts, required=("key", "year", "volume", "url"),
           document=lambda r: r["url"],
           # The portal publishes no schedule, so this is an estimate. The adapter's
           # docstring (audit_pdf_index.py, "live-verified 2026-07-23") records FY 2023-24
           # as the latest Vol-II then, about 28 months after that year ended. A fiscal
           # year ends in March, so the rule expects its Vol-II about 24 months later and
           # allows until 1 April: in 2026 that is FY 2023-24, and from April 2027,
           # FY 2024-25.
           freshness=(ExpectedEdition(release_month=3, grace_days=30, edition=_cag_edition,
                                      edition_of=lambda r: r["year"]),)),
    Source(id="myneta-ls2024", label="MyNeta Lok Sabha 2024 candidate affidavits", host="myneta.info",
           fetch=_myneta_records, required=("key", "name", "party", "source_url"),
           total=lambda: len(_myneta_constituencies()),
           freshness=(Sentinel(_myneta_sentinel, lambda r: r["name"] or "", "BISHNU PADA RAY"),
                      CountFloor())),
    Source(id="legacy-dspace-assam-ala", label="Legacy DSpace (Assam Legislative Assembly Library)",
           host="aladigitallibrary.in",
           fetch=_dspace_items, required=("handle_id", "title", "bitstream_url"),
           document=lambda r: r["bitstream_url"],
           total=_dspace_total,
           freshness=(Sentinel(_dspace_sentinel, lambda r: r["title"] or "", "Economic Survey Assam"),
                      CountFloor())),
    Source(id="mines-dmft-ministry", label="Ministry of Mines DMF dashboards (CSV)", host="mines.gov.in",
           fetch=lambda: _dmft("mines-gov-in"), required=("key", "filename", "url", "source_last_modified"),
           record_date=_last_modified_date,
           document=lambda r: r["url"], document_kind="any-non-html",
           # The dashboards carry no period; the file's own `Last-Modified` is the only date.
           freshness=(MaxAge(90),)),
    Source(id="mines-dmft-odisha", label="Odisha DMF summary files (JSON)", host="dmf.odisha.gov.in",
           fetch=lambda: _dmft("odisha"), required=("key", "filename", "url", "source_last_modified"),
           record_date=_last_modified_date,
           document=lambda r: r["url"], document_kind="any-non-html",
           freshness=(MaxAge(90),)),
]
