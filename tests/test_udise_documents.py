"""Tests for the UDISE+ public document endpoint.

No network. The two traps this covers are both about a response that looks
like a success: a JSON envelope served under a `.pdf` URL, and a 200 carrying
something that is not a PDF at all.
"""

from __future__ import annotations

import base64
import json

import pytest

from commoner_probe.spa_jwt_api import (
    UDISE_DOCUMENTS,
    UdiseDocumentProbe,
    document_pairs,
    document_url,
    extract_document_pairs,
    unwrap_document,
)

PDF = b"%PDF-1.7\n" + b"x" * 4000
ENVELOPE = json.dumps({"pdf": base64.b64encode(PDF).decode()}).encode()


class _Response:
    def __init__(self, body: bytes, status: int = 200, content_type: str = "application/json"):
        self.content = body
        self.status_code = status
        self.headers = {"Content-Type": content_type,
                        "Content-Disposition": "inline;filename=f.txt"}


class _Portal:
    """Serves the JSON envelope. A name in `gone` answers 200 with an error page."""

    def __init__(self, gone: set[str] | None = None):
        self.gone = gone or set()
        self.calls: list[str] = []

    def get(self, url: str, **kwargs):
        self.calls.append(url)
        if any(name in url for name in self.gone):
            return _Response(b"<html>Not found</html>", 200, "text/html")
        return _Response(ENVELOPE)


# ── the catalogue ─────────────────────────────────────────────────────────


def test_the_catalogue_holds_every_pair_once():
    pairs = document_pairs()
    assert len(pairs) == 86
    assert len(set(pairs)) == 86, "a duplicate pair fetches one document twice"
    assert set(UDISE_DOCUMENTS) == {"UploadedFiles", "dcf2021", "pdfFiles"}


def test_the_catalogue_can_be_re_extracted_from_a_bundle():
    """There is no listing endpoint. The names live in the Angular bundle, so
    the catalogue is pinned and must be re-derivable when the build changes."""
    bundle = ('...dcfDownload("pdfFiles","Metadata")...'
              "dcfDownload('dcf2021','DOletter2026')..."
              '...dcfDownload("pdfFiles","Metadata")...')

    assert extract_document_pairs(bundle) == [
        ("pdfFiles", "Metadata"), ("dcf2021", "DOletter2026")]


def test_the_url_is_the_folder_and_the_name():
    assert document_url("pdfFiles", "Metadata") == (
        "https://api.udiseplus.gov.in/udise-fms/api/fileUpload/getDocument/"
        "pdfFiles/Metadata.pdf")


# ── trap 1: the endpoint answers a .pdf request with JSON ─────────────────


def test_the_json_envelope_is_unwrapped_to_the_pdf():
    """Verified live 2026-08-19 and 2026-08-20. The body is
    `{"pdf": "<base64>"}`, the Content-Type is application/json, and the
    Content-Disposition claims filename=f.txt. A caller writing the response
    straight to disk writes a JSON file under a .pdf name."""
    assert unwrap_document(ENVELOPE) == PDF


def test_a_body_that_is_already_a_pdf_is_returned_unchanged():
    """The unwrap must be safe to apply to any response from this host."""
    assert unwrap_document(PDF) == PDF


@pytest.mark.parametrize("body", [b"", b"<html>no</html>", b"{not json", b"{}",
                                  b'{"pdf": ""}', b'{"pdf": "!!!not base64!!"}'])
def test_a_body_that_is_neither_comes_back_as_it_arrived(body):
    """The caller decides what a non-PDF means. Guessing here would turn an
    error page into a zero-byte PDF."""
    assert unwrap_document(body) == body


# ── trap 2: a departed name answers 200 with a non-PDF ────────────────────


def test_a_name_that_left_the_bundle_is_recorded_as_not_pdf(tmp_path):
    """It answers 200, so a status check alone reports success. Only the magic
    bytes tell the two apart."""
    probe = UdiseDocumentProbe(tmp_path, sleep=0, session=_Portal(gone={"Metadata"}))
    records = probe.probe(folders=["pdfFiles"], max_records=40)

    gone = next(r for r in records if r["name"] == "Metadata")
    assert gone["fetch_status"] == "not_pdf"
    assert gone["http_status"] == 200
    assert gone["path"] is None
    assert not (tmp_path / "documents" / "pdfFiles__Metadata.pdf").exists()


def test_a_good_document_lands_with_its_hash(tmp_path):
    import hashlib

    probe = UdiseDocumentProbe(tmp_path, sleep=0, session=_Portal())
    records = probe.probe(folders=["dcf2021"], max_records=1)

    row = records[0]
    assert row["fetch_status"] == "ok"
    saved = (tmp_path / row["path"]).read_bytes()
    assert saved == PDF
    assert row["sha256"] == hashlib.sha256(PDF).hexdigest()
    assert row["bytes"] == len(PDF)


def test_one_failure_does_not_end_the_run(tmp_path):
    probe = UdiseDocumentProbe(tmp_path, sleep=0, session=_Portal(gone={"StudentProfile_18July2016"}))
    records = probe.probe(folders=["pdfFiles"])

    assert len(records) == len(UDISE_DOCUMENTS["pdfFiles"])
    assert sum(1 for r in records if r["fetch_status"] == "not_pdf") == 1
    assert sum(1 for r in records if r["fetch_status"] == "ok") == len(records) - 1


# ── resume, dry run, validation ───────────────────────────────────────────


def test_a_resume_refetches_nothing_it_holds(tmp_path):
    first = _Portal()
    UdiseDocumentProbe(tmp_path, sleep=0, session=first).probe(folders=["dcf2021"])

    second = _Portal()
    records = UdiseDocumentProbe(tmp_path, sleep=0, session=second).probe(folders=["dcf2021"])

    assert second.calls == []
    assert records == []


def test_a_row_stops_vouching_when_its_file_is_gone(tmp_path):
    first = _Portal()
    rows = UdiseDocumentProbe(tmp_path, sleep=0, session=first).probe(folders=["dcf2021"])
    (tmp_path / rows[0]["path"]).unlink()

    second = _Portal()
    again = UdiseDocumentProbe(tmp_path, sleep=0, session=second).probe(folders=["dcf2021"])

    assert [r["name"] for r in again] == [rows[0]["name"]]


def test_a_not_pdf_is_retried_next_run(tmp_path):
    """Unlike a resume over a good file, a name that returned no PDF may be a
    transient portal fault. Only `ok` vouches."""
    UdiseDocumentProbe(tmp_path, sleep=0, session=_Portal(gone={"Metadata"})).probe(
        folders=["pdfFiles"])

    working = _Portal()
    again = UdiseDocumentProbe(tmp_path, sleep=0, session=working).probe(
        folders=["pdfFiles"])

    assert [r["name"] for r in again] == ["Metadata"]
    assert again[0]["fetch_status"] == "ok"


def test_a_dry_run_fetches_nothing_and_names_everything(tmp_path):
    portal = _Portal()
    records = UdiseDocumentProbe(tmp_path, sleep=0, session=portal).probe(dry_run=True)

    assert portal.calls == []
    assert len(records) == 86
    assert {r["fetch_status"] for r in records} == {"dry_run"}
    assert not (tmp_path / "manifest.jsonl").exists()


def test_every_record_validates(tmp_path):
    import jsonschema

    from commoner_probe import schemas

    probe = UdiseDocumentProbe(tmp_path, sleep=0, session=_Portal(gone={"Metadata"}))
    probe.probe(folders=["pdfFiles"], max_records=40)

    rows = [json.loads(x) for x in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    for row in rows:
        jsonschema.validate(row, schemas.load("manifest_udise_document"))


# ── the defect the request named ──────────────────────────────────────────


def test_the_stdlib_response_carries_headers():
    """A caller reading Content-Type worked with `requests` installed and
    raised AttributeError without it. The stdlib fallback exists to remove
    exactly that difference."""
    from commoner_probe.http_client import StdlibResponse

    response = StdlibResponse("https://x/y", 200, b"{}",
                              {"content-type": "application/json"})

    assert response.headers.get("Content-Type") == "application/json"
    assert response.headers.get("content-type") == "application/json"
    assert response.headers.get("missing") is None
    assert "Content-Type" in response.headers


def test_a_stdlib_response_without_headers_still_reads_as_a_map():
    from commoner_probe.http_client import StdlibResponse

    assert StdlibResponse("https://x/y", 200, b"").headers.get("Content-Type") is None
