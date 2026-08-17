"""Parser and URL tests for commoner_probe.cdn_dashboard.

These never touch the network; the payloads are verbatim captures from
Dahod (state 17, district 112) for June 2026, cross-checked against the
rendered page.

The load-bearing case is test_growth_keeps_both_age_bands. The growth payload
reports stunting and underweight TWICE under identical `title` values, for two
different populations: 0-6 years (the programme population ICDS serves) and
0-5 years (the WHO Child Growth Standards band that NFHS and SDG 2.2 use). The
values differ — 22% vs 21% stunting, 10% vs 8% underweight — and the wider band
always reads higher, because stunting accumulates with age. A parser keying on
`title` alone silently keeps whichever row came last, so the number changes by
a quarter with no error anywhere. `title2` is the only field separating them.

test_payload_url_uses_internal_ids guards the other trap: CDN paths key on the
API's own `id` fields, not the census codes sitting beside them in the same
record. A census code returns 403, which the bucket also returns for a period
that was never published, so the mistake is invisible.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest

from commoner_probe import cdn_dashboard as pt

GROWTH = {
    "growthmonitoring": [{
        "id": 17,
        "districts": [{
            "district_id": 112,
            "data": [
                {"count": 242126, "title1": "(0 - 6 Years)", "percentage": "97.00",
                 "title": "Children Measured"},
                {"title1": "Severely/Moderately Stunted", "title2": "0m_6y",
                 "percent": "22.00", "title": "Stunting"},
                {"title1": "Severely/Moderately Underweight", "title2": "0m_6y",
                 "percent": "10.00", "title": "Underweight"},
                {"count": 217190, "title1": "(0 - 5 Years)", "percentage": "97.00",
                 "title": "Children Measured"},
                {"title1": "SAM/MAM", "title2": "0m_5y", "percent": "1.00",
                 "title": "Wasting"},
                {"title1": "Obese/Overweight", "title2": "0m_5y", "percent": "0.00",
                 "title": "Overweight"},
                {"title1": "Severely/Moderately Stunted", "title2": "0m_5y",
                 "percent": "21.00", "title": "Stunting"},
                {"title1": "Severely/Moderately Underweight", "title2": "0m_5y",
                 "percent": "8.00", "title": "Underweight"},
            ],
        }],
    }]
}

REGISTRATION = {
    "registrationPTdata": [{
        "id": 17,
        "districts": [{
            "district_id": 112,
            "data": [
                {"count": 0, "title": "States/UTs"},
                {"count": 0, "title": "Districts"},
                {"count": 21, "title": "Project"},
                {"count": 122, "title": "Sector"},
                {"count": 3056, "title": "Anganwadi Centers"},
                {"count": 2990, "title": "Anganwadi Workers"},
                {"count": 2912, "title": "Anganwadi Helpers"},
                {"count": 274458, "title": "Eligible Beneficiaries"},
                {"count": 46601, "title": "Adolescent Girl"},
            ],
        }],
    }]
}

INFRASTRUCTURE = {
    "awcInfrastructureCount": [{
        "id": 17,
        "districts": [{
            "district_id": 112,
            "data": [
                {"count": 1985, "state_id": 17, "district_id": 112,
                 "title": "awc_owned_building"},
                {"count": 3055, "state_id": 17, "district_id": 112,
                 "title": "awc_functional_toilet"},
                {"count": 3056, "state_id": 17, "district_id": 112,
                 "title": "awc_drinking_water_source"},
            ],
        }],
    }]
}


def test_growth_keeps_both_age_bands():
    out = pt.parse_growth(GROWTH)
    assert out["stunting_pct_0m_6y"] == 22.0
    assert out["stunting_pct_0m_5y"] == 21.0
    assert out["underweight_pct_0m_6y"] == 10.0
    assert out["underweight_pct_0m_5y"] == 8.0


def test_growth_keeps_both_denominators():
    """The bands are two populations, not two views of one."""
    out = pt.parse_growth(GROWTH)
    assert out["children_measured_0m_6y"] == 242126
    assert out["children_measured_0m_5y"] == 217190
    assert out["children_measured_0m_6y"] > out["children_measured_0m_5y"]


def test_wasting_and_overweight_are_under_five_only():
    """WHO does not define these past 59 months, and the payload reflects that."""
    out = pt.parse_growth(GROWTH)
    assert out["wasting_pct_0m_5y"] == 1.0
    assert out["overweight_pct_0m_5y"] == 0.0
    assert "wasting_pct_0m_6y" not in out
    assert "overweight_pct_0m_6y" not in out


def test_parse_registration():
    out = pt.parse_registration(REGISTRATION)
    assert out["anganwadi_centres"] == 3056
    assert out["anganwadi_workers"] == 2990
    assert out["anganwadi_helpers"] == 2912
    assert "States/UTs" not in out


def test_parse_infrastructure():
    out = pt.parse_infrastructure(INFRASTRUCTURE)
    assert out["awc_owned_building"] == 1985
    assert out["awc_functional_toilet"] == 3055
    assert out["awc_drinking_water_source"] == 3056


def test_payload_url_uses_internal_ids():
    """Gujarat is 17 and Dahod is 112 — NOT census codes 24 and 24445."""
    url = pt.payload_url(2026, 6, 17, 112, "PT_Dashboard")
    assert url.endswith("/2026/6/17/112/112_PT_Dashboard.json")
    assert "24445" not in url


def test_payload_url_month_is_unpadded():
    """A zero-padded month 403s."""
    assert "/2026/6/" in pt.payload_url(2026, 6, 17, 112, "PT_Dashboard")
    assert "/06/" not in pt.payload_url(2026, 6, 17, 112, "PT_Dashboard")


def test_months_spans_the_year_boundary():
    assert pt.months((2022, 11), (2023, 2)) == [
        (2022, 11), (2022, 12), (2023, 1), (2023, 2)
    ]


def test_months_covers_the_published_record():
    """2022-04 is the true floor; the site's own selector starts 2023-02."""
    assert pt.months(pt.EARLIEST, (2026, 6))[0] == (2022, 4)
    assert len(pt.months(pt.EARLIEST, (2026, 6))) == 51


class _Resp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError("raise_for_status called on an absent period")


class _Session:
    def __init__(self, status_code, payload=None):
        self._resp = _Resp(status_code, payload)
        self.calls: list[tuple] = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._resp


PLACE = pt.Place(17, "Gujarat", 112, "Dahod", "24", "24445")


@pytest.mark.parametrize("status", [403, 404])
def test_absent_period_returns_none(status):
    """The bucket denies listing, so an unpublished period and a wrong path are
    the same response. Neither is an error."""
    assert pt.fetch(_Session(status), 2021, 1, PLACE, "growth") is None


def test_fetch_parses_a_present_period():
    session = _Session(200, GROWTH)
    assert pt.parse_growth(pt.fetch(session, 2026, 6, PLACE, "growth"))["stunting_pct_0m_5y"] == 21.0


def test_fetch_skips_the_cdn_robots_check():
    """cdn.poshantracker.in serves no robots.txt, and RobotFileParser turns the
    resulting 403 into disallow-all. The publisher's real policy, at
    www.poshantracker.in, explicitly allows /statistics."""
    session = _Session(200, GROWTH)
    pt.fetch(session, 2026, 6, PLACE, "growth")
    assert session.calls[0][1]["respect_robots"] is False


def _envelope() -> str:
    return base64.b64encode(json.dumps({
        "iv": base64.b64encode(b"\x00" * 16).decode(),
        "value": base64.b64encode(b"ciphertext").decode(),
        "mac": hmac.new(b"wrong-key", b"whatever", hashlib.sha256).hexdigest(),
    }).encode()).decode()


def test_decrypt_envelope_rejects_a_tampered_mac():
    with pytest.raises(ValueError, match="MAC mismatch"):
        pt.decrypt_envelope(_envelope(), "0" * 32)


def test_no_key_says_so_rather_than_decrypting_with_nothing(monkeypatch):
    """The key is not this package's to carry, and it rotates. A missing one
    must name itself here, not surface as a JSON parse error further down."""
    monkeypatch.delenv(pt.ENVELOPE_KEY_ENV, raising=False)
    with pytest.raises(ValueError, match=pt.ENVELOPE_KEY_ENV):
        pt.decrypt_envelope(_envelope())


def test_the_key_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv(pt.ENVELOPE_KEY_ENV, "0" * 32)
    assert pt.envelope_key() == b"0" * 32
    with pytest.raises(ValueError, match="MAC mismatch"):
        pt.decrypt_envelope(_envelope())


def test_no_key_is_baked_into_the_package():
    """A published library must not ship a key, whatever its provenance."""
    import inspect
    import re

    source = inspect.getsource(pt)
    assert not re.search(r'=\s*b?["\'][0-9A-F]{32}["\']', source), \
        "a 32-char hex literal in the module reads as an embedded key"


# ---- added after both home-visit fields changed definition at 2026-04 ----

HOME_VISIT_OLD = {"homevisit": [{"districts": [{"district_id": 112, "data": [
    {"count": 18122, "title": "Pregnant Women &", "title1": "Lactating Mother"},
    {"count": 45347, "title": "Children", "title1": "(0 - 2 Years)"}]}], "id": 17}]}

HOME_VISIT_NEW = {"homevisit": [{"districts": [{"district_id": 112, "data": [
    {"count": 6129, "title": "Pregnant Women", "title1": ""},
    {"count": 46357, "title": "Children", "title1": "(0 - 3 Years)"}]}], "id": 17}]}


def test_home_visit_keys_carry_the_qualifier():
    """`title` alone hides a definitional change; `title1` holds the rest."""
    old = pt.parse_home_visit(HOME_VISIT_OLD)
    assert "home_visit_pregnant_women_lactating_mother" in old
    assert old["home_visit_pregnant_women_lactating_mother"] == 18122
    assert "home_visit_children_0_2_years" in old


def test_home_visit_definitions_do_not_collide_across_the_2026_04_boundary():
    """The whole point: two definitions must NOT share a key.

    Keyed on `title` alone, 2026-03's 18,122 (pregnant women AND lactating
    mothers) and 2026-04's 6,129 (pregnant women only) both landed on
    `home_visit_pregnant_women`, producing a 63% "fall" that never happened.
    The children band widened 0-2y to 0-3y in the same month, equally hidden.
    """
    old, new = pt.parse_home_visit(HOME_VISIT_OLD), pt.parse_home_visit(HOME_VISIT_NEW)
    assert set(old) & set(new) == set()
    assert "home_visit_pregnant_women" in new
    assert "home_visit_children_0_3_years" in new
