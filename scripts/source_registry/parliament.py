"""Registry entries for Parliament sources: session calendar and committee reports."""
from check_sources import MaxAge, SessionAware, SiblingLag, Source

from commoner_probe.committee_report_api import (
    LS_PDF_HEADERS,
    RS_PDF_HEADERS,
    CommitteeProbe,
    parse_ls_date,
    parse_rs_date,
)
from commoner_probe.parliament_qa_api import SansadProbe

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
           # The PDF host's robots.txt returns 403; see probe_rs in committee_report_api.py.
           document_respect_robots=False,
           freshness=(SessionAware(), SiblingLag("committees-ls", max_days=60))),
]
