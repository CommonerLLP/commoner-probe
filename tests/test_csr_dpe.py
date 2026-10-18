"""Tests for commoner_probe/csr/dpe.py. Every test uses canned responses; none touches the network."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
from pathlib import Path

import pytest

from commoner_probe.csr import dpe
from commoner_probe.csr.dpe import BASE_URL, DpeCsrProbe
from commoner_probe.ministry_pdf_index import SCHEME_FREE_USER_AGENT

PDF_URL = "https://www.dpe.gov.in/static/uploads/2026/06/abc.pdf"
HINDI_URL = "https://www.dpe.gov.in/static/uploads/2026/06/hindi.pdf"


class _Response:
    status = 200

    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _post(post_id, **acf):
    fields = {"pdf": None, "pdf_hindi": None, "pdf_both": None}
    fields.update(acf)
    return {
        "id": post_id,
        "date": "2026-06-19T17:24:39",
        "title": {"rendered": f"Post {post_id}"},
        "guid": {"rendered": f"https://cms-dpe.digifootprint.gov.in/?post_type=central_documents&p={post_id}"},
        "acf": fields,
    }


@pytest.fixture
def api(monkeypatch):
    """Serve canned posts and media; record each request."""
    served = {"posts": [], "media": {}, "requests": []}

    def urlopen(req, timeout=None):
        served["requests"].append(req)
        parsed = urllib.parse.urlparse(req.full_url)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path.endswith("/wp/v2/central_documents"):
            return _Response(served["posts"])
        if parsed.path.endswith("/wp/v2/media"):
            ids = [int(i) for i in query["include"][0].split(",")]
            return _Response([{"id": i, "source_url": served["media"][i]} for i in ids if i in served["media"]])
        raise AssertionError(f"unexpected request {req.full_url}")

    monkeypatch.setattr(dpe.urllib.request, "urlopen", urlopen)
    return served


def test_base_url_is_the_www_host() -> None:
    assert BASE_URL == "https://www.dpe.gov.in/cms/wp-json"


def test_requests_send_the_scheme_free_user_agent(api) -> None:
    api["posts"] = [_post(1, pdf=10)]
    api["media"] = {10: PDF_URL}
    DpeCsrProbe(Path("unused"), sleep=0).fetch_page(1)
    assert len(api["requests"]) == 2
    for req in api["requests"]:
        assert req.get_header("User-agent") == SCHEME_FREE_USER_AGENT
    assert "://" not in SCHEME_FREE_USER_AGENT


def test_fetch_page_reads_document_repository_posts_newest_first(api) -> None:
    api["posts"] = [_post(1, pdf=10)]
    api["media"] = {10: PDF_URL}
    DpeCsrProbe(Path("unused"), sleep=0).fetch_page(2, search="csr")
    url = urllib.parse.urlparse(api["requests"][0].full_url)
    assert f"{url.scheme}://{url.netloc}" == "https://www.dpe.gov.in"
    assert url.path == "/cms/wp-json/wp/v2/central_documents"
    query = urllib.parse.parse_qs(url.query)
    assert query["search"] == ["csr"]
    assert query["page"] == ["2"]
    assert query["orderby"] == ["date"] and query["order"] == ["desc"]


def test_fetch_page_resolves_each_post_file_in_one_media_request(api) -> None:
    api["posts"] = [_post(1, pdf=10), _post(2, pdf=11), _post(3, pdf=10)]
    api["media"] = {10: PDF_URL, 11: HINDI_URL}
    items = DpeCsrProbe(Path("unused"), sleep=0).fetch_page(1)
    assert [i["source_url"] for i in items] == [PDF_URL, HINDI_URL, PDF_URL]
    media = [r for r in api["requests"] if "/wp/v2/media" in r.full_url]
    assert len(media) == 1
    assert urllib.parse.parse_qs(urllib.parse.urlparse(media[0].full_url).query)["include"] == ["10,11"]


def test_fetch_page_falls_back_to_the_combined_then_hindi_file(api) -> None:
    api["posts"] = [_post(1, pdf_both=12), _post(2, pdf_hindi=11)]
    api["media"] = {11: HINDI_URL, 12: PDF_URL}
    items = DpeCsrProbe(Path("unused"), sleep=0).fetch_page(1)
    assert [i["source_url"] for i in items] == [PDF_URL, HINDI_URL]


def test_a_post_without_a_file_gets_no_source_url_and_no_record(api, tmp_path) -> None:
    api["posts"] = [_post(1), _post(2, pdf=10)]
    api["media"] = {10: PDF_URL}
    probe = DpeCsrProbe(tmp_path, sleep=0)
    items = probe.fetch_page(1)
    assert "source_url" not in items[0]
    # The WordPress guid is the CMS edit link, not a file, so it must not stand in.
    assert probe.download_item(items[0], dry_run=True) == {}
    record = probe.download_item(items[1], dry_run=True)
    assert record["key"] == "DPE_CSR|2" and record["url"] == PDF_URL
    assert record["status"] == "dry_run"


def test_no_posts_makes_no_media_request(api) -> None:
    assert DpeCsrProbe(Path("unused"), sleep=0).fetch_page(1) == []
    assert len(api["requests"]) == 1


def test_a_page_past_the_last_returns_nothing(monkeypatch) -> None:
    def urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, None)

    monkeypatch.setattr(dpe.urllib.request, "urlopen", urlopen)
    assert DpeCsrProbe(Path("unused"), sleep=0).fetch_page(99) == []


def test_file_download_sends_the_scheme_free_user_agent(monkeypatch, tmp_path) -> None:
    seen = []

    class _File(_Response):
        def __init__(self):
            self._body = b"%PDF-1.7"

    monkeypatch.setattr(dpe.urllib.request, "urlopen", lambda req, timeout=None: seen.append(req) or _File())
    record = DpeCsrProbe(tmp_path, sleep=0).download_item({"id": 5, "source_url": PDF_URL, "title": {}}, dry_run=False)
    assert record["status"] == "downloaded"
    assert seen[0].get_header("User-agent") == SCHEME_FREE_USER_AGENT
