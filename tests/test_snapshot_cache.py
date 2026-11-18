"""Snapshot adapters must fetch current bytes when a session has an HTTP cache."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from commoner_probe import http_client as hc
from commoner_probe.arcgis_rest_query import query_layer
from commoner_probe.datamart_file_api import fetch_file, list_path
from commoner_probe.portal_page import fetch_page
from commoner_probe.sec_edgar_submissions import fetch_submissions


class Response:
    status_code = 200
    headers = {"Content-Type": "text/html"}

    def __init__(self, body: bytes):
        self.body = body

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=16384):
        yield self.body


class CachingSession:
    def __init__(self, stale: bytes, fresh: bytes):
        self.stale = stale
        self.fresh = fresh
        self.disabled = False
        self.fetches = 0

    @contextmanager
    def cache_disabled(self):
        self.disabled = True
        try:
            yield
        finally:
            self.disabled = False

    def get(self, url, **kwargs):
        if self.disabled:
            self.fetches += 1
            return Response(self.fresh)
        return Response(self.stale)

    def request(self, method, url, **kwargs):
        return self.get(url, **kwargs)


@pytest.mark.parametrize(
    ("run", "stale", "fresh", "expected"),
    [
        (
            lambda session, out: list_path(session, "air"),
            b'"Path not found."',
            b'{"path_contents": []}',
            [],
        ),
        (
            lambda session, out: Path(fetch_file(session, "air/file.zip", out, max_bytes=100)["dest"]).read_bytes(),
            b"old file",
            b"new file",
            b"new file",
        ),
        (
            lambda session, out: query_layer(session, "https://example.org/FeatureServer/0", out_dir=out)[
                "features"
            ],
            b'{"type":"FeatureCollection","features":[{"id":"old"}]}',
            b'{"type":"FeatureCollection","features":[{"id":"new"}]}',
            [{"id": "new"}],
        ),
        (
            lambda session, out: fetch_submissions(session, "49938", out, contact="contact@example.org")[
                "entity_name"
            ],
            b'{"name":"OLD"}',
            b'{"name":"NEW"}',
            "NEW",
        ),
        (
            lambda session, out: Path(fetch_page(session, "https://example.org/page", out, expect="html")[
                "dest"
            ]).read_bytes(),
            b"<html>old</html>",
            b"<html>new</html>",
            b"<html>new</html>",
        ),
    ],
)
def test_snapshot_requests_bypass_cache(tmp_path, run, stale, fresh, expected):
    session = CachingSession(stale, fresh)
    assert run(session, tmp_path) == expected
    assert session.fetches == 1
    assert session.disabled is False


def test_cli_session_wrapper_bypasses_its_underlying_cache(tmp_path, monkeypatch):
    class AllowAll:
        def can_fetch(self, user_agent, url):
            return True

    base = CachingSession(b"<html>old</html>", b"<html>new</html>")
    base.headers = {}
    monkeypatch.setattr(hc, "_make_base_session", lambda: base)
    monkeypatch.setattr(hc, "is_safe_url", lambda url: True)
    monkeypatch.setattr(hc, "_get_robot_parser", lambda url, user_agent: AllowAll())
    session = hc.make_session(rate_limit_sec=0)

    record = fetch_page(session, "https://example.org/page", tmp_path, expect="html")

    assert Path(record["dest"]).read_bytes() == b"<html>new</html>"
    assert base.fetches == 1
