"""Datamart file API adapter. No test touches the network.

`tests/fixtures/datamart/path_contents_naps_root.json` is the real response of
`/api/path_contents` for the NAPS root, recorded 2026-11-16.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from commoner_probe import http_client as hc
from commoner_probe.datamart_file_api import (
    DatamartEntry,
    DatamartError,
    fetch_file,
    list_path,
)

FIXTURE = Path(__file__).parent / "fixtures" / "datamart" / "path_contents_naps_root.json"
NAPS = "air/monitor/national-air-pollution-surveillance-naps-program"
ZIP_PATH = f"{NAPS}/Data-Donnees/2024/IntegratedData-DonneesPonctuelles/2024_VOC-COV.zip"


class FakeResponse:
    def __init__(self, body: bytes, *, status: int = 200, resets: int = 0):
        self._body = body
        self.status_code = status
        self.headers: dict = {}
        self.resets = resets
        self.text = body.decode("utf-8", errors="replace")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=16384):
        if self.resets:
            self.resets -= 1
            yield self._body[:3]
            raise ConnectionResetError("Connection reset by peer")
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i : i + chunk_size]


class StubSession:
    """Serves `body`; the first `resets` reads die mid-body."""

    def __init__(self, body: bytes, *, resets: int = 0):
        self.body = body
        self.resets = resets
        self.urls: list[str] = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        resp = FakeResponse(self.body, resets=min(self.resets, 1))
        if self.resets:
            self.resets -= 1
        return resp


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(hc.time, "sleep", lambda s: None)


def test_list_path_parses_path_contents():
    session = StubSession(FIXTURE.read_bytes())
    entries = list_path(session, "/" + NAPS)
    assert len(entries) == 5
    by_name = {e.name: e for e in entries}
    assert isinstance(by_name["Data-Donnees"], DatamartEntry)
    assert by_name["Data-Donnees"].is_directory is True
    assert by_name["Data-Donnees"].path == f"{NAPS}/Data-Donnees"
    xml = by_name["1b36a356-defd-4813-acea-47bc3abd859b.xml"]
    assert xml.is_directory is False
    assert xml.content_length == "104 KiB"
    assert xml.last_modified == "2023-11-10"
    assert session.urls == [
        "https://data-donnees.az.ec.gc.ca/api/path_contents?path=%2F" + NAPS.replace("/", "%2F")
    ]


def test_path_not_found_is_an_error_not_a_listing():
    with pytest.raises(DatamartError, match="Path not found"):
        list_path(StubSession(b'"Path not found."'), "no/such/dir")


def test_fetch_file_records_sha256_and_status(tmp_path):
    body = b"PK\x03\x04 pretend zip bytes"
    session = StubSession(body)
    record = fetch_file(session, ZIP_PATH, tmp_path, max_bytes=1000)

    assert record["status"] == "downloaded"
    assert record["sha256"] == hashlib.sha256(body).hexdigest()
    assert record["bytes"] == len(body)
    assert record["path"] == ZIP_PATH
    assert record["source_url"].startswith("https://data-donnees.az.ec.gc.ca/api/file?path=%2F")
    assert record["source_url"] == session.urls[0]
    assert record["fetched_at"]
    written = Path(record["dest"])
    assert written.read_bytes() == body
    assert written.is_relative_to(tmp_path)
    lines = (tmp_path / "manifest.jsonl").read_text().splitlines()
    assert json.loads(lines[0]) == record


def test_fetch_file_path_not_found_writes_nothing(tmp_path):
    with pytest.raises(DatamartError, match="Path not found"):
        fetch_file(StubSession(b'"Path not found."'), "no/such.zip", tmp_path, max_bytes=1000)
    assert not any(tmp_path.rglob("*.zip"))
    assert not (tmp_path / "manifest.jsonl").exists()


def test_reset_mid_body_is_retried(tmp_path):
    body = b"x" * 50
    session = StubSession(body, resets=1)
    record = fetch_file(session, ZIP_PATH, tmp_path, max_bytes=1000)
    assert len(session.urls) == 2
    assert record["sha256"] == hashlib.sha256(body).hexdigest()


def test_oversize_file_names_the_cap(tmp_path):
    with pytest.raises(hc.ResponseTooLarge, match="10 byte"):
        fetch_file(StubSession(b"y" * 100), ZIP_PATH, tmp_path, max_bytes=10)
    assert not any(p.is_file() for p in tmp_path.rglob("*"))


def test_datamart_cli_fetches_and_prints_record(tmp_path, capsys, monkeypatch):
    from commoner_probe import cli

    body = b"abc"
    monkeypatch.setattr(cli, "make_session", lambda: StubSession(body))
    args = cli.build_parser().parse_args(["datamart", "--path", ZIP_PATH, "--out", str(tmp_path)])
    args.func(args)
    record = json.loads(capsys.readouterr().out)
    assert record["sha256"] == hashlib.sha256(body).hexdigest()
    assert (tmp_path / "manifest.jsonl").exists()


def test_rerun_keeps_earlier_file_and_hash_valid(tmp_path):
    fetch_file(StubSession(b"first body"), ZIP_PATH, tmp_path, max_bytes=1000)
    fetch_file(StubSession(b"second, changed body"), ZIP_PATH, tmp_path, max_bytes=1000)
    recs = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert len(recs) == 2 and recs[0]["dest"] != recs[1]["dest"]
    for rec in recs:
        assert hashlib.sha256(Path(rec["dest"]).read_bytes()).hexdigest() == rec["sha256"]
