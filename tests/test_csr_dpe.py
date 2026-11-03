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


def _post(post_id, *, category=418, date="2026-06-19T17:24:39", **acf):
    fields = {"pdf": None, "pdf_hindi": None, "pdf_both": None}
    fields.update(acf)
    return {
        "id": post_id,
        "date": date,
        "cd_category": [category],
        "title": {"rendered": f"Post {post_id}"},
        "guid": {"rendered": f"https://cms-dpe.digifootprint.gov.in/?post_type=central_documents&p={post_id}"},
        "acf": fields,
    }


@pytest.fixture
def api(monkeypatch):
    """Serve canned posts and media; record each request."""
    served = {"posts": [], "by_search": None, "media": {}, "requests": []}

    def urlopen(req, timeout=None):
        served["requests"].append(req)
        parsed = urllib.parse.urlparse(req.full_url)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path.endswith("/wp/v2/central_documents"):
            if served["by_search"] is None:
                return _Response(served["posts"])
            # Page through the posts of the requested search, as WordPress does.
            posts = served["by_search"].get(query["search"][0], [])
            size, page = int(query["per_page"][0]), int(query["page"][0])
            if page > 1 and (page - 1) * size >= len(posts):
                raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, None)
            return _Response(posts[(page - 1) * size:page * size])
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


def test_record_title_prefers_the_acf_title(tmp_path) -> None:
    item = _post(7, title="  Seventh Regional Workshop on CSR &amp; more ", pdf=10)
    item["title"]["rendered"] = "7th-csr"
    item["source_url"] = PDF_URL
    record = DpeCsrProbe(tmp_path, sleep=0).download_item(item, dry_run=True)
    assert record["title"] == "Seventh Regional Workshop on CSR & more"


@pytest.mark.parametrize("acf_title", [None, "", "   ", 5])
def test_record_title_falls_back_to_the_rendered_title(tmp_path, acf_title) -> None:
    item = _post(8, title=acf_title, pdf=10)
    item["source_url"] = PDF_URL
    record = DpeCsrProbe(tmp_path, sleep=0).download_item(item, dry_run=True)
    assert record["title"] == "Post 8"


def test_record_title_falls_back_when_the_post_has_no_acf(tmp_path) -> None:
    item = {"id": 9, "title": {"rendered": "Plain"}, "source_url": PDF_URL, "acf": []}
    record = DpeCsrProbe(tmp_path, sleep=0).download_item(item, dry_run=True)
    assert record["title"] == "Plain"


def _query(req) -> dict[str, list[str]]:
    return urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)


def test_fetch_page_asks_only_for_the_csr_document_types(api) -> None:
    api["posts"] = [_post(1, pdf=10)]
    api["media"] = {10: PDF_URL}
    DpeCsrProbe(Path("unused"), sleep=0).fetch_page(1)
    assert _query(api["requests"][0])["cd_category"] == ["418,400,428,434"]


@pytest.mark.parametrize("category", [418, 400, 428, 434])
def test_fetch_page_keeps_posts_in_each_csr_document_type(api, category) -> None:
    api["posts"] = [_post(1, category=category, pdf=10)]
    api["media"] = {10: PDF_URL}
    assert [i["id"] for i in DpeCsrProbe(Path("unused"), sleep=0).fetch_page(1)] == [1]


def test_fetch_page_drops_posts_in_other_types_even_if_the_server_returns_them(api) -> None:
    # 446 and 448 are Public Enterprises Survey annexures, 399 is a vacancy note.
    api["posts"] = [_post(1, category=446, pdf=10), _post(2, category=448, pdf=10),
                    _post(3, category=399, pdf=10), _post(4, category=428, pdf=10)]
    api["media"] = {10: PDF_URL}
    items = DpeCsrProbe(Path("unused"), sleep=0).fetch_page(1)
    assert [i["id"] for i in items] == [4]
    # The media request lists only the files of kept posts.
    assert len([r for r in api["requests"] if "/wp/v2/media" in r.full_url]) == 1


def test_fetch_documents_runs_both_searches_and_removes_duplicates(api) -> None:
    api["by_search"] = {
        "csr": [_post(1, date="2026-06-01T00:00:00", pdf=10), _post(2, date="2026-05-01T00:00:00", pdf=10)],
        "corporate social responsibility": [_post(2, date="2026-05-01T00:00:00", pdf=10),
                                            _post(3, category=428, date="2025-01-01T00:00:00", pdf=10)],
    }
    api["media"] = {10: PDF_URL}
    items = DpeCsrProbe(Path("unused"), sleep=0).fetch_documents()
    assert [i["id"] for i in items] == [1, 2, 3]
    searched = {_query(r)["search"][0] for r in api["requests"] if "/central_documents" in r.full_url}
    assert searched == {"csr", "corporate social responsibility"}


def test_fetch_documents_orders_the_merged_result_newest_first(api) -> None:
    api["by_search"] = {
        "csr": [_post(1, date="2026-03-01T00:00:00", pdf=10), _post(2, date="2024-01-01T00:00:00", pdf=10)],
        "corporate social responsibility": [_post(3, date="2026-09-01T00:00:00", pdf=10),
                                            _post(4, date="2025-01-01T00:00:00", pdf=10)],
    }
    api["media"] = {10: PDF_URL}
    probe = DpeCsrProbe(Path("unused"), sleep=0)
    assert [i["id"] for i in probe.fetch_documents()] == [3, 1, 4, 2]
    # One page of the newest posts comes from the union of both searches.
    assert [i["id"] for i in probe.fetch_documents(per_page=2, max_pages=1)] == [3, 1]


def test_fetch_documents_pages_through_each_search(api) -> None:
    api["by_search"] = {
        "csr": [_post(i, date=f"2026-01-{30 - i:02d}T00:00:00", pdf=10) for i in range(1, 6)],
        "corporate social responsibility": [_post(i, category=428, date=f"2025-01-{30 - i:02d}T00:00:00", pdf=10)
                                            for i in range(11, 14)],
    }
    api["media"] = {10: PDF_URL}
    items = DpeCsrProbe(Path("unused"), sleep=0).fetch_documents(per_page=2)
    assert [i["id"] for i in items] == [1, 2, 3, 4, 5, 11, 12, 13]


def test_fetch_documents_stops_each_search_at_max_pages(api) -> None:
    api["by_search"] = {"csr": [_post(i, pdf=10) for i in range(1, 7)], "corporate social responsibility": []}
    api["media"] = {10: PDF_URL}
    items = DpeCsrProbe(Path("unused"), sleep=0).fetch_documents(per_page=2, max_pages=2)
    assert len(items) == 4


def test_probe_lists_each_merged_post_once_newest_first(api, tmp_path) -> None:
    api["by_search"] = {
        "csr": [_post(1, date="2026-06-01T00:00:00", pdf=10), _post(2, date="2025-05-01T00:00:00", pdf=10)],
        "corporate social responsibility": [_post(3, category=428, date="2026-01-01T00:00:00", pdf=10),
                                            _post(1, date="2026-06-01T00:00:00", pdf=10)],
    }
    api["media"] = {10: PDF_URL}
    records = DpeCsrProbe(tmp_path, sleep=0).probe(dry_run=True)
    assert [r["key"] for r in records] == ["DPE_CSR|1", "DPE_CSR|3", "DPE_CSR|2"]
