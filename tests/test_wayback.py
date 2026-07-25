"""Tests for Internet Archive provenance capture (REQ-0036).

Fixtures mirror the real CDX contract: row 0 is a header naming the requested
`fl` fields, and `limit=-1` puts the most recent capture last.

The governing rule for this module is that snapshotting is best-effort — a
third-party outage must never stop an acquisition — so most of these tests are
about what happens when the Internet Archive does NOT cooperate.

No network.
"""

from __future__ import annotations

from commoner_probe import wayback

URL = "https://cag.gov.in/state-accounts-report"
DIGEST = "YWJDEFG3HIJKLMNOPQRSTUVWXYZ23456"

CDX_HEADER = ["timestamp", "original", "statuscode", "digest"]


class FakeResponse:
    def __init__(self, payload=None, *, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Routes the CDX index and the Save Page Now trigger."""

    def __init__(self, *, cdx=None, cdx_status=200, save_status=200):
        self.cdx = cdx
        self.cdx_status = cdx_status
        self.save_status = save_status
        self.calls: list[str] = []

    def get(self, url, params=None, timeout=None, **kwargs):
        self.calls.append(url)
        if url.startswith(wayback.SAVE_BASE):
            return FakeResponse(b"", status=self.save_status)
        assert url == wayback.CDX_API, f"unrouted url: {url}"
        return FakeResponse(self.cdx, status=self.cdx_status)


class ExplodingSession:
    """Every call raises — the Internet Archive being unreachable."""

    def get(self, *a, **k):
        raise ConnectionError("web.archive.org unreachable")


def _cdx(*rows):
    return [CDX_HEADER, *rows]


class TestLatestCapture:
    def test_returns_the_most_recent_row(self):
        s = FakeSession(cdx=_cdx(
            ["20240101000000", URL, "200", "OLDDIGEST"],
            ["20260720035455", URL, "200", DIGEST],
        ))
        cap = wayback.latest_capture(URL, session=s)
        assert cap["timestamp"] == "20260720035455"
        assert cap["digest"] == DIGEST
        assert cap["snapshot_url"] == f"https://web.archive.org/web/20260720035455/{URL}"

    def test_header_only_means_never_archived(self):
        assert wayback.latest_capture(URL, session=FakeSession(cdx=_cdx())) is None

    def test_empty_body_is_not_a_crash(self):
        assert wayback.latest_capture(URL, session=FakeSession(cdx=[])) is None

    def test_http_error_returns_none(self):
        assert wayback.latest_capture(URL, session=FakeSession(cdx=None, cdx_status=503)) is None

    def test_unreachable_archive_returns_none(self):
        assert wayback.latest_capture(URL, session=ExplodingSession()) is None


class TestRequestSave:
    def test_accepted(self):
        assert wayback.request_save(URL, session=FakeSession()) is True

    def test_throttled_is_false_not_an_exception(self):
        assert wayback.request_save(URL, session=FakeSession(save_status=429)) is False

    def test_unreachable_is_false(self):
        assert wayback.request_save(URL, session=ExplodingSession()) is False


class TestSnapshotFields:
    def test_captured_when_save_lands(self):
        s = FakeSession(cdx=_cdx(["20260720035455", URL, "200", DIGEST]))
        f = wayback.snapshot_fields(URL, session=s)
        assert f["wayback_status"] == "captured"
        assert f["wayback_timestamp"] == "20260720035455"
        assert f["wayback_digest"] == DIGEST
        assert f["wayback_url"].endswith(URL)
        assert set(f) == set(wayback.WAYBACK_FIELDS)

    def test_existing_when_save_not_requested(self):
        s = FakeSession(cdx=_cdx(["20260720035455", URL, "200", DIGEST]))
        f = wayback.snapshot_fields(URL, session=s, save=False)
        assert f["wayback_status"] == "existing"
        assert not any(c.startswith(wayback.SAVE_BASE) for c in s.calls), "save=False must not hit SPN2"

    def test_unarchived_when_save_accepted_but_not_yet_indexed(self):
        f = wayback.snapshot_fields(URL, session=FakeSession(cdx=_cdx()))
        assert f["wayback_status"] == "unarchived"
        assert f["wayback_url"] is None

    def test_unavailable_when_save_itself_failed(self):
        s = FakeSession(cdx=_cdx(), save_status=429)
        assert wayback.snapshot_fields(URL, session=s)["wayback_status"] == "unavailable"

    def test_total_outage_still_returns_usable_fields(self):
        """The whole point: IA being down must not break an acquisition."""
        f = wayback.snapshot_fields(URL, session=ExplodingSession())
        assert set(f) == set(wayback.WAYBACK_FIELDS)
        assert f["wayback_status"] == "unavailable"


class TestChangedSince:
    def test_same_digest_is_unchanged(self):
        s = FakeSession(cdx=_cdx(["20260720035455", URL, "200", DIGEST]))
        assert wayback.changed_since(URL, DIGEST, session=s) is False

    def test_different_digest_is_changed(self):
        s = FakeSession(cdx=_cdx(["20260720035455", URL, "200", "NEWDIGEST"]))
        assert wayback.changed_since(URL, DIGEST, session=s) is True

    def test_no_captures_is_none_not_false(self):
        """Unknown must stay distinguishable from unchanged."""
        assert wayback.changed_since(URL, DIGEST, session=FakeSession(cdx=_cdx())) is None

    def test_no_recorded_digest_is_none(self):
        assert wayback.changed_since(URL, "", session=FakeSession()) is None
