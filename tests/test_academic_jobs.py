from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

# --------------------------------------------------------------------------- #
# Fake HTTP session (requests-style: .text + .raise_for_status)               #
# --------------------------------------------------------------------------- #


class FakeResp:
    def __init__(self, text: str = "", status: int = 200) -> None:
        self.text = text
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, routes: dict[str, FakeResp], *, forbid_calls: bool = False) -> None:
        self.routes = routes
        self.forbid_calls = forbid_calls
        self.calls: list[str] = []

    def get(self, url: str, **kwargs) -> FakeResp:
        self.calls.append(url)
        if self.forbid_calls:
            raise AssertionError(f"network should not be called (got {url})")
        for needle, resp in self.routes.items():
            if needle in url:
                return resp
        return FakeResp("", 404)


def _registry(tmp_path, entries):
    p = tmp_path / "reg.json"
    p.write_text(json.dumps(entries), encoding="utf-8")
    return p


_INST_GENERIC = {
    "id": "demo-univ", "name": "Demo University", "short_name": "DemoU",
    "type": "StateUniversity", "state": "Goa",
    "career_page_url_guess": "https://demo.example.ac.in/careers", "parser": "generic",
}
_INST_IIM = {
    "id": "iim-demo", "name": "IIM Demo", "short_name": "IIM-D",
    "type": "IIM", "state": "Kerala",
    "career_page_url_guess": "https://iimdemo.example.ac.in/faculty", "parser": "iim_recruit",
}


# --------------------------------------------------------------------------- #
# Parser registry / dispatch                                                  #
# --------------------------------------------------------------------------- #


def test_get_parser_dispatch_and_fallback():
    from commoner_probe.academia.parsers import UNMIGRATED_PARSERS, generic, get_parser, iim_recruit

    assert get_parser("iim_recruit") is iim_recruit.parse
    assert get_parser("generic") is generic.parse
    # Still-unmigrated specialised parsers fall back to generic, not error.
    assert "iit_delhi" in UNMIGRATED_PARSERS
    assert get_parser("iit_delhi") is generic.parse
    assert get_parser(None) is generic.parse


# --------------------------------------------------------------------------- #
# generic parser                                                              #
# --------------------------------------------------------------------------- #


def test_generic_parser_extracts_recruitment_links():
    pytest.importorskip("bs4")
    from commoner_probe.academia.parsers import generic

    html = """
    <ul>
      <li><a href="/about">About Us</a></li>
      <li><a href="/careers/faculty-recruitment-advt-2026.pdf">Faculty Recruitment Advertisement</a>
          (Last date: 30/06/2026)</li>
    </ul>
    """
    ads = generic.parse(html, "https://demo.example.ac.in/careers", datetime(2026, 6, 1))
    assert len(ads) == 1
    ad = ads[0]
    assert ad["institution_id"] == "__placeholder__"
    assert ad["post_type"] == "Faculty"
    assert ad["original_url"].endswith("faculty-recruitment-advt-2026.pdf")
    assert ad["closing_date"] == "2026-06-30"
    assert ad["pdf_parsed"] is False  # generic never fetches PDFs


# --------------------------------------------------------------------------- #
# iim_recruit parser (with injected pdf callable)                             #
# --------------------------------------------------------------------------- #


def test_iim_recruit_parses_pdf_fields():
    pytest.importorskip("bs4")
    from commoner_probe.academia.parsers import iim_recruit

    html = '<a href="/files/faculty-positions-strategy.pdf">Faculty Recruitment in Strategy Area</a>'
    pdf_text = (
        "Applications are invited. Last date: April 30, 2026. "
        "Reservation roster: UR-2 SC-1 ST-1 OBC-3 EWS-1. "
        "Candidates must have a minimum of FIVE publications in reputed journals."
    )

    class FakeFetcher:
        def pdf_text(self, pdf_url):
            return ("pdfs/strategy.pdf", pdf_text)

    ads = iim_recruit.parse(html, "https://iimdemo.example.ac.in/faculty", datetime(2026, 6, 1), FakeFetcher())
    assert len(ads) == 1
    ad = ads[0]
    assert ad["pdf_parsed"] is True
    assert ad["pdf_path"] == "pdfs/strategy.pdf"
    assert ad["closing_date"] == "2026-04-30"
    assert ad["category_breakdown"] == {"UR": 2, "SC": 1, "ST": 1, "OBC": 3, "EWS": 1}
    assert ad["number_of_posts"] == 8
    assert "publications" in (ad["publications_required"] or "").lower()
    assert ad["apply_url"] == "https://iimdemo.example.ac.in/files/faculty-positions-strategy.pdf"


def test_iim_recruit_emits_rolling_stub_when_no_pdfs():
    pytest.importorskip("bs4")
    from commoner_probe.academia.parsers import iim_recruit

    html = '<a href="/about">About</a>'
    ads = iim_recruit.parse(html, "https://iimdemo.example.ac.in/faculty", datetime(2026, 6, 1), None)
    assert len(ads) == 1
    assert ads[0]["rolling_stub"] is True
    assert ads[0]["pdf_parsed"] is False
    assert ads[0]["apply_url"] == "https://iimdemo.example.ac.in/faculty"


# --------------------------------------------------------------------------- #
# AcademicJobsProbe                                                           #
# --------------------------------------------------------------------------- #


def test_probe_dry_run_lists_institutions_without_fetching(tmp_path):
    from commoner_probe.academia import AcademicJobsProbe

    reg = _registry(tmp_path, [_INST_GENERIC, _INST_IIM])
    probe = AcademicJobsProbe(tmp_path, sleep=0, registry_path=reg)
    probe.session = FakeSession({}, forbid_calls=True)  # dry-run must not hit network

    records = probe.probe(dry_run=True)

    assert len(records) == 2
    assert {r["institution_id"] for r in records} == {"demo-univ", "iim-demo"}
    assert all(r["fetch_status"] == "dry_run" for r in records)
    assert all(r["kind"] == "academic_job_posting" for r in records)
    assert probe.session.calls == []  # dry-run made no network calls
    assert not (tmp_path / "manifest.jsonl").exists()


def test_probe_end_to_end_writes_manifest(tmp_path):
    pytest.importorskip("bs4")
    from commoner_probe.academia import AcademicJobsProbe

    html = '<a href="/careers/advt-2026.pdf">Faculty Recruitment Advertisement</a> last date 30/06/2026'
    reg = _registry(tmp_path, [_INST_GENERIC])
    probe = AcademicJobsProbe(tmp_path, sleep=0, registry_path=reg)
    probe.session = FakeSession({"demo.example.ac.in/careers": FakeResp(html)})

    records = probe.probe(download=False)

    assert len(records) == 1
    rec = records[0]
    assert rec["kind"] == "academic_job_posting"
    assert rec["institution_id"] == "demo-univ"
    assert rec["institution_name"] == "Demo University"
    assert rec["parser"] == "generic"
    assert rec["fetch_status"] == "ok"
    assert rec["key"].startswith("ACAD|demo-univ|")

    manifest = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert manifest == records

    # Dedup on rerun: same key, nothing added.
    probe2 = AcademicJobsProbe(tmp_path, sleep=0, registry_path=reg)
    probe2.session = FakeSession({"demo.example.ac.in/careers": FakeResp(html)})
    assert probe2.probe(download=False) == []


def test_probe_records_fetch_error(tmp_path):
    from commoner_probe.academia import AcademicJobsProbe

    reg = _registry(tmp_path, [_INST_GENERIC])
    probe = AcademicJobsProbe(tmp_path, sleep=0, registry_path=reg)
    probe.session = FakeSession({"demo.example.ac.in/careers": FakeResp("", 503)})

    records = probe.probe(download=False)
    assert len(records) == 1
    assert records[0]["fetch_status"] == "fetch_error"
    assert records[0]["institution_id"] == "demo-univ"


# --------------------------------------------------------------------------- #
# CLI + schema + corpus wiring                                                #
# --------------------------------------------------------------------------- #


def test_academic_jobs_cli_dry_run(tmp_path, capsys):
    from commoner_probe.cli import build_parser

    reg = _registry(tmp_path, [_INST_GENERIC])
    parser = build_parser()
    args = parser.parse_args([
        "academic-jobs", "--out", str(tmp_path), "--registry", str(reg), "--dry-run",
    ])
    args.func(args)

    lines = capsys.readouterr().out.splitlines()
    assert lines
    rec = json.loads(lines[0])
    assert rec["kind"] == "academic_job_posting"
    assert rec["fetch_status"] == "dry_run"
    assert not (tmp_path / "manifest.jsonl").exists()


def test_academic_job_schema_is_bundled_and_validates(tmp_path):
    pytest.importorskip("jsonschema")
    from commoner_probe import schemas
    from commoner_probe.validate import validate_corpus

    assert "manifest_academic_job" in schemas.list_all()

    record = {
        "key": "ACAD|demo-univ|abc123",
        "kind": "academic_job_posting",
        "record_type": "academic_job_posting",
        "source_family": "academia-india",
        "institution_id": "demo-univ",
        "institution_name": "Demo University",
        "parser": "generic",
        "title": "Faculty Recruitment Advertisement",
        "post_type": "Faculty",
        "contract_status": "Unknown",
        "category_breakdown": None,
        "number_of_posts": None,
        "original_url": "https://demo.example.ac.in/careers/advt.pdf",
        "pdf_parsed": False,
        "fetch_status": "ok",
        "parse_confidence": 0.5,
        "snapshot_fetched_at": datetime(2026, 6, 1, tzinfo=timezone.utc).isoformat(),
        "probed_at": "2026-06-24T10:00:00Z",
    }
    (tmp_path / "manifest.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

    assert validate_corpus(tmp_path, log=lambda _: None)


def test_corpus_streams_academic_jobs(tmp_path):
    from commoner_probe import Corpus

    record = {
        "key": "ACAD|iim-demo|stub",
        "kind": "academic_job_posting",
        "record_type": "academic_job_posting",
        "source_family": "academia-india",
        "institution_id": "iim-demo",
        "title": "Rolling faculty recruitment",
        "original_url": "https://iimdemo.example.ac.in/faculty",
        "fetch_status": "ok",
        "pdf_parsed": False,
        "probed_at": "2026-06-24T10:00:00Z",
    }
    (tmp_path / "manifest.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

    records = list(Corpus(tmp_path).manifest_academic_jobs())
    assert len(records) == 1
    assert records[0].institution_id == "iim-demo"
    assert records[0].fetch_status == "ok"


# --------------------------------------------------------------------------- #
# Fetch resilience (served-body 4xx, robots override, fallback PDF)           #
# --------------------------------------------------------------------------- #

_AD_HTML = '<a href="/careers/advt-2026.pdf">Faculty Recruitment Advertisement</a> last date 30/06/2026'


def test_probe_institution_accepts_served_body_4xx(tmp_path):
    """A 4xx that still serves a substantial listing body is parsed (e.g. iimcal 404)."""
    pytest.importorskip("bs4")
    from commoner_probe.academia import AcademicJobsProbe

    inst = {"id": "demo-univ", "career_page_url_guess": "https://demo.example.ac.in/careers",
            "parser": "generic"}
    probe = AcademicJobsProbe(tmp_path, sleep=0, registry_path=_registry(tmp_path, [inst]))
    big_body = _AD_HTML + ("padding " * 400)  # > 2000 chars
    probe.session = FakeSession({"demo.example.ac.in/careers": FakeResp(big_body, 404)})

    records = probe.probe_institution(inst, pdf=None, dry_run=False)
    assert len(records) == 1
    assert records[0]["fetch_status"] == "ok"
    assert records[0]["source_method"] == "official scrape"


def test_probe_institution_robots_override(tmp_path):
    """robots_override=True retries past a robots block and tags provenance."""
    pytest.importorskip("bs4")
    from commoner_probe.academia import AcademicJobsProbe

    inst = {"id": "iit-x", "career_page_url_guess": "https://iitx.ac.in/jobs",
            "parser": "generic", "robots_override": True}
    probe = AcademicJobsProbe(tmp_path, sleep=0, registry_path=_registry(tmp_path, [inst]))

    class RobotsSession:
        def get(self, url, *, respect_robots=True, **kwargs):
            if respect_robots:
                raise PermissionError("Disallowed by robots.txt")
            return FakeResp(_AD_HTML)

    probe.session = RobotsSession()
    records = probe.probe_institution(inst, pdf=None, dry_run=False)
    assert len(records) == 1
    assert records[0]["fetch_status"] == "ok"
    assert records[0]["source_method"] == "public-interest override"


def test_probe_institution_robots_blocked_without_override(tmp_path):
    from commoner_probe.academia import AcademicJobsProbe

    inst = {"id": "iit-x", "career_page_url_guess": "https://iitx.ac.in/jobs", "parser": "generic"}
    probe = AcademicJobsProbe(tmp_path, sleep=0, registry_path=_registry(tmp_path, [inst]))

    class RobotsSession:
        def get(self, url, *, respect_robots=True, **kwargs):
            raise PermissionError("Disallowed by robots.txt")

    probe.session = RobotsSession()
    records = probe.probe_institution(inst, pdf=None, dry_run=False)
    assert records[0]["fetch_status"] == "robots_blocked"  # no override -> recorded, not parsed


def test_probe_institution_falls_back_to_pdf_on_fetch_failure(tmp_path, monkeypatch):
    """When the listing fetch fails, a registry fallback_pdf_url keeps the institution visible."""
    from commoner_probe.academia import AcademicJobsProbe
    from commoner_probe.academia import probe as probe_mod

    inst = {"id": "iit-madras", "career_page_url_guess": "https://iitm.ac.in/jobs",
            "parser": "iit_rolling", "fallback_pdf_url": "https://iitm.ac.in/cached/ad.pdf"}
    probe = AcademicJobsProbe(tmp_path, sleep=0, registry_path=_registry(tmp_path, [inst]))

    class FailSession:
        def get(self, url, **kwargs):
            raise RuntimeError("listing down")

    probe.session = FailSession()

    def fake_parser(html, url, fetched_at, pdf):
        assert html == "" and url == "https://iitm.ac.in/cached/ad.pdf"
        return [{"id": "a1", "title": "Faculty — Aerospace", "original_url": url, "post_type": "Faculty"}]

    monkeypatch.setattr(probe_mod, "get_parser", lambda name: fake_parser)
    records = probe.probe_institution(inst, pdf=None, dry_run=False)
    assert len(records) == 1
    assert records[0]["fetch_status"] == "ok"
    assert records[0]["source_method"] == "fallback PDF"


# --------------------------------------------------------------------------- #
# Per-institution User-Agent                                                 #
# --------------------------------------------------------------------------- #

_BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/126.0.0.0"


def test_session_for_returns_the_default_session_without_a_user_agent(tmp_path):
    """An entry naming no user_agent keeps the shared default session."""
    from commoner_probe.academia import AcademicJobsProbe

    probe = AcademicJobsProbe(tmp_path, sleep=0, registry_path=_registry(tmp_path, [_INST_GENERIC]))
    assert probe._session_for(_INST_GENERIC) is probe.session
    assert probe._sessions == {}


def test_session_for_builds_a_session_carrying_the_requested_user_agent(tmp_path):
    """The registry string reaches the session, and the default is untouched."""
    from commoner_probe.academia import AcademicJobsProbe
    from commoner_probe.http_client import USER_AGENT

    inst = dict(_INST_IIM, user_agent=_BROWSER_UA)
    probe = AcademicJobsProbe(tmp_path, sleep=0, registry_path=_registry(tmp_path, [inst]))
    session = probe._session_for(inst)

    assert session is not probe.session
    assert session._user_agent == _BROWSER_UA
    assert probe.session._user_agent == USER_AGENT


def test_session_for_caches_by_user_agent_string(tmp_path):
    """Two institutions naming one string share one session; a third gets its own."""
    from commoner_probe.academia import AcademicJobsProbe

    a = dict(_INST_IIM, id="iim-a", user_agent=_BROWSER_UA)
    b = dict(_INST_IIM, id="iim-b", user_agent=_BROWSER_UA)
    c = dict(_INST_IIM, id="iim-c", user_agent="curl/8.7.1")
    probe = AcademicJobsProbe(tmp_path, sleep=0, registry_path=_registry(tmp_path, [a, b, c]))

    assert probe._session_for(a) is probe._session_for(b)
    assert probe._session_for(c) is not probe._session_for(a)
    assert len(probe._sessions) == 2


def test_probe_institution_fetches_the_listing_with_the_institution_session(tmp_path):
    """The WAF-blocked listing goes out on the registry User-Agent, not the default."""
    from commoner_probe.academia import AcademicJobsProbe

    html = "<html><body><a href='/ad.pdf'>Assistant Professor</a></body></html>"
    inst = dict(_INST_GENERIC, user_agent=_BROWSER_UA)
    probe = AcademicJobsProbe(tmp_path, sleep=0, registry_path=_registry(tmp_path, [inst]))
    probe.session = FakeSession({}, forbid_calls=True)
    ua_session = FakeSession({"demo.example.ac.in/careers": FakeResp(html)})
    probe._sessions[_BROWSER_UA] = ua_session

    probe.probe_institution(inst, pdf=None, dry_run=False)

    assert ua_session.calls == ["https://demo.example.ac.in/careers"]


def test_probe_institution_retries_the_robots_override_on_the_same_session(tmp_path):
    """The override retry must not fall back to the default User-Agent."""
    pytest.importorskip("bs4")
    from commoner_probe.academia import AcademicJobsProbe

    inst = dict(_INST_GENERIC, user_agent=_BROWSER_UA, robots_override=True)
    probe = AcademicJobsProbe(tmp_path, sleep=0, registry_path=_registry(tmp_path, [inst]))
    probe.session = FakeSession({}, forbid_calls=True)

    class RobotsSession:
        def __init__(self) -> None:
            self.calls: list[bool] = []

        def get(self, url, **kwargs):
            respect = kwargs.get("respect_robots", True)
            self.calls.append(respect)
            if respect:
                raise PermissionError("robots.txt disallows")
            return FakeResp(_AD_HTML)

    ua_session = RobotsSession()
    probe._sessions[_BROWSER_UA] = ua_session

    records = probe.probe_institution(inst, pdf=None, dry_run=False)

    assert ua_session.calls == [True, False]
    assert records[0]["source_method"] == "public-interest override"


def test_probe_hands_the_fetcher_the_institution_session(tmp_path):
    """A WAF refusing the listing refuses the PDF behind it for the same reason."""
    from commoner_probe.academia import AcademicJobsProbe

    inst = dict(_INST_GENERIC, user_agent=_BROWSER_UA)
    probe = AcademicJobsProbe(tmp_path, sleep=0, registry_path=_registry(tmp_path, [inst]))
    probe.session = FakeSession({}, forbid_calls=True)
    ua_session = FakeSession({"demo.example.ac.in/careers": FakeResp("<html>no ads</html>")})
    probe._sessions[_BROWSER_UA] = ua_session

    seen: list[object] = []
    original = probe._fetcher

    def record_fetcher(enabled, session=None, inst=None):
        seen.append(session)
        return original(enabled, session, inst)

    probe._fetcher = record_fetcher
    probe.probe(download=True)

    assert seen == [ua_session]


def test_probe_hands_the_fetcher_the_default_session_without_a_user_agent(tmp_path):
    """The default path is unchanged: no user_agent field, no extra session."""
    from commoner_probe.academia import AcademicJobsProbe

    probe = AcademicJobsProbe(tmp_path, sleep=0, registry_path=_registry(tmp_path, [_INST_GENERIC]))
    probe.session = FakeSession({"demo.example.ac.in/careers": FakeResp("<html>no ads</html>")})

    seen: list[object] = []
    original = probe._fetcher

    def record_fetcher(enabled, session=None, inst=None):
        seen.append(session)
        return original(enabled, session, inst)

    probe._fetcher = record_fetcher
    probe.probe(download=True)

    assert seen == [probe.session]
    assert probe._sessions == {}


def test_bundled_registry_names_a_user_agent_where_a_waf_needs_one():
    """The `user_agent` field must reach the BUNDLED registry, not only `--registry`.

    The field shipped in 0.17.0 with no bundled row using it, so a default CLI
    run kept the 403 the field exists to clear. Both institutions below answer
    403 to every commoner-probe User-Agent, measured 2026-08-23.
    """
    from commoner_probe.academia.registry import load_registry

    by_id = {inst["id"]: inst for inst in load_registry()}
    for inst_id in ("iim-bangalore", "iim-bodhgaya"):
        assert by_id[inst_id].get("user_agent"), inst_id


def test_bundled_registry_states_a_reason_for_every_override():
    """A `user_agent` or a `robots_override` departs from the default. Say why."""
    from commoner_probe.academia.registry import load_registry

    for inst in load_registry():
        if inst.get("user_agent"):
            assert inst.get("user_agent_reason"), inst["id"]
        if inst.get("robots_override") is True:
            assert inst.get("robots_override_reason"), inst["id"]


class RecordingSession:
    """Records the kwargs of every request, so a test can read `respect_robots`."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResp("<html>ok</html>")


def test_fetcher_carries_the_robots_override_to_the_institution_s_own_documents(tmp_path):
    """A registry override must reach the PDF, not stop at the listing page.

    A host that refuses `/robots.txt` to every User-Agent reads as disallow-all.
    The listing retry alone leaves ads with `pdf_path: null` and no error.
    """
    from commoner_probe.academia.pdf_text import Fetcher

    session = RecordingSession()
    fetcher = Fetcher(session, tmp_path / "pdfs", tmp_path,
                      "https://www.demo.example.ac.in/careers")

    fetcher.get_html("https://demo.example.ac.in/ad/1")

    assert session.calls[0][1].get("respect_robots") is False


def test_fetcher_leaves_a_third_party_link_under_robots(tmp_path):
    """The override covers the institution's own site. It covers nothing else."""
    from commoner_probe.academia.pdf_text import Fetcher

    session = RecordingSession()
    fetcher = Fetcher(session, tmp_path / "pdfs", tmp_path,
                      "https://www.demo.example.ac.in/careers")

    fetcher.get_html("https://cdn.other.example.com/ad.pdf")

    assert "respect_robots" not in session.calls[0][1]


def test_fetcher_without_an_override_sends_no_robots_kwarg(tmp_path):
    """The default path is unchanged: no override field, no extra kwarg."""
    from commoner_probe.academia.pdf_text import Fetcher

    session = RecordingSession()
    Fetcher(session, tmp_path / "pdfs", tmp_path).get_html("https://demo.example.ac.in/ad/1")

    assert "respect_robots" not in session.calls[0][1]


def test_probe_gives_the_fetcher_the_override_only_when_the_registry_asks(tmp_path):
    """`_fetcher` reads `robots_override` from the institution row."""
    from commoner_probe.academia import AcademicJobsProbe

    probe = AcademicJobsProbe(tmp_path, sleep=0, registry_path=_registry(tmp_path, [_INST_GENERIC]))

    plain = probe._fetcher(True, probe.session, _INST_GENERIC)
    opted_in = probe._fetcher(True, probe.session, dict(_INST_GENERIC, robots_override=True))

    assert plain.robots_override_for is None
    assert opted_in.robots_override_for == _INST_GENERIC["career_page_url_guess"]


# --------------------------------------------------------------------------- #
# Deadline coverage, and what a null closing_date means                        #
# --------------------------------------------------------------------------- #


def test_read_deadline_separates_never_looked_from_found_nothing():
    """The four states must stay four. A boolean collapses the last two.

    academiaindia cannot tell an expired posting from an unread one while a
    null `closing_date` carries both meanings.
    """
    from commoner_probe.academia.pdf_text import read_deadline

    assert read_deadline(None) == (None, "not_examined")
    assert read_deadline("   ") == (None, "not_examined")
    assert read_deadline("Last date: April 30, 2026")[1] == "read"
    assert read_deadline("This is a rolling advertisement; the PI will shortlist")[1] == "rolling"
    assert read_deadline("How to Apply: send a CV by email to hr@example.ac.in")[1] == "not_found"


def test_find_deadline_reads_a_numeric_date_after_the_word_deadline():
    """IITH, verbatim. `deadline` reached a month-name date and never a numeric one."""
    from commoner_probe.academia.pdf_text import find_deadline

    assert find_deadline("The deadline for applications is 5:00 pm, 22/04/2026") == "22/04/2026"


def test_find_deadline_survives_the_dropped_ti_ligature():
    """IITH renders `Applica ons`. A regex anchored on the correct spelling
    returns a plausible, quiet nothing on a document that states a date."""
    from commoner_probe.academia.pdf_text import find_deadline

    assert find_deadline("Applica ons must be submitted on or before September 15, 2026")
    assert find_deadline("Applica on Last Date : 15/09/2026") == "15/09/2026"
    assert find_deadline("Applications must be submitted on or before September 15, 2026")


def test_the_ligature_helper_has_one_home_and_stays_importable_from_dopo():
    """It is a property of PDF extraction, not of BPRD. Two copies would drift."""
    from commoner_probe.dopo_catalogue import term_pattern as from_dopo
    from commoner_probe.textparse import term_pattern as from_textparse

    assert from_dopo is from_textparse
    assert from_textparse("Sanctioned").search("Sanc oned")


def test_iit_hyderabad_reads_the_deadline_out_of_the_advertisement_pdf():
    """It read link text only and never opened the document behind the link."""
    pytest.importorskip("bs4")
    from commoner_probe.academia.parsers import iit_hyderabad

    html = '<a href="/ads/chy_akm_lab.pdf">Advertisement for Research Associate</a>'

    class FakeFetcher:
        def pdf_text(self, pdf_url):
            return ("pdfs/chy_akm_lab.pdf", "The deadline for applications is 5:00 pm, 22/04/2026")

    ads = iit_hyderabad.parse(html, "https://iith.ac.in/careers/", datetime(2026, 6, 1), FakeFetcher())

    assert len(ads) == 1
    assert ads[0]["closing_date"] == "2026-04-22"
    assert ads[0]["closing_date_status"] == "read"
    assert ads[0]["pdf_parsed"] is True


def test_iit_hyderabad_marks_a_rolling_call_rather_than_leaving_it_null():
    """A rolling call has no deadline by design. That is not a parser miss."""
    pytest.importorskip("bs4")
    from commoner_probe.academia.parsers import iit_hyderabad

    html = '<a href="/ads/mae_ra1.pdf">Advertisement for Research Associate-I</a>'

    class FakeFetcher:
        def pdf_text(self, pdf_url):
            return ("pdfs/mae_ra1.pdf",
                    "This is a rolling advertisement; the PI will evaluate and shortlist")

    ads = iit_hyderabad.parse(html, "https://iith.ac.in/careers/", datetime(2026, 6, 1), FakeFetcher())

    assert ads[0]["closing_date"] is None
    assert ads[0]["closing_date_status"] == "rolling"


def test_iit_hyderabad_says_not_examined_when_no_document_was_opened():
    """`--no-download` must not look like a failed read."""
    pytest.importorskip("bs4")
    from commoner_probe.academia.parsers import iit_hyderabad

    html = '<a href="/ads/x.pdf">Advertisement for Research Associate</a>'
    ads = iit_hyderabad.parse(html, "https://iith.ac.in/careers/", datetime(2026, 6, 1), None)

    assert ads[0]["closing_date_status"] == "not_examined"


def test_a_record_written_before_closing_date_status_still_validates():
    """The field is new. Rows on disk carry no value and must not start failing."""
    import json
    from importlib import resources

    schema = json.loads(
        resources.files("commoner_probe.schemas")
        .joinpath("manifest_academic_job.schema.json").read_text(encoding="utf-8")
    )
    assert "closing_date_status" not in schema.get("required", [])
    assert schema["properties"]["closing_date_status"]["enum"] == [
        "read", "rolling", "not_found", "not_examined"
    ]


def test_find_deadline_reads_apply_by_without_swallowing_a_start_date():
    """`should apply by email to <address> by 25-08-2026` (IITH, verbatim).

    Anchored on `apply by`, never on a bare `by`. A bare `by` also introduces a
    start date, and a wrong date sold as a deadline is worse than a null.
    """
    from commoner_probe.academia.pdf_text import find_deadline

    assert find_deadline(
        "candidates should apply by email to a@phy.iith.ac.in by 25-08-2026"
    ) == "25-08-2026"
    assert find_deadline("Eligible candidates should apply by 01-09-2026") == "01-09-2026"
    assert find_deadline("Eligible persons should apply by filling out the form") is None
    assert find_deadline("The position starts by 01-06-2026. Contact hr@x.ac.in") is None


def test_parse_deadline_iso_reads_a_day_first_ordinal_date():
    """`24th August 2026`. strptime has no directive for the ordinal suffix."""
    from commoner_probe.academia.pdf_text import parse_deadline_iso

    assert parse_deadline_iso("24th August 2026") == "2026-08-24"
    assert parse_deadline_iso("8th July 2026") == "2026-07-08"
    assert parse_deadline_iso("August 24, 2026") == "2026-08-24"


def test_a_record_with_a_date_never_reports_that_nobody_looked():
    """Seven parsers extract a closing date and only one names the status.

    A bare default stamps `not_examined` beside a real date, and a consumer
    filtering on `read` then drops known deadlines.
    """
    from commoner_probe.academia.probe import _closing_status

    assert _closing_status({"closing_date": "2026-04-22"}) == "read"
    assert _closing_status({"closing_date": "2026-04-22",
                            "closing_date_status": "not_examined"}) == "read"
    assert _closing_status({"closing_date": None}) == "not_examined"
    assert _closing_status({"closing_date": None,
                            "closing_date_status": "rolling"}) == "rolling"


def test_a_rolling_review_does_not_deny_a_printed_deadline():
    """`rolling basis` describes the review cadence, not the closing date.

    Reading it as rolling asserts that no deadline exists, on a document that
    prints one. That is worse than reading none.
    """
    from commoner_probe.academia.pdf_text import read_deadline

    assert read_deadline(
        "Applications are reviewed on a rolling basis and accepted until 31 December 2026"
    ) == ("31 December 2026", "read")
    assert read_deadline(
        "Reviewed on a rolling basis. Accepted until December 31, 2026"
    )[1] == "read"
    # No date anywhere. `rolling basis` alone must not assert one does not exist.
    assert read_deadline("Applications are reviewed on a rolling basis.")[1] == "not_found"
    # An explicitly deadline-free call still reads as rolling.
    assert read_deadline("This is a rolling advertisement; the PI will shortlist")[1] == "rolling"


def test_iit_hyderabad_publishes_no_date_it_cannot_normalise():
    """The numeric patterns accept a two-digit year and the ISO coercion does not.

    `22/04/26` would otherwise reach the corpus verbatim beside every other
    parser's ISO value, carrying status `read`.
    """
    pytest.importorskip("bs4")
    from commoner_probe.academia.parsers import iit_hyderabad

    html = '<a href="/ads/x.pdf">Advertisement for Research Associate</a>'

    class FakeFetcher:
        def pdf_text(self, pdf_url):
            return ("pdfs/x.pdf", "Application deadline: 22/04/26")

    ads = iit_hyderabad.parse(html, "https://iith.ac.in/careers/", datetime(2026, 6, 1), FakeFetcher())

    assert ads[0]["closing_date"] is None
    assert ads[0]["closing_date_status"] == "not_found"
