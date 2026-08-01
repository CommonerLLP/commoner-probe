"""Tests for Internet Archive provenance capture.

Fixtures mirror the real CDX contract: row 0 is a header naming the requested
`fl` fields, and `limit=-1` puts the most recent capture last.

The governing rule for this module is that snapshotting is best-effort — a
third-party outage must never stop an acquisition — so most of these tests are
about what happens when the Internet Archive does NOT cooperate.

No network.
"""

from __future__ import annotations

from commoner_probe import wayback
from commoner_probe import wayback as wb

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


class SequencedCdxSession(FakeSession):
    """Serves a different CDX payload per call, so a save can land mid-run."""

    def __init__(self, *payloads, save_status=200):
        super().__init__(cdx=payloads[0], save_status=save_status)
        self._payloads = list(payloads)
        self._n = 0

    def get(self, url, params=None, timeout=None, **kwargs):
        if url == wayback.CDX_API:
            self.cdx = self._payloads[min(self._n, len(self._payloads) - 1)]
            self._n += 1
        return super().get(url, params=params, timeout=timeout, **kwargs)


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
        s = SequencedCdxSession(
            _cdx(["20240101000000", URL, "200", "OLDDIGEST"]),
            _cdx(["20240101000000", URL, "200", "OLDDIGEST"], ["20260720035455", URL, "200", DIGEST]),
        )
        f = wayback.snapshot_fields(URL, session=s)
        assert f["wayback_status"] == "captured"
        assert f["wayback_timestamp"] == "20260720035455"
        assert f["wayback_digest"] == DIGEST
        assert f["wayback_url"].endswith(URL)
        assert set(f) == set(wayback.WAYBACK_FIELDS)

    def test_captured_when_the_url_had_never_been_archived(self):
        s = SequencedCdxSession(_cdx(), _cdx(["20260720035455", URL, "200", DIGEST]))
        assert wayback.snapshot_fields(URL, session=s)["wayback_status"] == "captured"

    def test_save_pending_does_not_claim_a_pre_existing_capture(self):
        """SPN2 queues. The capture still on the index is not this run's."""
        s = FakeSession(cdx=_cdx(["20240101000000", URL, "200", "OLDDIGEST"]))
        f = wayback.snapshot_fields(URL, session=s)
        assert f["wayback_status"] == "save-pending"
        assert f["wayback_timestamp"] == "20240101000000"

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

    def test_cdx_outage_is_unavailable_not_unarchived(self):
        """A read-only check during a CDX 503 must not assert "never archived"."""
        s = FakeSession(cdx=None, cdx_status=503)
        assert wayback.snapshot_fields(URL, session=s, save=False)["wayback_status"] == "unavailable"
        assert not any(c.startswith(wayback.SAVE_BASE) for c in s.calls)

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


# --- attach_snapshot: the probe wiring point (acceptance 2) ---

def test_attach_snapshot_merges_provenance_into_a_record(monkeypatch):
    monkeypatch.setattr(
        wb,
        "snapshot_fields",
        lambda url, **kw: {
            "wayback_url": f"https://web.archive.org/web/20260101000000/{url}",
            "wayback_timestamp": "20260101000000",
            "wayback_digest": "ABC",
            "wayback_status": "existing",
        },
    )
    record = {"key": "K", "url": "https://dae.gov.in/x.pdf", "status": "downloaded"}
    out = wb.attach_snapshot(record)
    assert out is record  # merged in place, not a copy
    assert record["wayback_digest"] == "ABC"
    assert record["status"] == "downloaded"  # nothing pre-existing is disturbed


def test_attach_snapshot_defaults_to_read_only():
    """save=False by default: acquiring a file must not write to a public archive."""
    seen = {}

    def fake_snapshot_fields(url, *, save=True, session=None, timeout=None):
        seen["save"] = save
        return dict.fromkeys(wb.WAYBACK_FIELDS)

    original = wb.snapshot_fields
    wb.snapshot_fields = fake_snapshot_fields
    try:
        wb.attach_snapshot({"url": "https://example.gov.in/a.pdf"})
    finally:
        wb.snapshot_fields = original
    assert seen["save"] is False


def test_attach_snapshot_leaves_a_urlless_record_untouched():
    """No URL means 'not checked', which must not look like 'checked and absent'."""
    record = {"key": "K", "status": "error"}
    assert wb.attach_snapshot(record) == {"key": "K", "status": "error"}
    assert not any(f in record for f in wb.WAYBACK_FIELDS)


# --- recheck: an unreachable index is not "nothing to compare" ---

class _CdxResponse:
    def __init__(self, rows=None, *, boom=False):
        self._rows = rows
        self._boom = boom

    def raise_for_status(self):
        if self._boom:
            raise RuntimeError("HTTP 503 Service Unavailable")

    def json(self):
        return self._rows


def _session_returning(resp):
    class S:
        def get(self, *a, **k):
            return resp
    return S()


_HEADER = ["timestamp", "original", "statuscode", "digest"]
_ROW = ["20260723223948", "http://www.rbi.org.in/", "200", "D5TMUA5FXKSPISRTQSHATWON3YG47K2Q"]


def test_recheck_same_digest_is_unchanged():
    s = _session_returning(_CdxResponse([_HEADER, _ROW]))
    assert wb.recheck("https://www.rbi.org.in/", _ROW[3], session=s) == {
        "changed": False, "reason": "unchanged",
    }


def test_recheck_older_digest_is_changed():
    """The live 2026-07-26 case: a 2020 capture's digest vs the newest one."""
    s = _session_returning(_CdxResponse([_HEADER, _ROW]))
    out = wb.recheck("https://www.rbi.org.in/", "QAGC3DECGYDNCJRSQFGEOWBBCYSTEDWG", session=s)
    assert out == {"changed": True, "reason": "changed"}


def test_recheck_distinguishes_a_503_from_an_unarchived_url():
    """The defect this function exists for.

    IA returned 503 to back-to-back CDX calls on 2026-07-26, and the boolean
    form reports that identically to "never archived" — so a re-check pipeline
    reads an outage as "no change to see" and quietly stops detecting change.
    """
    down = _session_returning(_CdxResponse(boom=True))
    never = _session_returning(_CdxResponse([_HEADER]))  # header only, no captures

    assert wb.recheck("https://x.gov.in/", "ABC", session=down)["reason"] == "index-unavailable"
    assert wb.recheck("https://x.gov.in/", "ABC", session=never)["reason"] == "never-archived"
    # Both collapse to the same answer in the boolean form — hence recheck().
    assert wb.changed_since("https://x.gov.in/", "ABC", session=down) is None
    assert wb.changed_since("https://x.gov.in/", "ABC", session=never) is None


def test_recheck_no_recorded_digest_is_its_own_reason():
    s = _session_returning(_CdxResponse([_HEADER, _ROW]))
    assert wb.recheck("https://x.gov.in/", "", session=s) == {
        "changed": None, "reason": "no-recorded-digest",
    }


def test_every_recheck_reason_is_declared():
    s = _session_returning(_CdxResponse([_HEADER, _ROW]))
    assert wb.recheck("https://x.gov.in/", "ABC", session=s)["reason"] in wb.RECHECK_REASONS
