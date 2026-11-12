"""CKAN discovery on the OpenCity fixtures (`tests/fixtures/opencity/README.md`).

No test touches the network.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import fields
from pathlib import Path

import pytest

from commoner_probe import records as R
from commoner_probe import schemas as sc
from commoner_probe.ckan_resource_api import (
    CKAN_CRAWL_DELAY_SEC,
    CkanClient,
    CkanError,
    CkanResource,
    CkanResourceProbe,
    filter_formats,
    media_type_for,
    parse_package,
    resource_filename,
)
from commoner_probe.corpus import Corpus
from commoner_probe.validate import validate_corpus

FIXTURES = Path(__file__).parent / "fixtures" / "opencity"
PORTAL = "https://data.opencity.in"
GBA = "gba-corporation-budgets-2026-27"
CHENNAI = "great-chennai-corporation-finances"
TABLES_ID = "598ade93-0ea8-4944-9d7e-96060a49c3e1"
SEVENT4_FORMATS = {"CSV", "XLSX", "XLS", "JSON", "GEOJSON"}


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _gba() -> list[CkanResource]:
    return parse_package(_load(f"package_show_{GBA}.json"), portal=PORTAL, dataset=GBA)


def _tables() -> CkanResource:
    return next(r for r in _gba() if r.resource_id == TABLES_ID)


class FakeResponse:
    def __init__(self, *, text="", content=b"", status=200):
        self.text = text
        self.content = content
        self.status_code = status
        self.headers = {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class RecordingSession:
    """Answers every GET with `json_body`, and records the URL and robots flag."""

    def __init__(self, json_body=None, status=200):
        self.body = json_body
        self.status = status
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, **kwargs):
        self.calls.append((url, {k: kwargs[k] for k in ("respect_robots",) if k in kwargs}))
        return FakeResponse(text=json.dumps(self.body) if self.body is not None else "", status=self.status)


class StatusSession(RecordingSession):
    def __init__(self, status):
        super().__init__({"success": False, "error": {"__type": "Not Found Error"}}, status)


class HtmlSession(RecordingSession):
    def get(self, url, **kwargs):
        super().get(url, **kwargs)
        return FakeResponse(text="<html>maintenance</html>")


def test_parse_package_gba_fixture_lists_19_resources():
    resources = _gba()
    assert len(resources) == 19
    assert Counter(r.format for r in resources) == {"PDF": 14, "XLSX": 5}
    assert resources[0].organization == "Greater Bengaluru Authority (GBA)"
    assert resources[0].license == "Other (Public Domain)"
    assert resources[0].dataset_title == "GBA Corporation Budgets 2026-27"
    assert resources[0].dataset_url == f"{PORTAL}/dataset/{GBA}"
    tables = _tables()
    assert tables.resource_name == "Bengaluru Central City Corporation Budget Tables"
    assert tables.url.endswith("/download/bccc_full_real_arjun.xlsx")
    assert tables.resource_last_modified == "2026-06-17T04:12:04.107941"


def test_filter_formats_keeps_sevent4_finance_set():
    kept = filter_formats(_gba(), SEVENT4_FORMATS)
    assert len(kept) == 5 and {r.format for r in kept} == {"XLSX"}
    assert len(filter_formats(_gba(), None)) == 19
    assert len(filter_formats(_gba(), {"pdf"})) == 14


def test_parse_package_chennai_fixture_lists_14_csv():
    resources = parse_package(_load(f"package_show_{CHENNAI}.json"), portal=PORTAL, dataset=CHENNAI)
    assert len(resources) == 14 and {r.format for r in resources} == {"CSV"}
    assert resources[0].organization == "Greater Chennai Corporation (GCC)"
    assert resources[0].resource_name == "GCC budget summary statement"
    assert resources[0].resource_last_modified == "2025-11-25T14:06:49.020691"


def test_parse_package_rejects_unsuccessful_payload():
    with pytest.raises(CkanError):
        parse_package({"success": False, "error": {"__type": "Not Found Error"}}, portal=PORTAL, dataset="x")
    with pytest.raises(CkanError):
        parse_package({"success": True}, portal=PORTAL, dataset="x")


def test_parse_package_skips_resources_without_url_and_falls_back_to_created():
    payload = {
        "success": True,
        "result": {
            "title": "T",
            "resources": [
                {"id": "a", "name": "no url", "format": "csv", "url": ""},
                {"id": "b", "name": "ok", "format": "csv", "url": "https://x/b.csv", "created": "2025-01-01T00:00:00"},
                {"id": "c", "url": "https://x/c"},
            ],
        },
    }
    resources = parse_package(payload, portal=PORTAL, dataset="d")
    assert [r.resource_id for r in resources] == ["b", "c"]
    assert resources[0].resource_last_modified == "2025-01-01T00:00:00"
    assert resources[0].format == "CSV"
    assert resources[0].organization is None and resources[0].license is None
    assert resources[1].format == "" and resources[1].resource_last_modified is None


def test_resource_filename_and_media_type():
    tables = _tables()
    assert resource_filename(tables) == "Bengaluru_Central_City_Corporation_Budget_Tables.xlsx"
    assert media_type_for(tables) == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    def make(**kw):
        base = dict(
            portal=PORTAL, dataset="d", dataset_title="D", organization=None, license=None,
            dataset_url=f"{PORTAL}/dataset/d", resource_id="abc", resource_name=None,
            format="", url="https://x/y/download/abc", resource_last_modified=None,
        )
        return CkanResource(**{**base, **kw})

    bare = make()
    assert resource_filename(bare) == "abc"
    assert media_type_for(bare) == "application/octet-stream"
    by_format = make(format="CSV")
    assert resource_filename(by_format) == "abc.csv"
    assert media_type_for(by_format) == "text/csv"
    assert resource_filename(make(resource_name="Speech / 2026", url="https://x/s.PDF")) == "Speech_2026.pdf"
    assert media_type_for(make(url="https://x/s.pdf")) == "application/pdf"
    # A name that already carries the extension keeps it once.
    assert resource_filename(make(resource_name="book.pdf", url="https://x/z.pdf")) == "book.pdf"
    # A traversal attempt in the name stays inside one path segment.
    assert "/" not in resource_filename(make(resource_name="../../etc/passwd"))


def test_client_calls_action_api_without_robots_check():
    session = RecordingSession(_load(f"package_show_{GBA}.json"))
    payload = CkanClient(PORTAL, session=session).package_show(GBA)
    assert payload["success"] is True
    assert session.calls == [(f"{PORTAL}/api/3/action/package_show?id={GBA}", {"respect_robots": False})]


def test_client_package_search_returns_results_list():
    session = RecordingSession({"success": True, "result": {"count": 1, "results": [{"name": "a"}]}})
    results = CkanClient(PORTAL + "/", session=session).package_search("budget", rows=5)
    assert results == [{"name": "a"}]
    assert session.calls == [
        (f"{PORTAL}/api/3/action/package_search?q=budget&rows=5", {"respect_robots": False})
    ]


def test_client_reports_dataset_not_found():
    with pytest.raises(CkanError, match="not found"):
        CkanClient(PORTAL, session=StatusSession(404)).package_show("gone")


def test_client_rejects_server_error_non_json_and_success_false():
    with pytest.raises(CkanError, match="HTTP 503"):
        CkanClient(PORTAL, session=StatusSession(503)).package_show("x")
    with pytest.raises(CkanError, match="not JSON"):
        CkanClient(PORTAL, session=HtmlSession()).package_show("x")
    with pytest.raises(CkanError, match="success=false"):
        CkanClient(PORTAL, session=RecordingSession({"success": False})).package_show("x")


def test_default_crawl_delay_is_ten_seconds():
    assert CKAN_CRAWL_DELAY_SEC == 10.0


def test_discover_record_validates_against_schema(tmp_path):
    session = RecordingSession(_load(f"package_show_{GBA}.json"))
    probe = CkanResourceProbe(tmp_path, PORTAL, session=session, formats=SEVENT4_FORMATS)
    records = probe.discover(GBA)
    assert len(records) == 5
    assert all(r["status"] == "pending" for r in records)
    assert records[0]["key"] == f"CKAN|data.opencity.in|{GBA}|{TABLES_ID}"
    assert records[0]["dest"] == str(
        tmp_path / "data.opencity.in" / GBA / "Bengaluru_Central_City_Corporation_Budget_Tables.xlsx"
    )
    assert records[0]["organization"] == "Greater Bengaluru Authority (GBA)"
    assert records[0]["portal"] == PORTAL
    (tmp_path / "manifest.jsonl").write_text(
        "".join(json.dumps({**r, "status": "dry_run"}) + "\n" for r in records)
    )
    assert validate_corpus(tmp_path, log=lambda _: None)


def test_discover_without_filter_keeps_all_and_keys_are_unique(tmp_path):
    session = RecordingSession(_load(f"package_show_{GBA}.json"))
    records = CkanResourceProbe(tmp_path, PORTAL, session=session).discover(GBA)
    assert len(records) == 19
    assert len({r["key"] for r in records}) == 19
    assert len({r["dest"] for r in records}) == 19


def test_discover_chennai_csv(tmp_path):
    session = RecordingSession(_load(f"package_show_{CHENNAI}.json"))
    records = CkanResourceProbe(tmp_path, PORTAL, session=session, formats={"CSV"}).discover(CHENNAI)
    assert len(records) == 14
    assert {r["media_type"] for r in records} == {"text/csv"}
    assert all(r["filename"].endswith(".csv") for r in records)


def test_discover_raises_for_a_missing_dataset(tmp_path):
    probe = CkanResourceProbe(tmp_path, PORTAL, session=StatusSession(404))
    with pytest.raises(CkanError, match="gone"):
        probe.discover("gone")


def _record(tmp_path, **kw):
    base = {
        "key": "CKAN|data.opencity.in|d|r1",
        "kind": "ckan_resource",
        "record_type": "ckan_resource",
        "source_family": "ckan",
        "portal": PORTAL,
        "dataset": "d",
        "dataset_title": "D",
        "organization": "Org",
        "license": None,
        "dataset_url": f"{PORTAL}/dataset/d",
        "resource_id": "r1",
        "resource_name": "Name",
        "format": "PDF",
        "resource_last_modified": None,
        "filename": "Name.pdf",
        "dest": str(tmp_path / "Name.pdf"),
        "url": "https://x/Name.pdf",
        "status": "downloaded",
        "media_type": "application/pdf",
        "fetched_at": "2026-11-13T00:00:00Z",
        "probed_at": "2026-11-13T00:00:00Z",
        "sha256": "a" * 64,
        "bytes": 12,
        "text_layer": None,
    }
    return {**base, **kw}


def test_corpus_streams_ckan_records(tmp_path):
    other = {"key": "BUDGET|x|y|z", "kind": "budget_source_file"}
    lines = [_record(tmp_path), other, _record(tmp_path, key="CKAN|data.opencity.in|d|r2", text_layer=True)]
    (tmp_path / "manifest.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    records = list(Corpus(tmp_path).manifest_ckan_resources())
    assert [r.key for r in records] == ["CKAN|data.opencity.in|d|r1", "CKAN|data.opencity.in|d|r2"]
    assert isinstance(records[0], R.ManifestCkanResourceRecord)
    assert records[0].organization == "Org" and records[0].license is None
    assert records[0].text_layer is None and records[1].text_layer is True
    assert records[0].bytes == 12


def test_schema_accepts_download_fields_and_rejects_bad_status(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    schema = sc.load("manifest_ckan_resource")
    jsonschema.validate(_record(tmp_path, text_layer=False, http_status=200), schema)
    jsonschema.validate(_record(tmp_path, status="error", error="boom"), schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(_record(tmp_path, status="not_found"), schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(_record(tmp_path, key="CKAN|bad host|d|r"), schema)


def test_every_schema_field_survives_the_typed_api():
    declared = set(sc.load("manifest_ckan_resource")["properties"])
    assert declared <= {f.name for f in fields(R.ManifestCkanResourceRecord)}
