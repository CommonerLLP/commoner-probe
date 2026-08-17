"""A WAF challenge is a 2xx, so every ordinary check passes it.

Measured against a Harvard Dataverse API on 2026-08-14: every scripted request
answered HTTP 202, `server: awselb/2.0`, `x-amzn-waf-action: challenge`,
`content-length: 0`. `raise_for_status()` succeeds and `json.loads(b"")` then
throws a confusing decode error, so the natural next move is to doubt the URL.
The DOI was correct the whole time.

No network.
"""

from __future__ import annotations

import pytest

from commoner_probe.http_client import (
    ChallengeDetected,
    challenge_reason,
    refuse_challenge,
)


class _Resp:
    def __init__(self, *, status=200, text="", headers=None):
        self.status_code = status
        self.text = text
        self.headers = {} if headers is None else headers


class TestTheHeaderIsUnambiguous:
    def test_the_waf_action_header_is_a_challenge_at_any_status(self):
        resp = _Resp(status=202, headers={"x-amzn-waf-action": "challenge"})
        assert "WAF answered" in (challenge_reason(resp) or "")

    def test_the_header_is_matched_whatever_its_case(self):
        resp = _Resp(status=200, text="{}", headers={"X-Amzn-Waf-Action": "challenge"})
        assert challenge_reason(resp) is not None

    def test_a_normal_response_is_not_a_challenge(self):
        assert challenge_reason(_Resp(status=200, text="{}")) is None


class TestTheEmptyBodyIsTheCallers_Statement:
    def test_an_empty_2xx_is_a_challenge_where_a_body_was_expected(self):
        reason = challenge_reason(_Resp(status=202, text=""), expect_body=True)
        assert reason is not None
        assert "not an empty result" in reason

    def test_an_empty_2xx_is_not_a_challenge_where_no_body_was_expected(self):
        """A 204 and a HEAD are legitimately empty, so this must not fire on them."""
        assert challenge_reason(_Resp(status=204, text="")) is None

    def test_a_4xx_with_an_empty_body_is_an_error_not_a_challenge(self):
        assert challenge_reason(_Resp(status=403, text=""), expect_body=True) is None

    def test_a_response_with_no_headers_attribute_is_handled(self):
        """The stdlib fallback response exposes no headers at all."""
        class Bare:
            status_code = 200
            text = "{}"

        assert challenge_reason(Bare()) is None


class TestRefusing:
    def test_it_raises_and_names_the_url(self):
        resp = _Resp(status=202, headers={"x-amzn-waf-action": "challenge"})
        with pytest.raises(ChallengeDetected) as excinfo:
            refuse_challenge(resp, "https://dataverse.harvard.edu/api/info/version")
        assert "dataverse.harvard.edu" in str(excinfo.value)

    def test_it_is_silent_on_a_real_answer(self):
        refuse_challenge(_Resp(status=200, text="{}"), "https://example.gov.in/api")
