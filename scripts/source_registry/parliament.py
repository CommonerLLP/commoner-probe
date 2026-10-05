"""Registry entries for Parliament sources: session calendars, questions, committees, bills, and more."""
import functools
import re
from datetime import date
from urllib.parse import urlparse

from check_sources import (
    CountFloor,
    ExpectedEdition,
    MaxAge,
    Sentinel,
    SessionAware,
    SiblingLag,
    Source,
)

from commoner_probe import bill_catalog_api, parliament_qa_api, question_list_api, verbatim_pdf_api
from commoner_probe.attendance_json_api import AttendanceProbe
from commoner_probe.bill_catalog_api import BillsProbe
from commoner_probe.committee_report_api import (
    LS_PDF_HEADERS,
    RS_PDF_BUCKET_HOST,
    RS_PDF_HEADERS,
    CommitteeProbe,
    parse_ls_date,
    parse_rs_date,
)
from commoner_probe.drupal_publication_index import (
    PRS_CRAWL_DELAY_SEC,
    PrsProbe,
)
from commoner_probe.members import fetch_committee_members
from commoner_probe.parliament_qa_api import SansadProbe
from commoner_probe.question_list_api import QuestionsListProbe
from commoner_probe.verbatim_pdf_api import DebateProbe

from ._shared import SCRATCH, TOPIC


def _sessions_ls() -> list[dict]:
    probe = SansadProbe(None, SCRATCH / "sessions", sleep=0)
    latest = probe.ls_portal_terms()[-1]
    return [{"loksabha": e.loksabha, "session": e.session,
             "first_sitting": e.sitting_dates[0], "last_sitting": e.sitting_dates[-1]}
            for e in probe.session_catalog("ls", loksabha=latest) if e.sitting_dates]


def _committees_ls() -> list[dict]:
    probe = CommitteeProbe(TOPIC, SCRATCH / "committees-ls", sleep=0)
    out: list[dict] = []
    for code in (12, 7, 11):  # Finance, Defence, External Affairs
        out += probe.ls_page(code, 1, size=5).get("records") or []
    return out


def _committees_rs() -> list[dict]:
    probe = CommitteeProbe(TOPIC, SCRATCH / "committees-rs", sleep=0)
    out: list[dict] = []
    for mst in (14, 15, 19):  # Health, Home Affairs, Science
        out += probe.rs_page(mst, 1, size=5).get("records") or []
    return out


def _ls_date(r: dict) -> str:  # mirrors CommitteeProbe.probe_ls's fallback order
    return (parse_ls_date(r.get("PresentedInLS")) or parse_ls_date(r.get("LaidInRS"))
            or parse_ls_date(r.get("PresentedToSpeaker")) or parse_ls_date(r.get("dateOfPresentation")))


def _rs_date(r: dict) -> str:
    return parse_rs_date(r.get("dateOfPresentation")) or parse_rs_date(r.get("dateOfAdoption"))


def _off_the_rs_bucket(url: str) -> bool:
    """Respect robots.txt for an RS committee PDF unless it's on the RS bucket."""
    return urlparse(url).hostname != RS_PDF_BUCKET_HOST


# -- Shared helpers for the calendar-driven entries -------------------------


def _recent_sittings(entries: list, today: str, limit: int) -> list[tuple]:
    """Return up to `limit` (entry, ISO date) pairs, newest first, for sitting
    dates before `today`. A date that hasn't happened has no documents yet."""
    out: list[tuple] = []
    for entry in sorted(entries, key=lambda e: (e.loksabha or 0, e.session), reverse=True):
        for day in sorted(entry.sitting_dates, reverse=True):
            if day < today:
                out.append((entry, day))
                if len(out) == limit:
                    return out
    return out


def _house_sittings(house: str, limit: int) -> list[tuple]:
    probe = SansadProbe(None, SCRATCH / "calendar", sleep=0)
    if house == "ls":
        entries = probe.session_catalog("ls", loksabha=probe.ls_portal_terms()[-1])
    else:
        entries = probe.session_catalog("rs")
    return _recent_sittings(entries, date.today().isoformat(), limit)


def _house_sessions(house: str, limit: int) -> list:
    """Return up to `limit` sessions that have started, newest first."""
    today = date.today().isoformat()
    probe = SansadProbe(None, SCRATCH / "calendar", sleep=0)
    if house == "ls":
        entries = probe.session_catalog("ls", loksabha=probe.ls_portal_terms()[-1])
    else:
        entries = probe.session_catalog("rs")
    started = [e for e in entries if e.sitting_dates and e.first_sitting < today]
    return sorted(started, key=lambda e: (e.loksabha or 0, e.session), reverse=True)[:limit]


def _session_rows(entries: list) -> list[dict]:
    return [{"session": e.session, "first_sitting": e.sitting_dates[0],
             "last_sitting": e.sitting_dates[-1]} for e in entries if e.sitting_dates]


# -- Session calendar, Rajya Sabha ------------------------------------------


def _sessions_rs() -> list[dict]:
    probe = SansadProbe(None, SCRATCH / "sessions-rs", sleep=0)
    return _session_rows(probe.session_catalog("rs")[-5:])


# -- Questions ----------------------------------------------------------------


def _sansad_ls() -> list[dict]:
    probe = SansadProbe(None, SCRATCH / "sansad-ls", sleep=0)
    for entry in _house_sessions("ls", 3):
        rows = probe.ls_question_list_page(entry.loksabha, entry.session, 1, page_size=20)
        if rows:
            return [probe._ls_portal_record(row, run_id="freshness", loksabha=entry.loksabha,
                                            session_number=entry.session) for row in rows]
    return []


def _sansad_rs() -> list[dict]:
    probe = SansadProbe(None, SCRATCH / "sansad-rs", sleep=0)
    for entry in _house_sessions("rs", 3):
        # One ministry keeps the response small; a bare ses_no returns the whole
        # session (about 5 MB). Health is the largest, so it's the least likely to be empty.
        rows = probe.rs_search_session(entry.session, "Health")
        if rows:
            return [probe._rs_record(row, run_id="freshness", found_via=f"rs_session:{entry.session}")
                    for row in rows]
    return []


# -- Tabled papers (eLibrary) ------------------------------------------------

# The query that docs/CLI.md uses for `sansad tabled`. It returns a small, closed set.
_TABLED_QUERY = '"Delhi Public Library" review'
# A 1997 item in that set (handle 123456789/355758).
_TABLED_SENTINEL_UUID = "776ed489-286f-488a-bc10-2edc8fc4ea8a"


@functools.cache
def _tabled_probe() -> SansadProbe:
    return SansadProbe(None, SCRATCH / "sansad-tabled", sleep=0)


@functools.cache
def _tabled_items() -> list[dict]:
    return list(_tabled_probe().search_titles(_TABLED_QUERY, size=100))


def _tabled_records() -> list[dict]:
    probe = _tabled_probe()
    return [probe._tabled_record(item, run_id="freshness", query=_TABLED_QUERY)
            for item in _tabled_items()[:5]]


def _tabled_sentinel() -> dict | None:
    probe = _tabled_probe()
    item = next((i for i in _tabled_items() if i.get("uuid") == _TABLED_SENTINEL_UUID), None)
    return probe._tabled_record(item, run_id="freshness", query=_TABLED_QUERY) if item else None


def _tabled_pdf(rec: dict) -> str:
    return _tabled_probe().ls_pdf_url(rec["uuid"]) or ""


# -- Daily documents: question lists and debate transcripts -----------------


def _question_lists(house: str) -> list[dict]:
    for entry, day in _house_sittings(house, 5):
        kwargs = {"loksabhas": [entry.loksabha]} if house == "ls" else {}
        probe = QuestionsListProbe(SCRATCH / f"questions-list-{house}-{day}", house=house,
                                   sessions=[entry.session], from_date=day, to_date=day,
                                   sleep=0, **kwargs)
        # The probe returns Bulletin I and II records too. A sitting with only a
        # bulletin has no question list, so it doesn't count.
        records = [r for r in probe.probe(download=False)
                   if r.get("document_kind") == "question_list"]
        if records:
            return records
    return []


def _debates(house: str) -> list[dict]:
    """Return the newest sitting's debate records.

    DebateProbe.probe() logs a failed day and moves on, so a 503 or timeout would
    reach the checker as an empty sample. This calls the adapter's per-day
    lookups, which raise. A sitting that answers with no PDF falls back to an
    earlier one, as does a sitting whose lookup fails; when every sitting
    fails, the last error propagates."""
    error: Exception | None = None
    for entry, day in _house_sittings(house, 5):
        kwargs = {"loksabhas": [entry.loksabha]} if house == "ls" else {}
        probe = DebateProbe(SCRATCH / f"debates-{house}-{day}", house=house,
                            sessions=[entry.session], from_date=day, to_date=day,
                            sleep=0, **kwargs)
        year, month, dom = day.split("-")
        try:
            if house == "ls":
                url = probe.debate_pdf_url(entry.loksabha, entry.session,
                                           f"{int(month)}/{int(dom)}/{year}")
                records = [probe._ls_record(entry.loksabha, entry.session, day, pdf_url=url,
                                            status="metadata_only", run_id="freshness")] if url else []
            else:
                records = [
                    probe._rs_record(entry.session, day, pdf_url=row.get("FileUrl"),
                                     status="metadata_only", run_id="freshness",
                                     segment=str(row.get("Time") or row.get("Name") or "").strip() or None)
                    for row in probe.rs_debate_pdfs(entry.session, f"{dom}/{month}/{year}")
                ]
        except Exception as exc:  # noqa: BLE001 - try an earlier sitting, then re-raise
            error = exc
            continue
        records = [r for r in records if r.get("pdf_url")]
        if records:
            return records
    if error is not None:
        raise error
    return []


# -- Bills --------------------------------------------------------------------


def _bills(house: str) -> list[dict]:
    probe = BillsProbe(SCRATCH / f"bills-{house}", sleep=0, houses=[house])
    return [probe._record(raw, house) for raw in probe.bills_page(house, 1, size=5).get("records") or []]


def _bill_date(r: dict) -> str:
    return r.get("introduced_date") or ""


# -- Attendance ---------------------------------------------------------------


def _attendance_ls() -> list[dict]:
    probe = AttendanceProbe(SCRATCH / "attendance-ls", sleep=0)
    catalog = probe.session_catalog()
    term = max(int(e["loksabha"]) for e in catalog if str(e.get("loksabha", "")).isdigit())
    today = date.today().isoformat()
    sessions = next(e for e in catalog if e.get("loksabha") == term).get("sessions", [])
    for sess in sorted(sessions, key=lambda s: s.get("sessionNo") or 0, reverse=True):
        sittings = sorted(d for d in (_dmy_to_iso(raw) for raw in sess.get("dates", [])) if d and d < today)
        if not sittings:
            continue
        rows = probe.fetch_session_attendance(term, sess["sessionNo"])
        if rows:
            # The feed carries no date, so `register_to` comes from the session
            # calendar, not from the data. This entry only checks that the newest
            # started session has a non-empty register while the calendar is
            # current. It can't detect a feed that stalled partway through a session.
            return [{**probe._record(term, sess["sessionNo"], row), "register_to": sittings[-1]}
                    for row in rows[:5]]
    return []


def _dmy_to_iso(value: str) -> str:
    """'28/01/2026' -> '2026-01-28', or "" when the value isn't DD/MM/YYYY."""
    try:
        day, month, year = (int(part) for part in value.strip().split("/"))
        return date(year, month, day).isoformat()
    except (ValueError, AttributeError):
        return ""


# -- Committee membership -----------------------------------------------------


def _committee_members_ls() -> list[dict]:
    out: list[dict] = []
    for code in (12, 7):  # Finance, Defence
        out += fetch_committee_members("ls", code, 18)[:5]
    return out


# -- PRS ----------------------------------------------------------------------

# PRS's robots.txt declares a 10-second crawl delay, so these probes keep it.
_PRS_LOKSABHA = 18  # the `prs` subcommand's default term


@functools.cache
def _prs_probe() -> PrsProbe:
    return PrsProbe(SCRATCH / "prs", sleep=PRS_CRAWL_DELAY_SEC)


@functools.cache
def _prs_listing(surface: str) -> list[dict]:
    """Every row of one PRS listing, as the adapter's dry-run records. One request."""
    probe = _prs_probe()
    if surface == "bill-track":
        return probe.probe_billtrack(dry_run=True)
    return probe.probe_publications(surface=surface, dry_run=True)


def _prs_publications(surface: str) -> list[dict]:
    return _prs_listing(surface)[:5]


def _prs_sentinel(surface: str, slug: str):
    def lookup() -> dict | None:
        return next((r for r in _prs_listing(surface) if r["slug"] == slug), None)
    return lookup


_MPTRACK_PERIOD = re.compile(r"\bto\s+(\d{2})-(\d{2})-(\d{4})\b")


def _mptrack_date(r: dict) -> str:
    """The end of the period an MP Track row covers, as an ISO date.

    The CSV carries it in `mp_note`, for example "Data corresponds to the
    period from 24-06-2024 to 13-08-2026."
    """
    match = _MPTRACK_PERIOD.search(r.get("mp_note") or "")
    if not match:
        return ""
    day, month, year = match.groups()
    return f"{year}-{month}-{day}"


def _mptrack(house: str) -> list[dict]:
    probe = PrsProbe(SCRATCH / f"prs-mp-track-{house}", sleep=PRS_CRAWL_DELAY_SEC)
    return probe.probe_mptrack(houses=[house], loksabhas=[_PRS_LOKSABHA], max_records=5)


# Committees are reconstituted each year in late September (formation date
# 2025-09-26 on the live API). Allow until 1 December before the new year's
# committees count as overdue.
def _committee_edition(rec: dict) -> str:
    return (rec.get("committeeFormationDate") or "")[:4]


SOURCES = [
    Source(id="sessions-ls", label="Lok Sabha session calendar", host="sansad.in",
           fetch=_sessions_ls, required=("first_sitting", "last_sitting"),
           record_date=lambda r: r["last_sitting"], freshness=(MaxAge(200),)),
    Source(id="committees-ls", label="Lok Sabha committee reports", host="sansad.in",
           fetch=_committees_ls, required=("reportNo", "SubjectOfTheReport", "url"),
           record_date=_ls_date, document=lambda r: r["url"], document_headers=LS_PDF_HEADERS,
           freshness=(SessionAware(),)),
    Source(id="committees-rs", label="Rajya Sabha committee reports",
           host="integration.rajyasabha.digital",
           fetch=_committees_rs, required=("reportNo", "subjectOfTheReport", "url"),
           record_date=_rs_date,
           document=lambda r: r["url"], document_headers=RS_PDF_HEADERS,
           # The PDF bucket's robots.txt returns 403; see probe_rs in
           # committee_report_api.py. A PDF on any other host keeps the check.
           document_respect_robots=_off_the_rs_bucket,
           freshness=(SessionAware(), SiblingLag("committees-ls", max_days=60))),
    Source(id="sessions-rs", label="Rajya Sabha session calendar", host="sansad.in",
           fetch=_sessions_rs, required=("first_sitting", "last_sitting"),
           record_date=lambda r: r["last_sitting"], freshness=(MaxAge(200),)),
    Source(id="sansad-ls", label="Lok Sabha questions", host="sansad.in",
           fetch=_sansad_ls, required=("key", "title", "date", "pdf_url"),
           record_date=lambda r: r["date"],
           document=lambda r: r["pdf_url"], document_headers=parliament_qa_api.PDF_HEADERS,
           freshness=(SessionAware(),)),
    Source(id="sansad-rs", label="Rajya Sabha questions", host="rsdoc.nic.in",
           fetch=_sansad_rs, required=("key", "title", "date", "pdf_url"),
           record_date=lambda r: r["date"],
           document=lambda r: r["pdf_url"], document_headers=parliament_qa_api.RS_HEADERS,
           freshness=(SessionAware(), SiblingLag("sansad-ls", 60))),
    Source(id="sansad-tabled", label="Tabled papers (Parliament Digital Library)",
           host="elibrary.sansad.in",
           fetch=_tabled_records, required=("uuid", "title", "uri"),
           document=_tabled_pdf, document_headers=parliament_qa_api.HEADERS,
           total=lambda: len(_tabled_items()),
           freshness=(Sentinel(_tabled_sentinel, lambda r: r["title"], "Delhi Public Library"),
                      CountFloor())),
    Source(id="questions-list-ls", label="Lok Sabha daily question lists", host="sansad.in",
           fetch=lambda: _question_lists("ls"), required=("key", "sitting_date", "pdf_url"),
           record_date=lambda r: r["sitting_date"],
           document=lambda r: r["pdf_url"], document_headers=question_list_api.PDF_HEADERS,
           freshness=(SessionAware(),)),
    Source(id="questions-list-rs", label="Rajya Sabha daily question lists", host="sansad.in",
           fetch=lambda: _question_lists("rs"), required=("key", "sitting_date", "pdf_url"),
           record_date=lambda r: r["sitting_date"],
           document=lambda r: r["pdf_url"], document_headers=question_list_api.PDF_HEADERS,
           freshness=(SessionAware(), SiblingLag("questions-list-ls", 60))),
    Source(id="debates-ls", label="Lok Sabha debate transcripts", host="sansad.in",
           fetch=lambda: _debates("ls"), required=("key", "date", "pdf_url"),
           record_date=lambda r: r["date"],
           document=lambda r: r["pdf_url"], document_headers=verbatim_pdf_api.PDF_HEADERS,
           freshness=(SessionAware(),)),
    Source(id="debates-rs", label="Rajya Sabha debate transcripts", host="rsdoc.nic.in",
           fetch=lambda: _debates("rs"), required=("key", "date", "pdf_url"),
           record_date=lambda r: r["date"],
           document=lambda r: r["pdf_url"], document_headers=verbatim_pdf_api.PDF_HEADERS,
           freshness=(SessionAware(), SiblingLag("debates-ls", 60))),
    Source(id="bills-ls", label="Lok Sabha bills", host="sansad.in",
           fetch=lambda: _bills("ls"), required=("key", "bill_name", "introduced_date", "introduced_file"),
           record_date=_bill_date,
           document=lambda r: r["introduced_file"], document_headers=bill_catalog_api.HEADERS,
           freshness=(SessionAware(),)),
    Source(id="bills-rs", label="Rajya Sabha bills", host="sansad.in",
           fetch=lambda: _bills("rs"), required=("key", "bill_name", "introduced_date", "introduced_file"),
           record_date=_bill_date,
           document=lambda r: r["introduced_file"], document_headers=bill_catalog_api.HEADERS,
           freshness=(SessionAware(), SiblingLag("bills-ls", 60))),
    Source(id="attendance-ls", label="Lok Sabha attendance (register present for latest session)", host="sansad.in",
           fetch=_attendance_ls, required=("key", "member_name", "signed_days_count"),
           record_date=lambda r: r["register_to"], freshness=(SessionAware(),)),
    Source(id="committees-members-ls", label="Lok Sabha committee membership", host="sansad.in",
           fetch=_committee_members_ls, required=("committeeCode", "memberName", "committeeFormationDate"),
           record_date=lambda r: r["committeeFormationDate"],
           freshness=(ExpectedEdition(release_month=10, grace_days=61, edition=str,
                                      edition_of=_committee_edition),)),
    Source(id="prs-report-summaries", label="PRS report summaries", host="prsindia.org",
           fetch=lambda: _prs_publications("report-summaries"), required=("slug", "title", "pdf_url"),
           document=lambda r: r["pdf_url"], document_rate_limit_sec=PRS_CRAWL_DELAY_SEC,
           total=lambda: len(_prs_listing("report-summaries")),
           freshness=(Sentinel(_prs_sentinel("report-summaries", "cyber-crimes-and-cyber-security-of-women"),
                               lambda r: r["title"], "Cyber Crimes"), CountFloor())),
    Source(id="prs-vital-stats", label="PRS vital stats", host="prsindia.org",
           fetch=lambda: _prs_publications("vital-stats"), required=("slug", "title", "pdf_url"),
           document=lambda r: r["pdf_url"], document_rate_limit_sec=PRS_CRAWL_DELAY_SEC,
           total=lambda: len(_prs_listing("vital-stats")),
           freshness=(Sentinel(_prs_sentinel("vital-stats", "direct-taxes-in-india"),
                               lambda r: r["title"], "Direct Taxes"), CountFloor())),
    Source(id="prs-bill-track", label="PRS bill track", host="prsindia.org",
           fetch=lambda: _prs_listing("bill-track")[:5], required=("slug", "title", "bill_status"),
           total=lambda: len(_prs_listing("bill-track")),
           freshness=(Sentinel(_prs_sentinel("bill-track", "the-forest-conservation-amendment-bill-2023"),
                               lambda r: r["title"], "Forest (Conservation)"), CountFloor())),
    Source(id="prs-mp-track-ls", label="PRS MP Track, Lok Sabha", host="prsindia.org",
           fetch=lambda: _mptrack("ls"), required=("mp_election_index", "mp_name", "mp_note"),
           record_date=_mptrack_date,
           document=lambda r: r["csv_url"], document_kind="any-non-html",
           document_rate_limit_sec=PRS_CRAWL_DELAY_SEC,
           freshness=(SessionAware(),)),
    Source(id="prs-mp-track-rs", label="PRS MP Track, Rajya Sabha", host="prsindia.org",
           fetch=lambda: _mptrack("rs"), required=("mp_election_index", "mp_name", "mp_note"),
           record_date=_mptrack_date,
           document=lambda r: r["csv_url"], document_kind="any-non-html",
           document_rate_limit_sec=PRS_CRAWL_DELAY_SEC,
           freshness=(SessionAware(), SiblingLag("prs-mp-track-ls", 60))),
]
