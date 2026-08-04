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

    def iter_content(self, chunk_size=16384):
        body = self.content or b""
        for i in range(0, len(body), chunk_size):
            yield body[i : i + chunk_size]


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


# The Rajya Sabha CSV's real column names, verified live 2026-07-29: the identity
# column is `mp_index`, the activity counts carry an `ag_` prefix, and attendance
# is `avg_attendance`. 13 of the 27 names differ from the Lok Sabha CSV, which is
# the only one the adapter was originally built and fixtured against.
RS_CSV_TEXT = """mp_index,mp_name,nature_membership,term_start_date,term_end_date,term,pc_name,state,mp_political_party,mp_gender,educational_qualification,educational_qualification_details,mp_age,ag_debates,ag_private_member_bills,ag_questions,avg_attendance,mp_note,ag_national_average_debate,ag_national_average_pmb,ag_national_average_questions,avg_attendance_national_average,ag_state_average_debate,ag_state_average_pmb,ag_state_average_questions,avg_attendance_state_average,mp_house
900411,Ravneet Singh,Elected,20-07-2022,19-07-2028,First Term,NA,Rajasthan,Bharatiya Janata Party,Male,Graduate,B.A.,55,41,1,88,0.82,Sample note.,74.28,0.9,120.5,0.79,60.1,0.5,99.2,0.71,Rajya Sabha
"""


class _RsSession:
    """Serves the Rajya Sabha page + its CSV with the real RS column names."""

    def __init__(self, csv_text: str = RS_CSV_TEXT):
        self.csv_text = csv_text
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if url.endswith("/mptrack/rajya-sabha"):
            return FakeResponse(
                '<a onclick="window.open(\'/mptrack/download?file_path=files/mptrack/'
                'rajya-sabha/Mp-Track/RS MP Track.csv\', \'_blank\').focus();" '
                'id="mptrack-expor-link">Download Data</a>'
            )
        if "/mptrack/download" in url:
            return FakeResponse(content=self.csv_text.encode("utf-8"))
        raise AssertionError(f"unrouted url: {url}")


def _rs_probe(tmp_path, csv_text: str = RS_CSV_TEXT):
    probe = PrsProbe(tmp_path, sleep=0)
    probe.session = _RsSession(csv_text)
    return probe


def test_the_rajya_sabha_surface_writes_records(tmp_path):
    """It wrote nothing at all and exited 0.

    Every RS row lacks `mp_election_index`, so all 828 were dropped for want of a
    key — no file, no directory, no log line, exit 0. Live before the fix: 0
    records. After: 828.
    """
    records = _rs_probe(tmp_path).probe_mptrack(houses=["rs"], loksabhas=[])

    assert len(records) == 1, "the RS row must survive the identity lookup"
    assert (tmp_path / "manifest.jsonl").exists(), "a real run must write its manifest"


def test_the_rs_activity_columns_are_not_silently_dropped(tmp_path):
    """The key was only the visible half. All 13 diverging columns are read by
    `_record`, so an index-only fix would still have emitted null metrics."""
    record = _rs_probe(tmp_path).probe_mptrack(houses=["rs"], loksabhas=[])[0]

    assert record["mp_election_index"] == 900411
    assert record["mp_name"] == "Ravneet Singh"
    assert record["debates"] == 41                      # ag_debates
    assert record["private_member_bills"] == 1          # ag_private_member_bills
    assert record["questions"] == 88                    # ag_questions
    assert record["attendance"] == 0.82                 # avg_attendance
    assert record["national_average_debate"] == 74.28   # ag_national_average_debate
    assert record["attendance_state_average"] == 0.71   # avg_attendance_state_average


def test_a_csv_with_no_identity_column_raises_instead_of_exiting_quietly(tmp_path):
    """The generalizable half.

    A parsed CSV that yields no usable identity on ANY row is a changed source
    contract, not an empty result. Without this the next column rename is silent
    again.
    """
    import pytest

    renamed = RS_CSV_TEXT.replace("mp_index,", "mp_unique_ref,", 1)
    with pytest.raises(ValueError, match="none carried an identity column"):
        _rs_probe(tmp_path, renamed).probe_mptrack(houses=["rs"], loksabhas=[])


def test_a_resume_run_that_writes_nothing_stays_quiet(tmp_path):
    """The guard must not fire on a legitimate no-op: every key already terminal
    is the normal resume path, and it is not a contract change."""
    assert len(_rs_probe(tmp_path).probe_mptrack(houses=["rs"], loksabhas=[])) == 1
    assert _rs_probe(tmp_path).probe_mptrack(houses=["rs"], loksabhas=[]) == []


def test_an_empty_or_header_only_csv_raises_too(tmp_path):
    """The guard's own silent-success hole (Codex, PR #87).

    `if parsed and ...` skipped the check entirely when the CSV parsed to zero
    rows — an empty or header-only body on an HTTP 200 — so the command wrote
    nothing and exited 0, which is the exact failure reported. Zero parsed
    rows cannot be a resume: a resume has rows whose keys are terminal.
    """
    import pytest

    header_only = RS_CSV_TEXT.splitlines()[0] + "\n"
    for body in (header_only, ""):
        with pytest.raises(ValueError, match="no rows at all"):
            _rs_probe(tmp_path / f"c{len(body)}", body).probe_mptrack(
                houses=["rs"], loksabhas=[]
            )
