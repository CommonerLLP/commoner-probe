# SPDX-License-Identifier: MIT
"""Drive a JWT-authenticated REST API that sits behind an Angular single-page app.

THE STACK, WHICH IS THE REUSABLE PART
=====================================
The page ships as an empty shell. It renders only after JavaScript runs.

The API base is not in the HTML. The build compiles it into one bundle as a
variable, and assembles endpoints with template literals. A grep for URL string
literals therefore finds almost nothing. Recover the base from the bundle:

    grep -oE '[A-Za-z0-9_$]+_apiBaseUrl' main.*.js | head -1

The auth flow is three calls. Fetch a captcha. Send an OTP. Verify the OTP for
a bearer token. A human must read the captcha image. This module ships no
solver. The OTP expires quickly, so build the verify call before you ask for
the code.

Downloads then use the bearer token. They key on ids, never on names.

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

import base64
import json
import re
from typing import Any

from .http_client import make_session

__all__ = [
    "KYS_BASE", "DSP_BASE", "KYS_ENDPOINTS", "DSP_ENDPOINTS",
    "ALL_INDIA", "YEAR_IDS", "REPORT_IDS", "SCHEMA_REPORT_ID",
    "csv_url", "request_otp", "verify_otp", "probe_public",
    "FMS_BASE", "UDISE_DOCUMENTS", "document_url", "document_pairs",
    "extract_document_pairs", "unwrap_document", "fetch_document",
    "UdiseDocumentProbe", "MANIFEST_KIND",
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
    sess = session if session is not None else make_session()
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
    sess = session if session is not None else make_session()
    r = sess.post(base + DSP_ENDPOINTS["verify_otp"],
                  json={"mobile": mobile, "otp": otp}, timeout=timeout)
    return r.json() if r.content else {}


def probe_public(base: str = DSP_BASE, *, session: Any = None,
                 timeout: int = 45) -> dict[str, Any]:
    """Report which endpoints answer without credentials. Reconnaissance only."""
    sess = session if session is not None else make_session()
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


# ── the public document endpoint ─────────────────────────────────────────────
#
# A THIRD host, and it needs no account at all. The two portals above serve the
# microdata. This one serves the forms, the reports and the metadata
# dictionaries that say what the microdata MEANS: the Data Capture Format each
# year's enumerators filled in, the annual report booklets, and the metadata
# dictionaries. A number from the DSP is uninterpretable without them.

FMS_BASE = "https://api.udiseplus.gov.in/udise-fms/api/fileUpload/getDocument/"

#: Every document the portal offers, by folder. Read out of the compiled
#: Angular bundles (`main-*.js`, `chunk-*.js`) on 2026-08-05 by the repo that
#: filed this, and re-counted here: 86 pairs, all distinct.
#:
#: **There is no listing endpoint.** The links are `dcfDownload(folder, name)`
#: calls inside the bundle, so this catalogue is pinned rather than discovered.
#: Re-extract it when the bundle hash changes; a name that has gone answers
#: with a non-PDF body rather than a 404, which is why `fetch_document` checks
#: the magic bytes.
UDISE_DOCUMENTS: dict[str, tuple[str, ...]] = {
    "UploadedFiles": (
        "DCF0112", "DCF1-10", "DCF1-12", "DCF1-5", "DCF1-8", "DCF11-12", "DCF6-10",
        "DCF6-12", "DCF6-8", "DCF9-10", "DCF9-12", "DCF_Final_2022_23_v15_1804",
        "DCF_Final_2023_24_v6_3101", "UDISE_DCF_Final_24_25_v4",
        "UDISE_DCF_Final_25_26_v3", "UDISE_DCF_Final_26_27_v3", "Guidelines2021_22",
        "GuidelinesDCF_UDISE_25_26", "MP_English_Blank", "MP_Hindi_Blank",
    ),
    "dcf2021": (
        "UDISE+2018-19-REPORT", "UDISE+2019_20_Booklet_English",
        "UDISE+2020_21_Booklet_English", "UDISE_Report_2021_22",
        "UDISE_Report_2022_23_Existing_Structure",
        "UDISE_Report_2022_23_NEP_Structure",
        "UDISE_Report_2023_24_Existing_Structure",
        "UDISE_Report_2023_24_NEP_Structure",
        "UDISE_Report_2024_25_Existing_Structure",
        "UDISE_Report_2024_25_NEP_Structure",
        "UDISE_Report_2025_26_Existing_Structure",
        "UDISE_Report_2025_26_NEP_Structure", "AdditionalChiefSecretaries",
        "CBSEChairperson", "DO_letter_portal_2025_26", "DOletter2026", "DoLetter12_24",
        "DoLetterStatesUTs2025", "extendedDated15Dec", "portal_closing",
    ),
    "pdfFiles": (
        "DCF1-10", "DCF1-10_2019", "DCF1-12", "DCF1-12_2019", "DCF1-4_2019", "DCF1-5",
        "DCF1-5_2019", "DCF1-7_2019", "DCF1-8", "DCF1-8_2019", "DCF11-12",
        "DCF11-12_2019", "DCF5-10_2019", "DCF5-12_2019", "DCF5-7_2019", "DCF5-8_2019",
        "DCF6-10", "DCF6-10_2019", "DCF6-12", "DCF6-12_2019", "DCF6-8", "DCF6-8_2019",
        "DCF8-10_2019", "DCF8-12_2019", "DCF9-10", "DCF9-10_2019", "DCF9-12",
        "DCF9-12_2019", "DISE2009_DCF_15July2009", "DISE2010_DCF", "DISE2011-12_DCF",
        "DISE2012-13_DCF", "UDISE_DCF_2013-14", "UDISE_DCF-2014-15",
        "UDISE_DCF2015-16", "UDISE_DCF2016-17_12Aug2016_2",
        "UDISE_DCF_2017-18_(U-DISE_Vocational_&_Student)", "Metadata",
        "UDISE_metadata_as_per_NMDS", "StudentProfile_18July2016", "DSP_english",
        "DSP_hi", "Gujarati_DCF1_12", "Hindi_DCF1_12", "UDISEBookletFinal",
        "UDISE_Plus_Booklet",
    ),
}

#: The `dcfDownload("folder","name")` call the catalogue is read from. Kept
#: here so a re-extraction runs the same pattern the first one did.
DCF_DOWNLOAD_CALL = re.compile(r"""dcfDownload\(\s*["']([^"']+)["']\s*,\s*["']([^"']+)["']""")


def document_url(folder: str, name: str, *, base: str = FMS_BASE) -> str:
    """The URL one catalogue entry is served from."""
    return f"{base}{folder}/{name}.pdf"


def document_pairs() -> list[tuple[str, str]]:
    """Every (folder, name) in the catalogue, in a stable order."""
    return [(folder, name) for folder in UDISE_DOCUMENTS
            for name in UDISE_DOCUMENTS[folder]]


def extract_document_pairs(bundle_text: str) -> list[tuple[str, str]]:
    """The (folder, name) pairs one Angular bundle names, de-duplicated.

    Use it to re-derive :data:`UDISE_DOCUMENTS` when the bundle changes. It
    reads a bundle a caller already fetched, because the bundle URL carries a
    build hash and finding it is the caller's problem, not this function's.
    """
    seen: dict[tuple[str, str], None] = {}
    for folder, name in DCF_DOWNLOAD_CALL.findall(bundle_text):
        seen.setdefault((folder, name), None)
    return list(seen)


def unwrap_document(body: bytes) -> bytes:
    """The PDF bytes inside what this endpoint actually returns.

    **It answers a request for a `.pdf` with JSON.** The body is
    ``{"pdf": "<base64>"}``, the Content-Type is ``application/json`` and the
    Content-Disposition claims ``filename=f.txt``. Verified live on
    2026-08-19 and again on 2026-08-20. A caller that writes the response
    straight to disk writes a JSON file under a `.pdf` name, and every reader
    downstream then reports a corrupt PDF.

    A body that is already a PDF is returned unchanged, so the unwrap is safe
    to apply to any response from this host.
    """
    if body[:5] == b"%PDF-":
        return body
    if body[:1] != b"{":
        return body
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body
    encoded = payload.get("pdf") if isinstance(payload, dict) else None
    if not encoded:
        return body
    try:
        return base64.b64decode(encoded)
    except (ValueError, TypeError):
        return body


def fetch_document(folder: str, name: str, *, session: Any = None,
                   base: str = FMS_BASE, timeout: int = 180) -> tuple[int, bytes]:
    """One document, unwrapped. Returns ``(status_code, pdf_bytes)``.

    The caller decides what a short body or a non-PDF means. This function
    does not raise on either, because a name that has left the bundle answers
    200 with something that is not a PDF rather than 404, and that is a
    catalogue fact worth recording rather than an exception.
    """
    session = session or make_session()
    response = session.get(document_url(folder, name, base=base), timeout=timeout)
    return response.status_code, unwrap_document(response.content)


MANIFEST_KIND = "udise_document"


class UdiseDocumentProbe:
    """Acquire the UDISE+ public documents, with a provenance manifest."""

    def __init__(self, out_dir: Any, *, sleep: float = 1.0, base: str = FMS_BASE,
                 session: Any = None, log=None) -> None:
        from pathlib import Path

        self.out_dir = Path(out_dir)
        self.sleep = sleep
        self.base = base
        self.session = session or make_session()
        self.manifest = self.out_dir / "manifest.jsonl"
        self._log = log

    def log(self, msg: str) -> None:
        if self._log:
            self._log(msg)

    def load_seen(self) -> dict[str, dict]:
        """Rows whose file is still on disk, keyed by URL.

        A row describes a file. It stops vouching for anything when the file
        stops existing.
        """
        seen: dict[str, dict] = {}
        if not self.manifest.exists():
            return seen
        with self.manifest.open(encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (isinstance(rec, dict) and rec.get("kind") == MANIFEST_KIND
                        and rec.get("fetch_status") == "ok" and rec.get("path")
                        and (self.out_dir / rec["path"]).exists()):
                    seen[rec["url"]] = rec
        return seen

    def probe(self, *, folders: list[str] | None = None, dry_run: bool = False,
              max_records: int | None = None) -> list[dict]:
        import hashlib
        import time
        from datetime import datetime, timezone

        pairs = [(folder, name) for folder, name in document_pairs()
                 if not folders or folder in folders]
        if dry_run:
            return [{
                "key": f"UDISEDOC|{folder}|{name}",
                "kind": MANIFEST_KIND,
                "record_type": MANIFEST_KIND,
                "source": "api.udiseplus.gov.in",
                "folder": folder,
                "name": name,
                "url": document_url(folder, name, base=self.base),
                "fetch_status": "dry_run",
                "probed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            } for folder, name in pairs]

        self.out_dir.mkdir(parents=True, exist_ok=True)
        docs_dir = self.out_dir / "documents"
        held = self.load_seen()
        out: list[dict] = []
        for folder, name in pairs:
            if max_records is not None and len(out) >= max_records:
                break
            url = document_url(folder, name, base=self.base)
            if url in held:
                continue
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            record = {
                "key": f"UDISEDOC|{folder}|{name}",
                "kind": MANIFEST_KIND,
                "record_type": MANIFEST_KIND,
                "source": "api.udiseplus.gov.in",
                "folder": folder,
                "name": name,
                "url": url,
                "fetch_status": "ok",
                "path": None,
                "bytes": 0,
                "sha256": None,
                "probed_at": now,
            }
            try:
                status, body = fetch_document(folder, name, session=self.session,
                                              base=self.base)
            except Exception as exc:  # noqa: BLE001 - one document never ends a run
                record["fetch_status"] = "error"
                record["error"] = f"{type(exc).__name__}: {exc}"[:500]
                status, body = 0, b""
            record["http_status"] = status
            # A name that has left the bundle answers 200 with a body that is
            # not a PDF. Only the magic bytes tell the two apart, so the check
            # is on the content and never on the status alone.
            if record["fetch_status"] == "ok" and (status != 200 or body[:5] != b"%PDF-"):
                record["fetch_status"] = "not_pdf"
            if record["fetch_status"] == "ok":
                dest = docs_dir / f"{folder}__{name}.pdf".replace("/", "_")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(body)
                record["path"] = str(dest.relative_to(self.out_dir))
                record["bytes"] = len(body)
                record["sha256"] = hashlib.sha256(body).hexdigest()
            self._append(record)
            out.append(record)
            self.log(f"{record['fetch_status']:<8} {record['bytes']:>9}B {folder}/{name}")
            if self.sleep:
                time.sleep(self.sleep)
        return out

    def _append(self, record: dict) -> None:
        with self.manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
