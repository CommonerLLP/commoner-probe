from __future__ import annotations

import hashlib
import json


class FakeHeaders:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def get(self, name: str, default=None):
        return self._values.get(name, default)


class FakeResponse:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None) -> None:
        self._body = body
        self.headers = FakeHeaders(headers or {})

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._body


class FakeOpener:
    def __init__(self, responses: dict[str, FakeResponse]) -> None:
        self.responses = responses
        self.opened: list[str] = []

    def open(self, req, timeout):
        self.opened.append(req.full_url)
        return self.responses[req.full_url]


def test_mines_dmft_dry_run_emits_ministry_and_odisha_records_without_manifest(tmp_path):
    from commoner_probe.dmft.mines import MinesDmftProbe

    probe = MinesDmftProbe(tmp_path, sleep=0)

    records = probe.probe_sources(["mines-gov-in", "odisha"], dry_run=True)

    keys = {record["key"] for record in records}
    assert "MINES_DMFT|mines-gov-in|DMF_Collection.csv" in keys
    assert "MINES_DMFT|odisha-dmf|state_summary_data.json" in keys
    assert "MINES_DMFT|odisha-dmf|report/fund_collection_report.html" in keys
    assert all(record["status"] == "dry_run" for record in records)
    assert all(record["kind"] == "mines_dmft_source_file" for record in records)
    assert not (tmp_path / "manifest.jsonl").exists()


def test_mines_dmft_downloads_files_and_writes_manifest(tmp_path, monkeypatch):
    from commoner_probe.dmft.mines import MinesDmftProbe

    csv_body = b"Sr. No.,State,Total\\n1,Odisha,35945.386\\n"
    json_body = b'{"districts": []}'
    html_body = b"<!doctype html><title>Fund Collection</title>"
    responses = {
        "https://mines.gov.in/webportal/assets/img/DMF_Collection.csv": FakeResponse(
            csv_body,
            {"Last-Modified": "Thu, 11 Jun 2026 09:15:11 GMT", "Content-Type": "text/csv"},
        ),
        "https://dmf.odisha.gov.in/assets/cron_files/state_summary_data.json": FakeResponse(
            json_body,
            {"Last-Modified": "Wed, 27 May 2026 00:00:00 GMT", "Content-Type": "application/json"},
        ),
        "https://dmf.odisha.gov.in/report/fund_collection_report": FakeResponse(
            html_body,
            {"Content-Type": "text/html; charset=UTF-8"},
        ),
    }
    opener = FakeOpener(responses)
    probe = MinesDmftProbe(
        tmp_path,
        sleep=0,
        ministry_endpoints=["DMF_Collection.csv"],
        odisha_endpoints=["state_summary_data.json", "report/fund_collection_report.html"],
    )
    monkeypatch.setattr(probe, "_build_opener", lambda: opener)

    records = probe.probe_sources(["mines-gov-in", "odisha"], dry_run=False)

    assert (tmp_path / "mines-gov-in" / "DMF_Collection.csv").read_bytes() == csv_body
    assert (tmp_path / "odisha-dmf" / "state_summary_data.json").read_bytes() == json_body
    assert (tmp_path / "odisha-dmf" / "report" / "fund_collection_report.html").read_bytes() == html_body
    manifest_records = [
        json.loads(line)
        for line in (tmp_path / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert manifest_records == records
    first = manifest_records[0]
    assert first["status"] == "downloaded"
    assert first["source_last_modified"] == "2026-06-11T09:15:11Z"
    assert first["source_last_modified_raw"] == "Thu, 11 Jun 2026 09:15:11 GMT"
    assert first["period_kind"] == "cumulative_snapshot"
    assert first["data_period"] is None
    assert first["sha256"] == hashlib.sha256(csv_body).hexdigest()


def test_mines_dmft_cli_dry_run_emits_records_without_manifest(tmp_path, capsys):
    from commoner_probe.cli import build_parser

    parser = build_parser()
    args = parser.parse_args([
        "mines-dmft",
        "--out",
        str(tmp_path),
        "--sources",
        "mines-gov-in",
        "--dry-run",
    ])

    args.func(args)

    lines = capsys.readouterr().out.splitlines()
    assert lines
    record = json.loads(lines[0])
    assert record["kind"] == "mines_dmft_source_file"
    assert record["source_name"] == "mines-gov-in"
    assert record["status"] == "dry_run"
    assert not (tmp_path / "manifest.jsonl").exists()


def test_corpus_streams_mines_dmft_manifest_records(tmp_path):
    from commoner_probe import Corpus

    record = {
        "key": "MINES_DMFT|mines-gov-in|DMF_Collection.csv",
        "kind": "mines_dmft_source_file",
        "record_type": "mines_dmft_source_file",
        "source_family": "mines-dmft",
        "source_name": "mines-gov-in",
        "publisher": "Ministry of Mines",
        "endpoint_kind": "static_csv",
        "filename": "DMF_Collection.csv",
        "dest": str(tmp_path / "mines-gov-in" / "DMF_Collection.csv"),
        "url": "https://mines.gov.in/webportal/assets/img/DMF_Collection.csv",
        "status": "downloaded",
        "media_type": "text/csv",
        "period_kind": "cumulative_snapshot",
        "data_period": None,
        "source_last_modified": "2026-06-11T09:15:11Z",
        "source_last_modified_raw": "Thu, 11 Jun 2026 09:15:11 GMT",
        "fetched_at": "2026-06-16T17:06:58Z",
        "probed_at": "2026-06-16T17:06:58Z",
        "sha256": "a" * 64,
    }
    (tmp_path / "manifest.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

    records = list(Corpus(tmp_path).manifest_mines_dmft())

    assert len(records) == 1
    assert records[0].key == "MINES_DMFT|mines-gov-in|DMF_Collection.csv"
    assert records[0].period_kind == "cumulative_snapshot"


def test_mines_dmft_manifest_schema_is_bundled_and_validates_record(tmp_path):
    import pytest

    jsonschema = pytest.importorskip("jsonschema")  # noqa: F841
    from commoner_probe import schemas
    from commoner_probe.validate import validate_corpus

    assert "manifest_mines_dmft" in schemas.list_all()

    record = {
        "key": "MINES_DMFT|mines-gov-in|DMF_Collection.csv",
        "kind": "mines_dmft_source_file",
        "record_type": "mines_dmft_source_file",
        "source_family": "mines-dmft",
        "source_name": "mines-gov-in",
        "publisher": "Ministry of Mines",
        "endpoint_kind": "static_csv",
        "filename": "DMF_Collection.csv",
        "dest": str(tmp_path / "mines-gov-in" / "DMF_Collection.csv"),
        "url": "https://mines.gov.in/webportal/assets/img/DMF_Collection.csv",
        "status": "downloaded",
        "media_type": "text/csv",
        "period_kind": "cumulative_snapshot",
        "data_period": None,
        "source_last_modified": "2026-06-11T09:15:11Z",
        "source_last_modified_raw": "Thu, 11 Jun 2026 09:15:11 GMT",
        "fetched_at": "2026-06-16T17:06:58Z",
        "probed_at": "2026-06-16T17:06:58Z",
        "sha256": "a" * 64,
    }
    (tmp_path / "manifest.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

    assert validate_corpus(tmp_path, log=lambda _: None)
