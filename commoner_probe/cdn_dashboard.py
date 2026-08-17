# SPDX-License-Identifier: MIT
"""Reading a client-rendered dashboard whose numbers are CDN objects, not HTML.

**The mechanics, which is what a caller must implement.**

1. **The page carries no data.** It ships as an empty shell and renders only
   after JavaScript runs, so an HTTP fetch returns a document with no numbers
   in it — and the Wayback Machine holds 19 captures of this one, none of which
   contains a number either. The figures are static JSON objects on a CDN,
   addressed by period and place, so fetching them directly is both cheaper and
   more complete than driving a browser.

2. **Two transports, and confusing them reads as an outage.** Statistics are
   plain JSON on the CDN. The REFERENCE data — the place list supplying the ids
   every CDN path needs — comes from a POST API that answers with an AES-256-CBC
   envelope: `base64(json({iv, value, mac}))`, HMAC-SHA256 over the concatenated
   base64 iv and value, decrypted client-side with a key the JS bundle carries.
   A caller that assumes one transport gets 403s or unreadable base64 from the
   other.

3. **The path keys on the API's ids, NOT the census codes** sitting beside them
   in the same record. A census code returns 403, which is indistinguishable
   from a period that does not exist.

4. **The month segment is unpadded.** `/6/`, never `/06/`, which 403s.

5. **The edge is geo-fenced.** It rejects clients outside the publisher's
   country with HTTP 403 and a body naming the country block, so a blanket 403
   is a vantage-point problem rather than a dead source.

6. **The envelope key rotates and is not in this package.** The MAC is verified
   rather than ignored, so a stale key says so instead of surfacing as a JSON
   parse error three frames downstream.

**Context for the instance below, which is not part of the mechanics.** The
worked example is the Ministry of Women and Child Development's Anganwadi
service-delivery dashboard (Mission Saksham Anganwadi / Poshan 2.0), whose
figures are cited in answer to parliamentary questions. Its published record
begins 2022-04 — ten months before its own month selector offers anything.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Iterator

from .http_client import make_session

# --- the instance this was built against, kept as data ----------------------
CDN_BASE = "https://cdn.poshantracker.in/pt_dashboard"
API_BASE = "https://api.poshantracker.in/api"

#: Environment variable holding the envelope key. The site ships the key in its
#: own JS bundle, as `VITE_POSHAN_CY_SET`, because the browser must hold it to
#: render the page. That does not make it this package's to carry: a key baked
#: into a published library is wrong whatever its provenance, and this one
#: ROTATES — the MAC check below exists to say so. The operator reads it out of
#: the bundle and exports it.
ENVELOPE_KEY_ENV = "POSHAN_ENVELOPE_KEY"

EARLIEST = (2022, 4)

ENDPOINTS = {
    "registration": "PT_Dashboard",
    "growth": "PT_Dashboard_growthmonitoring",
    "infrastructure": "awcInfrastructure_InternalDashboard",
    "key_services": "keyServices_v3",
    "home_visit": "PT_Dashboard_homevisit",
    "beneficiaries": "monthWiseActiveBeneficiaries",
    "centre_detail": "AnganwadiCenter_InternalDashboard",
}


@dataclass(frozen=True)
class Place:
    """One district, with the ids the CDN paths actually use."""

    state_id: int
    state_name: str
    district_id: int
    district_name: str
    state_code: str
    district_code: str


class GeoFenced(RuntimeError):
    """The edge refused the client's country.

    Separate from an absent period ON PURPOSE. Both answer 403, and conflating
    them turns a run that was blocked outright into a clean empty dataset —
    the failure this package exists to refuse.
    """


#: The edge names the country block in the body. Matched case-insensitively.
GEO_FENCE_MARKERS = ("block access from your country", "not available in your region")


def payload_url(year: int, month: int, state_id: int, district_id: int, endpoint: str) -> str:
    """The CDN object for one place, period and endpoint.

    `month` is unpadded — `/6/`, never `/06/`, which 403s.
    """
    return (f"{CDN_BASE}/{year}/{month}/{state_id}/{district_id}/"
            f"{district_id}_{endpoint}.json")


def months(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    year, month = start
    out = []
    while (year, month) <= end:
        out.append((year, month))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def envelope_key(key: str | bytes | None = None) -> bytes:
    """The envelope key, from the argument or `POSHAN_ENVELOPE_KEY`.

    Raises when neither supplies one, naming where to find it. Failing here
    beats decrypting with a placeholder and failing as a JSON parse error three
    frames away.
    """
    resolved = key or os.environ.get(ENVELOPE_KEY_ENV)
    if not resolved:
        raise ValueError(
            f"no envelope key: pass one or set {ENVELOPE_KEY_ENV}. The site ships it "
            "in its JS bundle as VITE_POSHAN_CY_SET, and it rotates."
        )
    return resolved.encode() if isinstance(resolved, str) else resolved


def decrypt_envelope(response_data: str, key: str | bytes | None = None) -> Any:
    """Unwrap the API's AES-256-CBC envelope.

    Shape is base64(json({iv, value, mac})); `value` is base64 ciphertext and
    `mac` is HMAC-SHA256 over the concatenated base64 iv and value. The mac is
    verified rather than ignored: a silently-wrong key would otherwise surface
    as a JSON parse error somewhere much further downstream.
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    secret = envelope_key(key)
    inner = json.loads(base64.b64decode(response_data))
    expected = hmac.new(secret,
                        (inner["iv"] + inner["value"]).encode(),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, inner["mac"]):
        raise ValueError("envelope MAC mismatch — the bundle key has rotated")

    iv = base64.b64decode(inner["iv"])
    ct = base64.b64decode(inner["value"])
    decryptor = Cipher(algorithms.AES(secret), modes.CBC(iv)).decryptor()
    plain = decryptor.update(ct) + decryptor.finalize()
    return json.loads(plain[: -plain[-1]])


def places(session: Any, key: str | bytes | None = None) -> list[Place]:
    """Every district the tracker knows, with both id systems."""
    resp = session.post(f"{API_BASE}/states",
                        data="request_data=",
                        headers={"Content-Type": "application/x-www-form-urlencoded",
                                 "Origin": "https://www.poshantracker.in",
                                 "Referer": "https://www.poshantracker.in/"})
    resp.raise_for_status()
    payload = resp.json()
    data = decrypt_envelope(payload["response_data"], key)["data"]
    return [
        Place(state_id=s["id"], state_name=s["state_name"],
              district_id=d["id"], district_name=d["district_name"],
              state_code=s["state_code"], district_code=d["district_code"])
        for s in data for d in s["districts"]
    ]


def _rows(payload: dict, wrapper: str) -> list[dict]:
    blocks = payload.get(wrapper) or []
    return [row for block in blocks
            for district in (block.get("districts") or [])
            for row in (district.get("data") or [])]


def parse_registration(payload: dict) -> dict:
    """Anganwadi centre, worker and helper counts."""
    wanted = {"Anganwadi Centers": "anganwadi_centres",
              "Anganwadi Workers": "anganwadi_workers",
              "Anganwadi Helpers": "anganwadi_helpers",
              "Project": "projects", "Sector": "sectors",
              "Eligible Beneficiaries": "eligible_beneficiaries",
              "Adolescent Girl": "adolescent_girls"}
    return {wanted[r["title"]]: r.get("count")
            for r in _rows(payload, "registrationPTdata") if r.get("title") in wanted}


def parse_growth(payload: dict) -> dict:
    """Malnutrition prevalence, keyed by age band.

    Stunting, Underweight and "Children Measured" each appear TWICE, once for
    0-6 years and once for 0-5 years, with identical `title` and different
    values (22% vs 21% stunting in Dahod, June 2026). `title2` is the only
    field that separates them, so it is part of the key rather than dropped.
    """
    out: dict[str, Any] = {}
    for row in _rows(payload, "growthmonitoring"):
        title = row.get("title", "").lower().replace(" ", "_")
        band = row.get("title2") or ("0m_6y" if "6 Years" in (row.get("title1") or "") else "0m_5y")
        if "percent" in row:
            out[f"{title}_pct_{band}"] = float(row["percent"])
        if "count" in row:
            out[f"children_measured_{band}"] = row["count"]
            out[f"children_measured_pct_{band}"] = float(row.get("percentage") or 0)
    return out


def parse_infrastructure(payload: dict) -> dict:
    """How many centres have their own building, a toilet, drinking water."""
    return {r["title"]: r.get("count")
            for r in _rows(payload, "awcInfrastructureCount") if r.get("title")}


def parse_home_visit(payload: dict) -> dict:
    """Home visits, keyed on the FULL label including its qualifier.

    Both fields here changed definition at 2026-04 and the change is invisible
    in `title` alone:

        2026-03  title="Pregnant Women &"  title1="Lactating Mother"  18,122
        2026-04  title="Pregnant Women"    title1=""                   6,129

    The count falls 63% because lactating mothers left the definition, not
    because visiting collapsed. In the same month the children band widened
    from "(0 - 2 Years)" to "(0 - 3 Years)".

    An earlier version keyed on `title` only. It produced one series that
    silently spanned two definitions, and a second whose age band changed
    underneath it. `title1` is therefore part of the key — the same rule the
    growth parser applies to `title2`.
    """
    out: dict[str, Any] = {}
    for row in _rows(payload, "homevisit"):
        label = (row.get("title", "") + " " + (row.get("title1") or "")).strip()
        key = "_".join(p for p in re.split(r"[^a-z0-9]+", label.lower()) if p)
        out[f"home_visit_{key}"] = row.get("count")
    return out


def parse_centre_detail(payload: dict) -> dict:
    """Centre counts by type and settlement — regular/mini, rural/urban/tribal/PVTG."""
    return {r["title"]: r.get("count")
            for r in _rows(payload, "AnganwadiCenterCount") if r.get("title")}


def parse_key_services(payload: dict) -> dict:
    """Supplementary Nutrition Programme delivery, with the THR/HCM split.

    The rows are heterogeneous dicts rather than title/count pairs, and
    `thr_per`/`hcm_per` REPEAT across rows with different values — they qualify
    whichever `snp_Given_for_*` threshold shares their row. Flattening on key
    alone would keep only the last. Each is therefore suffixed with its
    threshold, and rows are matched by the snp key they carry.

    Observed on every row sampled so far: thr_per + hcm_per = 99, never 100.
    Cause unknown; carried through unaltered rather than normalised.
    """
    out: dict[str, Any] = {}
    for row in _rows(payload, "keyservices"):
        threshold = next((k for k in row if k.startswith("snp_Given")), None)
        if threshold is None:
            out.update({k: v for k, v in row.items() if not k.startswith("_")})
            continue
        days = "".join(c for c in threshold if c.isdigit())
        out[f"snp_given_{days}d"] = row[threshold]
        for share in ("thr_per", "hcm_per"):
            if share in row:
                out[f"{share}_{days}d"] = float(row[share])
    return out


def parse_beneficiaries(payload: dict) -> dict:
    """Active beneficiaries by category, plus Aadhaar and Health ID linkage.

    `title` alone collides — three separate rows are called "Children" and are
    separated only by `title1` ("(0-6 Months)", "(6 Months - 3 Years)",
    "(3 - 6 Years)"). Same failure mode as the growth age bands, so `title1` is
    part of the key here too.
    """
    out: dict[str, Any] = {}
    for block in payload.get("monthWiseActiveBeneficiaries") or []:
        for district in block.get("districts") or []:
            for row in district.get("activeBeneficiaries") or []:
                label = (row.get("title", "") + " " + (row.get("title1") or "")).strip()
                key = "".join(c if c.isalnum() else "_" for c in label.lower()).strip("_")
                out[f"beneficiaries_{key}"] = row.get("count")
            for row in district.get("aadharHealthData") or []:
                key = (row.get("title") or "").lower().replace(" ", "_")
                out[f"{key}_count"] = row.get("count")
                out[f"{key}_pct"] = row.get("percent")
    return out


PARSERS = {
    "registration": parse_registration,
    "growth": parse_growth,
    "infrastructure": parse_infrastructure,
    "home_visit": parse_home_visit,
    "centre_detail": parse_centre_detail,
    "key_services": parse_key_services,
    "beneficiaries": parse_beneficiaries,
}

ALL_KINDS = tuple(PARSERS)


def fetch(session: Any, year: int, month: int, place: Place, kind: str) -> dict | None:
    """One endpoint for one district-month. None when the period is absent.

    **Most 403s mean "no object at this path", not "forbidden."** The bucket
    denies listing, so a period that was never published and a path that is
    wrong produce the same response. Callers cannot separate those two, and
    should not try.

    **One 403 is different, and it raises.** The edge refuses a client outside
    the publisher's country, and names the country block in the body. Every
    object answers that way from such a client, so returning None would turn a
    blocked run into a clean empty dataset. That case raises `GeoFenced`.

    `respect_robots=False` is deliberate and narrow. The CDN host publishes NO
    robots.txt — the request returns S3 AccessDenied, and RobotFileParser turns
    a 403 on robots.txt into disallow-all, so an absent file reads as a blanket
    ban. The publisher's actual policy is at www.poshantracker.in/robots.txt and
    explicitly ALLOWS /statistics, the page these payloads render; it disallows
    only authenticated routes (/addBeneficiary, /beneficiaryList, /eKYC*,
    /consent), none of which is touched here. The rate limit and backoff still
    apply. Revisit if the CDN ever starts serving a real robots.txt.
    """
    url = payload_url(year, month, place.state_id, place.district_id, ENDPOINTS[kind])
    resp = session.get(url, respect_robots=False)
    if resp.status_code == 403:
        body = (getattr(resp, "text", "") or "").lower()
        if any(m in body for m in GEO_FENCE_MARKERS):
            raise GeoFenced(
                f"the edge refused this client's country for {url}. "
                "Every object will 403 from here, so an empty harvest would be "
                "a vantage-point artefact. Fetch from within the publisher's "
                "country.")
        return None
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def harvest(session: Any, places_: list[Place], period: tuple[int, int],
            kinds: tuple[str, ...] = ("registration", "growth", "infrastructure")
            ) -> Iterator[dict]:
        """One flat row per district for a single month."""
        year, month = period
        for place in places_:
            row = {"year": year, "month": month,
                   "state_id": place.state_id, "state_name": place.state_name,
                   "district_id": place.district_id, "district_name": place.district_name,
                   "state_code": place.state_code, "district_code": place.district_code}
            found = False
            for kind in kinds:
                payload = fetch(session, year, month, place, kind)
                if payload is None:
                    continue
                found = True
                row.update(PARSERS[kind](payload))
            if found:
                yield row


def session(rate_limit_sec: float = 1.0) -> Any:
    """A session pinned to the polite default.

    One request per second per domain. The CDN does not rate-limit visibly, but
    an unthrottled fleet against it produced a 74% connection-failure rate in
    testing while a serial jittered client produced none.
    """
    return make_session(rate_limit_sec=rate_limit_sec)
