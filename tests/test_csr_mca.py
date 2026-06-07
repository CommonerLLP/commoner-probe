from __future__ import annotations

import hashlib
import json

from commoner_probe.csr.mca import McaCsrProbe, parse_csrf_token


def test_parse_csrf_token_reads_hidden_input() -> None:
    html = '<input type="hidden" name="csrf_test_name" value="token-123">'
    assert parse_csrf_token(html) == "token-123"


def test_dry_run_does_not_open_network_or_write_manifest(tmp_path, monkeypatch) -> None:
    probe = McaCsrProbe(tmp_path, sleep=0)

    def fail_init_session():
        raise AssertionError("dry-run should not initialize a network session")

    monkeypatch.setattr(probe, "init_session", fail_init_session)

    records = probe.probe_years(["2022-23"], dry_run=True)

    assert records[0]["status"] == "dry_run"
    assert not (tmp_path / "manifest.jsonl").exists()


def test_probe_years_writes_csv_and_manifest_record(tmp_path, monkeypatch) -> None:
    body = b"company,expenditure_cr\nExample Ltd,1.25\n"

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return body

    class FakeOpener:
        def open(self, req, timeout):
            return FakeResponse()

    probe = McaCsrProbe(tmp_path, sleep=0)
    monkeypatch.setattr(probe, "init_session", lambda: (FakeOpener(), "csrf-token"))

    records = probe.probe_years(["2022-23"], dry_run=False)

    csv_path = tmp_path / "mca_csr_company_spend_2022-23.csv"
    assert csv_path.read_bytes() == body

    manifest_lines = (tmp_path / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(manifest_lines) == 1
    manifest_record = json.loads(manifest_lines[0])
    assert manifest_record == records[0]
    assert manifest_record["year"] == "2022-23"
    assert manifest_record["status"] == "downloaded"
    assert manifest_record["sha256"] == hashlib.sha256(body).hexdigest()
