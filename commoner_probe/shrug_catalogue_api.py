# SPDX-License-Identifier: MIT
"""Acquire SHRUG tables from Devdatalab's DataTables catalogue and presigned S3.

SHRUG is the Socioeconomic High-resolution Rural-Urban Geographic Platform. The
Development Data Lab publishes it: 52 tables covering the Census, SECC, the
Economic Census, night lights and polygons, keyed on the shrid. The release is
public, DOI-cited and CC BY-NC-SA 4.0.

Three stacks answer here, and each fails in its own quiet way. The catalogue is
a **DataTables** grid bound to a JSON endpoint on ``www.devdatalab.org``. The
files are **presigned Amazon S3** objects on ``shrug-assets-ddl.s3.amazonaws.com``.
The canonical archive is **Harvard Dataverse** behind **AWS WAF** (``awselb/2.0``).
Everything below was measured on 2026-08-14.

THE DATAVERSE TRAP — AN EMPTY 202 THAT READS AS SUCCESS
=======================================================
Every scripted request to ``dataverse.harvard.edu/api/*`` returns::

    HTTP/2 202
    server: awselb/2.0
    x-amzn-waf-action: challenge
    content-length: 0

``/api/info/version`` included. This is a WAF JavaScript challenge. It is worse
than a block because it is a **2xx**. ``raise_for_status()`` passes.
``json.loads(b"")`` then throws ``JSONDecodeError: Expecting value: line 1
column 1``. The natural next move is to doubt the DOI. The DOI was correct the
whole time — ``doi:10.7910/DVN/DPESAK``.

So this module reads the Devdatalab mirror. That mirror serves the same release,
issues links that carry no challenge, and lets the caller cite the DOI. **Look
for a non-challenged mirror first. Do not add challenge-solving.**
:func:`catalogue` refuses an empty-bodied 2xx by name. The next host with this
grammar then reads as a challenge, not as an empty catalogue.

THE CATALOGUE IS AN ENDPOINT, NOT A PAGE
========================================
``/shrug_download/`` renders an empty table and fills it from
``/shrug_download/data`` with ``Accept: application/json`` and a ``Referer``.
That returns 52 rows, one per table. Scraping the rendered page finds no links
at all. The general lesson is short. A download page with a sortable table and
no links keeps the links in the JSON endpoint the table is bound to.

The two download fields hold **HTML anchors**, not bare URLs. The cell text is
the word "Download". The URL is in the ``href``.

PRESIGNED LINKS ARE SIGNED FOR GET ONLY
=======================================
A HEAD against a ``primary_download`` URL returns **403**. The same URL downloads
fine. A HEAD pre-flight therefore reports every file as forbidden, and looks like
an authorisation failure. :func:`size_of` issues a ranged GET instead.
``Range: bytes=0-0`` answers 206 with ``Content-Range: bytes 0-0/79180800``.
It raises when that header is absent. A returned 0 would read as an empty file
and pass any size check.

TWO FACTS A CONSUMER MUST BE TOLD, NOT LEFT TO DISCOVER
=======================================================
**A shrid is not a village.** It is SHRUG's stable spatial unit and can contain
several Census villages. Any per-unit rate computed on shrid rows is per shrid,
not per village. This has already produced one near-miss downstream.

**Variable coverage differs enormously by census.** ``pc91_vd`` carries 100
variables, ``pc01_vd`` 110 and ``pc11_vd`` 284. The public-library variable
``pc11_vd_pub_lib`` exists **only in 2011**, though the Census has asked the
question since 1961. A caller assuming a three-census panel gets silently empty
results for two of them. :func:`census_years_for` answers per variable, and
returns None for a variable this module has not measured — None is "not
measured", never "all three years".

LICENCE
=======
CC BY-NC-SA 4.0, share-alike: anything redistributing SHRUG rows inherits the SA
term. Every manifest row carries the licence and the DOI. Cite the DOI. Do not
re-host the tables.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .base import safe_filename_segment
from .http_client import iter_capped, make_session

DOWNLOAD_PAGE_URL = "https://www.devdatalab.org/shrug_download/"
CATALOGUE_URL = "https://www.devdatalab.org/shrug_download/data"
PUBLISHER = "Development Data Lab"
SOURCE_FAMILY = "shrug-devdatalab"
#: Tables the catalogue endpoint served when this module was written
#: (2026-08-14), and the floor below which a response is a changed endpoint
#: rather than a smaller archive. The floor is deliberately loose: SHRUG adds and
#: retires tables, and a hard equality would break on the next release.
CATALOGUE_ROWS = 52
MIN_CATALOGUE_ROWS = 40

DOI = "doi:10.7910/DVN/DPESAK"
LICENCE = "CC BY-NC-SA 4.0"

#: The unit of every shrid-keyed table. Recorded on each manifest row, because
#: the mistake this prevents is arithmetic, not acquisition.
UNIT = "shrid"

#: Variables in the Census village directory, per census. Counted 2026-08-14.
VILLAGE_DIRECTORY_VARIABLES = {"1991": 100, "2001": 110, "2011": 284}

#: Variables measured to exist in ONE census only. Deliberately short: a longer
#: list would be guesswork, and :func:`census_years_for` returns None for
#: anything absent here rather than implying a full panel.
SINGLE_CENSUS_VARIABLES = {"pc11_vd_pub_lib": ("2011",)}

#: Presets, as case-insensitive patterns matched against the table label. The
#: labels are long and typo-prone ("2011 Population Census Village Directory"),
#: and a pattern survives a label the site rewords. Every pattern must match at
#: least one table or :func:`resolve_preset` refuses the whole preset.
#:
#: The `census-directories` and `caste` patterns are verified against the live
#: catalogue (2026-08-14). The rest are written from the published table list
#: and are NOT yet verified, which is why an unmatched pattern raises and names
#: the labels the catalogue actually carries.
PRESETS: dict[str, tuple[str, ...]] = {
    "census-directories": (
        r"1991.*village directory",
        r"2001.*village directory",
        r"2011.*village directory",
        r"1991.*town directory",
        r"2001.*town directory",
        r"2011.*town directory",
        r"location names",
    ),
    "census-abstracts": (
        r"1991.*census abstract",
        r"2001.*census abstract",
        r"2011.*census abstract",
    ),
    "caste": (r"secc rural", r"secc urban"),
    "geometry": (r"shrid.*polygon", r"pc11.*polygon"),
    "keys": (r"shrid.*key", r"location names"),
}


class ShrugCatalogueError(RuntimeError):
    """The catalogue or a file could not be read.

    Raised rather than returning empty. "SHRUG holds nothing" and "the endpoint
    changed shape" are opposite facts, and one of them is a lie.
    """


@dataclass(frozen=True)
class ShrugTable:
    """One row of the Devdatalab catalogue."""

    module_label: str
    table_label: str
    filetype: str
    citation: str
    #: The presigned URL from the anchor, or None when the row carries no anchor.
    url: str | None
    secondary_url: str | None


def caveats() -> list[str]:
    """What a consumer must be told before using these rows.

    Returned as text rather than left in this docstring so a caller can print
    it, log it or write it beside the data.
    """
    return [
        f"A {UNIT} is not a village. It is SHRUG's stable spatial unit and can "
        f"contain several Census villages. Every per-unit rate here is per {UNIT}.",
        "Variable coverage differs by census. pc91_vd has 100 variables, pc01_vd "
        "110, pc11_vd 284. pc11_vd_pub_lib exists only in 2011. A three-census "
        "panel on it returns two empty years and no error.",
        f"Licence {LICENCE}, share-alike. Cite {DOI}. Do not re-host the tables.",
    ]


def census_years_for(variable: str) -> tuple[str, ...] | None:
    """Census years measured to carry *variable*, or None when unmeasured.

    None is not "every year". Read it as "this module has not checked", and
    check the table's own variable list before assuming a panel.
    """
    return SINGLE_CENSUS_VARIABLES.get(variable.strip().lower())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _visible_text(value: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(value or ""))).strip()


def _anchor_href(value: Any) -> str | None:
    """The href of the first anchor in a catalogue cell.

    None when there is no anchor. The cell text is the word "Download", so
    falling back to it would hand the caller a filename to fetch.
    """
    match = re.search(r"""href=["']([^"']+)["']""", str(value or ""))
    return match.group(1) if match else None


def _public_url(url: str) -> str:
    """The S3 URL with its presigned query string removed.

    ``X-Amz-Signature`` and ``X-Amz-Expires`` are a temporary credential, and
    the credential must not enter a manifest.
    """
    parts = urlparse(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def catalogue(*, session: Any = None, timeout: int = 90,
              min_rows: int = MIN_CATALOGUE_ROWS) -> dict[str, ShrugTable]:
    """Every table the Devdatalab endpoint advertises, keyed by table label.

    ``min_rows`` is the floor below which a response is treated as a changed
    endpoint rather than a smaller archive. This function is public and its row
    count gets printed as fact, so a partial render must not pass quietly. Lower
    the floor only to state deliberately that a short catalogue is expected.
    """
    sess = session if session is not None else make_session()
    resp = sess.get(
        CATALOGUE_URL,
        headers={"Accept": "application/json", "Referer": DOWNLOAD_PAGE_URL},
        timeout=timeout,
    )
    resp.raise_for_status()
    body = resp.text
    if not body.strip():
        raise ShrugCatalogueError(
            f"{CATALOGUE_URL} answered HTTP {getattr(resp, 'status_code', '?')} with an "
            "empty body. An empty-bodied 2xx is the AWS WAF grammar "
            "(x-amzn-waf-action: challenge), not an empty catalogue. Find a "
            "non-challenged mirror. Do not solve the challenge."
        )
    try:
        rows = json.loads(body)
    except json.JSONDecodeError:
        raise ShrugCatalogueError(
            f"{CATALOGUE_URL} did not return JSON — an interstitial or an outage, "
            f"NOT an empty catalogue: {_visible_text(body)[:200]}"
        ) from None
    if not isinstance(rows, list) or not rows:
        raise ShrugCatalogueError(
            f"{CATALOGUE_URL} returned no rows. The endpoint shape changed. SHRUG "
            "still publishes 52 tables."
        )
    # A SHORT catalogue is a changed endpoint, not a shrunken archive. SHRUG has
    # published 52 tables since this was measured, so a three-row answer is an
    # interstitial, a filtered response or a partial render — and `catalogue()` is
    # public, so its row count gets printed as fact.
    if len(rows) < min_rows:
        raise ShrugCatalogueError(
            f"{CATALOGUE_URL} returned {len(rows)} row(s). SHRUG publishes "
            f"{CATALOGUE_ROWS} tables, and fewer than {min_rows} is a changed or "
            "partial endpoint rather than a smaller archive. Pass min_rows to state "
            "a different floor deliberately."
        )
    tables: dict[str, ShrugTable] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ShrugCatalogueError(
                f"a catalogue row is a {type(row).__name__}, not an object: "
                f"{str(row)[:80]!r}. The endpoint shape changed."
            )
        label = _visible_text(row.get("table_short_label"))
        if label in tables:
            raise ShrugCatalogueError(
                f"{label!r} appears twice in the catalogue; keying on the label would "
                "drop one table in silence"
            )
        tables[label] = ShrugTable(
            module_label=_visible_text(row.get("module_short_label")),
            table_label=label,
            filetype=_visible_text(row.get("primary_filetype")),
            citation=_visible_text(row.get("citation")),
            url=_anchor_href(row.get("primary_download")),
            secondary_url=_anchor_href(row.get("secondary_download")),
        )
    return tables


def resolve_preset(name: str, tables: dict[str, ShrugTable]) -> list[ShrugTable]:
    """The tables a preset names, in preset order.

    Refuses the whole preset when any pattern matches no table. A preset that
    quietly returns six of seven tables hands the caller an incomplete panel
    that reads as complete.
    """
    if name not in PRESETS:
        raise ShrugCatalogueError(
            f"unknown preset {name!r}. Expected one of {sorted(PRESETS)}"
        )
    chosen: list[ShrugTable] = []
    for pattern in PRESETS[name]:
        matched = [t for label, t in tables.items() if re.search(pattern, label, re.I)]
        if not matched:
            raise ShrugCatalogueError(
                f"preset {name!r}: pattern {pattern!r} matched no table. The "
                f"catalogue carries: {sorted(tables)}"
            )
        for table in matched:
            if table not in chosen:
                chosen.append(table)
    return chosen


def size_of(url: str, *, session: Any = None, timeout: int = 90) -> int:
    """The file's total size in bytes, from a ranged GET.

    A HEAD returns 403 against these presigned links because they are signed
    for GET only, so a HEAD pre-flight reports every file as forbidden.
    ``Range: bytes=0-0`` answers 206 with ``Content-Range: bytes 0-0/<total>``.
    """
    sess = session if session is not None else make_session()
    resp = sess.get(url, headers={"Range": "bytes=0-0"}, timeout=timeout)
    headers = getattr(resp, "headers", None)
    if headers is None:
        raise ShrugCatalogueError(
            "this session's response exposes no headers, so Content-Range cannot be "
            "read. Install commoner-probe[http], or skip the size check."
        )
    content_range = str(headers.get("Content-Range") or "")
    match = re.search(r"/(\d+)\s*$", content_range)
    if not match:
        raise ShrugCatalogueError(
            f"{_public_url(url)} answered HTTP {getattr(resp, 'status_code', '?')} with "
            f"Content-Range {content_range!r}. A ranged GET is the only honest size "
            "check here. A HEAD 403s on a link that downloads fine, and a returned 0 "
            "would read as an empty file."
        )
    return int(match.group(1))


def _download(session: Any, url: str, dest: Path, *, timeout: int = 900,
              max_bytes: int | None = None, expect: int | None = None) -> tuple[str, int]:
    """Stream *url* to *dest* and return (sha256, bytes).

    Writes a ``.part`` file and renames it, so an aborted write leaves no short
    file that a later run would read as complete. ``expect`` is the size the
    ranged GET reported: a stream that ends early is the common S3 failure, and
    the ``.part`` rename does not protect against it. A sha256 of an incomplete
    table is worse than no sha256, because it looks like verification.

    The ``.part`` file is removed on any failure. A refused or capped download
    left it behind, where the next run's byte count would start from junk.
    """
    digest = hashlib.sha256()
    written = 0
    part = dest.with_suffix(dest.suffix + ".part")
    try:
        resp = session.get(url, timeout=timeout, stream=True)
        resp.raise_for_status()
        with part.open("wb") as handle:
            for chunk in iter_capped(resp, max_bytes=max_bytes):
                handle.write(chunk)
                digest.update(chunk)
                written += len(chunk)
        if expect is not None and written != expect:
            raise ShrugCatalogueError(
                f"{url} delivered {written} of {expect} bytes. A short stream is the "
                "common failure here, and a sha256 of a partial table reads as "
                "verification of a whole one."
            )
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    part.replace(dest)
    return digest.hexdigest(), written


def _recorded_keys(manifest: Path) -> set[str]:
    """Keys the manifest already carries, so a re-run does not duplicate a row."""
    if not manifest.exists():
        return set()
    keys = set()
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            keys.add(json.loads(line).get("key", ""))
        except json.JSONDecodeError:
            continue
    return keys - {""}


def _append(manifest: Path, record: dict, *, seen: set[str]) -> None:
    """One manifest row per table, written for a skip as well as a download.

    A file already on disk used to produce no row at all, so a reader of the
    manifest saw an empty archive while the returned rows said the preset was
    complete. Both were wrong, in opposite directions. The key guard keeps a
    re-run from appending the same row again.
    """
    if record["key"] in seen:
        return
    with manifest.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    seen.add(record["key"])


def _filename_for(table: ShrugTable) -> str:
    """A destination name unique to the TABLE, not only to its S3 basename.

    Several presigned URLs end in the same basename, so keying on it alone put
    two tables at one path: the first downloaded, the second reported
    ``skipped_exists``, and the caller could not tell which content it held. The
    module already refuses a duplicate catalogue label for this reason.
    """
    basename = Path(urlparse(table.url or "").path).name or "download.zip"
    stem = safe_filename_segment(table.table_label, collapse=True)
    return safe_filename_segment(f"{stem}-{basename}", collapse=True)


def fetch_preset(
    name: str,
    out_dir: Path | str,
    *,
    session: Any = None,
    timeout: int = 900,
    max_bytes: int | None = None,
    min_rows: int = MIN_CATALOGUE_ROWS,
    log: Callable[[str], None] = print,
) -> list[dict]:
    """Fetch every table in a preset, and append one manifest row per file.

    Rows go to ``manifest.jsonl`` under *out_dir*, one per table, each carrying
    sha256, byte count, licence, DOI and the unit. A file already on disk is
    reported as ``skipped_exists`` and not fetched again.
    """
    sess = session if session is not None else make_session()
    out = Path(out_dir)
    tables = resolve_preset(name, catalogue(session=sess, min_rows=min_rows))
    missing = [t.table_label for t in tables if not t.url]
    if missing:
        raise ShrugCatalogueError(
            f"no download link in the catalogue row for {missing} — the anchor is "
            "absent, which is a changed endpoint and not a withdrawn table"
        )
    for line in caveats():
        log(line)

    out.mkdir(parents=True, exist_ok=True)
    manifest = out / "manifest.jsonl"
    seen = _recorded_keys(manifest)
    rows: list[dict] = []
    for table in tables:
        filename = _filename_for(table)
        dest = out / filename
        record = {
            "key": f"SHRUG|{table.table_label}",
            "kind": "shrug_table",
            "record_type": "shrug_table",
            "source_family": SOURCE_FAMILY,
            "publisher": PUBLISHER,
            "module_label": table.module_label,
            "table_label": table.table_label,
            "filetype": table.filetype,
            "citation": table.citation,
            "url": _public_url(table.url or ""),
            "dest": filename,
            "bytes": None,
            "sha256": None,
            "doi": DOI,
            "licence": LICENCE,
            "unit": UNIT,
            "status": "skipped_exists",
            "fetched_at": _now(),
        }
        total = size_of(table.url, session=sess)
        if dest.exists():
            # A file on disk was trusted with no check at all, and the skip was
            # never written to the manifest. Two zero-byte files produced two
            # rows saying the preset was held, while the manifest stayed absent:
            # one reader saw an empty archive, another saw a complete preset.
            held = dest.stat().st_size
            record.update(bytes=held,
                          status="skipped_exists" if held == total else "short_on_disk")
            if held != total:
                log(f"SHORT {filename}: {held} of {total} bytes on disk — re-fetch it")
            else:
                log(f"have {filename} ({held} bytes)")
            _append(manifest, record, seen=seen)
            rows.append(record)
            continue
        log(f"{table.table_label} ({total / 1e6:.1f} MB)")
        sha256, written = _download(
            sess, table.url, dest, timeout=timeout, max_bytes=max_bytes, expect=total
        )
        record.update(sha256=sha256, bytes=written, status="downloaded")
        _append(manifest, record, seen=seen)
        rows.append(record)
        log(f"  -> {filename}  sha256 {sha256[:16]}…")
    return rows
