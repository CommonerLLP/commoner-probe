"""Tests for the ADR/MyNeta Lok Sabha 2024 candidate-affidavit probe.

Fixtures are trimmed real HTML fragments (myneta.info, LokSabha2024,
constituency_id=579, candidate_id=17/248) verified live 2026-07-08.
"""

from __future__ import annotations

import json

from commoner_probe.myneta import MyNetaProbe

INDEX_HTML = """
<div class='w3-dropdown-content w3-bar-block w3-card-4'>
<a class='w3-bar-item w3-button w3-padding-small' href=index.php?action=show_constituencies&state_id=1 style='text-decoration:none; color:black' > ALL CONSTITUENCIES </a>
<a class='w3-bar-item w3-button w3-padding-small' href=index.php?action=show_candidates&constituency_id=579  title='Date of Election 19-04-2024'>ANDAMAN AND NICOBAR ISLANDS</a>
</div>
"""

CANDIDATES_HTML = """
<a href="candidate.php?candidate_id=17">BISHNU PADA RAY</a>
<a href="candidate.php?candidate_id=248">MANOJ PAUL</a>
"""

# Winner, no criminal cases (candidate_id=17)
CANDIDATE_WINNER_HTML = """
<h2>BISHNU PADA RAY <font color=green>(Winner)</font></h2>
<div><b>Party:</b>BJP </div>
<div><b>Age:</b> 73 </div>
<p>
    <b>Self Profession:</b>Social Service<br>
    <b>Spouse Profession:</b>Pensioner
</p>
<h3>Educational Details</h3>
Category: Graduate <br>
<tr><td> Assets:      </td><td> <b>Rs&nbsp;2,74,39,170</b></td></tr>
<tr><td> Liabilities: </td><td> <b>Rs&nbsp;3,02,788</b></td></tr>
['Label', 'Value'],
['Cases', 0]
"""

# Non-winner, declared criminal cases (candidate_id=248)
CANDIDATE_CRIME_HTML = """
<h2>MANOJ PAUL</h2>
<div><b>Party:</b>Andaman Nicobar Democratic Congress </div>
<div><b>Age:</b> 35 </div>
<p>
    <b>Self Profession:</b>Social Worker<br>
    <b>Spouse Profession:</b>Doctor
</p>
<h3>Educational Details</h3>
Category: Graduate Professional <br>
<tr><td> Assets:      </td><td> <b>Rs&nbsp;24,33,734</b></td></tr>
<tr><td> Liabilities: </td><td> <b>Rs&nbsp;10,00,000</b></td></tr>
['Label', 'Value'],
['Cases', 15]
"""


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self):
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if "candidate.php?candidate_id=17" in url:
            return FakeResponse(CANDIDATE_WINNER_HTML)
        if "candidate.php?candidate_id=248" in url:
            return FakeResponse(CANDIDATE_CRIME_HTML)
        if "show_candidates&constituency_id=579" in url:
            return FakeResponse(CANDIDATES_HTML)
        if url.rstrip("/").endswith("LokSabha2024"):
            return FakeResponse(INDEX_HTML)
        raise AssertionError(f"unrouted url: {url}")


def _probe(tmp_path):
    probe = MyNetaProbe(tmp_path, sleep=0)
    probe.session = FakeSession()
    return probe


def test_discover_constituencies(tmp_path):
    probe = _probe(tmp_path)
    constituencies = probe.discover_constituencies()
    assert constituencies == [
        {"constituency_id": 579, "election_date": "19-04-2024", "name": "ANDAMAN AND NICOBAR ISLANDS"}
    ]


def test_probe_extracts_known_fields(tmp_path):
    probe = _probe(tmp_path)
    records = probe.probe()

    assert len(records) == 2
    winner = next(r for r in records if r["candidate_id"] == 17)
    assert winner["key"] == "MYNETA|LS2024|17"
    assert winner["name"] == "BISHNU PADA RAY"
    assert winner["winner_status"] == "Winner"
    assert winner["party"] == "BJP"
    assert winner["age"] == 73
    assert winner["self_profession"] == "Social Service"
    assert winner["spouse_profession"] == "Pensioner"
    assert winner["education_category"] == "Graduate"
    assert winner["assets_rupees"] == 27439170
    assert winner["liabilities_rupees"] == 302788
    assert winner["criminal_cases_declared"] == 0
    assert winner["constituency_id"] == 579
    assert winner["constituency_name"] == "ANDAMAN AND NICOBAR ISLANDS"

    crime = next(r for r in records if r["candidate_id"] == 248)
    assert crime["winner_status"] is None
    assert crime["criminal_cases_declared"] == 15
    assert crime["assets_rupees"] == 2433734

    manifest = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert manifest == records


def test_dry_run_lists_candidates_without_fetching_details(tmp_path):
    probe = _probe(tmp_path)
    records = probe.probe(dry_run=True)
    assert {r["candidate_id"] for r in records} == {17, 248}
    assert all(r["status"] == "dry_run" for r in records)
    assert not any("candidate.php" in u for u in probe.session.calls)
    assert not (tmp_path / "manifest.jsonl").exists()


def test_dedup_on_rerun(tmp_path):
    _probe(tmp_path).probe()
    assert _probe(tmp_path).probe() == []


def test_max_records_brake(tmp_path):
    probe = _probe(tmp_path)
    records = probe.probe(max_records=1)
    assert len(records) == 1


def test_schema_bundled_and_validates(tmp_path):
    import pytest

    pytest.importorskip("jsonschema")
    from commoner_probe import schemas
    from commoner_probe.validate import validate_corpus

    assert "manifest_myneta" in schemas.list_all()
    record = {
        "key": "MYNETA|LS2024|17",
        "kind": "myneta_candidate",
        "record_type": "myneta_candidate",
        "source": "myneta.info",
        "election": "LokSabha2024",
        "candidate_id": 17,
        "name": "BISHNU PADA RAY",
        "winner_status": "Winner",
        "party": "BJP",
        "age": 73,
        "assets_rupees": 27439170,
        "liabilities_rupees": 302788,
        "criminal_cases_declared": 0,
        "source_url": "https://myneta.info/LokSabha2024/candidate.php?candidate_id=17",
        "probed_at": "2026-07-08T18:00:00Z",
    }
    (tmp_path / "manifest.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    assert validate_corpus(tmp_path, log=lambda _: None)


def test_corpus_streams_myneta(tmp_path):
    from commoner_probe import Corpus

    record = {
        "key": "MYNETA|LS2024|17",
        "kind": "myneta_candidate",
        "record_type": "myneta_candidate",
        "source": "myneta.info",
        "election": "LokSabha2024",
        "candidate_id": 17,
        "name": "BISHNU PADA RAY",
        "source_url": "https://myneta.info/LokSabha2024/candidate.php?candidate_id=17",
        "probed_at": "2026-07-08T18:00:00Z",
    }
    (tmp_path / "manifest.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    records = list(Corpus(tmp_path).manifest_myneta())
    assert len(records) == 1
    assert records[0].name == "BISHNU PADA RAY"
