"""Tests for Internet Archive document recovery.

Each test names the defect it prevents. The measurements come from the
theright2read recovery of the Samagra Shiksha Project Approval Board minutes
(391 URLs, 2026-08-04), recorded in the request.

Fixtures mirror the real CDX contract: row 0 is a header naming the requested
``fl`` fields, and a resumeKey response ends with a blank row then a one-element
row holding the key.

No network. Every test injects a session.
"""

from __future__ import annotations

import json

import pytest

from commoner_probe import wayback_recover as wr

HOST = "dsel.education.gov.in"
BIG = f"https://{HOST}/sites/default/files/AN_PAB_2018_2019.pdf"
SMALL = f"https://{HOST}/sites/default/files/GO_2019.pdf"
PAGE = f"https://{HOST}/en/pab-minutes"

CDX_HEADER = ["original", "timestamp", "statuscode", "length"]


def pdf(size: int) -> bytes:
    """A complete PDF of *size* bytes."""
    head = b"%PDF-1.4\n"
    tail = b"\n%%EOF\n"
    return head + b"x" * (size - len(head) - len(tail)) + tail


def truncated_pdf(size: int) -> bytes:
    """A PDF cut off mid-stream: magic bytes present, no %%EOF."""
    return b"%PDF-1.4\n" + b"x" * (size - 9)


class FakeResponse:
    def __init__(self, body: bytes, *, status: int = 200):
        self.status_code = status
        self._body = body
        self.text = body.decode("utf-8", errors="replace")

    @property
    def content(self) -> bytes:
        return self._body

    def iter_content(self, chunk_size: int = 16384):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i : i + chunk_size]

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Serves one CDX payload and a body per replay URL.

    ``replay`` maps a capture timestamp to the bytes that capture returns, so a
    test can make the newest capture a fragment and an older one whole.
    """

    def __init__(self, *, cdx=None, replay=None, replay_status=None):
        self.cdx = cdx if cdx is not None else [CDX_HEADER]
        self.replay = replay or {}
        self.replay_status = replay_status or {}
        self.index_calls: list[dict] = []
        self.replay_calls: list[str] = []

    def get(self, url, params=None, timeout=None, **kwargs):
        if url == wr.CDX_API:
            self.index_calls.append(dict(params or {}))
            return FakeResponse(json.dumps(self.cdx).encode("utf-8"))
        self.replay_calls.append(url)
        timestamp = url.split("/web/")[1].split("id_/")[0]
        status = self.replay_status.get(timestamp, 200)
        return FakeResponse(self.replay.get(timestamp, b""), status=status)


class ThrottlingSession(FakeSession):
    """Refuses the replay endpoint *n* times, then serves the body.

    The measured failure: after ~19 successful downloads the archive began
    answering 429, the refusals raised instantly, and a naive ``except:
    continue`` recorded 365 present files as incomplete.
    """

    def __init__(self, *, refusals: int, **kwargs):
        super().__init__(**kwargs)
        self.refusals = refusals

    def get(self, url, params=None, timeout=None, **kwargs):
        if url != wr.CDX_API and self.refusals > 0:
            self.refusals -= 1
            self.replay_calls.append(url)
            return FakeResponse(b"Too Many Requests", status=429)
        return super().get(url, params=params, timeout=timeout, **kwargs)


def cdx(*rows) -> list:
    return [CDX_HEADER, *[list(r) for r in rows]]


def no_sleep(_seconds: float) -> None:
    return None


# ---------------------------------------------------------------------------
# 1. Largest complete capture, not the newest
# ---------------------------------------------------------------------------

def test_newest_capture_truncated_at_5mib_is_not_chosen():
    """The measured case: a 14,561,108-byte capture and a 5,242,957-byte newest.

    Four of the first eleven files recovered by newest were silently truncated.
    """
    captures = wr.parse_captures(
        cdx(
            (BIG, "20220121062121", "200", "14561108"),
            (BIG, "20231015155748", "200", "14561045"),
            (BIG, "20250517032756", "200", "5242957"),
        )
    )
    order = wr.rank_captures(captures[wr.canonical_key(BIG)], prefer="largest")
    assert [c.length for c in order] == [14561108, 14561045, 5242957]
    assert order[0].timestamp == "20220121062121"


def test_prefer_newest_keeps_the_old_semantics():
    captures = wr.parse_captures(
        cdx(
            (BIG, "20220121062121", "200", "14561108"),
            (BIG, "20250517032756", "200", "5242957"),
        )
    )
    order = wr.rank_captures(captures[wr.canonical_key(BIG)], prefer="newest")
    assert order[0].timestamp == "20250517032756"


def test_capture_of_unknown_length_ranks_last_but_is_still_tried():
    """CDX writes ``-`` for length. Ranking it as 0 is fine; dropping it is not."""
    captures = wr.parse_captures(
        cdx(
            (BIG, "20220121062121", "200", "-"),
            (BIG, "20231015155748", "200", "14561045"),
        )
    )
    order = wr.rank_captures(captures[wr.canonical_key(BIG)], prefer="largest")
    assert [c.timestamp for c in order] == ["20231015155748", "20220121062121"]


# ---------------------------------------------------------------------------
# 2. Verify the artefact, then fall back to the next-largest
# ---------------------------------------------------------------------------

def test_pdf_missing_eof_is_not_accepted_as_complete():
    assert wr.verify_pdf(pdf(2048)) is True
    assert wr.verify_pdf(truncated_pdf(2048)) is False
    assert wr.verify_pdf(b"<html>404</html>") is False


def test_largest_capture_that_fails_verification_falls_back(tmp_path):
    """Byte-length ordering is a heuristic. The check is what makes it safe."""
    session = FakeSession(
        cdx=cdx(
            (BIG, "20220121062121", "200", "4000"),
            (BIG, "20231015155748", "200", "3000"),
        ),
        replay={"20220121062121": truncated_pdf(4000), "20231015155748": pdf(3000)},
    )
    rows = list(wr.recover(tmp_path, urls=[BIG], session=session, sleep_fn=no_sleep))
    assert [r["status"] for r in rows] == ["ok"]
    assert rows[0]["wayback_timestamp"] == "20231015155748"
    assert rows[0]["bytes"] == 3000
    assert "20220121062121" in session.replay_calls[0], "the largest must be tried first"


def test_every_capture_failing_verification_reports_unverified_not_ok(tmp_path):
    session = FakeSession(
        cdx=cdx((BIG, "20220121062121", "200", "4000")),
        replay={"20220121062121": truncated_pdf(4000)},
    )
    rows = list(wr.recover(tmp_path, urls=[BIG], session=session, sleep_fn=no_sleep))
    assert rows[0]["status"] == "unverified"
    assert rows[0]["local_file"] is None
    assert "verify" in (rows[0]["reason"] or "")


def test_verify_none_accepts_any_body(tmp_path):
    session = FakeSession(
        cdx=cdx((PAGE, "20220121062121", "200", "12")),
        replay={"20220121062121": b"<html>hi</html>"},
    )
    rows = list(wr.recover(tmp_path, urls=[PAGE], verify="none", session=session, sleep_fn=no_sleep))
    assert rows[0]["status"] == "ok"


def test_unknown_verifier_raises_rather_than_verifying_nothing():
    with pytest.raises(ValueError):
        wr.verifier_for("docx")


# ---------------------------------------------------------------------------
# 3. One index call per host, not one per URL
# ---------------------------------------------------------------------------

def test_a_list_of_urls_makes_one_index_call_per_host(tmp_path):
    """Per-URL concurrent CDX queries get throttled, and a throttled response
    comes back EMPTY: that reported 375 of 391 documents as "no capture" when
    every one was present.
    """
    session = FakeSession(
        cdx=cdx(
            (BIG, "20220121062121", "200", "3000"),
            (SMALL, "20220121062121", "200", "3000"),
        ),
        replay={"20220121062121": pdf(3000)},
    )
    rows = list(wr.recover(tmp_path, urls=[BIG, SMALL], session=session, sleep_fn=no_sleep))
    assert len(session.index_calls) == 1
    assert [r["status"] for r in rows] == ["ok", "ok"]


def test_host_query_asks_for_the_prefix_and_only_http_200():
    params = wr.index_query(HOST)
    assert params["url"] == f"{HOST}*"
    assert params["filter"] == "statuscode:200"
    assert params["fl"] == "original,timestamp,statuscode,length"


def test_host_mode_applies_the_filename_match(tmp_path):
    session = FakeSession(
        cdx=cdx(
            (BIG, "20220121062121", "200", "3000"),
            (PAGE, "20220121062121", "200", "10"),
        ),
        replay={"20220121062121": pdf(3000)},
    )
    rows = list(wr.recover(tmp_path, host=HOST, match=r"\.pdf$", session=session, sleep_fn=no_sleep))
    assert [r["source_url"] for r in rows] == [BIG]


def test_an_empty_index_for_a_requested_url_reports_no_capture(tmp_path):
    session = FakeSession(cdx=cdx((SMALL, "20220121062121", "200", "3000")))
    rows = list(wr.recover(tmp_path, urls=[BIG], session=session, sleep_fn=no_sleep))
    assert rows[0]["status"] == "no-capture"


def test_an_unreadable_index_raises_instead_of_reporting_no_capture(tmp_path):
    class DeadIndex(FakeSession):
        def get(self, url, params=None, timeout=None, **kwargs):
            if url == wr.CDX_API:
                return FakeResponse(b"", status=503)
            return super().get(url, params=params, timeout=timeout, **kwargs)

    with pytest.raises(wr.IndexUnavailable):
        list(wr.recover(tmp_path, urls=[BIG], session=DeadIndex(), retries=2, sleep_fn=no_sleep))


def test_an_index_body_of_the_wrong_shape_raises(tmp_path):
    class HtmlIndex(FakeSession):
        def get(self, url, params=None, timeout=None, **kwargs):
            if url == wr.CDX_API:
                return FakeResponse(b'{"error": "throttled"}')
            return super().get(url, params=params, timeout=timeout, **kwargs)

    with pytest.raises(wr.IndexUnavailable):
        list(wr.recover(tmp_path, urls=[BIG], session=HtmlIndex(), retries=2, sleep_fn=no_sleep))


def test_the_index_walk_follows_the_resume_key():
    """A host with more captures than one batch must not end at the first page."""
    first = cdx((BIG, "20220121062121", "200", "3000"))
    first.extend([[], ["gov,in,education,dsel)/x 20220121"]])
    second = cdx((BIG, "20231015155748", "200", "3000"))

    class PagedIndex(FakeSession):
        def __init__(self):
            super().__init__(cdx=first)
            self._pages = [first, second]

        def get(self, url, params=None, timeout=None, **kwargs):
            if url == wr.CDX_API:
                self.cdx = self._pages[min(len(self.index_calls), len(self._pages) - 1)]
            return super().get(url, params=params, timeout=timeout, **kwargs)

    session = PagedIndex()
    captures = wr.host_captures(HOST, session=session, sleep_fn=no_sleep)
    assert len(session.index_calls) == 2
    assert session.index_calls[1]["resumeKey"] == "gov,in,education,dsel)/x 20220121"
    assert len(captures[wr.canonical_key(BIG)]) == 2


# ---------------------------------------------------------------------------
# 4. Back off on 429/503 rather than reading them as absence
# ---------------------------------------------------------------------------

def test_a_throttled_replay_is_retried_and_then_succeeds(tmp_path):
    session = ThrottlingSession(
        refusals=2,
        cdx=cdx((BIG, "20220121062121", "200", "3000")),
        replay={"20220121062121": pdf(3000)},
    )
    slept: list[float] = []
    rows = list(wr.recover(tmp_path, urls=[BIG], session=session, sleep_fn=slept.append))
    assert rows[0]["status"] == "ok"
    assert slept and slept == sorted(slept), "backoff must escalate"


def test_persistent_throttling_reports_throttled_and_names_the_status(tmp_path):
    """A refusal must never be recorded as "the archive does not have it"."""
    session = ThrottlingSession(
        refusals=99,
        cdx=cdx((BIG, "20220121062121", "200", "3000")),
        replay={"20220121062121": pdf(3000)},
    )
    rows = list(wr.recover(tmp_path, urls=[BIG], session=session, retries=2, sleep_fn=no_sleep))
    assert rows[0]["status"] == "throttled"
    assert "429" in rows[0]["reason"]
    assert rows[0]["status"] != "no-capture"


def test_a_replay_404_is_not_read_as_a_throttle(tmp_path):
    session = FakeSession(
        cdx=cdx((BIG, "20220121062121", "200", "3000")),
        replay_status={"20220121062121": 404},
    )
    rows = list(wr.recover(tmp_path, urls=[BIG], session=session, retries=2, sleep_fn=no_sleep))
    assert rows[0]["status"] == "fetch-failed"
    assert "404" in rows[0]["reason"]


# ---------------------------------------------------------------------------
# 5. Manifest rows, and resume by re-verifying
# ---------------------------------------------------------------------------

def test_manifest_row_carries_the_six_contract_fields(tmp_path):
    session = FakeSession(
        cdx=cdx((BIG, "20220121062121", "200", "3000")),
        replay={"20220121062121": pdf(3000)},
    )
    rows = list(wr.recover(tmp_path, urls=[BIG], session=session, sleep_fn=no_sleep))
    row = rows[0]
    for field in wr.RECOVERY_FIELDS:
        assert field in row, field
    assert row["sha256"] == wr.sha256_hex(pdf(3000))
    assert (tmp_path / row["local_file"]).read_bytes() == pdf(3000)
    written = [json.loads(x) for x in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert written[0]["source_url"] == BIG
    assert written[0]["wayback_timestamp"] == "20220121062121"


def test_resume_reverifies_and_refetches_a_file_the_manifest_calls_ok(tmp_path):
    """The manifest's own ``ok`` is not proof. A run interrupted mid-write left
    a truncated file beside a row that said it was complete.
    """
    session = FakeSession(
        cdx=cdx((BIG, "20220121062121", "200", "3000")),
        replay={"20220121062121": pdf(3000)},
    )
    first = list(wr.recover(tmp_path, urls=[BIG], session=session, sleep_fn=no_sleep))
    local = tmp_path / first[0]["local_file"]
    local.write_bytes(truncated_pdf(3000))

    session.replay_calls.clear()
    again = list(wr.recover(tmp_path, urls=[BIG], session=session, sleep_fn=no_sleep))
    assert session.replay_calls, "a corrupt file must be fetched again"
    assert again[0]["status"] == "ok"
    assert local.read_bytes() == pdf(3000)


def test_resume_skips_a_file_that_still_verifies(tmp_path):
    session = FakeSession(
        cdx=cdx((BIG, "20220121062121", "200", "3000")),
        replay={"20220121062121": pdf(3000)},
    )
    list(wr.recover(tmp_path, urls=[BIG], session=session, sleep_fn=no_sleep))
    session.replay_calls.clear()
    rows = list(wr.recover(tmp_path, urls=[BIG], session=session, sleep_fn=no_sleep))
    assert session.replay_calls == []
    assert rows[0]["status"] == "ok"
    lines = (tmp_path / "manifest.jsonl").read_text().splitlines()
    assert len(lines) == 1, "a skipped file must not append a second row"


def test_two_urls_sharing_a_basename_get_separate_files(tmp_path):
    other = f"https://{HOST}/sites/other/AN_PAB_2018_2019.pdf"
    session = FakeSession(
        cdx=cdx(
            (BIG, "20220121062121", "200", "3000"),
            (other, "20220121062121", "200", "3000"),
        ),
        replay={"20220121062121": pdf(3000)},
    )
    rows = list(wr.recover(tmp_path, urls=[BIG, other], session=session, sleep_fn=no_sleep))
    assert rows[0]["local_file"] != rows[1]["local_file"]


def test_recover_accepts_a_single_url_string(tmp_path):
    session = FakeSession(
        cdx=cdx((BIG, "20220121062121", "200", "3000")),
        replay={"20220121062121": pdf(3000)},
    )
    rows = list(wr.recover(tmp_path, urls=BIG, session=session, sleep_fn=no_sleep))
    assert [r["source_url"] for r in rows] == [BIG]


def test_recover_without_a_target_raises():
    with pytest.raises(ValueError):
        list(wr.recover("/tmp/nowhere"))


# --- review findings, 2026-08-17 -------------------------------------------
# Seven paths that returned a plausible wrong answer rather than an error. Each
# test states the answer the old code gave.


def test_an_http_capture_of_an_https_url_is_found(tmp_path):
    """CDX `original` is the URL as CRAWLED. A gov host crawled in 2018 is
    recorded `http://` while a target list scraped off today's site carries
    `https://`, and keying the index on the raw string reported `no-capture` for
    a document the index visibly held."""
    crawled = BIG.replace("https://", "http://")
    session = FakeSession(
        cdx=cdx((crawled, "20220121062121", "200", "3000")),
        replay={"20220121062121": pdf(3000)},
    )
    rows = list(wr.recover(tmp_path, urls=[BIG], session=session, sleep_fn=no_sleep))
    assert rows[0]["status"] == "ok"


def test_a_trailing_slash_difference_still_matches():
    key = wr.canonical_key
    assert key("https://a.gov.in/x/") == key("http://www.A.gov.in/x")


def test_the_path_case_is_not_folded():
    """Gov hosts serve case-sensitive paths, so folding them would merge two
    real documents into one."""
    key = wr.canonical_key
    assert key("https://a.gov.in/PAB.pdf") != key("https://a.gov.in/pab.pdf")


def test_two_hosts_sharing_a_path_do_not_overwrite_each_other(tmp_path):
    """Both wrote `recovered/files_pab.pdf`, so the first row said `ok` and
    carried the sha256 of bytes no longer on disk."""
    a, b = "https://a.gov.in/files/pab.pdf", "https://b.gov.in/files/pab.pdf"
    session = FakeSession(
        cdx=cdx((a, "20220121062121", "200", "3000"), (b, "20220121062121", "200", "4000")),
        replay={"20220121062121": pdf(3000)},
    )
    rows = list(wr.recover(tmp_path, urls=[a, b], session=session, sleep_fn=no_sleep))
    assert rows[0]["local_file"] != rows[1]["local_file"]
    assert len(list((tmp_path / "recovered").iterdir())) == 2


def test_query_string_variants_do_not_collide(tmp_path):
    a, b = "https://a.gov.in/get?id=101", "https://a.gov.in/get?id=102"
    session = FakeSession(
        cdx=cdx((a, "20220121062121", "200", "3000"), (b, "20220121062121", "200", "3000")),
        replay={"20220121062121": pdf(3000)},
    )
    rows = list(wr.recover(tmp_path, urls=[a, b], session=session, sleep_fn=no_sleep))
    assert rows[0]["local_file"] != rows[1]["local_file"]


def test_a_url_with_no_host_raises_instead_of_reporting_absence(tmp_path):
    """A scraped anchor list is full of relative hrefs. With no host there is no
    query to build, so every URL came back `no-capture` with zero index calls —
    an absence claim made without asking the archive."""
    session = FakeSession(cdx=cdx(), replay={})
    with pytest.raises(ValueError) as excinfo:
        list(wr.recover(tmp_path, urls=[f"{HOST}/a.pdf"], session=session, sleep_fn=no_sleep))
    assert "no host" in str(excinfo.value)
    assert session.index_calls == []


def test_an_index_serving_no_row_at_all_raises(tmp_path):
    """A whole gov host with a decade of PDFs and zero rows is a block, a wrong
    prefix or a throttle answering 200. It became one absence claim per URL."""
    session = FakeSession(cdx=cdx(), replay={})
    with pytest.raises(wr.IndexUnavailable):
        list(wr.recover(tmp_path, urls=[BIG], session=session, sleep_fn=no_sleep))


def test_rows_that_are_all_non_200_are_a_real_absence_not_an_outage(tmp_path):
    """Rows served and none of them 200 is evidence. It must not raise."""
    session = FakeSession(cdx=cdx((BIG, "20220121062121", "404", "27")), replay={})
    rows = list(wr.recover(tmp_path, urls=[BIG], session=session, sleep_fn=no_sleep))
    assert rows[0]["status"] == "no-capture"


def test_a_404_capture_is_never_saved_as_the_document(tmp_path):
    """The 200-ness is REQUESTED of the server, and a request is no guarantee.
    With verify="none" the archived error page was written and reported `ok`."""
    session = FakeSession(
        cdx=cdx((BIG, "20220121062121", "404", "27"), (BIG, "20220121062200", "200", "3000")),
        replay={"20220121062121": b"Page not found", "20220121062200": pdf(3000)},
    )
    rows = list(wr.recover(tmp_path, urls=[BIG], session=session,
                           verify="none", sleep_fn=no_sleep))
    assert rows[0]["bytes"] == 3000, "the 404 row outranked it on nothing but order"


def test_a_transport_failure_is_not_reported_as_unverified(tmp_path):
    """`unverified` says the bytes arrived and did not form a whole document. A
    timeout on every candidate delivered no bytes at all, and reporting it the
    same way tells a reader the archive's copy is broken."""
    class TimingOutSession(FakeSession):
        def get(self, url, params=None, timeout=None, stream=False, **kwargs):
            if url == wr.CDX_API:
                return super().get(url, params=params, timeout=timeout)
            raise TimeoutError("read timed out")

    session = TimingOutSession(cdx=cdx((BIG, "20220121062121", "200", "3000")), replay={})
    rows = list(wr.recover(tmp_path, urls=[BIG], session=session, sleep_fn=no_sleep))
    assert rows[0]["status"] == "fetch-failed"


def test_a_timestamp_containing_429_is_not_read_as_a_throttle():
    """The replay URL carries the 14-digit timestamp, so a substring search made
    every capture from 29 April a reported throttle — an assertion that the
    archive refused bytes it holds."""
    assert wr._is_throttle("ConnectionError: /web/20220429062121id_/x") is False
    assert wr._is_throttle("RuntimeError: HTTP 429 https://web.archive.org/x") is True


def test_a_url_outside_the_named_host_is_still_indexed(tmp_path):
    """With both --host and --url given, only the host was indexed while the URL
    list stayed the target set, so a URL on another host came back `no-capture`
    without its host ever being queried."""
    other = "https://mospi.gov.in/files/report.pdf"
    session = FakeSession(
        cdx=cdx((BIG, "20220121062121", "200", "3000"),
                (other, "20220121062121", "200", "4000")),
        replay={"20220121062121": pdf(3000)},
    )
    rows = list(wr.recover(tmp_path, urls=[other], host=HOST, session=session,
                           sleep_fn=no_sleep))
    assert [r["status"] for r in rows] == ["ok"], "the other host must be indexed too"
    assert len(session.index_calls) == 2, "one index call per host, both hosts"
