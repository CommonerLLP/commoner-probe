"""Registry entries for city construction sources: state RERA project listings."""
from urllib.parse import urlencode

from check_sources import CountFloor, MaxAge, Source

from commoner_probe.http_client import (
    make_session,
    read_capped_response,
    refuse_challenge,
)
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
]
