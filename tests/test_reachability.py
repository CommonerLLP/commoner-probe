"""Geo-fencing is per-host, and it has to be measured.

`goir.ap.gov.in` returns 000 from Canadian egress and serves from ap-south-1.
`apsac.ap.gov.in` serves from both. A session assumed the second needed an
Indian host because the first did, and wasted the setup on it.

No network. Every probe is an injected callable.
"""

from __future__ import annotations

import pytest

from commoner_probe.reachability import (
    INCONCLUSIVE,
    NOT_MEASURED,
    REACHABLE,
    UNREACHABLE,
    reachability,
    status_via_session,
)


class FakeSession:
    """A session that answers GET and fails loudly on HEAD."""

    def __init__(self, code=200):
        self.code = code
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        return type("Response", (), {"status_code": self.code})()

    def head(self, url, **kwargs):
        raise AssertionError("a HEAD request must never decide reachability")


def ok(_url):
    return 200


def blocked(_url):
    raise OSError("nodename nor servname provided")


class TestFromHere:
    def test_a_host_that_answers_is_reachable(self):
        result = reachability("apsac.ap.gov.in", fetch=ok)
        assert result.here.status == REACHABLE
        assert result.here.code == 200

    def test_a_404_is_reachable_because_the_host_answered(self):
        """A HEAD on `goir.ap.gov.in` returns 404 for every path, including ones
        that serve. Any HTTP status proves the host answered."""
        result = reachability("goir.ap.gov.in", fetch=lambda url: 404)
        assert result.here.status == REACHABLE
        assert result.here.code == 404

    def test_no_answer_with_a_passing_control_is_unreachable(self):
        def fetch(url):
            if "goir" in url:
                raise OSError("timed out")
            return 200

        result = reachability("goir.ap.gov.in", fetch=fetch)
        assert result.here.status == UNREACHABLE
        assert result.here.control_code == 200

    def test_a_curl_style_zero_code_with_a_passing_control_is_unreachable(self):
        """`curl` reports 000 when nothing answered. It is not an HTTP status."""
        result = reachability("goir.ap.gov.in", fetch=lambda url: 0 if "goir" in url else 200)
        assert result.here.status == UNREACHABLE

    def test_no_answer_and_a_failing_control_is_inconclusive_not_unreachable(self):
        """A DNS blip on the operator's laptop killed a sweep on 2026-08-14. It
        says nothing about the host."""
        result = reachability("goir.ap.gov.in", fetch=blocked)
        assert result.here.status == INCONCLUSIVE
        assert "control" in result.here.detail

    def test_a_control_that_answers_an_error_status_does_not_pass(self):
        result = reachability("goir.ap.gov.in", fetch=lambda url: 0 if "goir" in url else 503)
        assert result.here.status == INCONCLUSIVE


class TestFromIndia:
    def test_no_relay_reports_not_measured_and_never_unreachable(self):
        result = reachability("goir.ap.gov.in", fetch=blocked)
        assert result.india.status == NOT_MEASURED
        assert result.india.code is None

    def test_the_report_names_the_india_side_as_not_measured(self):
        result = reachability("goir.ap.gov.in", fetch=ok)
        assert "not measured" in result.report

    def test_a_relay_that_answers_makes_the_india_side_reachable(self):
        result = reachability("goir.ap.gov.in", fetch=blocked, relay=ok)
        assert result.india.status == REACHABLE

    def test_a_broken_relay_is_inconclusive_not_unreachable(self):
        """Never report unreachable-from-India when the relay itself failed."""
        result = reachability("goir.ap.gov.in", fetch=ok, relay=blocked)
        assert result.india.status == INCONCLUSIVE

    def test_a_relay_reaching_the_control_but_not_the_host_is_unreachable(self):
        def relay(url):
            if "goir" in url:
                return 0
            return 200

        result = reachability("goir.ap.gov.in", fetch=ok, relay=relay)
        assert result.india.status == UNREACHABLE


class TestVerdict:
    def test_the_goir_case_needs_indian_egress(self):
        result = reachability("goir.ap.gov.in", fetch=blocked, relay=ok)
        assert result.here.status == INCONCLUSIVE
        result = reachability(
            "goir.ap.gov.in",
            fetch=lambda url: 0 if "goir" in url else 200,
            relay=ok,
        )
        assert result.needs_indian_egress is True

    def test_the_apsac_case_does_not_need_indian_egress(self):
        """The wasted setup. apsac serves from both sides."""
        result = reachability("apsac.ap.gov.in", fetch=ok, relay=ok)
        assert result.needs_indian_egress is False

    def test_an_unmeasured_india_side_leaves_the_verdict_unestablished(self):
        result = reachability("goir.ap.gov.in", fetch=lambda url: 0 if "goir" in url else 200)
        assert result.needs_indian_egress is None
        assert "not established" in result.report

    def test_a_reachable_host_needs_no_relay_verdict(self):
        result = reachability("apsac.ap.gov.in", fetch=ok)
        assert result.needs_indian_egress is False


class TestUrlHandling:
    def test_a_bare_host_becomes_an_https_url(self):
        seen = []

        def fetch(url):
            seen.append(url)
            return 200

        reachability("goir.ap.gov.in", fetch=fetch)
        assert seen[0] == "https://goir.ap.gov.in/"

    def test_a_full_url_is_probed_as_given(self):
        seen = []

        def fetch(url):
            seen.append(url)
            return 200

        reachability("http://goir.ap.gov.in/GOIR/", fetch=fetch)
        assert seen[0] == "http://goir.ap.gov.in/GOIR/"

    def test_a_blank_host_raises_rather_than_probing_nothing(self):
        with pytest.raises(ValueError):
            reachability("  ", fetch=ok)


class TestSessionProbe:
    def test_it_uses_get_and_never_head(self):
        """`curl -I` reported goir dead. A GET on the same URL returned the
        register."""
        session = FakeSession(code=404)
        assert status_via_session(session, "https://goir.ap.gov.in/") == 404
        assert session.calls[0][0] == "get"

    def test_it_does_not_raise_for_an_error_status(self):
        session = FakeSession(code=503)
        assert status_via_session(session, "https://goir.ap.gov.in/") == 503
