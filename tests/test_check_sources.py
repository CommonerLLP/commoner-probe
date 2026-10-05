"""Tests for scripts/check_sources.py — the source freshness check engine.

Every test uses fakes; none touches the network.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
from dataclasses import dataclass
from datetime import date

import pytest
import requests

from commoner_probe.http_client import ChallengeDetected
from tests.conftest import load_script

# Load the script as a module without requiring it on PYTHONPATH.
cs = load_script("check_sources")

TODAY = date(2026, 10, 5)


def ctx(**kw):
    kw.setdefault("geo_fenced", frozenset())
    return cs.Context(today=TODAY, results={}, records={}, state={}, **kw)


def src(fetch, **kw):
    kw.setdefault("required", ("title",))
    return cs.Source(
        id=kw.pop("id", "s"), label="S", host=kw.pop("host", "h.example"), fetch=fetch, **kw
    )


def PDF(url, headers):
    return (200, "application/pdf", b"%PDF-1.7 ...")


def check(s, **kw):
    kw.setdefault("control", lambda: True)
    kw.setdefault("fetch_document", PDF)
    return cs.check_source(s, kw.pop("context", None) or ctx(), **kw)


class FakeRule:
    def __init__(self, ok=True, reason="", deps=()):
        self._ok, self._reason, self._deps = ok, reason, tuple(deps)
        self.calls = []

    def depends_on(self):
        return self._deps

    def evaluate(self, *, source_id, records, newest, count, ctx):
        self.calls.append((source_id, len(records), newest, count))
        return cs.RuleResult(self._ok, self._reason)


class RaisingRule(FakeRule):
    def evaluate(self, **kw):
        raise KeyError("boom")


# -- newest_date ------------------------------------------------------------


def test_future_and_garbage_dates_are_ignored():
    recs = [{"d": "2062-01-01"}, {"d": "18/03/2026"}, {"d": "2026-03-18"}, {"d": ""}]
    assert cs.newest_date(recs, lambda r: r["d"], TODAY) == "2026-03-18"


def test_newest_date_accepts_today_and_picks_latest():
    recs = [{"d": "2026-10-05"}, {"d": "2026-01-01"}]
    assert cs.newest_date(recs, lambda r: r["d"], TODAY) == "2026-10-05"


def test_newest_date_empty_when_none_usable():
    assert cs.newest_date([{"d": "x"}, {"d": None}], lambda r: r["d"], TODAY) == ""


def test_newest_date_ignores_a_record_date_that_raises():
    recs = [{"d": "2026-03-18"}, {}]
    assert cs.newest_date(recs, lambda r: r["d"], TODAY) == "2026-03-18"


# -- classify_exception -----------------------------------------------------


class _Resp:
    def __init__(self, code):
        self.status_code = code


class _WithResponse(Exception):
    def __init__(self, code):
        super().__init__(f"boom {code}")
        self.response = _Resp(code)


def test_classify_response_attribute():
    assert cs.classify_exception(_WithResponse(503)) == (cs.HTTP_ERROR, 503, "HTTP 503")


def test_classify_real_requests_http_error():
    exc = requests.HTTPError("bad", response=_Resp(404))
    assert cs.classify_exception(exc) == (cs.HTTP_ERROR, 404, "HTTP 404")


def test_classify_urllib_http_error():
    exc = urllib.error.HTTPError("https://x", 502, "Bad Gateway", {}, None)
    assert cs.classify_exception(exc) == (cs.HTTP_ERROR, 502, "HTTP 502")


def test_classify_runtime_error_raise_for_status_form():
    kind, status, reason = cs.classify_exception(RuntimeError("HTTP 500 for https://x"))
    assert (kind, status) == (cs.HTTP_ERROR, 500)
    assert reason.startswith("HTTP 500")


def test_classify_runtime_error_retry_wrapper_form_without_for():
    kind, status, _ = cs.classify_exception(RuntimeError("HTTP 503 https://x"))
    assert (kind, status) == (cs.HTTP_ERROR, 503)


def test_classify_retry_error_has_no_status():
    kind, status, _ = cs.classify_exception(requests.exceptions.RetryError("too many"))
    assert (kind, status) == (cs.HTTP_ERROR, None)


@pytest.mark.parametrize(
    "exc",
    [
        requests.exceptions.ConnectionError("refused"),
        requests.exceptions.Timeout("slow"),
        urllib.error.URLError("dns"),
        TimeoutError("timed out"),
        ConnectionError("reset"),
    ],
)
def test_classify_no_response(exc):
    kind, status, reason = cs.classify_exception(exc)
    assert (kind, status) == (cs.NO_RESPONSE, None)
    assert reason.startswith(type(exc).__name__ + ": ")


@pytest.mark.parametrize("exc", [
    requests.exceptions.SSLError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"),
    requests.exceptions.SSLError("UNSAFE_LEGACY_RENEGOTIATION_DISABLED"),
    urllib.error.URLError(ssl.SSLCertVerificationError(1, "certificate verify failed")),
    ssl.SSLCertVerificationError(1, "certificate verify failed"),
])
def test_classify_tls_failure_is_broken_not_no_response(exc):
    # The host answered and the client rejected the handshake, so it isn't a
    # transient "no response": it fails the same way every run.
    kind, status, reason = cs.classify_exception(exc)
    assert (kind, status) == (cs.BROKEN, None)
    assert reason.startswith("TLS handshake failed: ")


@pytest.mark.parametrize("exc", [
    requests.exceptions.SSLError("EOF occurred in violation of protocol (_ssl.c:1006)"),
    ssl.SSLEOFError(8, "EOF occurred in violation of protocol"),
    urllib.error.URLError(ssl.SSLEOFError(8, "EOF occurred in violation of protocol")),
])
def test_classify_tls_connection_drop_is_still_no_response(exc):
    kind, _, _ = cs.classify_exception(exc)
    assert kind == cs.NO_RESPONSE


def test_tls_failure_on_the_main_fetch_skips_the_control():
    def fetch():
        raise requests.exceptions.SSLError("CERTIFICATE_VERIFY_FAILED")

    def control():
        raise AssertionError("control must not run for a TLS failure")

    obs, _ = check(src(fetch), control=control)
    assert obs.outcome == cs.BROKEN and obs.reason.startswith("TLS handshake failed: ")


def test_classify_challenge_is_broken_not_http_error():
    # ChallengeDetected subclasses RuntimeError; its message can carry a
    # URL and must not be read as an HTTP status.
    kind, status, reason = cs.classify_exception(ChallengeDetected("https://x: HTTP 403 page"))
    assert (kind, status) == (cs.BROKEN, None)
    assert reason.startswith("ChallengeDetected: ")


def test_classify_json_decode_error_is_broken():
    exc = json.JSONDecodeError("Expecting value", "<html>", 0)
    kind, status, reason = cs.classify_exception(exc)
    assert (kind, status) == (cs.BROKEN, None)
    assert reason.startswith("JSONDecodeError: ")


def test_classify_truncates_reason():
    _, _, reason = cs.classify_exception(ValueError("x" * 500))
    assert len(reason) == 200


# -- check_source -----------------------------------------------------------


def test_fresh_when_contract_document_and_rules_pass():
    s = src(
        lambda: [{"title": "a", "d": "2026-09-01", "u": "x"}],
        record_date=lambda r: r["d"],
        document=lambda r: r["u"],
    )
    obs, recs = check(s)
    assert obs.outcome == cs.FRESH and obs.newest == "2026-09-01" and len(recs) == 1
    assert obs.count == 1


def test_only_unusable_dates_is_broken_not_stale():
    s = src(lambda: [{"title": "a", "d": "2062-01-01"}], record_date=lambda r: r["d"])
    obs, _ = check(s)
    assert obs.outcome == cs.BROKEN and "no parseable dates" in obs.reason


def test_geo_fenced_host_skips_fetch():
    def fetch():
        raise AssertionError("fetch must not run")

    obs, recs = check(src(fetch, host="blocked.example"),
                      context=ctx(geo_fenced=frozenset({"blocked.example"})))
    assert obs.outcome == cs.GEO_FENCED and recs == []


def test_no_response_with_passing_control():
    def fetch():
        raise requests.exceptions.ConnectionError("refused")

    obs, _ = check(src(fetch), control=lambda: True)
    assert obs.outcome == cs.NO_RESPONSE and "ConnectionError" in obs.reason


def test_no_response_with_failing_control():
    def fetch():
        raise TimeoutError("slow")

    obs, _ = check(src(fetch), control=lambda: False)
    assert obs.outcome == cs.CONTROL_FAILED


def test_no_response_when_control_raises():
    def fetch():
        raise TimeoutError("slow")

    def control():
        raise OSError("no network")

    obs, _ = check(src(fetch), control=control)
    assert obs.outcome == cs.CONTROL_FAILED


def test_http_error_carries_status_and_skips_control():
    def fetch():
        raise RuntimeError("HTTP 503 https://x")

    def control():
        raise AssertionError("control must not run for an HTTP error")

    obs, _ = check(src(fetch), control=control)
    assert obs.outcome == cs.HTTP_ERROR and obs.http_status == 503


def test_total_failure_is_classified_too():
    def total():
        raise RuntimeError("HTTP 500 for https://x")

    obs, _ = check(src(lambda: [{"title": "a"}], total=total))
    assert obs.outcome == cs.HTTP_ERROR and obs.http_status == 500


def test_challenge_page_is_broken():
    def fetch():
        raise ChallengeDetected("https://x: challenge page")

    obs, _ = check(src(fetch))
    assert obs.outcome == cs.BROKEN


def test_invalid_json_is_broken():
    def fetch():
        raise json.JSONDecodeError("Expecting value", "<html>", 0)

    obs, _ = check(src(fetch))
    assert obs.outcome == cs.BROKEN


def test_fetch_returning_dict_is_broken():
    obs, recs = check(src(lambda: {"title": "a"}))
    assert obs.outcome == cs.BROKEN and recs == []


def test_missing_required_fields_is_broken():
    s = src(lambda: [{"title": ""}, {"other": 1}, "not a dict"], required=("title", "url"))
    obs, _ = check(s)
    assert obs.outcome == cs.BROKEN
    assert "no record has all required fields: title, url" in obs.reason


def test_valid_records_only_are_passed_on():
    s = src(lambda: [{"title": "a"}, {"title": ""}, {"title": "b"}])
    obs, recs = check(s)
    assert [r["title"] for r in recs] == ["a", "b"]
    assert obs.count == 3  # counts every fetched record, valid or not


def test_count_comes_from_total_when_set():
    obs, _ = check(src(lambda: [{"title": "a"}], total=lambda: 42))
    assert obs.outcome == cs.FRESH and obs.count == 42


def test_document_status_error_is_broken():
    s = src(lambda: [{"title": "a", "u": "x"}], document=lambda r: r["u"])
    obs, _ = check(s, fetch_document=lambda u, h: (406, "application/pdf", b"%PDF"))
    assert obs.outcome == cs.BROKEN and obs.reason.startswith("document: ")


def test_document_html_labeled_as_pdf_is_broken():
    s = src(lambda: [{"title": "a", "u": "x"}], document=lambda r: r["u"])
    obs, _ = check(s, fetch_document=lambda u, h: (200, "application/pdf", b"<html>oops"))
    assert obs.outcome == cs.BROKEN and obs.reason.startswith("document: ")


def _raising_document(exc):
    def fetch_document(url, headers):
        raise exc

    return fetch_document


def _doc_source():
    return src(lambda: [{"title": "a", "u": "x", "d": "2026-10-01"}],
               document=lambda r: r["u"], record_date=lambda r: r["d"])


def test_document_fetch_raising_a_parser_error_is_broken():
    obs, _ = check(_doc_source(), fetch_document=_raising_document(ValueError("bad url")))
    assert obs.outcome == cs.BROKEN and obs.reason.startswith("document: ")


@pytest.mark.parametrize("exc", [
    TimeoutError("slow"),
    requests.exceptions.ConnectionError("reset"),
    requests.exceptions.ReadTimeout("slow"),
])
def test_document_fetch_without_a_response_is_a_no_response_candidate(exc):
    obs, _ = check(_doc_source(), fetch_document=_raising_document(exc), control=lambda: True)
    assert obs.outcome == cs.NO_RESPONSE and obs.reason.startswith("document: ")
    assert obs.newest == "2026-10-01" and obs.count == 1


def test_document_fetch_without_a_response_runs_the_control():
    obs, _ = check(_doc_source(), fetch_document=_raising_document(TimeoutError("slow")),
                   control=lambda: False)
    assert obs.outcome == cs.CONTROL_FAILED


@pytest.mark.parametrize("exc", [
    RuntimeError("HTTP 503 https://x/a.pdf"),
    requests.exceptions.RetryError("too many 429s"),
])
def test_document_fetch_server_error_is_an_http_error_candidate(exc):
    obs, _ = check(_doc_source(), fetch_document=_raising_document(exc))
    assert obs.outcome == cs.HTTP_ERROR and obs.reason.startswith("document: ")


@pytest.mark.parametrize("status", [500, 503, 429])
def test_document_returned_server_status_is_an_http_error_candidate(status):
    obs, _ = check(_doc_source(), fetch_document=lambda u, h: (status, "text/html", b""))
    assert obs.outcome == cs.HTTP_ERROR and obs.http_status == status
    assert obs.reason == f"document: HTTP {status}"


def test_document_404_stays_broken():
    obs, _ = check(_doc_source(), fetch_document=lambda u, h: (404, "text/html", b""))
    assert obs.outcome == cs.BROKEN and obs.reason == "document: HTTP 404"


def test_document_tls_failure_is_broken():
    exc = requests.exceptions.SSLError("CERTIFICATE_VERIFY_FAILED")
    obs, _ = check(_doc_source(), fetch_document=_raising_document(exc))
    assert obs.outcome == cs.BROKEN and obs.reason.startswith("document: TLS handshake failed")


def test_document_is_fetched_for_newest_record_with_headers():
    seen = []

    def fetch_document(url, headers):
        seen.append((url, dict(headers)))
        return (200, "application/pdf", b"%PDF")

    recs = [
        {"title": "old", "d": "2025-01-01", "u": "old.pdf"},
        {"title": "new", "d": "2026-06-01", "u": "new.pdf"},
    ]
    s = src(lambda: recs, record_date=lambda r: r["d"], document=lambda r: r["u"],
            document_headers={"Referer": "r"})
    obs, _ = check(s, fetch_document=fetch_document)
    assert obs.outcome == cs.FRESH
    assert seen == [("new.pdf", {"Referer": "r"})]


def test_document_uses_first_record_without_record_date():
    seen = []

    def fetch_document(url, headers):
        seen.append(url)
        return (200, "application/pdf", b"%PDF")

    recs = [{"title": "a", "u": "first"}, {"title": "b", "u": "second"}]
    check(src(lambda: recs, document=lambda r: r["u"]), fetch_document=fetch_document)
    assert seen == ["first"]


@pytest.mark.parametrize(
    "kind,head,ctype,ok",
    [
        ("pdf", b"%PDF-1.4", "application/pdf", True),
        ("pdf", b"PK\x03\x04", "application/pdf", False),
        ("spreadsheet", b"PK\x03\x04", "x", True),
        ("spreadsheet", b"\xd0\xcf\x11\xe0rest", "x", True),
        ("spreadsheet", b"%PDF", "x", False),
        ("zip", b"PK\x03\x04", "x", True),
        ("zip", b"\xd0\xcf\x11\xe0", "x", False),
        ("any-non-html", b"id,name\n1,a", "text/csv", True),
        ("any-non-html", b"<!DOCTYPE html><html>", "application/octet-stream", False),
        ("any-non-html", b"  \n<HTML>", "application/octet-stream", False),
        ("any-non-html", b"id,name", "text/html; charset=utf-8", False),
    ],
)
def test_document_kinds(kind, head, ctype, ok):
    s = src(lambda: [{"title": "a", "u": "x"}], document=lambda r: r["u"], document_kind=kind)
    obs, _ = check(s, fetch_document=lambda u, h: (200, ctype, head))
    assert (obs.outcome == cs.FRESH) is ok


def test_failing_rule_makes_source_stale_with_its_reason():
    rules = (FakeRule(ok=False, reason="too old"), FakeRule(ok=True),
             FakeRule(ok=False, reason="no session"))
    obs, _ = check(src(lambda: [{"title": "a"}], freshness=rules))
    assert obs.outcome == cs.STALE and obs.reason == "too old; no session"
    assert all(len(r.calls) == 1 for r in rules)


def test_rule_receives_valid_records_newest_and_count():
    rule = FakeRule()
    s = src(lambda: [{"title": "a", "d": "2026-02-02"}, {"title": ""}],
            record_date=lambda r: r["d"], total=lambda: 9, freshness=(rule,))
    check(s)
    assert rule.calls == [("s", 1, "2026-02-02", 9)]


def test_rule_raising_is_broken():
    obs, _ = check(src(lambda: [{"title": "a"}], freshness=(RaisingRule(),)))
    assert obs.outcome == cs.BROKEN
    assert obs.reason == "rule RaisingRule raised KeyError: 'boom'"


def test_check_source_never_raises():
    class Hostile(dict):
        def get(self, key, default=None):
            raise ValueError("nope")

    obs, _ = check(src(lambda: [Hostile()]))
    assert obs.outcome == cs.BROKEN and obs.reason.startswith("checker error: ValueError")

    s = src(lambda: [{"title": "a"}], document=lambda r: 1 / 0)
    obs, _ = check(s)
    assert obs.outcome == cs.BROKEN and "ZeroDivisionError" in obs.reason


# -- order_sources ----------------------------------------------------------


def _s(id_, deps=()):
    return src(lambda: [], id=id_, freshness=(FakeRule(deps=deps),))


def test_order_sources_puts_dependencies_first():
    a, b, c = _s("a"), _s("b", ("a",)), _s("c", ("b",))
    assert [s.id for s in cs.order_sources([c, b, a])] == ["a", "b", "c"]


def test_order_sources_keeps_registry_order_when_independent():
    a, b, c = _s("a"), _s("b"), _s("c")
    assert [s.id for s in cs.order_sources([a, b, c])] == ["a", "b", "c"]


def test_order_sources_duplicate_id():
    with pytest.raises(ValueError, match="duplicate"):
        cs.order_sources([_s("a"), _s("a")])


def test_order_sources_unknown_dependency():
    with pytest.raises(ValueError, match="unknown"):
        cs.order_sources([_s("a", ("ghost",))])


def test_order_sources_cycle():
    with pytest.raises(ValueError, match="cycle"):
        cs.order_sources([_s("a", ("b",)), _s("b", ("a",))])


# -- run_checks -------------------------------------------------------------


def _fresh_source(id_, calls=None, deps=()):
    def fetch():
        if calls is not None:
            calls.append(id_)
        return [{"title": id_}]

    return src(fetch, id=id_, freshness=(FakeRule(deps=deps),))


def _run(sources, **kw):
    kw.setdefault("today", TODAY)
    kw.setdefault("state", {})
    kw.setdefault("geo_fenced", frozenset())
    kw.setdefault("control", lambda: True)
    kw.setdefault("fetch_document", PDF)
    return cs.run_checks(sources, **kw)


def test_run_checks_isolates_a_failing_source():
    def boom():
        raise ValueError("parser broke")

    results = _run([src(boom, id="a"), _fresh_source("b")])
    assert results["a"].outcome == cs.BROKEN
    assert results["b"].outcome == cs.FRESH


def test_run_checks_only_adds_transitive_dependencies():
    calls = []
    sources = [
        _fresh_source("a", calls),
        _fresh_source("b", calls, deps=("a",)),
        _fresh_source("c", calls),
    ]
    results = _run(sources, only={"b"})
    assert set(results) == {"a", "b"} and sorted(calls) == ["a", "b"]


def test_run_checks_unknown_only_id_raises():
    with pytest.raises(ValueError, match="ghost"):
        _run([_fresh_source("a")], only={"ghost"})


def test_run_checks_registry_error_raises_before_any_fetch():
    calls = []
    sources = [_fresh_source("a", calls), _fresh_source("a", calls)]
    with pytest.raises(ValueError):
        _run(sources)
    assert calls == []


def test_run_checks_fills_context_for_later_sources():
    seen = {}

    @dataclass
    class Peek:
        def depends_on(self):
            return ("a",)

        def evaluate(self, *, source_id, records, newest, count, ctx):
            seen["a_outcome"] = ctx.results["a"].outcome
            seen["a_records"] = ctx.records["a"]
            seen["state"] = ctx.state
            return cs.RuleResult(True)

    b = src(lambda: [{"title": "b"}], id="b", freshness=(Peek(),))
    _run([b, _fresh_source("a")], state={"sources": {"a": {"status": "fresh"}}})
    assert seen["a_outcome"] == cs.FRESH
    assert seen["a_records"] == [{"title": "a"}]
    assert seen["state"] == {"a": {"status": "fresh"}}


def test_run_checks_does_not_store_records_for_geo_fenced():
    captured = {}

    class Peek:
        def depends_on(self):
            return ("a",)

        def evaluate(self, *, source_id, records, newest, count, ctx):
            captured["records"] = dict(ctx.records)
            return cs.RuleResult(True)

    a = src(lambda: [], id="a", host="blocked.example")
    b = src(lambda: [{"title": "b"}], id="b", freshness=(Peek(),))
    results = _run([a, b], geo_fenced=frozenset({"blocked.example"}))
    assert results["a"].outcome == cs.GEO_FENCED
    assert "a" not in captured["records"]


def test_run_checks_defaults_state_to_empty_sources():
    seen = {}

    class Peek:
        def depends_on(self):
            return ()

        def evaluate(self, *, source_id, records, newest, count, ctx):
            seen["state"] = ctx.state
            return cs.RuleResult(True)

    _run([src(lambda: [{"title": "a"}], freshness=(Peek(),))], state={})
    assert seen["state"] == {}


# -- default_* --------------------------------------------------------------


def test_document_robots_opt_out_is_forwarded_only_when_set():
    seen = []

    def fetch_document(url, headers, **kw):
        seen.append(kw)
        return (200, "application/pdf", b"%PDF")

    recs = [{"title": "t", "u": "a.pdf"}]
    check(src(lambda: recs, document=lambda r: r["u"]), fetch_document=fetch_document)
    check(src(lambda: recs, document=lambda r: r["u"], document_respect_robots=False),
          fetch_document=fetch_document)
    assert seen == [{}, {"respect_robots": False}]


def test_default_fetch_document_forwards_respect_robots(monkeypatch):
    seen = {}

    class Resp:
        status_code = 200
        headers = {"Content-Type": "application/pdf"}

        def iter_content(self, chunk_size):
            yield b"%PDF"

        def close(self):
            pass

    class Session:
        def get(self, url, **kw):
            seen.update(kw)
            return Resp()

    monkeypatch.setattr(cs, "make_session", lambda: Session())
    cs.default_fetch_document("https://x/a.pdf", {})
    assert seen["respect_robots"] is True
    cs.default_fetch_document("https://x/a.pdf", {}, respect_robots=False)
    assert seen["respect_robots"] is False


def test_default_fetch_document_reads_only_the_first_chunk(monkeypatch):
    class Resp:
        status_code = 200
        headers = {"Content-Type": "application/pdf"}
        closed = False
        requested = None

        def iter_content(self, chunk_size):
            Resp.requested = chunk_size
            yield b"%PDF" + b"x" * 10
            raise AssertionError("must not read a second chunk")

        def close(self):
            Resp.closed = True

    class Session:
        def get(self, url, **kw):
            Session.kw = kw
            return Resp()

    monkeypatch.setattr(cs, "make_session", lambda: Session())
    status, ctype, head = cs.default_fetch_document("https://x/a.pdf", {"Referer": "r"})
    assert (status, ctype) == (200, "application/pdf")
    assert head.startswith(b"%PDF") and Resp.closed
    assert Resp.requested == 2048
    assert Session.kw["stream"] is True and Session.kw["headers"] == {"Referer": "r"}


def test_default_fetch_document_works_with_the_stdlib_response(monkeypatch):
    # Without requests installed, make_session() returns StdlibSession, whose
    # StdlibResponse has no close method.
    from commoner_probe.http_client import StdlibResponse

    class Session:
        def get(self, url, **kw):
            return StdlibResponse(url, 200, b"%PDF-1.7 body", {"content-type": "application/pdf"})

    monkeypatch.setattr(cs, "make_session", lambda: Session())
    status, ctype, head = cs.default_fetch_document("https://x/a.pdf", {})
    assert (status, ctype, head) == (200, "application/pdf", b"%PDF-1.7 body")


def test_default_control_true_on_2xx_and_3xx(monkeypatch):
    for code, expected in [(200, True), (302, True), (404, False), (500, False)]:
        class Session:
            def get(self, url, **kw):
                return type("R", (), {"status_code": code})()

        monkeypatch.setattr(cs, "make_session", lambda: Session())
        assert cs.default_control() is expected


def test_default_control_opts_out_of_robots(monkeypatch):
    seen = {}

    class Session:
        def get(self, url, **kw):
            seen.update(kw, url=url)
            return type("R", (), {"status_code": 200})()

    monkeypatch.setattr(cs, "make_session", lambda: Session())
    assert cs.default_control() is True
    assert seen["respect_robots"] is False
    assert seen["url"] == cs.reachability.DEFAULT_CONTROL_URL


# -- freshness rules --------------------------------------------------------


def cal_ctx(calendar_outcome=cs.FRESH, calendar=None):
    cal = calendar if calendar is not None else [
        {"first_sitting": "2026-01-28", "last_sitting": "2026-04-04"},
        {"first_sitting": "2026-07-20", "last_sitting": "2026-08-21"},
        {"first_sitting": "2026-11-24", "last_sitting": "2026-12-19"},  # future: not ended
    ]
    return cs.Context(today=TODAY,
                      results={"sessions-ls": cs.Observation("sessions-ls", calendar_outcome)},
                      records={"sessions-ls": cal}, state={}, geo_fenced=frozenset())


def evaluate(rule, *, newest="", records=(), count=None, context=None, source_id="x"):
    return rule.evaluate(source_id=source_id, records=list(records), newest=newest,
                         count=count, ctx=context or ctx())


def test_session_aware_flags_the_rs_committee_case():
    r = cs.SessionAware().evaluate(source_id="committees-rs", records=[], newest="2026-03-18",
                                   count=None, ctx=cal_ctx())
    assert not r.ok and "2026-07-20" in r.reason
    assert r.reason == ("newest 2026-03-18 is before the start of the last ended session "
                        "(2026-07-20)")


def test_session_aware_passes_a_record_from_the_last_ended_session():
    assert cs.SessionAware().evaluate(source_id="x", records=[], newest="2026-08-07",
                                      count=None, ctx=cal_ctx()).ok


def test_session_aware_passes_on_the_session_start_date():
    assert evaluate(cs.SessionAware(), newest="2026-07-20", context=cal_ctx()).ok


def test_session_aware_falls_back_when_calendar_is_stale():
    r = cs.SessionAware().evaluate(source_id="x", records=[], newest="2026-03-18",
                                   count=None, ctx=cal_ctx(cs.STALE))
    assert not r.ok and r.reason.startswith("calendar unavailable; ")


def test_session_aware_fallback_prefix_appears_on_a_pass():
    r = evaluate(cs.SessionAware(), newest="2026-09-01", context=cal_ctx(cs.BROKEN))
    assert r.ok and r.reason.startswith("calendar unavailable; ")


def test_session_aware_falls_back_when_calendar_is_missing_or_empty():
    missing = evaluate(cs.SessionAware(), newest="2026-03-18")
    assert not missing.ok and missing.reason.startswith("calendar unavailable; ")
    empty = evaluate(cs.SessionAware(), newest="2026-03-18", context=cal_ctx(calendar=[]))
    assert not empty.ok and empty.reason.startswith("calendar unavailable; ")


def test_session_aware_falls_back_when_no_session_has_ended():
    future = [{"first_sitting": "2026-11-24", "last_sitting": "2026-12-19"}]
    r = evaluate(cs.SessionAware(), newest="2026-09-01", context=cal_ctx(calendar=future))
    assert r.ok and r.reason.startswith("calendar unavailable; ")


def test_session_aware_fallback_uses_fallback_days():
    r = evaluate(cs.SessionAware(fallback_days=10), newest="2026-09-01",
                 context=cal_ctx(cs.STALE))
    assert not r.ok and "limit 10" in r.reason


@pytest.mark.parametrize("bad", [
    {"first_sitting": "2026-09-01"},  # missing last_sitting
    {"last_sitting": "2026-09-30"},  # missing first_sitting
    {"first_sitting": None, "last_sitting": "2026-09-30"},
    {"first_sitting": "2026-09-01", "last_sitting": None},
    {"first_sitting": "2026-09-01", "last_sitting": ""},
    {"first_sitting": "", "last_sitting": "2026-09-30"},
    {"first_sitting": "2026-09-01", "last_sitting": "30 Sep 2026"},
    {"first_sitting": "not a date", "last_sitting": "2026-09-30"},
    "not a dict",
])
def test_session_aware_falls_back_when_the_only_ended_record_is_malformed(bad):
    future = {"first_sitting": "2026-11-24", "last_sitting": "2026-12-19"}
    r = evaluate(cs.SessionAware(), newest="2026-03-18",
                 context=cal_ctx(calendar=[bad, future]))
    assert not r.ok and r.reason.startswith("calendar unavailable; ")


def test_session_aware_skips_a_malformed_record_beside_a_good_one():
    bad = {"first_sitting": "", "last_sitting": ""}
    r = evaluate(cs.SessionAware(), newest="2026-03-18",
                 context=cal_ctx(calendar=[bad, {"first_sitting": "2026-07-20",
                                                  "last_sitting": "2026-08-21"}]))
    assert not r.ok and "2026-07-20" in r.reason
    assert not r.reason.startswith("calendar unavailable")


def test_session_aware_treats_a_session_ending_today_as_not_ended():
    cal = [{"first_sitting": "2026-09-01", "last_sitting": "2026-10-05"},
           {"first_sitting": "2026-01-28", "last_sitting": "2026-04-04"}]
    r = evaluate(cs.SessionAware(), newest="2026-02-01", context=cal_ctx(calendar=cal))
    assert r.ok and not r.reason  # threshold is 2026-01-28


def test_session_aware_reads_a_custom_calendar_id():
    rule = cs.SessionAware(calendar_id="cal")
    assert rule.depends_on() == ("cal",)
    context = cs.Context(today=TODAY, results={"cal": cs.Observation("cal", cs.FRESH)},
                         records={"cal": [{"first_sitting": "2026-07-20",
                                           "last_sitting": "2026-08-21"}]},
                         state={}, geo_fenced=frozenset())
    assert not evaluate(rule, newest="2026-03-18", context=context).ok


def test_depends_on_values():
    assert cs.SessionAware().depends_on() == ("sessions-ls",)
    assert cs.SiblingLag("committees-ls", 60).depends_on() == ("committees-ls",)
    ed = cs.ExpectedEdition(2, 14, str, str)
    for rule in (cs.MaxAge(5), ed, cs.Sentinel(lambda: None, str, "x"), cs.CountFloor()):
        assert rule.depends_on() == ()


def test_max_age_at_the_limit_passes_and_one_day_over_fails():
    assert evaluate(cs.MaxAge(30), newest="2026-09-05").ok  # exactly 30 days
    r = evaluate(cs.MaxAge(30), newest="2026-09-04")
    assert not r.ok
    assert r.reason == "newest 2026-09-04 is 31 days old (limit 30)"


def test_max_age_fails_without_a_newest_date():
    assert not evaluate(cs.MaxAge(30), newest="").ok


def sibling_ctx(outcome=cs.FRESH, newest="2026-08-01"):
    return cs.Context(today=TODAY,
                      results={"ls": cs.Observation("ls", outcome, newest=newest)},
                      records={}, state={}, geo_fenced=frozenset())


def test_sibling_lag_at_the_limit_passes_and_one_day_over_fails():
    rule = cs.SiblingLag("ls", 60)
    assert evaluate(rule, newest="2026-06-02", context=sibling_ctx()).ok  # 60 days
    r = evaluate(rule, newest="2026-06-01", context=sibling_ctx())
    assert not r.ok
    assert r.reason == "newest 2026-06-01 trails ls (2026-08-01) by 61 days (limit 60)"


def test_sibling_lag_passes_when_this_source_is_ahead():
    assert evaluate(cs.SiblingLag("ls", 60), newest="2026-09-01", context=sibling_ctx()).ok


def test_sibling_lag_passes_when_the_sibling_is_missing():
    r = evaluate(cs.SiblingLag("ls", 60), newest="2026-01-01")
    assert r == cs.RuleResult(True, "sibling unavailable")


def test_sibling_lag_passes_when_the_sibling_has_no_newest():
    r = evaluate(cs.SiblingLag("ls", 60), newest="2026-01-01", context=sibling_ctx(newest=""))
    assert r == cs.RuleResult(True, "sibling unavailable")


def test_sibling_lag_compares_a_stale_sibling():
    r = evaluate(cs.SiblingLag("ls", 60), newest="2026-01-01",
                 context=sibling_ctx(cs.STALE))
    assert not r.ok


@pytest.mark.parametrize("outcome", [cs.BROKEN, cs.DOWN, cs.HTTP_ERROR, cs.GEO_FENCED])
def test_sibling_lag_ignores_a_sibling_that_did_not_reach_freshness(outcome):
    # A document failure is BROKEN but still carries `newest`.
    r = evaluate(cs.SiblingLag("ls", 60), newest="2026-01-01",
                 context=sibling_ctx(outcome, newest="2026-08-01"))
    assert r == cs.RuleResult(True, "sibling unavailable")


def budget_rule(edition_of=lambda r: r["edition"]):
    return cs.ExpectedEdition(release_month=2, grace_days=14,
                              edition=lambda y: f"{y}-{(y + 1) % 100:02d}",
                              edition_of=edition_of)


def ctx_on(day):
    return cs.Context(today=day, results={}, records={}, state={}, geo_fenced=frozenset())


def test_expected_edition_wants_the_previous_cycle_inside_grace():
    recs = [{"edition": "2025-26"}]
    assert evaluate(budget_rule(), records=recs, context=ctx_on(date(2026, 2, 10))).ok
    r = evaluate(budget_rule(), records=[{"edition": "2024-25"}],
                 context=ctx_on(date(2026, 2, 10)))
    assert not r.ok
    assert r.reason == "expected edition 2025-26 (due 2025-02-15) not found"


def test_expected_edition_wants_the_new_cycle_after_grace():
    recs = [{"edition": "2025-26"}]
    r = evaluate(budget_rule(), records=recs, context=ctx_on(date(2026, 2, 20)))
    assert not r.ok
    assert r.reason == "expected edition 2026-27 (due 2026-02-15) not found"
    assert evaluate(budget_rule(), records=recs + [{"edition": "2026-27"}],
                    context=ctx_on(date(2026, 2, 20))).ok


def test_expected_edition_deadline_day_counts_as_due():
    r = evaluate(budget_rule(), records=[{"edition": "2025-26"}],
                 context=ctx_on(date(2026, 2, 15)))
    assert not r.ok and "2026-27" in r.reason


def test_expected_edition_skips_a_record_whose_edition_of_raises():
    rule = budget_rule(edition_of=lambda r: r["edition"])
    recs = [{"odd": True}, {"edition": "2025-26"}]
    assert evaluate(rule, records=recs, context=ctx_on(date(2026, 2, 10))).ok
    assert not evaluate(rule, records=[{"odd": True}], context=ctx_on(date(2026, 2, 10))).ok


def sentinel(rec, contains="Union Budget"):
    return cs.Sentinel(lookup=lambda: rec, title_of=lambda r: r["title"],
                       title_contains=contains)


def test_sentinel_passes_when_the_record_resolves_with_the_title():
    assert evaluate(sentinel({"title": "The union budget 2025"})).ok


def test_sentinel_fails_when_the_record_is_missing():
    r = evaluate(sentinel(None))
    assert r == cs.RuleResult(False, "sentinel record not found")


def test_sentinel_fails_when_the_title_changed():
    r = evaluate(sentinel({"title": "x" * 200}))
    assert not r.ok
    assert r.reason == "sentinel title changed: " + "x" * 80


def test_sentinel_lookup_exceptions_propagate_and_report_broken():
    def lookup():
        raise urllib.error.URLError("nope")

    rule = cs.Sentinel(lookup=lookup, title_of=lambda r: "", title_contains="x")
    with pytest.raises(urllib.error.URLError):
        evaluate(rule)
    obs, _ = check(src(lambda: [{"title": "t"}], freshness=(rule,)))
    assert obs.outcome == cs.BROKEN and "rule Sentinel raised" in obs.reason


def floor_ctx(best):
    state = {"x": {"max_count": best}} if best is not None else {}
    return cs.Context(today=TODAY, results={}, records={}, state=state, geo_fenced=frozenset())


def test_count_floor_passes_without_a_baseline():
    r = evaluate(cs.CountFloor(), count=3, context=floor_ctx(None))
    assert r == cs.RuleResult(True, "no count baseline yet")
    assert evaluate(cs.CountFloor(), count=3, context=floor_ctx(0)).ok


def test_count_floor_boundary_95_of_100_passes_and_94_fails():
    assert evaluate(cs.CountFloor(), count=95, context=floor_ctx(100)).ok
    r = evaluate(cs.CountFloor(), count=94, context=floor_ctx(100))
    assert not r.ok
    assert r.reason == "count 94 is below 95% of the highest seen (100)"


def test_count_floor_rounds_the_floor_down():
    assert evaluate(cs.CountFloor(), count=9, context=floor_ctx(10)).ok  # floor(9.5) = 9
    assert not evaluate(cs.CountFloor(), count=8, context=floor_ctx(10)).ok


def test_count_floor_requires_a_count():
    with pytest.raises(ValueError, match="CountFloor needs a count"):
        evaluate(cs.CountFloor(), count=None, context=floor_ctx(100))


def test_rules_read_today_from_the_context_not_the_clock(monkeypatch):
    import datetime

    class Frozen(datetime.date):
        @classmethod
        def today(cls):
            raise AssertionError("rule read the system clock")

    monkeypatch.setattr(cs, "date", Frozen)
    assert evaluate(cs.MaxAge(30), newest="2026-09-05").ok
    assert evaluate(cs.SessionAware(), newest="2026-08-07", context=cal_ctx()).ok
    assert evaluate(budget_rule(), records=[{"edition": "2025-26"}],
                    context=ctx_on(datetime.date(2026, 2, 10))).ok


def test_check_source_reports_stale_from_sibling_lag_and_a_fresh_sibling():
    s = src(lambda: [{"title": "t", "d": "2026-01-01"}], record_date=lambda r: r["d"],
            freshness=(cs.SiblingLag("ls", 60),))
    obs, _ = check(s, context=sibling_ctx(cs.FRESH, newest="2026-08-01"))
    assert obs.outcome == cs.STALE
    assert obs.reason == "newest 2026-01-01 trails ls (2026-08-01) by 212 days (limit 60)"


def test_check_source_passes_when_the_sibling_is_broken():
    s = src(lambda: [{"title": "t", "d": "2026-01-01"}], record_date=lambda r: r["d"],
            freshness=(cs.SiblingLag("ls", 60),))
    obs, _ = check(s, context=sibling_ctx(cs.BROKEN, newest="2026-08-01"))
    assert obs.outcome == cs.FRESH


# -- resolve ----------------------------------------------------------------

import os  # noqa: E402


def obs(id_, outcome, reason="", newest="", count=None, http_status=None):
    return cs.Observation(id_, outcome, reason, newest, count, http_status)


def committed_with(**entries):
    return {
        "geo_fenced": {},
        "sources": {k: {"status": v[0], "since": v[1], "reason": v[2]} for k, v in entries.items()},
    }


def do_resolve(observations, *, committed=None, state=None, registry_ids=None, today=TODAY):
    committed = committed if committed is not None else {"geo_fenced": {}, "sources": {}}
    if registry_ids is None:
        registry_ids = set(committed["sources"]) | {o.source_id for o in observations}
    return cs.resolve(
        {o.source_id: o for o in observations}, committed=committed,
        state=state if state is not None else {}, registry_ids=registry_ids, today=today)


def test_two_run_rule_for_a_committed_source():
    master = committed_with(a=("fresh", "2026-09-01", ""))
    # First failure: pending, the committed status holds, and there's no transition.
    committed, state, transitions, pending = do_resolve(
        [obs("a", cs.HTTP_ERROR, "HTTP 500")], committed=master)
    assert committed["sources"]["a"] == master["sources"]["a"]
    assert transitions == [] and pending == []
    assert state["sources"]["a"]["fail_count"] == 1
    # Second failure: down, with a transition against master.
    committed, state, transitions, pending = do_resolve(
        [obs("a", cs.HTTP_ERROR, "HTTP 500")], committed=master, state=state)
    assert committed["sources"]["a"] == {"status": "down", "since": "2026-10-05", "reason": "HTTP 500"}
    assert transitions == [cs.Transition("a", "fresh", "down", "HTTP 500")]
    assert state["sources"]["a"]["fail_count"] == 2
    # A fresh result resets the counter.
    committed, state, transitions, pending = do_resolve(
        [obs("a", cs.FRESH)], committed=committed, state=state)
    assert committed["sources"]["a"]["status"] == "fresh"
    assert state["sources"]["a"]["fail_count"] == 0


def test_two_run_rule_for_a_new_source():
    committed, state, transitions, pending = do_resolve([obs("n", cs.NO_RESPONSE, "timed out")])
    assert pending == ["n"]
    assert "n" not in committed["sources"]
    assert transitions == []
    committed, state, transitions, pending = do_resolve(
        [obs("n", cs.NO_RESPONSE, "timed out")], state=state)
    assert pending == []
    assert committed["sources"]["n"] == {
        "status": "unreachable", "since": "2026-10-05", "reason": "timed out"}
    assert transitions == [cs.Transition("n", None, "unreachable", "timed out")]


def test_stale_since_and_reason_stay_put_while_the_lag_grows():
    day1, day8 = date(2026, 10, 5), date(2026, 10, 12)
    c1, s1, _, _ = do_resolve([obs("a", cs.STALE, "lag 61")], today=day1)
    c2, s2, transitions, _ = do_resolve(
        [obs("a", cs.STALE, "lag 68")], committed=c1, state=s1, today=day8)
    assert c2["sources"]["a"] == {"status": "stale", "since": "2026-10-05", "reason": "lag 61"}
    assert cs.render_committed(c1) == cs.render_committed(c2)
    assert transitions == []


def test_reason_updates_when_its_category_changes_but_since_stays():
    day1, day8 = date(2026, 10, 5), date(2026, 10, 12)
    c1, s1, _, _ = do_resolve([obs("a", cs.BROKEN, "document: HTTP 404")], today=day1)
    c2, s2, transitions, _ = do_resolve(
        [obs("a", cs.BROKEN, "no record has all required fields: title")],
        committed=c1, state=s1, today=day8)
    assert c2["sources"]["a"] == {"status": "broken", "since": "2026-10-05",
                                  "reason": "no record has all required fields: title"}
    assert s2["sources"]["a"]["reason"] == "no record has all required fields: title"
    assert transitions == []  # the status didn't change


def test_reason_keeps_its_first_wording_when_only_numbers_change():
    c1, s1, _, _ = do_resolve([obs("a", cs.BROKEN, "document: HTTP 404 at 2026-10-05")])
    c2, _, _, _ = do_resolve([obs("a", cs.BROKEN, "document: HTTP 410 at 2026-10-12")],
                             committed=c1, state=s1, today=date(2026, 10, 12))
    assert c2["sources"]["a"]["reason"] == "document: HTTP 404 at 2026-10-05"


def test_down_reason_updates_on_a_new_failure_kind():
    master = committed_with(a=("down", "2026-09-01", "HTTP 500"))
    state = {"sources": {"a": {"status": "down", "since": "2026-09-01", "reason": "HTTP 500",
                               "fail_count": 3}}}
    committed, _, _, _ = do_resolve(
        [obs("a", cs.HTTP_ERROR, "document: HTTP 503")], committed=master, state=state)
    assert committed["sources"]["a"] == {
        "status": "down", "since": "2026-09-01", "reason": "document: HTTP 503"}


def test_pending_failure_keeps_the_previous_reason():
    master = committed_with(a=("broken", "2026-09-01", "no parseable dates"))
    committed, _, _, _ = do_resolve([obs("a", cs.HTTP_ERROR, "HTTP 500")], committed=master)
    assert committed["sources"]["a"]["reason"] == "no parseable dates"


def test_since_comes_from_state_not_master():
    master = committed_with(a=("fresh", "2026-08-01", ""))
    state = {"version": 1, "sources": {"a": {
        "status": "stale", "since": "2026-09-01", "reason": "lag 30", "fail_count": 0}}}
    committed, _, transitions, _ = do_resolve(
        [obs("a", cs.STALE, "lag 37")], committed=master, state=state)
    assert committed["sources"]["a"] == {"status": "stale", "since": "2026-09-01", "reason": "lag 30"}
    assert transitions == [cs.Transition("a", "fresh", "stale", "lag 30")]


def test_a_down_then_fresh_flap_keeps_masters_since_and_reason():
    master = committed_with(a=("fresh", "2026-08-01", ""))
    state = {"version": 1, "sources": {"a": {
        "status": "down", "since": "2026-09-20", "reason": "HTTP 500", "fail_count": 3}}}
    committed, new_state, transitions, _ = do_resolve(
        [obs("a", cs.FRESH)], committed=master, state=state)
    assert cs.render_committed(committed) == cs.render_committed(master)
    assert transitions == []
    assert new_state["sources"]["a"]["since"] == "2026-08-01"
    assert new_state["sources"]["a"]["fail_count"] == 0


def test_missing_state_keeps_committed_since_and_reason_when_status_matches():
    master = committed_with(a=("stale", "2026-09-01", "lag 61"))
    committed, _, transitions, _ = do_resolve(
        [obs("a", cs.STALE, "lag 90")], committed=master, state={})
    assert committed["sources"]["a"] == master["sources"]["a"]
    assert transitions == []


def test_removed_ids_drop_out_with_a_transition_and_unobserved_ids_stay():
    master = committed_with(
        gone=("stale", "2026-09-01", "r"), kept=("down", "2026-09-02", "HTTP 500"),
        seen=("fresh", "2026-09-03", ""))
    state = {"version": 1, "sources": {"kept": {"status": "down", "fail_count": 4}}}
    committed, new_state, transitions, _ = do_resolve(
        [obs("seen", cs.FRESH)], committed=master, state=state,
        registry_ids={"kept", "seen"})
    assert set(committed["sources"]) == {"kept", "seen"}
    assert committed["sources"]["kept"] == master["sources"]["kept"]
    assert new_state["sources"]["kept"] == state["sources"]["kept"]
    assert transitions == [cs.Transition("gone", "stale", None, "removed from the registry")]


def test_geo_fenced_list_passes_through_and_sets_the_manual_reason():
    master = {"geo_fenced": {"h.example": {"note": "n", "verified": "2026-08-14"}}, "sources": {}}
    committed, state, transitions, _ = do_resolve(
        [obs("g", cs.GEO_FENCED, "verify by hand (last verified 2026-08-14)")], committed=master)
    assert committed["geo_fenced"] == master["geo_fenced"]
    assert committed["sources"]["g"] == {
        "status": "geo-fenced", "since": "2026-10-05",
        "reason": "verify by hand (last verified 2026-08-14)"}
    assert state["sources"]["g"]["fail_count"] == 0


def test_state_tracks_newest_and_the_highest_count():
    _, state, _, _ = do_resolve([obs("a", cs.FRESH, newest="2026-10-01", count=120)])
    assert state["sources"]["a"]["newest"] == "2026-10-01"
    assert state["sources"]["a"]["max_count"] == 120
    assert state["last_run"] == "2026-10-05"
    _, state, _, _ = do_resolve([obs("a", cs.FRESH, newest="", count=None)], state=state)
    assert state["sources"]["a"]["newest"] == "2026-10-01"
    assert state["sources"]["a"]["max_count"] == 120
    _, state, _, _ = do_resolve([obs("a", cs.FRESH, count=80)], state=state)
    assert state["sources"]["a"]["max_count"] == 120


def test_resolve_does_not_mutate_its_inputs():
    master = committed_with(a=("fresh", "2026-09-01", ""))
    state = {"version": 1, "sources": {"a": {"status": "fresh", "fail_count": 0}}}
    before = json.dumps([master, state], sort_keys=True)
    do_resolve([obs("a", cs.HTTP_ERROR, "HTTP 500"), obs("b", cs.STALE, "x")],
               committed=master, state=state)
    assert json.dumps([master, state], sort_keys=True) == before


# -- inconclusive_reason ----------------------------------------------------


def test_inconclusive_when_the_control_failed():
    reason = cs.inconclusive_reason({"a": obs("a", cs.CONTROL_FAILED), "b": obs("b", cs.FRESH)})
    assert reason == "the positive control failed: this runner's network is broken"


def test_inconclusive_when_more_than_80_percent_gave_no_response():
    observations = {f"s{i}": obs(f"s{i}", cs.NO_RESPONSE) for i in range(5)}
    observations["ok"] = obs("ok", cs.FRESH)
    assert cs.inconclusive_reason(observations) == "5 of 6 sources gave no response"


def test_not_inconclusive_below_five_sources():
    observations = {f"s{i}": obs(f"s{i}", cs.NO_RESPONSE) for i in range(4)}
    assert cs.inconclusive_reason(observations) is None


def test_not_inconclusive_at_exactly_80_percent():
    observations = {f"s{i}": obs(f"s{i}", cs.NO_RESPONSE) for i in range(4)}
    observations["ok"] = obs("ok", cs.FRESH)
    assert cs.inconclusive_reason(observations) is None


def test_geo_fenced_observations_are_left_out_of_the_ratio():
    observations = {f"s{i}": obs(f"s{i}", cs.NO_RESPONSE) for i in range(4)}
    for i in range(10):
        observations[f"g{i}"] = obs(f"g{i}", cs.GEO_FENCED)
    assert cs.inconclusive_reason(observations) is None  # 4 non-geo-fenced sources
    observations["s4"] = obs("s4", cs.NO_RESPONSE)
    assert cs.inconclusive_reason(observations) == "5 of 5 sources gave no response"


# -- load_committed, load_state, rendering ----------------------------------


def test_load_committed_missing_file_is_empty(tmp_path):
    assert cs.load_committed(tmp_path / "nope.json") == {"geo_fenced": {}, "sources": {}}


@pytest.mark.parametrize("text", ["{not json", "[]", '{"sources": {}}', '{"geo_fenced": {}}',
                                  '{"sources": [], "geo_fenced": {}}'])
def test_load_committed_rejects_malformed_files(tmp_path, text):
    path = tmp_path / "sources.json"
    path.write_text(text)
    with pytest.raises(ValueError, match="sources.json"):
        cs.load_committed(path)


def test_load_committed_reads_a_valid_file(tmp_path):
    data = committed_with(a=("fresh", "2026-09-01", ""))
    path = tmp_path / "sources.json"
    path.write_text(cs.render_committed(data))
    assert cs.load_committed(path) == data


def test_load_state_missing_or_unreadable_is_empty(tmp_path):
    assert cs.load_state(tmp_path / "nope.json") == {}
    bad = tmp_path / "state.json"
    bad.write_text("{oops")
    assert cs.load_state(bad) == {}
    bad.write_text("[1]")
    assert cs.load_state(bad) == {}


def test_render_committed_is_sorted_indented_and_keeps_unicode():
    text = cs.render_committed({"sources": {"b": {"reason": "→"}}, "geo_fenced": {}})
    assert text == '{\n  "geo_fenced": {},\n  "sources": {\n    "b": {\n      "reason": "→"\n    }\n  }\n}\n'


def test_render_sources_md_sorts_by_status_then_label_and_escapes_pipes():
    sources = [
        cs.Source(id="f", label="Zed fresh", host="f.example", fetch=list, required=()),
        cs.Source(id="s2", label="Beta stale", host="s2.example", fetch=list, required=()),
        cs.Source(id="s1", label="Alpha stale", host="s1.example", fetch=list, required=()),
        cs.Source(id="b", label="Broken one", host="b.example", fetch=list, required=()),
        cs.Source(id="g", label="Geo", host="g.example", fetch=list, required=()),
    ]
    committed = {
        "geo_fenced": {"g.example": {"note": "No response outside India", "verified": "2026-08-14"}},
        "sources": {
            "f": {"status": "fresh", "since": "2026-10-01", "reason": ""},
            "s2": {"status": "stale", "since": "2026-10-02", "reason": "a | b"},
            "s1": {"status": "stale", "since": "2026-10-03", "reason": "r"},
            "b": {"status": "broken", "since": "2026-10-04", "reason": "bad"},
            "g": {"status": "geo-fenced", "since": "2026-10-05", "reason": "verify"},
            "orphan": {"status": "down", "since": "2026-10-06", "reason": "HTTP 500"},
        },
    }
    text = cs.render_sources_md(committed, sources)
    assert text.startswith("<!-- Generated by scripts/check_sources.py. ")
    rows = [line for line in text.splitlines() if line.startswith("| ") and "`" in line]
    assert [row.split("|")[1].strip() for row in rows] == [
        "Broken one", "Alpha stale", "Beta stale", "orphan", "Geo", "Zed fresh"]
    assert "a \\| b" in text
    assert "| orphan |  | `down` |" in text
    assert "## Geo-fenced hosts" in text
    assert "| g.example | No response outside India | 2026-08-14 |" in text
    assert cs.render_sources_md(committed, sources) == text


def test_render_sources_md_omits_the_geo_fenced_section_when_empty():
    text = cs.render_sources_md({"geo_fenced": {}, "sources": {}}, [])
    assert "Geo-fenced hosts" not in text
    assert text.endswith("|---|---|---|---|---|\n")


def test_render_summary_lists_every_observation_and_the_pending_sources():
    text = cs.render_summary(
        {"a": obs("a", cs.FRESH, newest="2026-10-01", count=7),
         "b": obs("b", cs.HTTP_ERROR, "HTTP 500 | x", http_status=500)},
        ["b"], {"sources": {"b": {"fail_count": 1}}})
    assert "| `a` | fresh | 2026-10-01 | 7 |" in text
    assert "HTTP 500 \\| x" in text
    assert "Pending (first failure)" in text


def test_render_pr_body_lists_each_transition():
    text = cs.render_pr_body([
        cs.Transition("a", "fresh", "stale", "newest 2026-03-18"),
        cs.Transition("b", None, "down", "HTTP 500"),
        cs.Transition("c", "stale", None, "removed from the registry"),
    ])
    assert text.startswith("## Status changes\n")
    assert "- `a`: fresh → stale (newest 2026-03-18)" in text
    assert "- `b`: (new) → down (HTTP 500)" in text
    assert "- `c`: stale → (removed)" in text
    assert "SOURCES.md" in text and "manual verification" in text


# -- main -------------------------------------------------------------------


def paths(tmp_path):
    return {
        "state": tmp_path / "state.json",
        "json": tmp_path / "staleness" / "sources.json",
        "md": tmp_path / "SOURCES.md",
        "summary": tmp_path / "summary.md",
        "pr": tmp_path / "pr.md",
        "out": tmp_path / "gh_output",
    }


def argv(p, *extra, today="2026-10-05"):
    return ["--state", str(p["state"]), "--sources-json", str(p["json"]),
            "--sources-md", str(p["md"]), "--summary", str(p["summary"]),
            "--pr-body", str(p["pr"]), "--github-output", str(p["out"]),
            "--today", today, *extra]


def simple_registry(calls=None):
    def fetch_for(id_):
        def fetch():
            if calls is not None:
                calls.append(id_)
            return [{"title": id_}]
        return fetch

    return [
        cs.Source(id="ok", label="Fine source", host="ok.example", fetch=fetch_for("ok"),
                  required=("title",)),
        cs.Source(id="old", label="Old source", host="old.example", fetch=fetch_for("old"),
                  required=("title",), freshness=(FakeRule(ok=False, reason="lag 61"),)),
    ]


def run_main(p, *extra, registry=None, today="2026-10-05"):
    return cs.main(argv(p, *extra, today=today), registry=registry or simple_registry(),
                   control=lambda: True, fetch_document=PDF)


def test_main_normal_run_writes_every_file(tmp_path):
    p = paths(tmp_path)
    assert run_main(p) == 0
    committed = json.loads(p["json"].read_text())
    assert committed["sources"]["ok"]["status"] == "fresh"
    assert committed["sources"]["old"] == {"status": "stale", "since": "2026-10-05", "reason": "lag 61"}
    assert "Old source" in p["md"].read_text()
    assert json.loads(p["state"].read_text())["version"] == 1
    assert "`old`" in p["summary"].read_text()
    assert "`old`: (new) → stale (lag 61)" in p["pr"].read_text()
    assert p["out"].read_text() == "changes=2\n"


def test_main_second_identical_run_changes_nothing(tmp_path):
    p = paths(tmp_path)
    run_main(p)
    tracked = [p["json"], p["md"]]
    for path in tracked:
        os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in tracked]
    p["out"].unlink()
    assert run_main(p, today="2026-10-12") == 0
    assert [(path.read_bytes(), path.stat().st_mtime_ns) for path in tracked] == before
    assert p["out"].read_text() == "changes=0\n"


def test_main_missing_state_with_unchanged_statuses_writes_identical_files(tmp_path):
    p = paths(tmp_path)
    run_main(p)
    first = (p["json"].read_bytes(), p["md"].read_bytes())
    p["state"].unlink()
    assert run_main(p, today="2026-10-19") == 0
    assert (p["json"].read_bytes(), p["md"].read_bytes()) == first


def test_main_malformed_sources_json_returns_1_and_leaves_it_alone(tmp_path, capsys):
    p = paths(tmp_path)
    p["json"].parent.mkdir()
    p["json"].write_text("{not json")
    assert run_main(p) == 1
    assert p["json"].read_text() == "{not json"
    assert not p["md"].exists() and not p["state"].exists()
    assert "sources.json" in capsys.readouterr().err


def test_main_duplicate_registry_ids_return_1_before_any_fetch(tmp_path):
    p = paths(tmp_path)
    calls = []
    registry = simple_registry(calls) + simple_registry(calls)
    assert run_main(p, registry=registry) == 1
    assert calls == []
    assert not p["json"].exists()


def test_main_unknown_only_id_returns_1(tmp_path, capsys):
    p = paths(tmp_path)
    assert run_main(p, "--only", "nope") == 1
    assert "nope" in capsys.readouterr().err
    assert not p["json"].exists()


def test_main_only_checks_a_subset_and_keeps_the_rest(tmp_path):
    p = paths(tmp_path)
    run_main(p)
    calls = []
    assert run_main(p, "--only", "ok", registry=simple_registry(calls), today="2026-10-12") == 0
    assert calls == ["ok"]
    assert json.loads(p["json"].read_text())["sources"]["old"]["status"] == "stale"


def test_main_inconclusive_run_returns_2_and_writes_no_committed_files(tmp_path, capsys):
    p = paths(tmp_path)

    def down():
        raise ConnectionError("no route")

    registry = [cs.Source(id=f"s{i}", label=f"S{i}", host=f"h{i}.example", fetch=down,
                          required=("title",)) for i in range(5)]
    assert run_main(p, registry=registry) == 2
    assert not p["json"].exists() and not p["md"].exists() and not p["state"].exists()
    assert not p["pr"].exists() and not p["out"].exists()
    assert "5 of 5 sources gave no response" in capsys.readouterr().err
    assert p["summary"].exists()


def test_main_geo_fenced_host_is_skipped_with_the_manual_reason(tmp_path):
    p = paths(tmp_path)
    p["json"].parent.mkdir()
    p["json"].write_text(cs.render_committed(
        {"geo_fenced": {"old.example": {"note": "n", "verified": "2026-08-14"}}, "sources": {}}))
    calls = []
    assert run_main(p, registry=simple_registry(calls)) == 0
    assert calls == ["ok"]
    committed = json.loads(p["json"].read_text())
    assert committed["sources"]["old"]["status"] == "geo-fenced"
    assert committed["sources"]["old"]["reason"] == "verify by hand (last verified 2026-08-14)"
    assert committed["geo_fenced"]["old.example"]["verified"] == "2026-08-14"


@pytest.mark.parametrize("value, note", [
    ("blocks non-India IPs", "blocks non-India IPs"),
    (None, ""),
    (["odd"], ""),
])
def test_main_tolerates_a_geo_fenced_value_that_is_not_an_object(tmp_path, value, note):
    p = paths(tmp_path)
    p["json"].parent.mkdir()
    p["json"].write_text(cs.render_committed(
        {"geo_fenced": {"old.example": value}, "sources": {}}))
    assert run_main(p, registry=simple_registry([])) == 0
    committed = json.loads(p["json"].read_text())
    assert committed["sources"]["old"]["reason"] == "verify by hand (last verified unknown)"
    assert committed["geo_fenced"]["old.example"] == value  # the hand-edited list is kept as is
    assert f"| old.example | {note} |  |" in p["md"].read_text()


def test_main_passes_the_whole_state_dict_so_count_floor_sees_max_count(tmp_path):
    p = paths(tmp_path)
    p["state"].write_text(json.dumps(
        {"version": 1, "sources": {"floor": {"status": "fresh", "max_count": 100}}}))
    registry = [cs.Source(id="floor", label="Floor", host="f.example",
                          fetch=lambda: [{"title": "t"}], required=("title",),
                          total=lambda: 90, freshness=(cs.CountFloor(),))]
    assert run_main(p, registry=registry) == 0
    committed = json.loads(p["json"].read_text())
    assert committed["sources"]["floor"]["status"] == "stale"
    assert "below 95%" in committed["sources"]["floor"]["reason"]
    assert json.loads(p["state"].read_text())["sources"]["floor"]["max_count"] == 100


def test_main_creates_the_staleness_directory(tmp_path):
    p = paths(tmp_path)
    assert not p["json"].parent.exists()
    run_main(p)
    assert p["json"].parent.is_dir()


def test_main_reports_a_missing_registry_package(tmp_path, capsys, monkeypatch):
    p = paths(tmp_path)

    def missing(name):
        raise ImportError(f"No module named {name!r}")

    monkeypatch.setattr(cs.importlib, "import_module", missing)
    code = cs.main(argv(p), control=lambda: True, fetch_document=PDF)
    assert code == 1
    assert "source_registry" in capsys.readouterr().err


# -- document status and empty bodies (review fix 3) ------------------------


@pytest.mark.parametrize("kind", ["pdf", "spreadsheet", "zip", "any-non-html"])
def test_document_empty_body_is_broken(kind):
    s = src(lambda: [{"title": "a", "u": "x"}], document=lambda r: r["u"], document_kind=kind)
    obs, _ = check(s, fetch_document=lambda u, h: (200, "application/octet-stream", b""))
    assert obs.outcome == cs.BROKEN and obs.reason == "document: empty body"


def test_document_whitespace_only_body_is_broken():
    s = src(lambda: [{"title": "a", "u": "x"}], document=lambda r: r["u"],
            document_kind="any-non-html")
    obs, _ = check(s, fetch_document=lambda u, h: (200, "text/csv", b" \r\n\t"))
    assert obs.outcome == cs.BROKEN and obs.reason == "document: empty body"


@pytest.mark.parametrize("status", [202, 204, 301, 302, 304])
def test_document_status_that_serves_no_document_is_broken(status):
    # A WAF challenge answers 202, and a 3xx that reaches the checker is a
    # redirect the client didn't follow; neither is the document.
    s = src(lambda: [{"title": "a", "u": "x"}], document=lambda r: r["u"],
            document_kind="any-non-html")
    obs, _ = check(s, fetch_document=lambda u, h: (status, "text/csv", b"a,b\n1,2\n"))
    assert obs.outcome == cs.BROKEN
    assert obs.reason.startswith(f"document: HTTP {status}")


def test_document_200_with_csv_passes_any_non_html():
    s = src(lambda: [{"title": "a", "u": "x"}], document=lambda r: r["u"],
            document_kind="any-non-html")
    obs, _ = check(s, fetch_document=lambda u, h: (200, "text/csv", b"a,b\n1,2\n"))
    assert obs.outcome == cs.FRESH
