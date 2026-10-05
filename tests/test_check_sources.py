"""Tests for scripts/check_sources.py — the source freshness check engine.

Every test uses fakes; none touches the network.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest
import requests

from commoner_probe.http_client import ChallengeDetected

# Load the script as a module without requiring it on PYTHONPATH.
_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_sources.py"
_spec = importlib.util.spec_from_file_location("check_sources", _SCRIPT)
assert _spec and _spec.loader
cs = importlib.util.module_from_spec(_spec)
sys.modules["check_sources"] = cs
_spec.loader.exec_module(cs)

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


def test_document_fetch_raising_is_broken():
    def fetch_document(url, headers):
        raise TimeoutError("slow")

    s = src(lambda: [{"title": "a", "u": "x"}], document=lambda r: r["u"])
    obs, _ = check(s, fetch_document=fetch_document)
    assert obs.outcome == cs.BROKEN and obs.reason.startswith("document: ")


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
