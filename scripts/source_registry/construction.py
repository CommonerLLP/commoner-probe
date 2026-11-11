"""Registry entries for city construction sources: state RERA project listings."""
from urllib.parse import urlencode

from check_sources import CountFloor, MaxAge, Source

from commoner_probe.http_client import (
    make_session,
    read_capped_response,
    refuse_challenge,
)
from commoner_probe.rera import haryana, tamil_nadu
from commoner_probe.rera.karnataka import (
    LISTING_URL,
    MAX_LISTING_BYTES,
    listing_form,
    parse_listing,
)

# Bengaluru Urban is the only district that registers projects often enough to
# watch. On November 11, 2026 (tests/fixtures/rera_karnataka/README.md) it had 95
# registrations in the last 90 days and a newest approved-on date of November 7,
# 2026. The small districts are dormant: Kodagu had 1 in 90 days (newest
# September 11, 2026), and Yadgir and Gadag had 0 (newest January 27, 2025 and
# June 29, 2026), so a rule on them would report `stale` while the portal is fine.
# Every response carries about 6.5 MB of page chrome, and Bengaluru Urban's is
# about 33 MB, so this entry costs one 33 MB POST per run.
_RERA_KA_DISTRICT = "Bengaluru Urban"


def _rera_karnataka_rows() -> list[dict]:
    """POST the Bengaluru Urban listing and parse it with the adapter's own parser."""
    resp = make_session().post(
        LISTING_URL,
        data=urlencode(listing_form(_RERA_KA_DISTRICT)),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        stream=True,
        timeout=300,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} from {LISTING_URL}")
    refuse_challenge(resp, LISTING_URL, expect_body=True)
    body = read_capped_response(resp, max_bytes=MAX_LISTING_BYTES)
    return parse_listing(body.decode("utf-8", errors="replace"))


# Haryana's Gurugram listing is the one to watch: the Panchkula listing prints no
# certificate date for 1,063 of its 1,132 rows. On November 11, 2026 the Gurugram
# listing had 1,086 rows, 12 certificates dated in the last 90 days, a newest
# date of November 2, 2026, and no gap longer than 25 days since January 2025.
# The whole table comes in one 2.6 MB GET.
_RERA_HR_AUTHORITY = "gurugram"


def _rera_haryana_rows() -> list[dict]:
    """GET the Gurugram listing and parse it with the adapter's own parser."""
    url = haryana.listing_url(_RERA_HR_AUTHORITY)
    resp = make_session().get(url, stream=True, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} from {url}")
    refuse_challenge(resp, url, expect_body=True)
    body = read_capped_response(resp, max_bytes=haryana.MAX_LISTING_BYTES)
    return haryana.parse_listing(body.decode("utf-8", errors="replace"), url=url)


# Tamil Nadu's registered-building list is the one to watch: the layout list is 11 MB,
# and the offline years are static archives. The page's "Last Updated" stamp equals the
# newest registration date on it (10/11/2026 on the 2026 list, 29/12/2025 on the 2025
# list, both measured November 11, 2026), so the registration date is the check. The
# list for 2026 had 402 rows, 123 registrations in the last 90 days, a newest date of
# November 10, 2026, and no gap longer than 14 days (12 in 2025). There is no
# CountFloor: the list holds one year, so its count restarts every January and would
# report a false drop. The GET needs no token, because the default year is the current one.
_RERA_TN_KIND = "building"


def _rera_tamil_nadu_rows() -> list[dict]:
    """GET the current-year building list and parse it with the adapter's own parser."""
    url = tamil_nadu.listing_url(_RERA_TN_KIND)
    tamil_nadu.refuse_disallowed_url(url)
    resp = make_session().get(url, stream=True, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} from {url}")
    refuse_challenge(resp, url, expect_body=True)
    body = read_capped_response(resp, max_bytes=tamil_nadu.MAX_LISTING_BYTES)
    return tamil_nadu.parse_listing(body.decode("utf-8", errors="replace"), kind=_RERA_TN_KIND, url=url)


SOURCES = [
    # The listing is the record, so the entry has no `document`.
    Source(id="rera-karnataka", label="Karnataka RERA project registrations (Bengaluru Urban)",
           host="rera.karnataka.gov.in",
           fetch=_rera_karnataka_rows,
           required=("key", "promoter", "project_name", "district"),
           record_date=lambda r: r.get("approved_on") or "",
           # 95 registrations in 90 days is about one every day, so 30 days without
           # a new one means the listing stopped, not that the district was quiet.
           freshness=(MaxAge(days=30), CountFloor(0.95))),
    Source(id="rera-haryana", label="Haryana RERA registered projects (Gurugram)",
           host="haryanarera.gov.in",
           fetch=_rera_haryana_rows,
           required=("key", "promoter", "project_name", "district"),
           record_date=lambda r: r.get("approved_on") or "",
           # The longest gap between two certificate dates since January 2025 is 25
           # days, so 60 days without a new one means the listing stopped.
           freshness=(MaxAge(days=60), CountFloor(0.95))),
    Source(id="rera-tamil-nadu", label="Tamil Nadu RERA registered buildings (current year)",
           host="rera.tn.gov.in",
           fetch=_rera_tamil_nadu_rows,
           required=("key", "promoter", "approved_on"),
           record_date=lambda r: r.get("approved_on") or "",
           # The longest gap between two registration dates in 2025 and 2026 is 14
           # days, so 30 days without a new one means the listing stopped.
           freshness=(MaxAge(days=30),)),
]
