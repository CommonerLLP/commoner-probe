from __future__ import annotations

import hashlib
import json

from commoner_probe.prs import PrsProbe, parse_mptrack_csv, parse_mptrack_download

PAGE_HTML = """
<a onclick="window.open('/mptrack/download?file_path=files/mptrack/17-lok-sabha/Mp-Track/17 LS MP Track.csv', '_blank').focus();" id="mptrack-expor-link">Download Data</a>
"""

CSV_TEXT = """mp_election_index,mp_name,nature_membership,term_start_date,term_end_date,term,pc_name,state,mp_political_party,mp_gender,educational_qualification,educational_qualification_details,mp_age,debates,private_member_bills,questions,attendance,mp_note,national_average_debate,national_average_pmb,national_average_questions,attendance_national_average,state_average_debate,state_average_pmb,state_average_questions,attendance_state_average,mp_house
170141,Jugal Kishore,Elected,23-05-2019,05-06-2024,Second Term,Jammu,Jammu and Kashmir,Bharatiya Janata Party,Male,Matric,Matriculation,61,63,2,310,0.875912409,Data corresponds to the period from 01-06-2019 to 10-02-2024.,46.72828028,1.503146169,209.6655443,0.788685748,49.4,0.6,102.6,0.717518248,Lok Sabha
170164,Suresh Channabasappa Angadi,Elected,23-05-2019,23-09-2020,Fourth Term,Belgaum,Karnataka,Bharatiya Janata Party,Male,Professional Graduate,\"B. Com, LL.B.\",68,N/A,N/A,N/A,N/A,This MP was a minister.,N/A,N/A,N/A,N/A,N/A,N/A,N/A,N/A,Lok Sabha
"""


class FakeResponse:
    def __init__(self, text: str = "", content: bytes | None = None):
        self.text = text
        self.content = content if content is not None else text.encode("utf-8")

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self):
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if url.endswith("/mptrack/17-lok-sabha"):
            return FakeResponse(PAGE_HTML)
        if "/mptrack/download" in url:
            return FakeResponse(content=CSV_TEXT.encode("utf-8"))
        raise AssertionError(f"unrouted url: {url}")


def _probe(tmp_path):
    probe = PrsProbe(tmp_path, sleep=0)
    probe.session = FakeSession()
    return probe


def test_parse_mptrack_download_encodes_spaces():
    raw_path, encoded_path = parse_mptrack_download(PAGE_HTML)
    assert raw_path.endswith("17 LS MP Track.csv")
    assert encoded_path.endswith("17%20LS%20MP%20Track.csv")


def test_crawl_delay_paces_page_then_csv(tmp_path, monkeypatch):
    events: list[str] = []
    probe = PrsProbe(tmp_path, sleep=7)
    session = FakeSession()
    original_get = session.get

    def logging_get(url, **kwargs):
        events.append(f"get:{url}")
        return original_get(url, **kwargs)

    session.get = logging_get
    probe.session = session
    monkeypatch.setattr("commoner_probe.prs.time.sleep", lambda s: events.append(f"sleep:{s}"))

    probe.probe_mptrack(houses=["ls"], loksabhas=[17], download=True)

    page = next(i for i, e in enumerate(events) if "/mptrack/17-lok-sabha" in e)
    csv_fetch = next(i for i, e in enumerate(events) if "/mptrack/download" in e)
    # the crawl delay must sit between the page request and the CSV request,
    # independent of the optional requests stack's session limiter
    assert "sleep:7" in events[page + 1 : csv_fetch]


def test_parse_mptrack_download_does_not_double_encode():
    html = "window.open('/mptrack/download?file_path=sites%2Fdefault%2Ffiles%2F17%20LS%20MP%20Track.csv')"
    _, encoded_path = parse_mptrack_download(html)
    assert "%252F" not in encoded_path
    assert encoded_path.endswith("file_path=sites/default/files/17%20LS%20MP%20Track.csv")


def test_parse_mptrack_csv():
    rows = parse_mptrack_csv(CSV_TEXT)
    assert rows[0]["mp_name"] == "Jugal Kishore"
    assert rows[1]["debates"] == "N/A"


def test_probe_mptrack_writes_metadata_records(tmp_path):
    records = _probe(tmp_path).probe_mptrack(houses=["ls"], loksabhas=[17])
    assert len(records) == 2
    first = records[0]
    assert first["key"] == "PRS_MP_TRACK|ls|17|170141"
    assert first["kind"] == "prs_mp_track"
    assert first["source"] == "prsindia.org"
    assert first["mp_name"] == "Jugal Kishore"
    assert first["questions"] == 310
    assert first["attendance"] == 0.875912409
    assert first["status"] == "metadata_only"
    assert first["csv_path"] is None
    assert first["csv_sha256"] is None
    assert records[1]["debates"] is None
    manifest = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert manifest == records


def test_metadata_only_rerun_skips_until_download_rerun(tmp_path):
    _probe(tmp_path).probe_mptrack(houses=["ls"], loksabhas=[17])
    assert _probe(tmp_path).probe_mptrack(houses=["ls"], loksabhas=[17]) == []
    downloaded = _probe(tmp_path).probe_mptrack(houses=["ls"], loksabhas=[17], download=True)
    assert len(downloaded) == 2
    assert all(r["status"] == "downloaded" for r in downloaded)
    assert all(r["csv_sha256"] == hashlib.sha256(CSV_TEXT.encode("utf-8")).hexdigest() for r in downloaded)
    assert (tmp_path / downloaded[0]["csv_path"]).exists()
    assert _probe(tmp_path).probe_mptrack(houses=["ls"], loksabhas=[17], download=True) == []


def test_dry_run_discovers_csv_without_writing_manifest(tmp_path):
    records = _probe(tmp_path).probe_mptrack(houses=["ls"], loksabhas=[17], dry_run=True)
    assert records == [{
        "key": "PRS_MP_TRACK|ls|17|_csv",
        "house_code": "ls",
        "loksabha": 17,
        "source_page_url": "https://prsindia.org/mptrack/17-lok-sabha",
        "csv_url": "https://prsindia.org/mptrack/download?file_path=files/mptrack/17-lok-sabha/Mp-Track/17%20LS%20MP%20Track.csv",
        "status": "dry_run",
    }]
    assert not (tmp_path / "manifest.jsonl").exists()


def test_schema_bundled_and_validates(tmp_path):
    import pytest

    pytest.importorskip("jsonschema")
    from commoner_probe import schemas
    from commoner_probe.validate import validate_corpus

    assert "manifest_prs_mp_track" in schemas.list_all()
    records = _probe(tmp_path).probe_mptrack(houses=["ls"], loksabhas=[17], max_records=1)
    assert records
    assert validate_corpus(tmp_path, log=lambda _: None)


def test_corpus_streams_prs_mp_track(tmp_path):
    from commoner_probe import Corpus

    _probe(tmp_path).probe_mptrack(houses=["ls"], loksabhas=[17], max_records=1)
    records = list(Corpus(tmp_path).manifest_prs_mp_track())
    assert len(records) == 1
    assert records[0].mp_name == "Jugal Kishore"
