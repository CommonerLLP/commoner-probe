"""Tests for the MoSPI eSankhyiki API client.

Fixture payloads mirror the live API contract verified 2026-07-13 via
India-region egress: tidy row dicts under "data", pagination via
"meta_data" (page/limit params), <filter>_code query params, and the
HTML-on-unknown-route behaviour. Oracle values in the fixtures are the
real API's (UDISE Gujarat 2024-25 secondary dropout 16.9; PLFS Gujarat
2025 regular-wage ₹20,170.82). No network.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from urllib.parse import urlparse

import pytest

from commoner_probe.mospi import (
    DATASETS,
    MospiApiError,
    MospiClient,
    MospiProbe,
)

DROPOUT_ROWS = [
    {"indicator": "Dropout Rate", "year": "2024-25", "state": s, "gender": "Total",
     "level_of_education": "Secondary (9-10)", "value": v}
    for s, v in [("Gujarat", "16.9"), ("Kerala", "4.8"), ("West Bengal", "20.0")]
]


class FakeResponse:
    def __init__(self, payload=None, *, html=False, status=200):
        self._payload = payload
        self._html = html
        self.status_code = status

    def json(self):
        if self._html:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeMospiSession:
    """Routes the live-verified UDISE/PLFS route shapes."""

    def __init__(self, *, page_rows: list[dict] | None = None, page_size_cap: int = 2):
        self.rows = page_rows if page_rows is not None else DROPOUT_ROWS
        self.page_size_cap = page_size_cap
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, *, params=None, timeout=None, **kwargs):
        params = {k: str(v) for k, v in (params or {}).items()}
        self.calls.append((url, params))
        path = urlparse(url).path
        if path.endswith("/udise/getIndicatorList"):
            return FakeResponse({"data": [
                {"indicator_code": 41, "description": "Dropout Rate", "viz": "line"},
            ]})
        if path.endswith("/udise/getUdiseFilterByIndicatorId"):
            return FakeResponse({"data": {
                "year": [{"id": "2024-25", "label": "2024-25"}, {"id": "2023-24", "label": "2023-24"}],
                "state": [{"id": 8, "label": "Gujarat"}],
            }})
        if path.endswith("/udise/getUdiseRecords"):
            rows = self.rows
            if params.get("state_code") == "8":
                rows = [r for r in rows if r["state"] == "Gujarat"]
            page = int(params.get("page", "1"))
            limit = min(int(params.get("limit", "10")), self.page_size_cap)
            chunk = rows[(page - 1) * limit: page * limit]
            return FakeResponse({
                "data": chunk,
                "meta_data": {
                    "page": page,
                    "totalRecords": len(rows),
                    "totalPages": max(1, math.ceil(len(rows) / limit)),
                    "recordPerPage": limit,
                },
                "msg": "Data fetched successfully",
                "statusCode": True,
            })
        if path.endswith("/plfs/getData"):
            # PLFS returns everything in one response, no meta_data.
            return FakeResponse({"data": [
                {"year": "2025", "state": "Gujarat", "gender": "person",
                 "sector": "rural + urban", "value": "20170.82", "unit": "₹"},
            ]})
        if path.endswith("/nas/getNasData"):
            return FakeResponse({"error": "Please check the input parameters passed"})
        # Unknown routes: the API 200s with the docs SPA's HTML.
        return FakeResponse(html=True)


def _client(session=None) -> MospiClient:
    return MospiClient(sleep=0, session=session or FakeMospiSession())


def test_registry_covers_priority_datasets():
    assert {"PLFS", "AISHE", "UDISE", "ASI", "NAS", "HCES"} <= set(DATASETS)


def test_indicators():
    inds = _client().indicators("UDISE")
    assert inds == [{"indicator_code": 41, "description": "Dropout Rate", "viz": "line"}]


def test_indicators_unknown_dataset():
    with pytest.raises(MospiApiError, match="unknown dataset"):
        _client().indicators("NOPE")


def test_indicators_unverified_route_raises():
    with pytest.raises(MospiApiError, match="no verified indicator-list route"):
        _client().indicators("NAS")


def test_filters():
    f = _client().filters("UDISE", indicator_code=41)
    assert f["state"] == [{"id": 8, "label": "Gujarat"}]


def test_pull_follows_pagination():
    session = FakeMospiSession(page_size_cap=2)
    rows = list(_client(session).pull("UDISE", {"indicator_code": 41}))
    assert len(rows) == 3
    pages = [p.get("page") for _, p in session.calls]
    assert pages == ["1", "2"]


def test_pull_filter_param_and_oracle():
    rows = list(_client().pull("UDISE", {"indicator_code": 41, "year": "2024-25", "state_code": 8}))
    assert len(rows) == 1
    assert rows[0]["state"] == "Gujarat"
    assert rows[0]["value"] == "16.9"


def test_pull_single_response_without_meta_data():
    rows = list(_client().pull("PLFS", {"indicator_code": 6, "frequencyCode": 1}))
    assert rows[0]["value"] == "20170.82"


def test_pull_max_rows_brake():
    rows = list(_client().pull("UDISE", {"indicator_code": 41}, max_rows=1))
    assert len(rows) == 1


def test_api_error_payload_raises():
    with pytest.raises(MospiApiError, match="check the input parameters"):
        list(_client().pull("NAS", {"indicator_code": 1}))


def test_html_fallback_raises_not_parses():
    client = _client()
    with pytest.raises(MospiApiError, match="did not return JSON"):
        client._get_json("/plfs/getNoSuchRoute", {})


def test_probe_pull_to_csv_writes_manifest_and_csv(tmp_path):
    probe = MospiProbe(tmp_path, sleep=0, session=FakeMospiSession())
    rec = probe.pull_to_csv("UDISE", {"indicator_code": 41, "year": "2024-25", "state_code": 8})
    assert rec["kind"] == "mospi_pull"
    assert rec["rows"] == 1
    assert rec["endpoint"] == "/udise/getUdiseRecords"
    csv_path = tmp_path / rec["csv_path"]
    body = csv_path.read_text(encoding="utf-8")
    assert "Gujarat" in body and "16.9" in body
    assert rec["sha256"] == hashlib.sha256(csv_path.read_bytes()).hexdigest()
    manifest = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    assert manifest == [rec]


def test_probe_key_is_stable_per_params(tmp_path):
    probe = MospiProbe(tmp_path, sleep=0, session=FakeMospiSession())
    r1 = probe.pull_to_csv("UDISE", {"indicator_code": 41, "state_code": 8})
    r2 = probe.pull_to_csv("UDISE", {"state_code": 8, "indicator_code": 41})
    r3 = probe.pull_to_csv("UDISE", {"indicator_code": 41})
    assert r1["key"] == r2["key"]
    assert r1["key"] != r3["key"]


def test_dump_all_pulls_every_year_from_filters(tmp_path):
    probe = MospiProbe(tmp_path, sleep=0, session=FakeMospiSession())
    records = probe.dump_all("UDISE", {"indicator_code": 41})
    assert [r["params"]["year"] for r in records] == ["2024-25", "2023-24"]
    assert all(r["kind"] == "mospi_pull" for r in records)


def test_dump_all_explicit_years(tmp_path):
    probe = MospiProbe(tmp_path, sleep=0, session=FakeMospiSession())
    records = probe.dump_all("UDISE", {"indicator_code": 41}, years=["2024-25"])
    assert len(records) == 1


def test_records_validate_against_schema(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    probe = MospiProbe(tmp_path, sleep=0, session=FakeMospiSession())
    probe.pull_to_csv("UDISE", {"indicator_code": 41, "year": "2024-25"})
    schema = json.loads(
        (Path(__file__).resolve().parent.parent / "commoner_probe" / "schemas" / "manifest_mospi.schema.json")
        .read_text(encoding="utf-8")
    )
    for line in (tmp_path / "manifest.jsonl").read_text(encoding="utf-8").splitlines():
        jsonschema.validate(json.loads(line), schema)


def test_cli_registers_mospi():
    from commoner_probe.cli import build_parser

    parser = build_parser()
    args = parser.parse_args([
        "mospi", "--dataset", "UDISE", "--pull",
        "--param", "indicator_code=41", "--out", "corpus",
    ])
    assert args.func.__name__ == "mospi_cmd"
    assert args.param == ["indicator_code=41"]
