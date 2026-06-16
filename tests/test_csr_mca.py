from __future__ import annotations

import hashlib
import json
import urllib.parse

from commoner_probe.csr.mca import McaCsrProbe, parse_csrf_token


def test_parse_csrf_token_reads_hidden_input() -> None:
    html = '<input type="hidden" name="csrf_test_name" value="token-123">'
    assert parse_csrf_token(html) == "token-123"


def test_parse_csrf_token_reads_live_cdm_field_name() -> None:
    html = '<input type="hidden" name="csrf_token" id="csrf_token" value="live-token">'
    assert parse_csrf_token(html) == "live-token"


def test_dry_run_does_not_open_network_or_write_manifest(tmp_path, monkeypatch) -> None:
    probe = McaCsrProbe(tmp_path, sleep=0)

    def fail_init_session():
        raise AssertionError("dry-run should not initialize a network session")

    monkeypatch.setattr(probe, "init_session", fail_init_session)

    records = probe.probe_years(["2022-23"], dry_run=True)

    assert records[0]["status"] == "dry_run"
    assert not (tmp_path / "manifest.jsonl").exists()


def test_probe_years_writes_csv_and_manifest_record(tmp_path, monkeypatch) -> None:
    body = (
        b'"Company Name","Financial Year",PSU/Non-PSU,"CSR State",'
        b'"CSR Development Sector","CSR Sub Development Sector",'
        b'"Project Amount Spent (In INR Cr.)"\n'
        b'"Example Ltd","FY 2022-23",Non-PSU,"Gujarat","Education",'
        b'"Education",1.25\n'
    )
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return body

    class FakeOpener:
        def open(self, req, timeout):
            captured["url"] = req.full_url
            captured["data"] = urllib.parse.parse_qs(req.data.decode("utf-8"))
            return FakeResponse()

    probe = McaCsrProbe(tmp_path, sleep=0)
    monkeypatch.setattr(probe, "init_session", lambda: (FakeOpener(), "csrf-token"))

    records = probe.probe_years(["2022-23"], dry_run=False)

    csv_path = tmp_path / "mca_csr_company_spend_2022-23.csv"
    assert csv_path.read_bytes() == body
    assert captured["url"] == "https://www.mcacdm.nic.in/cdm/export.php"
    assert captured["data"] == {
        "csrf_token": ["csrf-token"],
        "financialyear[]": ["FY 2022-23"],
        "psunonpsu[]": ["all"],
        "csrstate[]": ["all"],
        "csrdevelopmentsector[]": ["all"],
        "captcha_input": ["COMMON"],
        "captcha_hidden": ["COMMON"],
        "export": ["true"],
    }

    manifest_lines = (tmp_path / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(manifest_lines) == 1
    manifest_record = json.loads(manifest_lines[0])
    assert manifest_record == records[0]
    assert manifest_record["kind"] == "mca_csr_company_spend"
    assert manifest_record["key"] == "MCA_CSR|FY 2022-23"
    assert manifest_record["year"] == "2022-23"
    assert manifest_record["financial_year"] == "FY 2022-23"
    assert manifest_record["status"] == "downloaded"
    assert manifest_record["sha256"] == hashlib.sha256(body).hexdigest()


def test_mca_csr_manifest_schema_is_bundled_and_validates_record(tmp_path) -> None:
    from commoner_probe import schemas
    from commoner_probe.validate import validate_corpus

    assert "manifest_mca_csr" in schemas.list_all()

    record = {
        "key": "MCA_CSR|FY 2022-23",
        "kind": "mca_csr_company_spend",
        "record_type": "mca_csr_company_spend",
        "year": "2022-23",
        "financial_year": "FY 2022-23",
        "filename": "mca_csr_company_spend_2022-23.csv",
        "dest": str(tmp_path / "mca_csr_company_spend_2022-23.csv"),
        "source_page": "https://www.mcacdm.nic.in/csr-data",
        "url": "https://www.mcacdm.nic.in/cdm/export.php",
        "status": "downloaded",
        "sha256": "a" * 64,
        "timestamp_utc": "2026-06-16T16:19:02+00:00",
        "probed_at": "2026-06-16T16:19:02+00:00",
    }
    (tmp_path / "manifest.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

    assert validate_corpus(tmp_path, log=lambda _: None)


def test_mca_csr_cli_dry_run_emits_records_without_manifest(tmp_path, capsys) -> None:
    from commoner_probe.cli import build_parser

    parser = build_parser()
    args = parser.parse_args([
        "mca-csr",
        "--out",
        str(tmp_path),
        "--years",
        "2022-23",
        "--dry-run",
    ])

    args.func(args)

    captured = capsys.readouterr()
    record = json.loads(captured.out.splitlines()[0])
    assert record["kind"] == "mca_csr_company_spend"
    assert record["financial_year"] == "FY 2022-23"
    assert record["status"] == "dry_run"
    assert not (tmp_path / "manifest.jsonl").exists()


def test_corpus_streams_mca_csr_manifest_records(tmp_path) -> None:
    from commoner_probe import Corpus

    record = {
        "key": "MCA_CSR|FY 2022-23",
        "kind": "mca_csr_company_spend",
        "record_type": "mca_csr_company_spend",
        "year": "2022-23",
        "financial_year": "FY 2022-23",
        "filename": "mca_csr_company_spend_2022-23.csv",
        "dest": str(tmp_path / "mca_csr_company_spend_2022-23.csv"),
        "source_page": "https://www.mcacdm.nic.in/csr-data",
        "url": "https://www.mcacdm.nic.in/cdm/export.php",
        "status": "downloaded",
        "sha256": "a" * 64,
        "timestamp_utc": "2026-06-16T16:19:02+00:00",
        "probed_at": "2026-06-16T16:19:02+00:00",
    }
    (tmp_path / "manifest.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

    records = list(Corpus(tmp_path).manifest_mca_csr())

    assert len(records) == 1
    assert records[0].key == "MCA_CSR|FY 2022-23"
    assert records[0].financial_year == "FY 2022-23"
    assert records[0].sha256 == "a" * 64
