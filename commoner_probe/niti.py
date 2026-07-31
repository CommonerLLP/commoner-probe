# SPDX-License-Identifier: MIT
"""NITI Aayog Annual Reports — acquisition with provenance.

REQ-0020's residual. The Annual Reports carry the "Young Professionals"
headcount, a named primary source for the contractualisation / consultant face
of the outsourcing panel. This module acquires the documents; extracting the
headcount from them is a separate step.

Same job as :mod:`commoner_probe.doe`, different listing — a page of per-year
PDFs, downloaded with a text-layer check and a manifest row each.

**Three traps, all measured against the live listing on 2026-07-30, and each
would produce a wrong manifest:**

1. **Every PDF link appears twice.** The listing renders each document in two
   places, so an undeduped parse doubles the corpus.
2. **English and Hindi are separate files**, and the spacing before the
   parenthesis is inconsistent — ``Annual Report of NITI Aayog 2025-26
   (English).pdf`` against ``...2025-26(Hindi).pdf``. Language is selected
   explicitly rather than by position.
3. **The upload directory looks like a fiscal year.** Documents live under
   ``/sites/default/files/<YYYY-MM>/``, so a ``(20\\d{2})[-_ ](\\d{2})`` pattern
   matches ``2025-02`` and invents the fiscal years ``2020-02`` and ``2023-02``.
   The year is therefore read from the FILENAME, never from the path.

The obvious URLs are wrong and fail loudly only if you check the status:
``/annual-reports`` and ``/documents/annual-report`` both return **404 with a
41 KB error body**, so a "did we get bytes back" check passes on the error page.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

from commoner_probe.http_client import make_session

#: The canonical path, NOT the `/index.php/` alias. Both serve the same 34
#: PDFs, but the shared HTTP client refuses the alias under NITI's robots.txt,
#: which disallows a list of `/index.php/...` paths. The repo respects
#: robots.txt strictly (see the `moca` decision), so the fix is to request the
#: allowed URL rather than to relax the check.
NITI_LISTING_URL = "https://www.niti.gov.in/publication/annual-report"
PUBLISHER = "NITI Aayog"
DEFAULT_SLEEP = 3.0

#: A SHORT honest UA, because NITI's WAF 403s the default one.
#:
#: Nothing here is a bypass, and the distinction matters. NITI's robots.txt
#: **permits this crawl** — `can_fetch` returns True for `*` and for this tool
#: on `/publication/annual-report` and on `/sites/default/files/`, and the file
#: contains no `Disallow: /`. The problem was never permission: the WAF returned
#: 403 to the *fetch of robots.txt itself* when the UA carried a URL fragment,
#: and this repo treats an unreadable robots.txt as disallow-all (the `moca`
#: decision), so the probe refused itself over a file that says yes.
#:
#: Measured 2026-07-30: `commoner-probe/<ver> (research)`, `commoner-probe/<ver>`
#: and bare `commoner-probe` all return 200; only the default UA's
#: `(+https://github.com/...)` fragment is refused. So the tool still names
#: itself, robots.txt is genuinely read and obeyed, and no `respect_robots=False`
#: opt-out is needed. A browser token is NOT used — that would contradict the
#: standing NAI/Abhilekh Patal decision to stay honest and simply not fetch.
NITI_UA = "commoner-probe/{version} (research)"

#: A downloaded report must read back as text. The listing's own 404 page is
#: 41 KB of HTML, so "we received bytes" proves nothing.
TEXT_LAYER_MIN_CHARS = 500

_PDF_HREF_RE = re.compile(r'href="([^"]+\.pdf[^"]*)"', re.IGNORECASE)

#: Fiscal year as printed in the FILENAME — `2025-26`, `2024-25`, `2020-2021`.
#: The second half may be two OR four digits, and a `(\d{2})` tail silently
#: turns `2020-2021` into the nonsense year `2020-20`.
#:
#: Deliberately not run against the URL path, which carries an upload directory
#: of the same shape (see the module docstring).
_YEAR_RE = re.compile(r"(20\d{2})\s*[-–_]\s*(20)?(\d{2})")

#: Language marker. Neither parenthesis-anchored nor `\b`-anchored, because the
#: real listing carries all three of `...2025-26 (English).pdf`,
#: `...2025-26(Hindi).pdf` and `Annual Report 2024-25 Hindi_V3 LOWRES.pdf`.
#:
#: `\bhindi\b` does NOT match `Hindi_V3` — `_` is a word character, so there is
#: no boundary after the marker. Both that and the parenthesis form labelled a
#: Hindi document english by default, handing a caller who asked for English the
#: wrong language. The lookarounds exclude only letters.
_LANG_RE = re.compile(r"(?<![a-z])(english|hindi)(?![a-z])", re.IGNORECASE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_listing(html: str, *, listing_url: str = NITI_LISTING_URL) -> list[dict[str, Any]]:
    """One entry per distinct Annual Report PDF.

    Deduped by absolute URL because the listing renders each document twice.
    The fiscal year comes from the filename; a document whose filename carries
    no year is skipped rather than guessed at, since the only other candidate
    on the URL is the upload directory.
    """
    reports: list[dict[str, Any]] = []
    seen: set[str] = set()
    for href in _PDF_HREF_RE.findall(html):
        url = urljoin(listing_url, unescape(href))
        if url in seen:
            continue
        seen.add(url)
        basename = Path(unquote(urlparse(url).path)).name
        year_m = _YEAR_RE.search(basename)
        if not year_m:
            continue
        lang_m = _LANG_RE.search(basename)
        reports.append({
            "year": f"{year_m.group(1)}-{year_m.group(3)}",
            "language": (lang_m.group(1).lower() if lang_m else "english"),
            "filename": basename,
            "url": url,
        })
    return reports


class NitiAnnualReportProbe:
    """Acquire the NITI Aayog Annual Report series with provenance."""

    def __init__(
        self,
        out_dir: Path,
        *,
        sleep: float = DEFAULT_SLEEP,
        listing_url: str = NITI_LISTING_URL,
        session: Any = None,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.sleep = sleep
        self.listing_url = listing_url
        self.manifest = self.out_dir / "manifest.jsonl"
        if session is None:
            from . import __version__

            session = make_session(
                rate_limit_sec=sleep, user_agent=NITI_UA.format(version=__version__)
            )
        self.session = session

    def discover(self, *, years: list[str] | None = None) -> list[dict[str, Any]]:
        """The English Annual Reports, newest first.

        English only, deliberately. The consumer wants the Young Professionals
        headcount and nobody asked for the Hindi editions, so there is no
        language option to get them wrong with.

        The language is still DETECTED, and that is load-bearing rather than
        decorative: the listing carries a Hindi edition named
        `Annual Report 2024-25 Hindi_V3 LOWRES.pdf`, which no parenthesis- or
        word-boundary-anchored match catches. Without detection this probe would
        hand the caller a Hindi PDF labelled English and the extractor would
        find nothing.
        """
        r = self.session.get(self.listing_url, timeout=60)
        r.raise_for_status()
        reports = parse_listing(r.text, listing_url=self.listing_url)
        if not reports:
            raise ValueError(
                f"no Annual Report PDFs found at {self.listing_url}. The listing moved or its "
                "markup changed — note that /annual-reports and /documents/annual-report both "
                "return 404 with a 41 KB body, which looks like content."
            )
        out = [r_ for r_ in reports if r_["language"] == "english"]
        if years:
            wanted = set(years)
            out = [r_ for r_ in out if r_["year"] in wanted]
        return sorted(out, key=lambda r_: r_["year"], reverse=True)

    def _record(self, report: dict[str, Any], *, status: str) -> dict[str, Any]:
        now = _now()
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", report["filename"])
        dest = self.out_dir / f"{report['year']}_{report['language']}_{safe}"
        return {
            "key": f"NITI_AR|{report['year']}|{report['language']}",
            "kind": "niti_annual_report",
            "record_type": "niti_annual_report",
            "source_family": "niti-annual-report",
            "publisher": PUBLISHER,
            "report_year": report["year"],
            "language": report["language"],
            "filename": report["filename"],
            "dest": str(dest.relative_to(self.out_dir)),
            "url": report["url"],
            "source_page_url": self.listing_url,
            "status": status,
            "sha256": None,
            "bytes": None,
            "text_layer": None,
            "fetched_at": now,
            "probed_at": now,
        }

    def append_manifest(self, record: dict[str, Any]) -> None:
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

    def download(self, report: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
        record = self._record(report, status="dry_run" if dry_run else "pending")
        if dry_run:
            return record
        dest = self.out_dir / record["dest"]
        if dest.exists() and dest.stat().st_size > 0:
            record["status"] = "skipped_exists"
            record["bytes"] = dest.stat().st_size
            record["sha256"] = hashlib.sha256(dest.read_bytes()).hexdigest()
            return record
        r = self.session.get(report["url"], timeout=180)
        r.raise_for_status()
        body = r.content
        if not body.startswith(b"%PDF"):
            raise ValueError(
                f"{report['url']} did not return a PDF (first bytes {body[:8]!r}). The listing's "
                "own 404 page is 41 KB of HTML, so a size check would not catch this."
            )
        self.out_dir.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        record["bytes"] = len(body)
        record["sha256"] = hashlib.sha256(body).hexdigest()
        # Whether the PDF carries a text layer decides if the Young
        # Professionals table can be read without OCR. Recorded, never assumed.
        try:
            from .textparse import extract_pdf_text

            record["text_layer"] = len((extract_pdf_text(dest) or "").strip()) >= TEXT_LAYER_MIN_CHARS
        except Exception:  # noqa: BLE001 — extraction is advisory, acquisition is not
            record["text_layer"] = None
        record["status"] = "downloaded"
        return record

    def probe(
        self, *, years: list[str] | None = None, dry_run: bool = False
    ) -> list[dict[str, Any]]:
        seen = self.load_seen()
        records: list[dict[str, Any]] = []
        for report in self.discover(years=years):
            record = self._record(report, status="pending")
            if not dry_run and record["key"] in seen:
                continue
            record = self.download(report, dry_run=dry_run)
            if not dry_run:
                self.append_manifest(record)
            records.append(record)
        return records
