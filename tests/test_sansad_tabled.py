"""Tests for the tabled-papers / title-search mode (commoner-probe sansad tabled).

Fixture payloads mirror the live eLibrary DSpace 7 contract: generic
``discover/search/objects`` queries (no Q&A category facet), the
``core/items/{uuid}/bundles`` -> ORIGINAL -> bitstreams walk, and
``core/bitstreams/{uuid}/content`` downloads. No network.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from commoner_probe.cli import build_parser
from commoner_probe.sansad import SansadProbe, geo_fence_hint
from commoner_probe.topics import TopicProfile

PDF_BYTES = b"%PDF-1.4 tabled paper fixture " + b"x" * 2000


class FakeResponse:
    def __init__(self, payload=None, *, status=200, body: bytes | None = None):
        self._payload = payload
        self._body = body
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=16384):
        body = self._body or b""
        for i in range(0, len(body), chunk_size):
            yield body[i : i + chunk_size]


def _item(uuid: str, title: str, date: str = "1994-08-25") -> dict:
    return {"_embedded": {"indexableObject": {
        "uuid": uuid,
        "handle": f"123456789/{uuid}",
        "metadata": {
            "dc.title": [{"value": title}],
            "dc.date.issued": [{"value": date}],
            "dc.identifier.uri": [{"value": f"http://hdl.handle.net/123456789/{uuid}"}],
        },
    }}}


class FakeTabledSession:
    """Routes discover search, bundle walks, and bitstream content."""

    def __init__(self, items: list[dict], *, bitstreams_by_uuid: dict[str, list[dict]] | None = None, page_size: int = 2):
        self.items = items
        self.bitstreams_by_uuid = bitstreams_by_uuid or {}
        self.page_size = page_size
        self.search_urls: list[str] = []

    def get(self, url, **kwargs):
        if "/discover/search/objects" in url:
            self.search_urls.append(url)
            q = parse_qs(urlparse(url).query)
            page = int(q.get("page", ["0"])[0])
            chunk = self.items[page * self.page_size:(page + 1) * self.page_size]
            return FakeResponse({"_embedded": {"searchResult": {
                "_embedded": {"objects": chunk},
                "page": {
                    "number": page,
                    "totalPages": math.ceil(len(self.items) / self.page_size),
                    "totalElements": len(self.items),
                },
            }}})
        if "/core/items/" in url and url.endswith("/bundles"):
            item_uuid = url.rsplit("/", 2)[-2]
            return FakeResponse({"_embedded": {"bundles": [{
                "name": "ORIGINAL",
                "_links": {"bitstreams": {"href": f"https://elibrary.sansad.in/server/api/core/bundles/{item_uuid}/bitstreams"}},
            }]}})
        if "/core/bundles/" in url and url.endswith("/bitstreams"):
            item_uuid = url.rsplit("/", 2)[-2]
            return FakeResponse({"_embedded": {"bitstreams": self.bitstreams_by_uuid.get(item_uuid, [])}})
        if url.endswith("/content"):
            return FakeResponse(body=PDF_BYTES)
        raise AssertionError(f"unrouted url: {url}")


def _bitstream(uuid: str, name: str) -> dict:
    return {
        "uuid": uuid,
        "name": name,
        "_links": {"content": {"href": f"https://elibrary.sansad.in/server/api/core/bitstreams/{uuid}/content"}},
    }


def _probe(out: Path, session) -> SansadProbe:
    topic = TopicProfile(
        name="tabled-papers", description="", search_groups=[],
        lok_sabha_ministries=[], rajya_sabha_ministry_likes=[],
    )
    probe = SansadProbe(topic, out, sleep=0)
    probe.session = session
    return probe


def _manifest(out: Path) -> list[dict]:
    path = out / "manifest.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_search_titles_is_title_scoped_and_category_free(tmp_path):
    session = FakeTabledSession([_item("aa11", "DPL Review 1994-95")])
    probe = _probe(tmp_path, session)
    items = list(probe.search_titles('"Delhi Public Library" review'))
    assert len(items) == 1
    q = parse_qs(urlparse(session.search_urls[0]).query)
    assert q["query"] == ['title:("Delhi Public Library" review)']
    assert "f.category" not in q


def test_search_titles_full_text_and_pagination(tmp_path):
    items = [_item(f"aa{i:02d}", f"Paper {i}") for i in range(5)]
    session = FakeTabledSession(items, page_size=2)
    probe = _probe(tmp_path, session)
    got = list(probe.search_titles("library", title_scoped=False))
    assert len(got) == 5
    q = parse_qs(urlparse(session.search_urls[0]).query)
    assert q["query"] == ["library"]
    assert len(session.search_urls) == 3  # 5 items / page_size 2


def test_probe_tabled_downloads_all_pdf_bitstreams(tmp_path):
    session = FakeTabledSession(
        [_item("aa11", "Review of the Working of the Delhi Public Library 1994-95")],
        bitstreams_by_uuid={"aa11": [
            _bitstream("bb22", "part1.pdf"),
            _bitstream("cc33", "part2.PDF"),
            _bitstream("dd44", "thumbnail.jpg"),
        ]},
    )
    probe = _probe(tmp_path, session)
    added = probe.probe_tabled(query='"Delhi Public Library"')
    assert added == 1
    (rec,) = _manifest(tmp_path)
    assert rec["key"] == "TABLED|aa11"
    assert rec["kind"] == "tabled_paper"
    assert rec["status"] == "downloaded"
    assert len(rec["downloads"]) == 2
    expected_sha = hashlib.sha256(PDF_BYTES).hexdigest()
    for row in rec["downloads"]:
        assert row["sha256"] == expected_sha
        assert row["bytes"] == len(PDF_BYTES)
        dest = tmp_path / row["dest"]
        assert dest.exists()
        assert dest.read_bytes() == PDF_BYTES
        assert str(dest).startswith(str(tmp_path / "pdfs" / "tabled"))


def test_probe_tabled_title_filter(tmp_path):
    session = FakeTabledSession(
        [
            _item("aa11", "Annual Report of the Delhi Public Library"),
            _item("bb22", "Starred Question on Libraries"),
        ],
        bitstreams_by_uuid={"aa11": [_bitstream("cc33", "report.pdf")]},
    )
    probe = _probe(tmp_path, session)
    added = probe.probe_tabled(query="library", title_filter=r"review|annual report|account")
    assert added == 1
    (rec,) = _manifest(tmp_path)
    assert rec["uuid"] == "aa11"


def test_probe_tabled_metadata_only_is_not_terminal(tmp_path):
    """A --no-download pass then a downloads-enabled rerun must still fetch."""
    bitstreams = {"aa11": [_bitstream("bb22", "report.pdf")]}
    session = FakeTabledSession([_item("aa11", "DPL Review")], bitstreams_by_uuid=bitstreams)
    probe = _probe(tmp_path, session)
    assert probe.probe_tabled(query="dpl", download=False) == 1
    assert _manifest(tmp_path)[-1]["status"] == "metadata_only"

    probe2 = _probe(tmp_path, FakeTabledSession([_item("aa11", "DPL Review")], bitstreams_by_uuid=bitstreams))
    assert probe2.probe_tabled(query="dpl", download=True) == 1
    assert _manifest(tmp_path)[-1]["status"] == "downloaded"

    # downloaded IS terminal: a third run adds nothing
    probe3 = _probe(tmp_path, FakeTabledSession([_item("aa11", "DPL Review")], bitstreams_by_uuid=bitstreams))
    assert probe3.probe_tabled(query="dpl", download=True) == 0


def test_probe_tabled_metadata_only_rerun_without_download_skips(tmp_path):
    session = FakeTabledSession([_item("aa11", "DPL Review")])
    probe = _probe(tmp_path, session)
    assert probe.probe_tabled(query="dpl", download=False) == 1
    probe2 = _probe(tmp_path, FakeTabledSession([_item("aa11", "DPL Review")]))
    assert probe2.probe_tabled(query="dpl", download=False) == 0


def test_probe_tabled_record_validates_against_schema(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    session = FakeTabledSession(
        [_item("aa11", "DPL Review")],
        bitstreams_by_uuid={"aa11": [_bitstream("bb22", "report.pdf")]},
    )
    probe = _probe(tmp_path, session)
    probe.probe_tabled(query="dpl")
    schema = json.loads(
        (Path(__file__).resolve().parent.parent / "commoner_probe" / "schemas" / "manifest_tabled_paper.schema.json")
        .read_text(encoding="utf-8")
    )
    for rec in _manifest(tmp_path):
        jsonschema.validate(rec, schema)


def test_geo_fence_hint_detects_dns_and_ssrf_shapes(monkeypatch):
    import socket as socket_mod

    from commoner_probe import sansad as sansad_mod

    assert geo_fence_hint(OSError("getaddrinfo failed")) is not None

    def _raise(*args, **kwargs):
        raise socket_mod.gaierror(8, "nodename nor servname provided")

    monkeypatch.setattr(sansad_mod.socket, "getaddrinfo", _raise)
    hint = geo_fence_hint(ValueError("URL rejected by SSRF guard: https://elibrary.sansad.in/x"))
    assert hint is not None
    assert "geo-fenced" in hint

    monkeypatch.setattr(sansad_mod.socket, "getaddrinfo", lambda *a, **k: [("ok",)])
    assert geo_fence_hint(ValueError("URL rejected by SSRF guard: https://elibrary.sansad.in/x")) is None
    assert geo_fence_hint(RuntimeError("HTTP 500")) is None


def test_probe_tabled_raises_clear_geo_block_message(tmp_path, monkeypatch):
    from commoner_probe import sansad as sansad_mod

    class GeoBlockedSession:
        def get(self, url, **kwargs):
            raise ValueError(f"URL rejected by SSRF guard: {url}")

    import socket as socket_mod

    def _raise(*args, **kwargs):
        raise socket_mod.gaierror(8, "nodename nor servname provided")

    monkeypatch.setattr(sansad_mod.socket, "getaddrinfo", _raise)
    probe = _probe(tmp_path, GeoBlockedSession())
    with pytest.raises(SystemExit) as exc_info:
        probe.probe_tabled(query="dpl")
    assert "geo-fenced" in str(exc_info.value)


def test_cli_tabled_subcommand_and_flat_sansad_coexist():
    parser = build_parser()
    args = parser.parse_args(["sansad", "tabled", "--query", "dpl review", "--out", "corpus"])
    assert args.func.__name__ == "sansad_tabled_cmd"
    assert args.query == "dpl review"

    flat = parser.parse_args(["sansad", "--member", "Test MP", "--out", "corpus"])
    assert flat.func.__name__ == "sansad_cmd"
    assert flat.member == "Test MP"
