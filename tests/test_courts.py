"""Tests for the India court-data adapter (REQ-0038).

Fixture payloads mirror the Indian Kanoon API contract read from IKAPI's own
source (``sushant354/IKAPI``, MIT) on 2026-07-26: POST-only endpoints,
``Authorization: Token`` header, ``docs``/``tid``/``docsource``/``publishdate``
search rows, ``pagenum`` advancing by ``maxpages``, base64 ``doc`` in origdoc,
and errors arriving in the *body* (``errmsg`` or a bare ``error code:`` string)
rather than as an HTTP status. No network.

The eCourts tests never install or import ``openjustice-in/ecourts`` — that is
the whole point of the boundary, and ``test_no_gpl_ecourts_linkage`` guards it.
A throwaway ``python -c`` script stands in for the external executable.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

from commoner_probe import corpus as corpus_mod
from commoner_probe import validate as validate_mod
from commoner_probe.courts import (
    ECOURTS_CMD_ENV,
    IK_TOKEN_ENV,
    CourtProbe,
    ECourtsUnavailable,
    IndianKanoonClient,
    IndianKanoonError,
    build_query,
    ecourts_available,
    ecourts_command,
    ecourts_record,
    ik_token,
    parse_search_response,
    run_ecourts,
)

SEARCH_PAGE = {
    "found": "3",
    "docs": [
        {
            "tid": 1766147,
            "title": "Olga Tellis &amp; Ors vs Bombay Municipal Corporation",
            "docsource": "Supreme Court of India",
            "publishdate": "1985-07-10",
            "numcites": 12,
            "numcitedby": 480,
            "headline": "right to livelihood",
        },
        {
            "tid": 257876,
            "title": "Maneka Gandhi vs Union Of India",
            "docsource": "Supreme Court of India",
            "publishdate": "1978-01-25",
            "numcites": 30,
            "numcitedby": 900,
        },
    ],
}


class FakeResponse:
    def __init__(self, body: str, status: int = 200) -> None:
        self.text = body
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Records every POST so the tests can assert on the wire contract."""

    def __init__(self, bodies: list[str], status: int = 200) -> None:
        self.bodies = list(bodies)
        self.status = status
        self.calls: list[dict] = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        body = self.bodies.pop(0) if self.bodies else "{}"
        return FakeResponse(body, self.status)

    def get(self, url, **kwargs):  # pragma: no cover - the API is POST-only
        raise AssertionError("Indian Kanoon endpoints are POST, not GET")


def make_client(bodies, **kwargs) -> IndianKanoonClient:
    client = IndianKanoonClient("test-token", sleep=0, **kwargs)
    client.session = FakeSession([json.dumps(b) if not isinstance(b, str) else b for b in bodies])
    return client


# --- query composition -----------------------------------------------------

def test_build_query_appends_api_filter_tokens_not_params():
    q = build_query(
        "right to livelihood",
        doctypes="supremecourt",
        from_date="01-01-2020",
        to_date="31-12-2024",
        sort_by="mostrecent",
    )
    assert q == (
        "right to livelihood doctypes: supremecourt fromdate: 01-01-2020 "
        "todate: 31-12-2024 sortby: mostrecent"
    )


def test_build_query_rejects_unknown_sort_order():
    with pytest.raises(ValueError, match="sort_by"):
        build_query("x", sort_by="relevance")


def test_build_query_rejects_empty():
    with pytest.raises(ValueError, match="empty query"):
        build_query("   ")


# --- token handling --------------------------------------------------------

def test_ik_token_reads_environment(monkeypatch):
    monkeypatch.setenv(IK_TOKEN_ENV, "  secret-token  ")
    assert ik_token() == "secret-token"


def test_ik_token_error_names_the_env_var(monkeypatch):
    monkeypatch.delenv(IK_TOKEN_ENV, raising=False)
    with pytest.raises(IndianKanoonError, match=IK_TOKEN_ENV):
        ik_token()


# --- search parsing --------------------------------------------------------

def test_parse_search_response_maps_api_field_names():
    rows = parse_search_response(SEARCH_PAGE, query="q")
    assert [r["docid"] for r in rows] == [1766147, 257876]
    first = rows[0]
    assert first["court"] == "Supreme Court of India"
    assert first["judgment_date"] == "1985-07-10"
    assert first["num_cited_by"] == 480
    assert first["url"] == "https://indiankanoon.org/doc/1766147/"
    assert first["position"] == 0
    # A row without a headline must not fabricate one.
    assert rows[1]["headline"] is None


def test_parse_search_response_empty_docs_is_end_of_results_not_an_error():
    assert parse_search_response({"found": "0", "docs": []}) == []
    assert parse_search_response({"found": "0"}) == []


def test_parse_search_response_rejects_non_object_payload():
    with pytest.raises(IndianKanoonError, match="expected an object"):
        parse_search_response(["nope"])


# --- error shapes (body, not status) ---------------------------------------

def test_bare_error_code_string_body_raises():
    client = make_client(["error code: 429 too many requests"])
    with pytest.raises(IndianKanoonError, match="error code: 429"):
        client.search("x")


def test_errmsg_in_a_200_body_raises():
    client = make_client([{"errmsg": "Invalid token"}])
    with pytest.raises(IndianKanoonError, match="Invalid token"):
        client.search("x")


def test_html_body_raises_with_a_snippet_rather_than_parsing_as_success():
    client = make_client(["<html><body>Access denied</body></html>"])
    with pytest.raises(IndianKanoonError, match="not JSON"):
        client.search("x")


def test_edge_block_403_reports_the_body_not_just_the_status():
    """The real 2026-07-26 response from this repo's egress.

    Cloudflare's 1010 block and a rejected token both arrive as HTTP 403 but
    need opposite fixes (change network path vs change credential), so the
    body has to survive into the error message.
    """
    cloudflare_403 = json.dumps(
        {
            "type": "https://developers.cloudflare.com/.../error-1010/",
            "title": "Error 1010: Access denied",
            "status": 403,
        }
    )
    client = make_client([cloudflare_403])
    client.session.status = 403
    with pytest.raises(IndianKanoonError, match="Error 1010"):
        client.search("x")


# --- wire contract ---------------------------------------------------------

def test_search_posts_to_the_documented_path_with_the_token_header():
    client = make_client([SEARCH_PAGE])
    client.search("right to livelihood", page_num=0, max_pages=5)
    call = client.session.calls[0]
    assert call["url"] == (
        "https://api.indiankanoon.org/search/"
        "?formInput=right+to+livelihood&pagenum=0&maxpages=5"
    )
    assert call["headers"]["Authorization"] == "Token test-token"
    assert call["headers"]["Accept"] == "application/json"
    # robots.txt governs crawlers; this is an authenticated paid API call.
    assert call["respect_robots"] is False


def test_search_clamps_max_pages_to_the_api_cap():
    client = make_client([SEARCH_PAGE])
    client.search("x", max_pages=500)
    assert "maxpages=100" in client.session.calls[0]["url"]


def test_search_rejects_negative_page_num():
    client = make_client([SEARCH_PAGE])
    with pytest.raises(ValueError, match="0-indexed"):
        client.search("x", page_num=-1)


def test_iter_search_advances_pagenum_by_max_pages_and_stops_when_dry():
    page_two = {"docs": [dict(SEARCH_PAGE["docs"][0], tid=999, title="Third")]}
    client = make_client([SEARCH_PAGE, page_two, {"docs": []}])
    rows = list(client.iter_search("q", max_pages=2))
    assert [r["docid"] for r in rows] == [1766147, 257876, 999]
    urls = [c["url"] for c in client.session.calls]
    assert "pagenum=0" in urls[0]
    assert "pagenum=2" in urls[1]
    assert "pagenum=4" in urls[2]


def test_iter_search_deduplicates_overlapping_result_windows():
    client = make_client([SEARCH_PAGE, SEARCH_PAGE, {"docs": []}])
    rows = list(client.iter_search("q"))
    assert [r["docid"] for r in rows] == [1766147, 257876]


def test_iter_search_honours_max_records():
    client = make_client([SEARCH_PAGE])
    rows = list(client.iter_search("q", max_records=1))
    assert len(rows) == 1
    assert len(client.session.calls) == 1


def test_original_document_base64_decodes():
    client = make_client([{"doc": base64.b64encode(b"%PDF-1.4 body").decode()}])
    assert client.original_document(42) == b"%PDF-1.4 body"
    assert client.session.calls[0]["url"].endswith("/origdoc/42/")


def test_original_document_missing_doc_field_raises():
    client = make_client([{"tid": 42}])
    with pytest.raises(IndianKanoonError, match="no 'doc' field"):
        client.original_document(42)


def test_original_document_rejects_non_base64():
    client = make_client([{"doc": "not base64 !!!"}])
    with pytest.raises(IndianKanoonError, match="not valid base64"):
        client.original_document(42)


def test_cite_args_are_omitted_when_zero():
    client = make_client([{"tid": 1}, {"tid": 1}])
    client.doc(7)
    client.doc(7, max_cites=5, max_cited_by=3)
    assert client.session.calls[0]["url"].endswith("/doc/7/")
    assert client.session.calls[1]["url"].endswith("/doc/7/?maxcites=5&maxcitedby=3")


# --- probe / manifest ------------------------------------------------------

def test_probe_writes_one_manifest_row_per_result(tmp_path):
    probe = CourtProbe(tmp_path, client=make_client([SEARCH_PAGE, {"docs": []}]))
    records = probe.probe("q")
    assert len(records) == 2
    lines = (tmp_path / "manifest.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["kind"] == "court_record"
    assert rec["provider"] == "indiankanoon"
    assert rec["key"] == "COURT|indiankanoon|1766147"
    assert rec["status"] == "metadata_only"


def test_probe_dry_run_writes_nothing(tmp_path):
    probe = CourtProbe(tmp_path, client=make_client([SEARCH_PAGE, {"docs": []}]))
    records = probe.probe("q", dry_run=True)
    assert records and all(r["status"] == "dry_run" for r in records)
    assert not (tmp_path / "manifest.jsonl").exists()


def test_download_records_sha256_and_bytes(tmp_path):
    body = b"%PDF-1.4 judgment"
    client = make_client([SEARCH_PAGE, {"doc": base64.b64encode(body).decode()}, {"docs": []}])
    probe = CourtProbe(tmp_path, client=client)
    records = probe.probe("q", max_records=1, download=True)
    rec = records[0]
    assert rec["status"] == "downloaded"
    assert rec["bytes"] == len(body)
    assert Path(rec["dest"]).read_bytes() == body


def test_download_failure_is_recorded_not_raised(tmp_path):
    client = make_client([SEARCH_PAGE, {"errmsg": "quota exhausted"}, {"docs": []}])
    probe = CourtProbe(tmp_path, client=client)
    records = probe.probe("q", max_records=1, download=True)
    assert records[0]["status"] == "error"
    assert "quota exhausted" in records[0]["error"]


# --- end-to-end kind registration ------------------------------------------

def test_court_record_kind_is_registered_for_validation_and_corpus(tmp_path):
    """A new manifest kind must be wired in *both* places, not just written.

    Validation silently skips unknown kinds, so a record can "validate"
    simply by never being checked — the exact defect found in the CAG kind.
    """
    probe = CourtProbe(tmp_path, client=make_client([SEARCH_PAGE, {"docs": []}]))
    records = probe.probe("q")

    assert validate_mod._pick_schema_name(records[0]) == "manifest_court_record"

    jsonschema = pytest.importorskip("jsonschema")
    from commoner_probe import schemas as sc

    schema = sc.load("manifest_court_record")
    validator = jsonschema.Draft202012Validator(schema)
    for record in records:
        assert list(validator.iter_errors(record)) == []

    streamed = list(corpus_mod.Corpus(tmp_path).manifest_court_records())
    assert [r.docid for r in streamed] == [1766147, 257876]
    assert "manifest_court_records" in corpus_mod.Corpus._STREAM_MAP


def test_ecourts_record_validates_against_the_same_schema(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    from commoner_probe import schemas as sc

    record = ecourts_record(
        {"cnr": "DLHC010012342024", "court": "Delhi High Court"},
        args=["--court", "delhi"],
        command=["/opt/tools/ecourts"],
    )
    validator = jsonschema.Draft202012Validator(sc.load("manifest_court_record"))
    assert list(validator.iter_errors(record)) == []
    assert record["provider"] == "ecourts"
    # A full path would leak the local filesystem layout into a manifest.
    assert record["tool_command"] == "ecourts"
    assert record["raw"]["cnr"] == "DLHC010012342024"


# --- eCourts process boundary ----------------------------------------------

def fake_ecourts(tmp_path: Path, body: str, *, exit_code: int = 0) -> str:
    """A stand-in executable. Deliberately not the real GPL-3.0 tool."""
    script = tmp_path / "fake_ecourts.py"
    script.write_text(
        "import sys\n"
        f"sys.stdout.write({body!r})\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    return f"{sys.executable} {script}"


def test_ecourts_absent_is_a_reported_state_not_a_crash(monkeypatch):
    monkeypatch.setenv(ECOURTS_CMD_ENV, "/nonexistent/ecourts-binary")
    assert ecourts_command() is None
    assert ecourts_available() is False
    with pytest.raises(ECourtsUnavailable, match="GPL-3.0"):
        run_ecourts([])


def test_ecourts_command_survives_a_path_containing_spaces(tmp_path, monkeypatch):
    """str.split() would break the path in two and keep the quotes."""
    spaced = tmp_path / "My Tools"
    spaced.mkdir()
    exe = spaced / "ecourts"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv(ECOURTS_CMD_ENV, f'"{exe}" --verbose')
    assert ecourts_command() == [str(exe), "--verbose"]


def test_ecourts_command_on_unbalanced_quotes_is_none_not_a_crash(monkeypatch):
    monkeypatch.setenv(ECOURTS_CMD_ENV, '"/unclosed/path')
    assert ecourts_command() is None


def test_run_ecourts_reads_json_array_from_stdout(tmp_path, monkeypatch):
    monkeypatch.setenv(ECOURTS_CMD_ENV, fake_ecourts(tmp_path, '[{"cnr": "A"}, {"cnr": "B"}]'))
    assert [r["cnr"] for r in run_ecourts([])] == ["A", "B"]


def test_run_ecourts_reads_jsonl_and_wrapped_objects(tmp_path, monkeypatch):
    monkeypatch.setenv(ECOURTS_CMD_ENV, fake_ecourts(tmp_path, '{"cnr": "A"}\n{"cnr": "B"}\n'))
    assert [r["cnr"] for r in run_ecourts([])] == ["A", "B"]
    monkeypatch.setenv(ECOURTS_CMD_ENV, fake_ecourts(tmp_path, '{"results": [{"cnr": "C"}]}'))
    assert [r["cnr"] for r in run_ecourts([])] == ["C"]


def test_run_ecourts_nonzero_exit_raises(tmp_path, monkeypatch):
    monkeypatch.setenv(ECOURTS_CMD_ENV, fake_ecourts(tmp_path, "", exit_code=3))
    with pytest.raises(ECourtsUnavailable, match="exited 3"):
        run_ecourts([])


def test_run_ecourts_empty_output_is_empty_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv(ECOURTS_CMD_ENV, fake_ecourts(tmp_path, "   \n"))
    assert run_ecourts([]) == []


def test_run_ecourts_unparseable_output_raises(tmp_path, monkeypatch):
    monkeypatch.setenv(ECOURTS_CMD_ENV, fake_ecourts(tmp_path, "Traceback (most recent call last):\n"))
    with pytest.raises(ECourtsUnavailable, match="neither JSON nor JSONL"):
        run_ecourts([])


def test_no_gpl_ecourts_linkage():
    """The licence boundary, enforced in CI rather than in a comment.

    commoner-probe ships MIT to PyPI. ``openjustice-in/ecourts`` is GPL-3.0,
    so importing it or declaring it a dependency would relicense this package
    for every installer. A subprocess call is mere aggregation; an import is
    not. This test fails the moment someone crosses that line.
    """
    root = Path(__file__).resolve().parents[1]
    offenders = []
    for path in (root / "commoner_probe").rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("import ecourts", "from ecourts")):
                offenders.append(f"{path.relative_to(root)}:{lineno}")
    assert offenders == [], f"GPL-3.0 eCourts imported into an MIT package: {offenders}"

    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert "openjustice" not in pyproject
    assert not any(
        line.strip().startswith(('"ecourts', "'ecourts", "ecourts"))
        for line in pyproject.splitlines()
    ), "eCourts must not be declared a dependency, not even an optional extra"
