# SPDX-License-Identifier: MIT
"""Download bulk microdata from a portal that gates it behind a mobile OTP.

CONTEXT
=======
The Ministry of Education operates the source. The programme is UDISE+.
It is the national school-data system of India.
Two portals serve it. KYS answers hierarchy queries and holds no microdata.
The Data Sharing Portal holds the microdata and needs an account.
The hosts are kys.udiseplus.gov.in and microdata.udiseplus.gov.in.
The portal serves six CSV datasets for each academic year, from 2018-19.
A human must read the captcha image. This module ships no solver.
The OTP reaches the phone of the account holder. It expires quickly.

India's school data sits behind two Ministry of Education portals with completely
different contracts:

    KYS   https://kys.udiseplus.gov.in/web-app/api/     hierarchy, open, no data
    DSP   https://microdata.udiseplus.gov.in/dsp        the microdata, account required

The DSP is the one that matters. It serves **six CSV datasets per academic year,
all-India, 2018-19 to 2025-26** — profile x2, enrolment x2, facility, teacher. This
module records the whole route, because every step of it has a trap that returns a
plausible wrong answer instead of an error.

THE FIVE TRAPS, IN THE ORDER YOU MEET THEM
==========================================

**1. Egress.** The DSP times out from a non-Indian connection and answers 200 from
ap-south-1. DNS resolves either way (164.100.211.195). A blanket timeout is NOT an
outage — re-test from an Indian egress, or tunnel (``ssh -D 1080`` to an Indian box
and point the client at ``socks5://localhost:1080``).

**2. The API base is not in the page.** It is compiled into a single 2.25 MB Angular
bundle — there are no lazy-loaded chunks — as ``Y3_apiBaseUrl``, and endpoints are
assembled with template literals, so grepping for ``"/api/..."`` string literals
finds almost nothing. What works::

    grep -oE '[A-Za-z0-9_$]+_apiBaseUrl' main.*.js | head -1
    grep -oE '\\$\\{VAR\\}[A-Za-z0-9_/{}$.-]{2,60}' main.*.js | sort -u

**3. THE ALL-INDIA SENTINEL IS 99, NOT 0 — and 0 fails silently.** This is the
expensive one. ``stateId=0&districtId=0`` returns HTTP 200 with
``Content-Type: application/zip`` and a body that is actually ``%PDF-1.7``: the
schema document, not data. Every ``reportId`` except 1 then 404s, which reads
convincingly as "only one report exists". With ``stateId=99&districtId=99`` the same
URL returns the real 30-70 MB zip and reportIds 2-7 all work. Nothing anywhere says
99. **Always verify the payload's magic bytes are ``PK``, never trust the
Content-Type header.**

**4. The district select is disabled when All States is chosen.** Driving the form
with a browser, selecting into it times out. That is correct behaviour, not a bug.

**5. The schema PDF is two documents, not eight.** ``reportId=1`` across the eight
years yields four identical copies of one file and four of another (verified by
sha256): one schema for 2018-19..2021-22, another for 2022-23..2025-26.

THE AUTH FLOW
=============
Mobile OTP, three calls::

    GET  /api/public/captcha              -> {captchaKey, captchaImage}   (public)
    POST /api/v1/auth/mobile/send-otp     {mobile, captcha, captchaKey}
    POST /api/v1/auth/mobile/verify-otp   {mobile, otp} -> {accessToken, refreshToken}

The captcha image must be read by a human — this module deliberately provides no
solver. The OTP goes to the account holder's phone and expires quickly, so build the
verify call BEFORE asking for the code rather than after.

Thereafter ``Authorization: Bearer <accessToken>``. There is also
``/api/v1/auth/login`` {mobile, password, captcha, captchaKey} if a password is set,
and ``/api/v1/auth/refresh-token`` to extend a session mid-pull.

To drive the SPA instead of the API, inject the session into ``sessionStorage`` under
the keys ``token``, ``refreshToken``, ``user``, ``sessionStart`` and navigate to the
hash route ``#/CSVdata``. The app is hash-routed: ``/CSVdata`` as a path is a Tomcat
404, ``#/CSVdata`` is the page.

TERMS — READ BEFORE REDISTRIBUTING ANYTHING
===========================================
The portal's Data Sharing Policy, agreed at download time:

  - data shall **not be redistributed to other parties without prior consent**
  - **source must be acknowledged in all usages**
  - no unauthorised re-identification of anonymised data

That first clause constrains open-data deposits: derived aggregates and figures are
fine, republishing the raw rows is not. The schema also confirms the DSP substitutes
a ``pseudocode`` for the school's real UDISE code, and keys records on village NAME
rather than village code — so DSP rows answer "which schools are in village X" and
cannot be joined to a UDISE code without a separate bridge.
"""
from __future__ import annotations

import re
from typing import Any

from .http_client import make_session

__all__ = [
    "KYS_BASE", "DSP_BASE", "KYS_ENDPOINTS", "DSP_ENDPOINTS",
    "ALL_INDIA", "YEAR_IDS", "REPORT_IDS", "SCHEMA_REPORT_ID",
    "csv_url", "request_otp", "verify_otp", "probe_public",
]

KYS_BASE = "https://kys.udiseplus.gov.in/web-app/api/"
DSP_BASE = "https://microdata.udiseplus.gov.in/dsp"

#: The all-India sentinel. NOT 0 — see trap 3 in the module docstring.
ALL_INDIA = 99

#: Academic year -> yearId. From GET /csv-download/years.
YEAR_IDS: dict[str, int] = {
    "2018-19": 5, "2019-20": 6, "2020-21": 7, "2021-22": 8,
    "2022-23": 9, "2023-24": 10, "2024-25": 11, "2025-26": 12,
}

#: reportId -> the dataset it returns, and the filename stem the server sends.
#: Verified by Content-Disposition on ranged requests, 2026-08-15.
REPORT_IDS: dict[int, str] = {
    2: "profile_data_2",     # RTE and school management
    3: "profile_data_1",     # basic profile and location
    4: "facility_data",      # infrastructure and facilities
    5: "teacher_data",       # teacher and staff academic
    6: "enrolment_data_1",   # social category and minority
    7: "enrolment_data_2",   # age-wise enrolment
}
#: reportId=1 is the schema PDF, not data — and only two distinct PDFs exist.
SCHEMA_REPORT_ID = 1

KYS_ENDPOINTS: dict[str, str] = {
    "years": "getYears",
    "states": "getStates/{year_id}",
    "districts": "getDistricts/{state_id}/{year_id}",
    "blocks": "getBlocks/{district_id}/{year_id}",
    "managements": "getManagements",
    "categories": "getCategories",
    # Served, which is why KYS school SEARCH is gated: the captcha is real. A
    # school DETAIL page is reachable at /schooldetail/{udise}/{yearId} if the
    # code is already known, so KYS is a lookup, never a search.
    "captcha": "getCaptcha",
}

DSP_ENDPOINTS: dict[str, str] = {
    "captcha": "/api/public/captcha",
    "csv_download": "/csv-download",
    "csv_years": "/csv-download/years",
    "login": "/api/v1/auth/login",
    "send_otp": "/api/v1/auth/mobile/send-otp",
    "verify_otp": "/api/v1/auth/mobile/verify-otp",
    "refresh_token": "/api/v1/auth/refresh-token",
    "logout": "/api/v1/logout",
    # registration form scaffolding, not data
    "reg_submit": "/api/v1/registration/submit",
    "reg_otp": "/api/v1/registration/send-reg-otp",
    "reg_page_data": "/api/v1/registration/page-data",
    "reg_countries": "/api/v1/registration/countrys",
    "reg_states": "/api/v1/registration/states/{year_id}",
    "reg_districts": "/api/v1/registration/districts/{state_id}/{x}",
    # admin only; 403 for an ordinary account
    "admin_users": "/api/admin/users",
}


def csv_url(year: str | int, report_id: int, *, base: str = DSP_BASE,
            state_id: int = ALL_INDIA, district_id: int = ALL_INDIA) -> str:
    """The download URL for one dataset.

    ``year`` accepts "2018-19" or the raw yearId. Defaults are the all-India
    sentinel; passing 0 silently returns the schema PDF instead of data.
    """
    yid = YEAR_IDS[year] if isinstance(year, str) else year
    if report_id not in REPORT_IDS and report_id != SCHEMA_REPORT_ID:
        raise ValueError(f"reportId {report_id} is not one of "
                         f"{sorted(REPORT_IDS)} (data) or {SCHEMA_REPORT_ID} (schema)")
    return (f"{base}/csv-download?stateId={state_id}&districtId={district_id}"
            f"&yearId={yid}&reportId={report_id}")


def _captcha(base: str, session: Any, timeout: int) -> tuple[str, bytes]:
    import base64
    r = session.get(base + DSP_ENDPOINTS["captcha"], timeout=timeout)
    d = r.json().get("data", {})
    img = d.get("captchaImage") or d.get("image") or ""
    img = re.sub(r"^data:image/\w+;base64,", "", img)
    return d.get("captchaKey"), base64.b64decode(img) if img else b""


def request_otp(mobile: str, *, base: str = DSP_BASE, session: Any = None,
                timeout: int = 60, solve: Any = None) -> dict[str, Any]:
    """Fetch a captcha and send an OTP to ``mobile``.

    ``solve`` is a callable taking the PNG bytes and returning the characters. It
    is REQUIRED and has no default: a human reads the captcha. This module does not
    ship a solver, and adding one here would defeat a control the portal is
    entitled to have.

    Returns the server's reply plus the captchaKey used, so a caller can retry the
    same captcha if the OTP send fails for an unrelated reason.
    """
    if solve is None:
        raise ValueError("request_otp needs solve=<callable(png_bytes) -> str>; "
                         "a human must read the captcha")
    sess = session or make_session()
    key, png = _captcha(base, sess, timeout)
    value = solve(png)
    r = sess.post(base + DSP_ENDPOINTS["send_otp"],
                  json={"mobile": mobile, "captcha": value, "captchaKey": key},
                  timeout=timeout)
    out = r.json() if r.content else {}
    out["captchaKey"] = key
    return out


def verify_otp(mobile: str, otp: str, *, base: str = DSP_BASE,
               session: Any = None, timeout: int = 60) -> dict[str, Any]:
    """Exchange the OTP for a bearer token.

    OTPs expire in about a minute, so call this immediately. A common own-goal is
    shell-quoting the mobile so it arrives empty — the portal then answers
    ``mobile_invalid_strict`` rather than "expired", and the real OTP is burnt by
    the time the quoting is fixed.
    """
    sess = session or make_session()
    r = sess.post(base + DSP_ENDPOINTS["verify_otp"],
                  json={"mobile": mobile, "otp": otp}, timeout=timeout)
    return r.json() if r.content else {}


def probe_public(base: str = DSP_BASE, *, session: Any = None,
                 timeout: int = 45) -> dict[str, Any]:
    """Report which endpoints answer without credentials. Reconnaissance only."""
    sess = session or make_session()
    out: dict[str, Any] = {"base": base, "results": {}, "note": None}
    for path in ("/csv-download/years", "/csv-download", "/api/public/captcha"):
        try:
            resp = sess.get(base + path, timeout=timeout)
            out["results"][path] = getattr(resp, "status_code", None)
        except Exception as exc:
            out["results"][path] = f"error: {type(exc).__name__}"
    codes = list(out["results"].values())
    if all(isinstance(c, str) and c.startswith("error") for c in codes):
        out["note"] = ("every request failed — almost certainly EGRESS, not an "
                       "outage. Re-test from an Indian egress or a SOCKS tunnel.")
    elif out["results"].get("/csv-download") == 401:
        out["note"] = "401 is expected unauthenticated; authenticate with request_otp()."
    return out
