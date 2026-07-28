"""Tests for the Wayback CDX capture-list adapter.

Fixtures mirror the live API (contract re-verified 2026-07-28 against
web.archive.org, not taken from the earlier session's note).

The findings they encode, each of which shaped the code:

    "No captures" is HTTP 200 with a body of `[]`. An unreachable index is a
    5xx, a reset connection, or a read timeout. The API makes these easy to
    confuse and they are opposite facts — the SAME query returned `200 []` and
    then `503` three seconds apart, so the index must never be read as evidence
    about the source.

    resumeKey pagination ends a batch with a blank row followed by a
    one-element row holding the key. Neither is a capture.

    The default column order is NOT the order this module wants (the API leads
    with `urlkey` and puts `mimetype` before `statuscode`), so `fl` is always
    sent explicitly. A positional read of a defaulted response mis-assigns
    every column.

No network.
"""

from __future__ import annotations

import json

import pytest

from commoner_probe import wayback
from commoner_probe.wayback import (
    CAPTURE_FIELDS,
    IndexUnavailable,
    WaybackCaptureProbe,
    _iso_from_cdx,
    capture_query,
    iter_captures,
)

HEADER = list(CAPTURE_FIELDS)


def _row(ts, digest, *, url="http://mospi.gov.in:80/", status="200", mime="text/html", length="9965"):
    return [ts, url, status, digest, mime, length]


class FakeResponse:
    def __init__(self, payload, status=200):
        self.text = payload if isinstance(payload, str) else json.dumps(payload)
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Serves a scripted sequence of CDX responses, one per call."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def get(self, url, params=None, timeout=None, **kwargs):
        self.calls.append(dict(params or {}))
        payload = self._responses[min(len(self.calls) - 1, len(self._responses) - 1)]
        if isinstance(payload, Exception):
            raise payload
        return FakeResponse(payload)


class TestCaptureQuery:
    def test_always_sends_explicit_field_order(self):
        """A defaulted response has a different column order entirely."""
        assert capture_query("x")["fl"] == ",".join(CAPTURE_FIELDS)

    def test_prefix_appends_the_wildcard(self):
        assert capture_query("mospi.gov.in", match_prefix=True)["url"] == "mospi.gov.in/*"
        assert capture_query("mospi.gov.in/", match_prefix=True)["url"] == "mospi.gov.in/*"

    def test_exact_mode_leaves_the_url_alone(self):
        assert capture_query("mospi.gov.in")["url"] == "mospi.gov.in"

    def test_optional_filters_are_omitted_when_unset(self):
        q = capture_query("x")
        for key in ("from", "to", "collapse", "filter", "resumeKey"):
            assert key not in q

    def test_filters_are_passed_through(self):
        q = capture_query("x", from_date="2015", to_date="2016", collapse_digest=True, only_ok=True)
        assert q["from"] == "2015" and q["to"] == "2016"
        assert q["collapse"] == "digest"
        assert q["filter"] == "statuscode:200"


class TestIterCaptures:
    def test_yields_one_dict_per_capture_with_a_citation_url(self):
        session = FakeSession([HEADER, _row("20060413232357", "AAA"), _row("20200701205014", "BBB")])
        caps = list(iter_captures("mospi.gov.in", session=session))
        assert [c["timestamp"] for c in caps] == ["20060413232357", "20200701205014"]
        assert caps[0]["snapshot_url"] == (
            "https://web.archive.org/web/20060413232357/http://mospi.gov.in:80/"
        )
        assert caps[0]["digest"] == "AAA"

    def test_no_captures_yields_nothing_and_does_not_raise(self):
        """HTTP 200 with `[]` is the real 'never archived' signal."""
        assert list(iter_captures("x", session=FakeSession([]))) == []

    def test_header_only_yields_nothing(self):
        assert list(iter_captures("x", session=FakeSession([HEADER]))) == []

    def test_follows_the_resume_key_across_batches(self):
        first = [HEADER, _row("20060413232357", "AAA"), [], ["RESUME1"]]
        second = [HEADER, _row("20200701205014", "BBB")]
        session = FakeSession(first, second)
        caps = list(iter_captures("x", session=session, batch=1))
        assert [c["digest"] for c in caps] == ["AAA", "BBB"]
        assert "resumeKey" not in session.calls[0]
        assert session.calls[1]["resumeKey"] == "RESUME1"

    def test_resume_markers_are_never_emitted_as_captures(self):
        session = FakeSession([HEADER, _row("20060413232357", "AAA"), [], ["RESUME1"]], [HEADER])
        caps = list(iter_captures("x", session=session, batch=1))
        assert len(caps) == 1, "the blank row and the key row are markers, not captures"

    def test_max_records_stops_the_walk(self):
        session = FakeSession([HEADER] + [_row(f"2020070120501{i}", f"D{i}") for i in range(9)])
        assert len(list(iter_captures("x", session=session, max_records=4))) == 4

    def test_a_row_without_a_timestamp_is_skipped_not_emitted(self):
        session = FakeSession([HEADER, _row("", "AAA"), _row("20200701205014", "BBB")])
        assert [c["digest"] for c in iter_captures("x", session=session)] == ["BBB"]


class TestIndexUnavailable:
    def test_a_5xx_raises_rather_than_looking_like_no_captures(self):
        session = FakeSession(RuntimeError("HTTP 503"))
        with pytest.raises(IndexUnavailable, match="NOT evidence"):
            list(iter_captures("x", session=session, retries=1))

    def test_a_read_timeout_raises_too(self):
        session = FakeSession(TimeoutError("read timed out"))
        with pytest.raises(IndexUnavailable):
            list(iter_captures("x", session=session, retries=1))

    def test_it_retries_before_giving_up(self, monkeypatch):
        monkeypatch.setattr(wayback.time, "sleep", lambda _: None)
        session = FakeSession(RuntimeError("HTTP 503"))
        with pytest.raises(IndexUnavailable, match="after 3 attempts"):
            list(iter_captures("x", session=session, retries=3, backoff=0))
        assert len(session.calls) == 3

    def test_a_transient_failure_mid_walk_is_survived(self, monkeypatch):
        """Without retry, a flap truncates a capture history silently."""
        monkeypatch.setattr(wayback.time, "sleep", lambda _: None)

        class Flaky(FakeSession):
            def get(self, url, params=None, timeout=None, **kwargs):
                self.calls.append(dict(params or {}))
                if len(self.calls) == 1:
                    raise RuntimeError("HTTP 503")
                return FakeResponse([HEADER, _row("20200701205014", "BBB")])

        caps = list(iter_captures("x", session=Flaky(), retries=3, backoff=0))
        assert [c["digest"] for c in caps] == ["BBB"]

    def test_an_unparseable_body_is_an_index_problem_not_an_empty_result(self):
        session = FakeSession("<html>503 Service Unavailable</html>")
        with pytest.raises(IndexUnavailable, match="unparseable"):
            list(iter_captures("x", session=session, retries=1))


class TestProbe:
    def _probe(self, tmp_path, session):
        probe = WaybackCaptureProbe(tmp_path, sleep=0, session=session)
        return probe

    def test_writes_one_record_per_capture(self, tmp_path):
        session = FakeSession([HEADER, _row("20060413232357", "AAA"), _row("20200701205014", "BBB")])
        records = list(self._probe(tmp_path, session).probe(url="mospi.gov.in"))
        assert len(records) == 2
        rows = [json.loads(x) for x in (tmp_path / "manifest.jsonl").read_text().splitlines()]
        assert rows[0]["kind"] == "wayback_capture"
        assert rows[0]["key"] == "WAYBACK|http://mospi.gov.in:80/|20060413232357"
        assert rows[0]["captured_at"] == "2006-04-13T23:23:57Z"
        assert rows[0]["length"] == 9965
        assert rows[0]["status"] == "metadata_only"

    def test_rerun_appends_nothing(self, tmp_path):
        payload = [HEADER, _row("20060413232357", "AAA")]
        list(self._probe(tmp_path, FakeSession(payload)).probe(url="mospi.gov.in"))
        again = list(self._probe(tmp_path, FakeSession(payload)).probe(url="mospi.gov.in"))
        assert again == []

    def test_a_new_capture_extends_the_history(self, tmp_path):
        list(self._probe(tmp_path, FakeSession([HEADER, _row("20060413232357", "AAA")])).probe(url="u"))
        session = FakeSession([HEADER, _row("20060413232357", "AAA"), _row("20200701205014", "BBB")])
        records = list(self._probe(tmp_path, session).probe(url="u"))
        assert [r["digest"] for r in records] == ["BBB"]

    def test_dry_run_writes_nothing(self, tmp_path):
        session = FakeSession([HEADER, _row("20060413232357", "AAA")])
        records = list(self._probe(tmp_path, session).probe(url="u", dry_run=True))
        assert [r["status"] for r in records] == ["dry_run"]
        assert not (tmp_path / "manifest.jsonl").exists()

    def test_a_malformed_length_becomes_null_not_a_crash(self, tmp_path):
        session = FakeSession([HEADER, _row("20060413232357", "AAA", length="-")])
        assert list(self._probe(tmp_path, session).probe(url="u"))[0]["length"] is None


def test_iso_from_cdx():
    assert _iso_from_cdx("20260720035455") == "2026-07-20T03:54:55Z"
    assert _iso_from_cdx("2026") is None
    assert _iso_from_cdx("notatimestamp!") is None


def test_schema_bundled_and_validates(tmp_path):
    pytest.importorskip("jsonschema")
    from commoner_probe import schemas
    from commoner_probe.validate import validate_corpus

    assert "manifest_wayback_capture" in schemas.list_all()
    session = FakeSession([HEADER, _row("20060413232357", "AAA"), _row("20200701205014", "BBB")])
    assert list(WaybackCaptureProbe(tmp_path, sleep=0, session=session).probe(url="mospi.gov.in"))
    assert validate_corpus(tmp_path, log=lambda _: None)


def test_corpus_streams_wayback_captures(tmp_path):
    from commoner_probe.corpus import Corpus

    session = FakeSession([HEADER, _row("20060413232357", "AAA")])
    list(WaybackCaptureProbe(tmp_path, sleep=0, session=session).probe(url="mospi.gov.in"))
    rows = list(Corpus(tmp_path).manifest_wayback_captures())
    assert len(rows) == 1
    assert rows[0].digest == "AAA"
    assert rows[0].snapshot_url.startswith("https://web.archive.org/web/20060413232357/")
