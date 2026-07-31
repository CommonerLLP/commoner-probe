# SPDX-License-Identifier: MIT
"""ORGI / Census of India acquisition via the data.gov.in (OGD) API.

The Office of the Registrar General & Census Commissioner is a source family the
org covered nowhere before REQ-0045. It is **not** substitutable by the MoSPI
client: MoSPI's datasets are NSSO/CSO products, and the Census is a different
agency with different instruments.

**Why the API and not the District Census Handbooks.** The request named DCHB
Part A PDFs from the NADA catalog — roughly 640 districts at ~18 MB each, about
11.5 GB, plus PDF table extraction. Measured 2026-07-30, the same content is
served as structured rows by the OGD API:

    Village Amenities   1,128 resources, one per district, 396 fields
    Primary Census Abstract 2,225 resources, per state, 2001 and 2011
    Complete Town Directory   380 resources, per state

So this module reads rows. The PDFs remain the fallback for any field the API
turns out not to carry, not the default path.

**Two vintages, two title formats.** ``Primary Census Abstract, 2001 - Delhi``
carries a comma; ``Primary Census Abstract 2011 - Rajasthan`` does not. Any
title parsing must tolerate both, which is why the year is matched as a token
rather than by position.

**The API is slow and flaky.** A 100-row page took 11.8 s; a 100-item catalogue
listing timed out at 45 s; an identical query returned an HTTP error once and
200 seconds later. Every call goes through the shared retrying session, and page
sizes stay modest.

**The key never enters an artefact.** The OGD contract puts ``api-key`` in the
query string, so the request URL is a secret. Manifest rows and error messages
carry the key-free form; see :func:`_public_url`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from commoner_probe.http_client import make_session

BASE_URL = "https://api.data.gov.in"
PUBLISHER = "Office of the Registrar General & Census Commissioner (ORGI)"
SOURCE_FAMILY = "orgi-census"

#: Where the registered key lives, named in the error so nobody re-registers one.
#: The org convention predates this module — ``twenty27/scripts/ogd_ingest.py``
#: reads the same variable from the same file.
KEY_ENV = "DATA_GOV_IN_KEY"
KEY_HINT = "sevent4/.secrets/keys.env"

#: The public sample key published on data.gov.in/apis, identified by digest so
#: the probe can refuse to crawl 1,128 districts on a shared, rate-limited
#: credential — fine for a smoke test, wrong for a corpus.
#:
#: A digest, and NOT a prefix: every data.gov.in key begins ``579b464db66e``,
#: registered ones included, so a prefix test rejects the correct credential.
#: The first cut of this module did exactly that and refused the org's real key.
SAMPLE_KEY_SHA256 = "149027a69a838520183f92d985e79c28ca979b79c4b7fa94a22230d819fe08a0"


def _is_sample_key(key: str) -> bool:
    import hashlib as _h

    return _h.sha256(key.encode("utf-8")).hexdigest() == SAMPLE_KEY_SHA256


@dataclass(frozen=True)
class Surface:
    """One Census product on the OGD catalogue."""

    name: str
    #: Catalogue title filter. The API matches loosely, so results are
    #: re-checked against ``title_must_match`` before being accepted.
    query: str
    title_must_match: re.Pattern
    #: What one resource covers — the unit a manifest row describes.
    level: str


SURFACES: dict[str, Surface] = {
    "pca": Surface(
        name="pca",
        query="primary census abstract",
        title_must_match=re.compile(r"primary\s+census\s+abstract", re.I),
        level="state",
    ),
    "village-amenities": Surface(
        name="village-amenities",
        query="village amenities",
        title_must_match=re.compile(r"village\s+amenities", re.I),
        level="district",
    ),
    "town-amenities": Surface(
        name="town-amenities",
        query="town amenities",
        title_must_match=re.compile(r"town\s+amenities", re.I),
        level="district",
    ),
    #: A place INDEX, not amenities. "Complete Town Directory" carries 8 fields
    #: — state/district/sub-district/town codes and names — and no facility
    #: columns at all. Kept because the urban settlement roster with 2011 codes
    #: is a useful join target, but it does NOT answer amenity questions.
    "town-directory": Surface(
        name="town-directory",
        query="town directory",
        title_must_match=re.compile(r"town\s+directory", re.I),
        level="state",
    ),
}

#: NOT AVAILABLE ON THE OGD API. Searched 2026-07-30, and this is the half of
#: REQ-0045 the API does NOT cover — theright2read asked for ALL libraries, not
#: the rural ones, so this gap blocks the consumer rather than trimming it.
#:
#: What was checked:
#:   - `town-directory` ("Complete Town Directory"): 8 fields, codes and names
#:     only, no facility columns at all — a place index.
#:   - `town-amenities` ("Town Amenities for <district>"): all 232 field names
#:     read, not keyword-searched. It runs demographics -> health -> education
#:     and stops at degree colleges. ORGI's Statement V (Social, Recreational
#:     and Culture Facilities) is simply not in it.
#:   - catalogue titles `library`, `libraries`, `recreational`: 14, 14 and 1
#:     resources, every one a funding scheme or a city dataset, none a Census
#:     amenity table.
#:   - no district was found carrying a second town resource (the "997 town
#:     amenities" count is mostly unrelated noise the title filter removes).
#:
#: So the urban public-library COUNT — and with it REQ-0045's merge trap, since
#: that column fuses ORGI's separately-defined 9.11 Public Library and 9.12
#: Public Reading Room — lives only in the DCHB Part A PDFs. Summing it with the
#: rural availability flag is what produces the wrong "~75,000 libraries".
#:
#: Caveat kept deliberately: the catalogue holds 285,830 resources and its title
#: filter is lossy, so this is a well-searched NOT FOUND, not a proof of absence.
URBAN_LIBRARY_COUNT_UNAVAILABLE = (
    "Town Directory Statement V (the urban public-library count) is not in any OGD "
    "resource found; acquire it from the DCHB Part A PDFs. Never sum it with the "
    "rural village-amenities flag — one is a count, the other an availability flag."
)

_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


class CensusApiError(RuntimeError):
    """The catalogue or a resource could not be read.

    Raised rather than returning empty: an empty result and an unreachable API
    are opposite facts, and this repo has shipped that confusion before.
    """


def resolve_api_key(explicit: str | None = None) -> str:
    """The OGD key, from an explicit value, the environment, or the shared file.

    Fails with the path named. A probe that silently falls back to the public
    sample key would appear to work and then throttle part-way through a
    corpus, which reads as "the source is flaky" rather than "we used the wrong
    credential".
    """
    if explicit:
        return explicit
    if os.environ.get(KEY_ENV):
        return os.environ[KEY_ENV]
    for parent in Path(__file__).resolve().parents:
        candidate = parent / KEY_HINT
        if candidate.exists():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith(f"{KEY_ENV}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise CensusApiError(
        f"{KEY_ENV} is not set. Export it, or put it in {KEY_HINT} "
        f"(the org already keeps a registered data.gov.in key there)."
    )


def _public_url(path: str, params: dict[str, Any]) -> str:
    """The request URL with the credential removed.

    `api-key` rides in the query string, so the literal request URL is a secret.
    This is what goes in the manifest and in exception text.
    """
    safe = {k: v for k, v in params.items() if k != "api-key"}
    return f"{BASE_URL}/{path}?" + urllib.parse.urlencode(safe)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_title(title: str) -> dict[str, str | None]:
    """Pull year and area out of a catalogue title, tolerating both formats.

    ``Primary Census Abstract, 2001 - Delhi`` and ``Primary Census Abstract 2011
    - Rajasthan`` differ by a comma, and ``Village Amenities for Hoshangabad
    District of Madhya Pradesh, 2011`` puts the year last. The year is taken as
    a token wherever it falls; the area is whatever survives once the product
    name, the year and the separators are removed.
    """
    year_m = _YEAR_RE.search(title)
    year = year_m.group(0) if year_m else None
    area = title
    if year:
        area = area.replace(year, " ")
    area = re.sub(
        r"primary\s+census\s+abstract|complete\s+town\s+directory|town\s+directory"
        r"|village\s+amenities|town\s+amenities"
        r"|by\s+india/state/district/sub-district\s+level|census",
        " ",
        area,
        flags=re.I,
    )
    area = re.sub(r"[,\-–]+", " ", area)
    area = re.sub(r"\b(for|of|the)\b", " ", area, flags=re.I)
    area = re.sub(r"\s+", " ", area).strip(" -,")
    district = None
    dm = re.search(r"(.+?)\s+District\s+(.+)", area, flags=re.I)
    if dm:
        district, area = dm.group(1).strip(), dm.group(2).strip()
    return {"census_year": year, "state_name": area or None, "district_name": district}


class CensusProbe:
    """Acquire ORGI Census products from the OGD API with a provenance manifest."""

    def __init__(
        self,
        out_dir: Path,
        *,
        sleep: float = 1.0,
        session: Any = None,
        api_key: str | None = None,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.sleep = sleep
        self.session = session or make_session(rate_limit_sec=sleep)
        self.api_key = resolve_api_key(api_key)
        self.manifest = self.out_dir / "manifest.jsonl"
        self.rows_dir = self.out_dir / "rows"

    # ---------- API ----------

    def _no_cache(self):
        """Suppress on-disk caching for a request that carries the credential."""
        from contextlib import nullcontext

        disabled = getattr(self.session, "cache_disabled", None)
        return disabled() if callable(disabled) else nullcontext()

    def _get(self, path: str, params: dict[str, Any], *, timeout: int = 90) -> dict:
        query = {**params, "api-key": self.api_key, "format": "json"}
        public = _public_url(path, query)
        try:
            # `requests-cache`, when installed, persists the prepared request
            # URL alongside the response under /tmp — and the OGD contract puts
            # the credential in that URL, so a cached response would write the
            # key to disk. That is exactly the artefact this module promises to
            # keep it out of (Codex, PR #91).
            #
            # Detected rather than passed as a kwarg: `expire_after=0` reaches
            # `requests.Session.request()` on a non-caching install and raises
            # TypeError, which the first cut of this fix did.
            with self._no_cache():
                resp = self.session.get(f"{BASE_URL}/{path}", params=query, timeout=timeout)
            resp.raise_for_status()
            body = resp.text
        except Exception as exc:  # noqa: BLE001 — re-raised without the key
            raise CensusApiError(f"OGD request failed: {public} ({type(exc).__name__})") from None
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            raise CensusApiError(
                f"OGD returned a non-JSON body for {public} — an interstitial or an "
                "outage, NOT an empty result"
            ) from None
        if not isinstance(payload, dict):
            raise CensusApiError(f"OGD returned {type(payload).__name__}, expected an object: {public}")
        return payload

    def discover(
        self,
        surface: str,
        *,
        year: str | None = None,
        page: int = 20,
        max_resources: int | None = None,
    ) -> list[dict]:
        """Catalogue entries for a surface, filtered and title-verified.

        The catalogue filter matches loosely — ``filters[title]=census`` returns
        1,847 rows including a resource literally titled ``census`` — so every
        record is re-checked against the surface's own pattern before it is
        accepted as that product.
        """
        spec = SURFACES[surface]
        found: list[dict] = []
        offset = 0
        while True:
            payload = self._get(
                "lists",
                {"limit": page, "offset": offset, "filters[title]": spec.query},
            )
            records = payload.get("records") or []
            if not records:
                break
            for rec in records:
                title = str(rec.get("title") or "")
                if not spec.title_must_match.search(title):
                    continue
                meta = parse_title(title)
                if year and meta["census_year"] != year:
                    continue
                found.append({
                    "resource_id": rec.get("index_name"),
                    "title": title,
                    "surface": surface,
                    "level": spec.level,
                    **meta,
                })
                if max_resources is not None and len(found) >= max_resources:
                    return found
            offset += len(records)
            if offset >= int(payload.get("total") or 0):
                break
            if self.sleep:
                time.sleep(self.sleep)
        return found

    def fetch_rows(self, resource_id: str, *, page: int = 100, max_rows: int | None = None) -> Iterator[dict]:
        """Every row of one resource, paginated.

        Page size stays modest on purpose: a 100-row page measured 11.8 s and a
        larger catalogue call timed out outright.
        """
        offset = 0
        while True:
            payload = self._get("resource/" + resource_id, {"limit": page, "offset": offset})
            records = payload.get("records") or []
            if not records:
                return
            for row in records:
                yield row
                offset += 1
                if max_rows is not None and offset >= max_rows:
                    return
            if offset >= int(payload.get("total") or 0):
                return
            if self.sleep:
                time.sleep(self.sleep)

    # ---------- manifest ----------

    def _record(self, entry: dict, *, status: str) -> dict:
        rid = entry["resource_id"]
        return {
            "key": f"ORGI|{entry['surface']}|{rid}",
            "kind": "orgi_census_resource",
            "record_type": "orgi_census_resource",
            "source_family": SOURCE_FAMILY,
            "source_name": entry["surface"],
            "publisher": PUBLISHER,
            "resource_id": rid,
            "title": entry["title"],
            "census_year": entry.get("census_year"),
            "level": entry.get("level"),
            "state_name": entry.get("state_name"),
            "district_name": entry.get("district_name"),
            # Key-free by construction — see _public_url.
            "url": _public_url(f"resource/{rid}", {"format": "json"}),
            "dest": None,
            "rows": None,
            "sha256": None,
            "status": status,
            "fetched_at": _now(),
            "probed_at": _now(),
        }

    def append_manifest(self, record: dict) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def load_seen(self) -> set[str]:
        seen: set[str] = set()
        if not self.manifest.exists():
            return seen
        for line in self.manifest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("status") == "downloaded" and row.get("key"):
                seen.add(row["key"])
        return seen

    # ---------- the walk ----------

    def probe(
        self,
        surface: str,
        *,
        year: str | None = None,
        max_resources: int | None = None,
        max_rows: int | None = None,
        dry_run: bool = False,
    ) -> list[dict]:
        if surface not in SURFACES:
            raise ValueError(f"unknown surface {surface!r}; expected one of {sorted(SURFACES)}")
        if _is_sample_key(self.api_key) and not dry_run:
            raise CensusApiError(
                "refusing to crawl on the public sample key from data.gov.in/apis — it is "
                f"shared and rate-limited, so a corpus pass would throttle part-way and read "
                f"as a flaky source. Set {KEY_ENV} to the registered key ({KEY_HINT})."
            )
        seen = self.load_seen()
        entries = self.discover(surface, year=year, max_resources=max_resources)
        out: list[dict] = []
        for entry in entries:
            record = self._record(entry, status="dry_run" if dry_run else "pending")
            if dry_run:
                out.append(record)
                continue
            if record["key"] in seen:
                continue
            rows = list(self.fetch_rows(entry["resource_id"], max_rows=max_rows))
            if not rows:
                raise CensusApiError(
                    f"{entry['title']!r} returned zero rows. A Census resource with no rows is a "
                    "changed or withdrawn dataset, not an empty result."
                )
            self.rows_dir.mkdir(parents=True, exist_ok=True)
            dest = self.rows_dir / f"{surface}__{entry['resource_id']}.jsonl"
            payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
            dest.write_text(payload, encoding="utf-8")
            record["dest"] = str(dest.relative_to(self.out_dir))
            record["rows"] = len(rows)
            record["sha256"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            # A row-limited pull is a smoke test, not an acquisition. Marking it
            # `downloaded` makes load_seen() skip the resource forever, so a
            # later unrestricted run leaves the subset in place and the corpus
            # looks complete while holding a handful of rows (Codex, PR #91).
            record["status"] = "partial" if max_rows is not None else "downloaded"
            self.append_manifest(record)
            out.append(record)
        return out
