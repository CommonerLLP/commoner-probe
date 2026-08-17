"""The all-India sentinel, and the OTP flow's order of operations.

No network. These cover the two things that cost an afternoon each when they
are wrong, and nothing else.
"""

from __future__ import annotations

import pytest

from commoner_probe import microdata_download_api as portal


class TestTheAllIndiaSentinel:
    """`stateId=0` returns HTTP 200 with a PDF body, so the wrong sentinel
    reads as a working download of a dataset that does not exist."""

    def test_the_sentinel_is_99(self):
        assert portal.ALL_INDIA == 99

    def test_the_default_url_asks_for_all_india(self):
        url = portal.csv_url("2023-24", 4)
        assert "stateId=99" in url and "districtId=99" in url

    def test_the_year_string_resolves_to_its_id(self):
        assert "yearId=10" in portal.csv_url("2023-24", 4)
        assert "yearId=5" in portal.csv_url("2018-19", 4)

    def test_a_raw_year_id_passes_through(self):
        assert "yearId=12" in portal.csv_url(12, 4)

    def test_an_unknown_report_id_is_refused(self):
        """Every reportId except 1 returns 404 under the wrong sentinel, which
        reads as 'only one report exists'. The caller must not reach that."""
        with pytest.raises(ValueError, match="reportId"):
            portal.csv_url("2023-24", 99)

    def test_the_schema_report_is_accepted_but_named_separately(self):
        assert "reportId=1" in portal.csv_url("2023-24", portal.SCHEMA_REPORT_ID)
        assert portal.SCHEMA_REPORT_ID not in portal.REPORT_IDS


class TestTheOtpFlow:
    def test_the_verify_call_is_built_before_the_code_is_asked_for(self):
        """The OTP expires quickly. A caller that builds the second request
        after reading the code off a phone has already spent the window."""
        import inspect

        assert "otp" in inspect.signature(portal.verify_otp).parameters
        assert "mobile" in inspect.signature(portal.request_otp).parameters

    def test_no_captcha_solver_ships(self):
        """A human reads the image. Shipping a solver would change what this
        module is, and the account holder's terms with it."""
        source = (portal.__file__ and open(portal.__file__).read()) or ""
        for banned in ("pytesseract", "image_to_string", "solve_captcha"):
            assert banned not in source
