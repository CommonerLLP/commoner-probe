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


def test_iim_recruit_emits_rolling_stub_when_no_pdfs():
    pytest.importorskip("bs4")
    from commoner_probe.academia.parsers import iim_recruit

    html = '<a href="/about">About</a>'
    ads = iim_recruit.parse(html, "https://iimdemo.example.ac.in/faculty", datetime(2026, 6, 1), None)
    assert len(ads) == 1
    assert ads[0]["rolling_stub"] is True
    assert ads[0]["pdf_parsed"] is False


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
