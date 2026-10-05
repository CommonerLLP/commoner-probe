"""Registry entries for data, finance, publication, archive, and academia sources."""
import functools
import re
from datetime import datetime
from urllib.parse import urlparse

from check_sources import (
    CountFloor,
    ExpectedEdition,
    MaxAge,
    Sentinel,
    Source,
)

from commoner_probe import __version__
from commoner_probe.academia.probe import AcademicJobsProbe
from commoner_probe.academia.registry import load_registry
from commoner_probe.annual_report_index import NITI_UA, NitiAnnualReportProbe
from commoner_probe.budget.probe import BUDGET_USER_AGENT, BudgetProbe
from commoner_probe.catalogue_search_api import AbhilekhPatalProbe, parse_totals
from commoner_probe.csr.dpe import DpeCsrProbe
from commoner_probe.csr.mca import McaCsrProbe
from commoner_probe.koha import KohaProbe
from commoner_probe.ministry_pdf_index import (
    SCHEME_FREE_USER_AGENT,
    MinistryDDGProbe,
    get_portal,
)
from commoner_probe.nada import NadaProbe
from commoner_probe.ogd_town_release import DchbTownProbe
from commoner_probe.pay_report_index import DoePayAllowancesProbe
from commoner_probe.rest_dataset_api import MospiClient
from commoner_probe.shrug_catalogue_api import catalogue
from commoner_probe.spa_jwt_api import UdiseDocumentProbe
from commoner_probe.wayback_recover import host_captures, rank_captures, raw_replay_url

from ._shared import SCRATCH

# -- Union Budget and RBI State Finances ---------------------------------------


def _budget_edition(cycle: int) -> str:
    """The fiscal year whose Budget is presented in `cycle`, for example 2026 -> "2026-27"."""
    return f"{cycle}-{(cycle + 1) % 100:02d}"


@functools.cache
def _budget_probe() -> BudgetProbe:
    return BudgetProbe(SCRATCH / "budget", sleep=0)


def _union_budget_records() -> list[dict]:
    """One dry-run record per year for Demand 101, newest first, from the adapter's static table."""
    probe = _budget_probe()
    opener = probe._build_opener()
    return [probe.download_endpoint(opener, endpoint, dry_run=True)
            for endpoint in probe.endpoints_for(["union-budget"])]


_RBI_RELEASE = re.compile(r"[A-Z][a-z]{2} \d{1,2}, \d{4}")


def _rbi_release_date(r: dict) -> str:
    """The ISO release date of an RBI State Finances edition, or "".

    The publication page prints the release date as the heading of the edition's
    main report, for example "Jan 23, 2026". Every other row sits under a heading
    such as "Chapters" and has no date.
    """
    section = (r.get("section") or "").strip()
    if not _RBI_RELEASE.fullmatch(section):
        return ""
    try:
        return datetime.strptime(section, "%b %d, %Y").date().isoformat()
    except ValueError:
        return ""


def _rbi_records() -> list[dict]:
    probe = _budget_probe()
    opener = probe._build_opener()
    return [probe.download_endpoint(opener, endpoint, dry_run=True)
            for endpoint in probe.discover_rbi(opener)]


# -- NADA (MoSPI microdata catalogue) ------------------------------------------

# The study that tests/test_nada.py and its fixtures use: NSS 68th round,
# Household Consumer Expenditure.
_NADA_IDNO = "DDI-IND-MOSPI-NSSO-68Rnd-Sch1.0-July2011-June2012"


@functools.cache
def _nada_client():
    return NadaProbe(SCRATCH / "nada", sleep=0).client


@functools.cache
def _nada_search() -> tuple[list[dict], int]:
    return _nada_client().search(max_studies=3)


# -- Census DCHB Town Release (ORGI NADA catalogue) ----------------------------

# The Nagaland record that tests/fixtures/dchb/README.md cites: the state's Town
# Release (26 towns) ships inside every one of its DCHB studies.
_DCHB_BASE = "https://censusindia.gov.in/nada"
_DCHB_STUDY = "DH_2011_1301_PART_A_DCHB_MON"
# The first town in that file, and the one the adapter's own test fixture keeps.
_DCHB_SENTINEL_TOWN = "801450"


@functools.cache
def _dchb_rows() -> list[dict]:
    """Download the Town Release workbook through NADA, then read it with the DCHB reader."""
    nada = NadaProbe(SCRATCH / "dchb-nada", base_url=_DCHB_BASE, sleep=0)
    study = nada.client.study(_DCHB_STUDY)
    resources, status, error = nada.client.resources(study["id"])
    if status != "ok":
        raise RuntimeError(f"{_DCHB_STUDY}: document list unavailable: {error}")
    resource = next((r for r in resources if "town_release" in (r.get("filename") or "").lower()), None)
    if resource is None:
        raise LookupError(f"{_DCHB_STUDY} no longer ships a Town Release workbook")
    record = nada._acquire_resource(_DCHB_STUDY, _DCHB_STUDY, study["id"], resource, True)
    if record["fetch_status"] != "downloaded":
        raise RuntimeError(f"{record['filename']}: {record['fetch_status']}: {record['error']}")
    return DchbTownProbe(SCRATCH / "dchb-town").ingest(nada.out_dir / record["path"])


def _dchb_sentinel() -> dict | None:
    return next((r for r in _dchb_rows() if r["town_code"] == _DCHB_SENTINEL_TOWN), None)


# -- MoSPI eSankhyiki (UDISE dropout rate) -------------------------------------

# The query that tests/test_mospi.py uses: indicator 41 (Dropout Rate), Gujarat
# (state code 8), 2024-25.
_MOSPI_PARAMS = {"indicator_code": 41, "year": "2024-25", "state_code": 8}


@functools.cache
def _mospi_rows() -> list[dict]:
    return list(MospiClient(sleep=0).pull("UDISE", _MOSPI_PARAMS, page_size=5, max_rows=5))


def _mospi_sentinel() -> dict | None:
    return _mospi_rows()[0] if _mospi_rows() else None


def _mospi_label(r: dict) -> str:
    return f"{r.get('indicator')} {r.get('state')} {r.get('year')}"


# -- SHRUG (Development Data Lab) ----------------------------------------------


@functools.cache
def _shrug_tables() -> list[dict]:
    # `url` is empty when the catalogue row carries no link the adapter can read.
    return [{"table_label": table.table_label, "module_label": table.module_label,
             "filetype": table.filetype, "url": table.url or ""}
            for table in catalogue().values()]


def _shrug_sentinel() -> dict | None:
    return next((t for t in _shrug_tables() if t["table_label"] == "2011 Population Census Village Directory"),
                None)


# -- DoE Pay and Allowances annual reports -------------------------------------


@functools.cache
def _doe_reports() -> list[dict]:
    probe = DoePayAllowancesProbe(SCRATCH / "doe-pay-allowances", sleep=0)
    return [probe.download_report(report, dry_run=True) for report in probe.discover()]


def _doe_sentinel() -> dict | None:
    # The oldest edition in the adapter's test fixture.
    return next((r for r in _doe_reports() if r["year"] == "2016-17"), None)


# -- Ministry Detailed Demands for Grants --------------------------------------


def _ddg(code: str) -> list[dict]:
    probe = MinistryDDGProbe(SCRATCH / f"ministry-ddg-{code}", portal=get_portal(code), sleep=0)
    return [probe.download_document(doc, dry_run=True) for doc in probe.discover()]


# -- NITI Aayog Annual Reports -------------------------------------------------


def _niti_edition(cycle: int) -> str:
    """The fiscal year whose report is due in `cycle`, for example 2026 -> "2025-26"."""
    return f"{cycle - 1}-{cycle % 100:02d}"


def _niti_reports() -> list[dict]:
    probe = NitiAnnualReportProbe(SCRATCH / "niti-annual-report", sleep=0)
    return [probe.download(report, dry_run=True) for report in probe.discover()]


# -- CSR (DPE and MCA) ---------------------------------------------------------


def _dpe_date(r: dict) -> str:
    """The ISO date of a DPE media upload, from the WordPress `date` field, or ""."""
    return (r.get("date") or "")[:10]


def _dpe_documents() -> list[dict]:
    probe = DpeCsrProbe(SCRATCH / "dpe-csr", sleep=0)
    records = (probe.download_item(item, dry_run=True) for item in probe.fetch_page(1, per_page=10))
    return [record for record in records if record]


# The year the adapter's docs and CLI examples use.
_MCA_YEAR = "2022-23"


def _mca_export() -> list[dict]:
    """Run the adapter's full path for one year: open the portal, then post the export form."""
    return McaCsrProbe(SCRATCH / "mca-csr", sleep=0).probe_years([_MCA_YEAR])


# -- UDISE+ public documents ---------------------------------------------------


def _udise_documents() -> list[dict]:
    """Fetch the first document of the `UploadedFiles` folder through the adapter's own probe."""
    return UdiseDocumentProbe(SCRATCH / "udise-docs", sleep=0).probe(
        folders=["UploadedFiles"], max_records=1)


# -- Koha (NITI Aayog library) -------------------------------------------------


@functools.cache
def _koha_result():
    # The portal that tests/test_koha.py's live test uses.
    return KohaProbe(
        SCRATCH / "koha", base_url="https://library.niti.gov.in", portal_name="niti-aayog",
        per_page=5, sleep=0, log=None).probe(dry_run=True)


def _koha_items() -> list[dict]:
    return [{**record, "title": record["biblio"].get("title")} for record in _koha_result().records]


# -- Abhilekh Patal (National Archives of India) -------------------------------

# The query that tests/test_abhilekh_patal.py cites (59,414 records on 2026-07-28).
_NAI_QUERY = "police"


@functools.cache
def _nai_probe() -> AbhilekhPatalProbe:
    return AbhilekhPatalProbe(SCRATCH / "abhilekh-patal", sleep=0)


def _nai_records() -> list[dict]:
    return list(_nai_probe().probe(query=_NAI_QUERY, max_records=5, dry_run=True))


def _nai_total() -> int:
    probe = _nai_probe()
    total, _ = parse_totals(probe._get(probe.search_url(_NAI_QUERY)))
    return total or 0


# -- Wayback Machine (read path of wayback-recover) ----------------------------

# The file that wayback_recover.py's docstring measures: three captures, the
# largest 14,561,108 bytes, taken on 21 January 2022. The CLI epilog omits the
# `2019-05/` path segment, which the index has.
_WAYBACK_PREFIX = "dsel.education.gov.in/sites/default/files/2019-05/AN_PAB_2018_2019.pdf"


@functools.cache
def _wayback_captures() -> list[dict]:
    """The file's captures, largest first, with the replay URL each one is read from."""
    captures = host_captures(_WAYBACK_PREFIX, retries=1, backoff=0)
    return [{"key": f"{capture.original}|{capture.timestamp}", "original": capture.original,
             "timestamp": capture.timestamp, "length": capture.length,
             "replay_url": raw_replay_url(capture.timestamp, capture.original)}
            for ranked in captures.values() for capture in rank_captures(ranked)]


def _wayback_sentinel() -> dict | None:
    return next((r for r in _wayback_captures() if r["timestamp"] == "20220121062121"), None)


# -- Academic jobs (one entry per parser family) -------------------------------

# One bundled institution stands for each parser family. `jnu` has a parser and no
# bundled institution, so no entry can reach it. `iit_rolling` uses IIT Bombay,
# because IIT Madras needs a robots override.
_ACADEMIA = (
    ("generic", "iit-guwahati"),
    ("iim_recruit", "iim-calcutta"),
    ("iit_rolling", "iit-bombay"),
    ("iit_kanpur", "iit-kanpur"),
    ("iit_gandhinagar", "iit-gandhinagar"),
    ("iit_hyderabad", "iit-hyderabad"),
    ("iit_indore", "iit-indore"),
    ("private_university", "ashoka-university"),
    ("anna_university", "anna-university"),
)


# The one parser that reads its ads out of a PDF. Without downloads it returns no ads.
_PDF_PARSERS = frozenset({"iit_rolling"})


def _academic_ads(institution_id: str, *, download: bool = False) -> list[dict]:
    """The ads one institution's parser extracts. Raise when it extracts none.

    The probe turns every failure into a status record that has a title and a URL,
    so the contract check would pass on one. An institution with no ads is a
    parser or page failure here.
    """
    probe = AcademicJobsProbe(
        SCRATCH / f"academic-jobs-{institution_id}", sleep=0, institutions=[institution_id])
    records = probe.probe(download=download, dry_run=False)
    ads = [r for r in records if r["fetch_status"] == "ok"]
    if not ads:
        status = records[0]
        raise ValueError(f"{institution_id} yielded no ads: {status['fetch_status']} {status.get('error') or ''}".strip())
    return ads


def _academic_source(parser: str, institution_id: str) -> Source:
    institution = next(i for i in load_registry() if i["id"] == institution_id)
    return Source(
        id=f"academic-jobs-{parser.replace('_', '-')}",
        label=f"Academic job ads ({parser} parser, {institution['short_name']}): no posting date, "
              "can't see stale ads",
        host=urlparse(institution["career_page_url_guess"]).netloc,
        fetch=lambda: _academic_ads(institution_id, download=parser in _PDF_PARSERS),
        required=("key", "title", "original_url"),
        # No parser's records carry a posting date on the live pages (checked
        # 5 October 2026), so MaxAge has nothing to read. A career page that stops
        # adding ads still parses. The entry sees a page that loses its ads or its
        # markup, not one that goes quiet. CountFloor would false-stale as ads close.
        freshness=())


# -- Entries -------------------------------------------------------------------

SOURCES = [
    Source(id="budget-union", label="Union Budget Demand for Grants (static URL table)",
           host="indiabudget.gov.in",
           fetch=_union_budget_records, required=("key", "fiscal_year", "url"),
           # indiabudget.gov.in returns 403 to the default user agent, so the document
           # check sends the adapter's own.
           document=lambda r: r["url"], document_kind="spreadsheet",
           document_headers={"User-Agent": BUDGET_USER_AGENT},
           # The records come from a table in the adapter, not from the site. The rule
           # tests that the table holds the current Budget, and the document check is
           # the only live proof that its newest file exists. The Budget is presented
           # on 1 February, and the 30 days of grace run to 3 March.
           freshness=(ExpectedEdition(release_month=2, grace_days=30, edition=_budget_edition,
                                      edition_of=lambda r: r["fiscal_year"]),)),
    Source(id="budget-rbi", label="RBI State Finances: A Study of Budgets", host="rbi.org.in",
           fetch=_rbi_records, required=("key", "fiscal_year", "url", "filename"),
           record_date=_rbi_release_date,
           document=lambda r: r["url"], document_headers={"User-Agent": BUDGET_USER_AGENT},
           # The page lists only the latest edition, so no previous edition exists to
           # check against. The edition's own release date is the heading above its main
           # report: "Jan 23, 2026" for 2025-26. That is the one release seen, so the
           # schedule is unknown. The limit is one year plus about 85 days, so the rule
           # can't false-stale between a release and the next one.
           freshness=(MaxAge(450),)),
    Source(id="nada", label="NADA catalogue (MoSPI microdata): no date, can't see new studies",
           host="microdata.gov.in",
           # Known failure: microdata.gov.in serves a self-signed certificate chain, so
           # the adapter's default TLS check rejects it (SSLError).
           fetch=lambda: _nada_search()[0], required=("idno", "title", "url"),
           total=lambda: _nada_search()[1],
           # `changed` is the metadata edit time, not a publication date, so it isn't
           # used. The sentinel only proves that a known study still resolves.
           freshness=(Sentinel(lambda: _nada_client().study(_NADA_IDNO), lambda r: r.get("title") or "",
                               "Household Consumer Expenditure"),
                      CountFloor())),
    Source(id="dchb-town", label="Census DCHB Town Release, Nagaland: no date, can't see new releases",
           host="censusindia.gov.in",
           # Known failure: censusindia.gov.in omits the intermediate certificate from
           # its chain, so the adapter's default TLS check rejects it (SSLError).
           fetch=_dchb_rows,
           required=("key", "state_code", "town_code", "town_name", "public_library_total"),
           total=lambda: len(_dchb_rows()),
           # The sentinel only proves that a known town row is still in the workbook.
           freshness=(Sentinel(_dchb_sentinel, lambda r: r["town_name"] or "", "Naginimora"),
                      CountFloor())),
    Source(id="mospi-udise", label="MoSPI eSankhyiki UDISE dropout rate: no date, can't see new years",
           host="api.mospi.gov.in",
           # Known failure: api.mospi.gov.in needs legacy TLS renegotiation, which
           # OpenSSL 3 refuses by default (UNSAFE_LEGACY_RENEGOTIATION_DISABLED).
           fetch=_mospi_rows, required=("indicator", "year", "state", "value"),
           total=lambda: len(MospiClient(sleep=0).indicators("UDISE")),
           # Rows carry a school year such as "2024-25", not a date, and MoSPI
           # documents no release schedule, so ExpectedEdition has no evidence to use.
           # The sentinel only proves that a known row still resolves.
           freshness=(Sentinel(_mospi_sentinel, _mospi_label, "Dropout Rate Gujarat 2024-25"),
                      CountFloor())),
    Source(id="shrug", label="SHRUG table catalogue (Development Data Lab): no date, can't see new tables",
           host="www.devdatalab.org",
           # The catalogue carries no date. On 5 October 2026 its download cells hold
           # bare URLs where the adapter reads an anchor `href`, so `url` is empty and
           # the contract check fails.
           fetch=_shrug_tables, required=("table_label", "filetype", "url"),
           total=lambda: len(_shrug_tables()),
           document=lambda r: r["url"], document_kind="zip",
           # The sentinel only proves that a known table is still listed.
           freshness=(Sentinel(_shrug_sentinel, lambda r: r["table_label"],
                               "2011 Population Census Village Directory"),
                      CountFloor())),
    Source(id="doe-pay-allowances", label="DoE Pay and Allowances annual reports: no date, can't see new years",
           host="doe.gov.in",
           fetch=_doe_reports, required=("key", "year", "title", "url"),
           total=lambda: len(_doe_reports()),
           document=lambda r: r["url"],
           # The listing carries a year, not a date, and the page documents no release
           # schedule. The adapter's test fixture shows 2023-24 as the newest edition
           # on 8 July 2026, so ExpectedEdition has no evidence to use. The sentinel
           # only proves that an old edition is still listed.
           freshness=(Sentinel(_doe_sentinel, lambda r: r["year"], "2016-17"), CountFloor())),
    Source(id="ministry-ddg-dea", label="Detailed Demands for Grants (DEA, card template)",
           host="dea.gov.in",
           fetch=lambda: _ddg("dea"), required=("key", "title", "year", "url"),
           document=lambda r: r["url"],
           # The adapter's docstring lists 2026-27 on the DEA page on 8 July 2026, and
           # the Budget is presented on 1 February. No earlier release date is known,
           # so the rule allows until 1 July.
           freshness=(ExpectedEdition(release_month=6, grace_days=30, edition=_budget_edition,
                                      edition_of=lambda r: r["year"]),)),
    Source(id="ministry-ddg-mha", label="Detailed Demands for Grants (MHA, table template)",
           host="www.mha.gov.in",
           fetch=lambda: _ddg("mha"), required=("key", "title", "year", "url"),
           document=lambda r: r["url"], document_headers={"User-Agent": SCHEME_FREE_USER_AGENT},
           # The newest two editions' files sit under `2026-02/` and `2025-02/`, and their
           # names end in 11 February 2026 and 12 February 2025. The rule allows until
           # 1 April.
           freshness=(ExpectedEdition(release_month=3, grace_days=31, edition=_budget_edition,
                                      edition_of=lambda r: r["year"]),)),
    Source(id="ministry-ddg-dst", label="Detailed Demands for Grants (DST, list template)",
           host="dst.gov.in",
           fetch=lambda: _ddg("dst"), required=("key", "title", "year", "url"),
           document=lambda r: r["url"],
           # The adapter's docstring lists ten editions through 2026-27 on 9 July 2026.
           # No earlier release date is known, so the rule allows until 1 July.
           freshness=(ExpectedEdition(release_month=6, grace_days=30, edition=_budget_edition,
                                      edition_of=lambda r: r["year"]),)),
    Source(id="niti-annual-report", label="NITI Aayog Annual Reports (English)", host="www.niti.gov.in",
           fetch=_niti_reports, required=("key", "report_year", "filename", "url"),
           document=lambda r: r["url"],
           document_headers={"User-Agent": NITI_UA.format(version=__version__)},
           # The upload directories in the adapter's test fixture, which copies the live
           # listing of 30 July 2026, put the 2025-26 report in May 2026, the 2024-25
           # report in February 2025, and the 2022-23 report in February 2023. The rule
           # allows until 1 July for the report of the fiscal year that ended in March.
           freshness=(ExpectedEdition(release_month=6, grace_days=30, edition=_niti_edition,
                                      edition_of=lambda r: r["report_year"]),)),
    Source(id="dpe-csr", label="DPE CPSE CSR documents (WordPress media)", host="dpe.gov.in",
           fetch=_dpe_documents, required=("key", "id", "title", "url"),
           record_date=_dpe_date,
           document=lambda r: r["url"], document_kind="any-non-html",
           # Nothing documents how often DPE uploads CSR files. A year is the longest
           # gap an annual publication allows.
           freshness=(MaxAge(365),)),
    Source(id="mca-csr", label="MCA CSR company spend, FY 2022-23: no date, can't see new years",
           host="mcacdm.nic.in",
           # The export is the only path that returns data, and it is one CSV for a whole
           # year. The record carries no date and the portal documents no release
           # schedule, so the entry checks the contract and nothing else. The export
           # form needs a CSRF token from the portal page first.
           fetch=_mca_export, required=("key", "financial_year", "filename", "sha256"),
           freshness=()),
    Source(id="udise-docs", label="UDISE+ public documents: pinned catalogue, can't see new documents",
           host="api.udiseplus.gov.in",
           # The portal has no listing endpoint, so the adapter pins 86 names. The entry
           # fetches one real document and passes only when the body is a PDF. It can't
           # see a document that the portal adds.
           fetch=_udise_documents, required=("key", "url", "sha256"),
           freshness=()),
    Source(id="koha-niti", label="NITI Aayog library (Koha): no date, can't see new holdings",
           host="library.niti.gov.in",
           fetch=_koha_items, required=("key", "item_id", "title"),
           total=lambda: _koha_result().held_items_total_first or 0,
           # Neither the adapter's tests nor its docs cite a known item, so there is no
           # sentinel. The count is the only check, and it only sees holdings fall.
           freshness=(CountFloor(),)),
    Source(id="abhilekh-patal", label="Abhilekh Patal catalogue, \"police\": no date, can't see new records",
           host="www.abhilekh-patal.in",
           # Known failure: the site sits behind an AWS WAF that challenges every
           # commoner-probe user agent from non-India egress. The adapter raises
           # ChallengeBlocked, and the repo has decided not to send a browser user agent.
           fetch=_nai_records, required=("key", "item_id", "title", "url"),
           total=_nai_total,
           # The adapter's tests cite no known record, so there is no sentinel.
           freshness=(CountFloor(),)),
    Source(id="wayback-recover", label="Wayback Machine capture index (wayback-recover read path)",
           host="web.archive.org",
           fetch=_wayback_captures, required=("key", "original", "timestamp", "length", "replay_url"),
           total=lambda: len(_wayback_captures()),
           document=lambda r: r["replay_url"],
           # The index only grows, and the sentinel is the capture that the adapter's
           # docstring cites. It only proves that a known capture is still listed.
           freshness=(Sentinel(_wayback_sentinel, lambda r: r["original"], "AN_PAB_2018_2019"),
                      CountFloor())),
    *(_academic_source(parser, institution_id) for parser, institution_id in _ACADEMIA),
]
