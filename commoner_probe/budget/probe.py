# SPDX-License-Identifier: MIT
"""Acquire Union Budget SBE spreadsheets and RBI State-Finances documents.

Mirrors :class:`commoner_probe.dmft.mines.MinesDmftProbe`: a topic-less probe
that materialises a set of known/discovered source-file endpoints, downloads
each (with existence-skip + SHA-256 provenance), and appends one
``budget_source_file`` record per file to ``manifest.jsonl``.

Two source families:

* ``union-budget`` — a *static* table of per-fiscal-year URL templates
  (``UNION_BUDGET_YEARS``, ported verbatim from
  ``budget-crawler/publicfinance/union_budget_scraper.py``) expanded across the
  requested demand numbers. No network needed to enumerate; ``--dry-run`` is
  fully offline.
* ``rbi-state-finances`` — *discovered* by fetching the RBI publication page and
  parsing its document table (:func:`parse_rbi_documents`, lazy-imports lxml).

Acquisition only — the XLS→rows parsing (``parse_demand_xls`` and friends) stays
in ``budget-crawler``; it is an analysis concern and needs pandas, which the
acquisition path deliberately avoids.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

from commoner_probe.http_client import TOOL_VERSION

# indiabudget.gov.in's WAF returns 403 for any User-Agent containing a URL
# scheme (e.g. the standard probe USER_AGENT's "+https://..."). A short,
# scheme-free identifier is accepted — keep honest tool identification while
# staying past the filter. Overridable per-instance.
BUDGET_USER_AGENT = (
    f"commoner-probe/{TOOL_VERSION} "
    "(github.com/CommonerLLP/commoner-probe; public-interest research; rate-limited)"
)

# Union Budget Statement of Budget Estimates (SBE) "Demand for Grants" files.
# Per-fiscal-year URL templates with a ``{demand}`` placeholder. The current
# year is served from the site root; archived years live under a
# ``budget{fy}/`` path segment. Extensions vary (.xls before 2023-24, .xlsx
# after). Ported from budget-crawler/publicfinance/union_budget_scraper.py.
UNION_BUDGET_YEARS: tuple[tuple[str, str], ...] = (
    ("2026-27", "https://www.indiabudget.gov.in/doc/eb/sbe{demand}.xlsx"),
    ("2025-26", "https://www.indiabudget.gov.in/budget2025-26/doc/eb/sbe{demand}.xlsx"),
    ("2024-25", "https://www.indiabudget.gov.in/budget2024-25/doc/eb/sbe{demand}.xlsx"),
    ("2023-24", "https://www.indiabudget.gov.in/budget2023-24/doc/eb/sbe{demand}.xls"),
    ("2022-23", "https://www.indiabudget.gov.in/budget2022-23/doc/eb/sbe{demand}.xls"),
    ("2021-22", "https://www.indiabudget.gov.in/budget2021-22/doc/eb/sbe{demand}.xls"),
    ("2020-21", "https://www.indiabudget.gov.in/budget2020-21/doc/eb/sbe{demand}.xlsx"),
)

RBI_STATE_FINANCES_URL = (
    "https://www.rbi.org.in/scripts/AnnualPublications.aspx"
    "?head=State+Finances+%3a+A+Study+of+Budgets"
)

# RBI publication-table XPaths, ported from
# budget-crawler/publicfinance/rbi_budgets_scraper.py.
_RBI_TABLE_ROWS_XPATH = "//table[@class='tablebg']/tr"
_RBI_HEADER_XPATH = "./td[@class='tableheader']//text()"
_RBI_TITLE_XPATH = "./td[@style]//text()"
_RBI_XLS_LINK_XPATH = "./td[2]/a[@target]/@href"
_RBI_PDF_LINK_XPATH = "./td[3]/a[@target]/@href"
_RBI_PAGE_YEAR_XPATH = "//text()"

_MEDIA_TYPES: dict[str, str] = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "pdf": "application/pdf",
    "zip": "application/zip",
}

# Canonical source-name aliases accepted on the CLI / API.
_SOURCE_ALIASES = {
    "union": "union-budget",
    "union-budget": "union-budget",
    "rbi": "rbi-state-finances",
    "rbi-state-finances": "rbi-state-finances",
}


@dataclass(frozen=True)
class BudgetEndpoint:
    """One acquirable budget source file (a single XLS/PDF/zip URL)."""

    source_name: str  # "union-budget" | "rbi-state-finances"
    publisher: str
    fiscal_year: str
    document_type: str  # "demand_for_grants" | "state_finances_study"
    filename: str
    url: str
    media_type: str
    demand_no: str | None = None
    section: str | None = None  # RBI publication section / title context


def _media_type_for(url: str, fallback: str = "application/octet-stream") -> str:
    ext = url.rsplit(".", 1)[-1].split("?", 1)[0].lower()
    return _MEDIA_TYPES.get(ext, fallback)


def _http_date_to_iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return value
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def union_budget_endpoints(demands: list[str]) -> list[BudgetEndpoint]:
    """Expand the static SBE URL templates across the requested demand numbers."""
    endpoints: list[BudgetEndpoint] = []
    for fiscal_year, template in UNION_BUDGET_YEARS:
        for demand in demands:
            demand = demand.strip()
            if not demand:
                continue
            url = template.format(demand=demand)
            ext = url.rsplit(".", 1)[-1]
            endpoints.append(
                BudgetEndpoint(
                    source_name="union-budget",
                    publisher="Ministry of Finance",
                    fiscal_year=fiscal_year,
                    document_type="demand_for_grants",
                    demand_no=demand,
                    filename=f"sbe{demand}_{fiscal_year}.{ext}",
                    url=url,
                    media_type=_media_type_for(url),
                )
            )
    return endpoints


def parse_rbi_documents(
    html: str,
    *,
    base_url: str = RBI_STATE_FINANCES_URL,
    fiscal_year: str | None = None,
) -> list[BudgetEndpoint]:
    """Parse an RBI "State Finances" publication page into budget endpoints.

    Pure function (no network) so it is unit-testable with canned HTML. Lazy-
    imports lxml; raises a clear error if the ``budget`` extra is not installed.
    """
    try:
        from lxml import html as lxml_html  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised via guard message
        raise ImportError(
            "RBI State-Finances discovery requires lxml — "
            "run: pip install commoner-probe[budget]"
        ) from exc

    dom = lxml_html.fromstring(html)

    resolved_year: str
    if fiscal_year is not None:
        resolved_year = fiscal_year
    else:
        import re

        page_text = " ".join(dom.xpath(_RBI_PAGE_YEAR_XPATH))
        match = re.search(r"\b(20\d{2}-\d{2})\b", page_text)
        resolved_year = match.group(1) if match else "unknown"

    endpoints: list[BudgetEndpoint] = []
    current_section = "Publication"
    for node in dom.xpath(_RBI_TABLE_ROWS_XPATH):
        header = "".join(node.xpath(_RBI_HEADER_XPATH)).strip()
        if header:
            current_section = header
            continue
        title = "".join(node.xpath(_RBI_TITLE_XPATH)).strip()
        if not title:
            continue
        for ext_hint, link_xpath in (("xls", _RBI_XLS_LINK_XPATH), ("pdf", _RBI_PDF_LINK_XPATH)):
            links = node.xpath(link_xpath)
            if not links:
                continue
            doc_url = urljoin(base_url, links[0]).replace("http://", "https://")
            ext = Path(urlparse(doc_url).path).suffix.lstrip(".").lower() or ext_hint
            safe_title = "".join(c if c.isalnum() or c in "._- " else "_" for c in title).strip()
            endpoints.append(
                BudgetEndpoint(
                    source_name="rbi-state-finances",
                    publisher="Reserve Bank of India",
                    fiscal_year=resolved_year,
                    document_type="state_finances_study",
                    section=current_section,
                    filename=f"{safe_title}.{ext}",
                    url=doc_url,
                    media_type=_media_type_for(doc_url, _MEDIA_TYPES.get(ext, "application/octet-stream")),
                )
            )
    return endpoints


def normalize_sources(sources: list[str]) -> list[str]:
    """Map source aliases to canonical names, preserving order, de-duplicated."""
    out: list[str] = []
    for s in sources:
        canonical = _SOURCE_ALIASES.get(s.strip().lower())
        if canonical and canonical not in out:
            out.append(canonical)
    return out


class BudgetProbe:
    """Acquire Union Budget + RBI State-Finances source files with provenance."""

    def __init__(
        self,
        out_dir: Path,
        *,
        sleep: float = 1.0,
        demands: list[str] | None = None,
        rbi_url: str = RBI_STATE_FINANCES_URL,
        user_agent: str = BUDGET_USER_AGENT,
    ) -> None:
        self.out_dir = out_dir
        self.sleep = sleep
        self.demands = demands or ["101"]
        self.rbi_url = rbi_url
        self.user_agent = user_agent
        self.manifest = out_dir / "manifest.jsonl"

    def _build_opener(self) -> urllib.request.OpenerDirector:
        return urllib.request.build_opener()

    def _fetch_text(self, opener: urllib.request.OpenerDirector, url: str) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        with opener.open(req, timeout=60) as resp:
            return resp.read().decode("utf-8", errors="replace")

    def discover_rbi(
        self,
        opener: urllib.request.OpenerDirector,
        url: str | None = None,
        *,
        fiscal_year: str | None = None,
    ) -> list[BudgetEndpoint]:
        html = self._fetch_text(opener, url or self.rbi_url)
        return parse_rbi_documents(html, base_url=url or self.rbi_url, fiscal_year=fiscal_year)

    def endpoints_for(
        self,
        sources: list[str],
        opener: urllib.request.OpenerDirector | None = None,
    ) -> list[BudgetEndpoint]:
        sources = normalize_sources(sources)
        endpoints: list[BudgetEndpoint] = []
        if "union-budget" in sources:
            endpoints.extend(union_budget_endpoints(self.demands))
        if "rbi-state-finances" in sources:
            if opener is None:
                opener = self._build_opener()
            endpoints.extend(self.discover_rbi(opener))
        return endpoints

    def _record(self, endpoint: BudgetEndpoint, *, status: str) -> dict:
        now = _now_iso()
        dest = self.out_dir / endpoint.source_name / endpoint.filename
        return {
            "key": f"BUDGET|{endpoint.source_name}|{endpoint.fiscal_year}|{endpoint.filename}",
            "kind": "budget_source_file",
            "record_type": "budget_source_file",
            "source_family": "budget",
            "source_name": endpoint.source_name,
            "publisher": endpoint.publisher,
            "fiscal_year": endpoint.fiscal_year,
            "document_type": endpoint.document_type,
            "demand_no": endpoint.demand_no,
            "section": endpoint.section,
            "filename": endpoint.filename,
            "dest": str(dest),
            "url": endpoint.url,
            "status": status,
            "media_type": endpoint.media_type,
            "fetched_at": now,
            "probed_at": now,
        }

    def download_endpoint(
        self,
        opener: urllib.request.OpenerDirector,
        endpoint: BudgetEndpoint,
        *,
        dry_run: bool,
    ) -> dict:
        record = self._record(endpoint, status="dry_run" if dry_run else "pending")
        dest = Path(record["dest"])

        if dry_run:
            return record

        if dest.exists() and dest.stat().st_size > 0:
            record["status"] = "skipped_exists"
            record["sha256"] = hashlib.sha256(dest.read_bytes()).hexdigest()
            return record

        req = urllib.request.Request(endpoint.url, headers={"User-Agent": self.user_agent})
        try:
            with opener.open(req, timeout=60) as resp:
                body = resp.read()
                source_last_modified_raw = resp.headers.get("Last-Modified")
                content_type = resp.headers.get("Content-Type")
        except urllib.error.HTTPError as exc:
            # The Union Budget endpoint set is a cartesian product of years ×
            # demands; not every demand exists every year, so 404s are normal.
            record["status"] = "not_found" if exc.code == 404 else "error"
            record["http_status"] = exc.code
            return record
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            record["status"] = "error"
            record["error"] = str(exc)
            return record

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        record["status"] = "downloaded"
        record["sha256"] = hashlib.sha256(body).hexdigest()
        if content_type:
            record["media_type"] = content_type.split(";", 1)[0].strip()
        if source_last_modified_raw:
            record["source_last_modified_raw"] = source_last_modified_raw
            record["source_last_modified"] = _http_date_to_iso(source_last_modified_raw)
        if self.sleep:
            time.sleep(self.sleep)
        return record

    def append_manifest(self, record: dict) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def probe_sources(self, sources: list[str], *, dry_run: bool = False) -> list[dict]:
        opener = self._build_opener()
        endpoints = self.endpoints_for(sources, opener)
        records = [
            self.download_endpoint(opener, endpoint, dry_run=dry_run)
            for endpoint in endpoints
        ]
        if not dry_run:
            for record in records:
                self.append_manifest(record)
        return records
