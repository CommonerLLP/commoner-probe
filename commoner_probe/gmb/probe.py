# SPDX-License-Identifier: MIT
"""Acquire Gujarat Maritime Board (gmbports.org) public disclosures with provenance.

Sibling of :class:`commoner_probe.dmft.mines.MinesDmftProbe` and
:class:`commoner_probe.budget.probe.BudgetProbe`: a topic-less probe that
materialises a set of known content pages, saves each rendered page HTML for
provenance, discovers and downloads the PDFs linked from those pages, and — for
the traffic pages, which are HTML tables rather than PDFs — parses the tables
into a tidy long-format CSV. Every artifact (page HTML, discovered PDF, derived
CSV) becomes one ``gmb_document`` record in ``manifest.jsonl`` with a SHA-256 and
source URL.

gmbports.org is an IIS/ASP.NET site: content lives at ``showpage.aspx?contentid=N``
and documents under ``/assets/downloads/``.

Fetching goes through :func:`commoner_probe.http_client.make_session` — the shared
client with SSRF guard, robots.txt honouring, per-domain rate limiting, and 5xx
backoff. No raw urllib/requests here.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

from commoner_probe.http_client import make_session

GMB_BASE_URL = "https://gmbports.org"


@dataclass(frozen=True)
class GmbSource:
    """One selectable GMB source class (a ``--sources`` selector)."""

    name: str  # CLI selector, e.g. "admin-reports"
    doc_class: str  # document_class for discovered PDFs
    page_class: str  # document_class for the index-page HTML record
    content_ids: tuple[str, ...]  # showpage.aspx?contentid=N pages to visit
    kind: str = "pdf_index"  # "pdf_index" | "traffic"
    pdf_keywords: tuple[str, ...] | None = None  # keep only links matching these


@dataclass(frozen=True)
class GmbEndpoint:
    """One acquirable GMB artifact (a page, a discovered PDF, or a derived CSV)."""

    source_name: str
    document_class: str
    filename: str
    url: str
    media_type: str
    publisher: str = "Gujarat Maritime Board"
    fiscal_year: str | None = None
    language: str | None = None
    note: str | None = None
    discovered_on: str | None = None


GMB_SOURCES: tuple[GmbSource, ...] = (
    # Administrative (annual) reports — English + Gujarati, all available years.
    # Discover from the Publications page (56) and its parent listing (307).
    GmbSource(
        name="admin-reports",
        doc_class="admin-report",
        page_class="admin-report",
        content_ids=("56", "307"),
        pdf_keywords=("admin", "annual", "report"),
    ),
    # Maritime Horizon magazine + brochures, same Publications page.
    GmbSource(
        name="publications-misc",
        doc_class="publication",
        page_class="publication",
        content_ids=("56",),
        pdf_keywords=("horizon", "brochure", "magazine", "newsletter", "publication"),
    ),
    # Income/expenditure & account statements.
    GmbSource(
        name="financials",
        doc_class="financial",
        page_class="financial",
        content_ids=("50",),
    ),
    # Per-port cargo tonnage time-series — HTML tables → tidy CSV.
    GmbSource(
        name="traffic",
        doc_class="traffic-table",
        page_class="traffic-page",
        content_ids=("46", "504"),
        kind="traffic",
    ),
    GmbSource("tariff", "tariff", "tariff", ("212",)),
    GmbSource("circulars", "circular", "circular", ("3208",)),
    GmbSource("tenders", "tender", "tender", ("63",)),
    GmbSource("rti", "rti", "rti", ("69",)),
    GmbSource("vision-2047", "vision", "vision", ("30454",)),
    GmbSource("news-articles", "news", "news", ("876",)),
)

ALL_SOURCE_NAMES: tuple[str, ...] = tuple(s.name for s in GMB_SOURCES)

# --------------------------------------------------------------------------- #
# Small pure helpers                                                          #
# --------------------------------------------------------------------------- #

_A_PDF_RE = re.compile(
    r"""<a\b[^>]*?\bhref\s*=\s*["']([^"']+?\.pdf(?:\?[^"']*)?)["'][^>]*>(.*?)</a>""",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_FY_RE = re.compile(r"(20\d{2})[_\-](\d{2})(?!\d)")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _http_date_to_iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return value
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _sanitize_filename(url: str) -> str:
    path = urlparse(url).path
    name = unquote(path.rsplit("/", 1)[-1]) or "index.pdf"
    return "".join(c if c.isalnum() or c in "._- " else "_" for c in name).strip() or "index.pdf"


def _parse_fiscal_year(name: str) -> str | None:
    m = _FY_RE.search(name)
    return f"{m.group(1)}-{m.group(2)}" if m else None


def _parse_language(name: str) -> str | None:
    low = name.lower()
    if "english" in low or "_eng" in low or "-eng" in low:
        return "en"
    if "gujarati" in low or "_guj" in low or "-guj" in low:
        return "gu"
    return None


def _body_bytes(resp) -> bytes:
    content = getattr(resp, "content", None)
    if content is not None:
        return content
    return (getattr(resp, "text", "") or "").encode("utf-8")


def discover_pdf_links(html: str, *, base_url: str, source: GmbSource) -> list[GmbEndpoint]:
    """Parse an index page's ``<a href>`` links into PDF endpoints (pure, no network).

    Relative links are ``urljoin``-ed against *base_url* (the page URL). When the
    source declares ``pdf_keywords`` only links whose href/anchor-text match one
    are kept, so the same Publications page can feed both ``admin-reports`` and
    ``publications-misc`` without cross-contamination.
    """
    endpoints: list[GmbEndpoint] = []
    seen: set[str] = set()
    for m in _A_PDF_RE.finditer(html):
        href = m.group(1).strip()
        text = _TAG_RE.sub(" ", m.group(2)).strip()
        url = urljoin(base_url, href)
        if not url.lower().startswith(("http://", "https://")):
            continue
        if source.pdf_keywords:
            hay = f"{href} {text}".lower()
            if not any(k in hay for k in source.pdf_keywords):
                continue
        if url in seen:
            continue
        seen.add(url)
        filename = _sanitize_filename(url)
        endpoints.append(
            GmbEndpoint(
                source_name=source.name,
                document_class=source.doc_class,
                filename=filename,
                url=url,
                media_type="application/pdf",
                fiscal_year=_parse_fiscal_year(filename),
                language=_parse_language(filename),
                discovered_on=base_url,
            )
        )
    return endpoints


# --- traffic HTML tables → tidy long rows ---------------------------------- #

_FY_NORM_RE = re.compile(r"(20\d{2})\s*[-/]\s*(\d{2,4})")
_NUM_RE = re.compile(r"^-?[\d,]+(?:\.\d+)?$")


class _TrafficTableExtractor(HTMLParser):
    """Collect (section_heading, rows-of-cell-text) for every ``<table>``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[tuple[str, list[list[str]]]] = []
        self._rows: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._in_heading = False
        self._heading_buf: list[str] = []
        self._last_heading = ""

    def handle_starttag(self, tag, attrs):
        if tag in ("h1", "h2", "h3", "h4", "caption"):
            self._in_heading = True
            self._heading_buf = []
        elif tag == "table":
            self._rows = []
        elif tag == "tr" and self._rows is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3", "h4", "caption") and self._in_heading:
            self._in_heading = False
            text = "".join(self._heading_buf).strip()
            if text:
                self._last_heading = text
        elif tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))  # type: ignore[union-attr]
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self._rows.append(self._row)  # type: ignore[union-attr]
            self._row = None
        elif tag == "table" and self._rows is not None:
            self.tables.append((self._last_heading, self._rows))
            self._rows = None

    def handle_data(self, data):
        if self._in_heading:
            self._heading_buf.append(data)
        elif self._cell is not None:
            self._cell.append(data)


def _classify_section(heading: str) -> str:
    h = heading.lower()
    if "non-major" in h or "non major" in h:
        return "Traffic Handled at various Non-Major GMB ports"
    if "gmb owned" in h or "gmb-owned" in h:
        return "Traffic Handled at GMB owned Ports"
    return heading or "unknown"


def _normalize_fy(cell: str) -> str:
    m = _FY_NORM_RE.search(cell)
    if not m:
        return cell.strip()
    return f"{m.group(1)}-{m.group(2)[-2:]}"


def _clean_number(cell: str) -> str | None:
    v = cell.strip()
    if not v or v in {"-", "--", "NA", "N/A"}:
        return None
    if not _NUM_RE.match(v):
        return None
    return v.replace(",", "")


def parse_traffic_tables(html: str, *, source_url: str) -> list[dict]:
    """Parse GMB traffic HTML tables into tidy long-format rows (pure, no network).

    Output columns: ``table_section``, ``operator_class``, ``port_or_class``,
    ``fiscal_year``, ``tonnage_lakh_tonnes`` — one row per (port/class × fiscal
    year) cell. First table row is treated as the fiscal-year header; the first
    column of each data row is the port or jetty-class label.
    """
    ex = _TrafficTableExtractor()
    ex.feed(html)
    rows: list[dict] = []
    for heading, table in ex.tables:
        if len(table) < 2:
            continue
        years = table[0][1:]
        section = _classify_section(heading)
        non_major = "non-major" in section.lower()
        for data_row in table[1:]:
            if not data_row:
                continue
            label = data_row[0].strip()
            if not label:
                continue
            for i, value in enumerate(data_row[1:]):
                if i >= len(years):
                    break
                num = _clean_number(value)
                if num is None:
                    continue
                rows.append(
                    {
                        "table_section": section,
                        "operator_class": label if non_major else "GMB Owned Port",
                        "port_or_class": label,
                        "fiscal_year": _normalize_fy(years[i]),
                        "tonnage_lakh_tonnes": num,
                    }
                )
    return rows


_TRAFFIC_CSV_COLUMNS = (
    "table_section",
    "operator_class",
    "port_or_class",
    "fiscal_year",
    "tonnage_lakh_tonnes",
)


def _rows_to_csv(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(_TRAFFIC_CSV_COLUMNS))
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


# --------------------------------------------------------------------------- #
# Probe                                                                       #
# --------------------------------------------------------------------------- #


class GmbProbe:
    """Acquire Gujarat Maritime Board public disclosures with provenance."""

    def __init__(self, out_dir: Path, *, sleep: float = 1.0, base_url: str = GMB_BASE_URL) -> None:
        self.out_dir = out_dir
        self.sleep = sleep
        self.base_url = base_url.rstrip("/")
        self.manifest = out_dir / "manifest.jsonl"
        self.session = make_session()

    # --- selection / urls ---

    def selected_sources(self, sources: list[str]) -> list[GmbSource]:
        names: set[str] = set()
        for s in sources:
            token = s.strip().lower()
            if token == "all":
                return list(GMB_SOURCES)
            if token:
                names.add(token)
        return [s for s in GMB_SOURCES if s.name in names]

    def index_url(self, content_id: str) -> str:
        return f"{self.base_url}/showpage.aspx?contentid={content_id}"

    # --- record + io ---

    def _record(self, endpoint: GmbEndpoint, *, status: str) -> dict:
        now = _now_iso()
        dest = self.out_dir / endpoint.source_name / endpoint.filename
        record = {
            "key": f"GMB|{endpoint.source_name}|{endpoint.filename}",
            "kind": "gmb_document",
            "record_type": "gmb_document",
            "source_family": "gmb",
            "source_name": endpoint.source_name,
            "publisher": endpoint.publisher,
            "document_class": endpoint.document_class,
            "filename": endpoint.filename,
            "dest": str(dest),
            "url": endpoint.url,
            "status": status,
            "media_type": endpoint.media_type,
            "fetched_at": now,
            "probed_at": now,
        }
        if endpoint.fiscal_year:
            record["fiscal_year"] = endpoint.fiscal_year
        if endpoint.language:
            record["language"] = endpoint.language
        if endpoint.note:
            record["note"] = endpoint.note
        if endpoint.discovered_on:
            record["discovered_on"] = endpoint.discovered_on
        return record

    def append_manifest(self, record: dict) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _get(self, url: str):
        return self.session.get(url, timeout=60, respect_robots=True)

    def _nap(self) -> None:
        if self.sleep:
            time.sleep(self.sleep)

    def _apply_headers(self, record: dict, resp) -> None:
        headers = getattr(resp, "headers", None)
        if not headers or not hasattr(headers, "get"):
            return
        raw = headers.get("Last-Modified")
        if raw:
            record["source_last_modified_raw"] = raw
            record["source_last_modified"] = _http_date_to_iso(raw)
        content_type = headers.get("Content-Type")
        if content_type:
            record["media_type"] = content_type.split(";", 1)[0].strip()

    # --- fetch primitives ---

    def _download_page(self, endpoint: GmbEndpoint) -> tuple[str | None, dict]:
        dest = self.out_dir / endpoint.source_name / endpoint.filename
        if dest.exists() and dest.stat().st_size > 0:
            body = dest.read_bytes()
            record = self._record(endpoint, status="skipped_exists")
            record["sha256"] = _sha256(body)
            return body.decode("utf-8", "replace"), record
        try:
            resp = self._get(endpoint.url)
        except PermissionError:
            return None, self._record(endpoint, status="robots_blocked")
        except Exception as exc:  # noqa: BLE001 — SSRF reject / network / retries exhausted
            record = self._record(endpoint, status="fetch_error")
            record["error"] = str(exc)[:500]
            return None, record
        status_code = getattr(resp, "status_code", 200)
        if status_code >= 400:
            record = self._record(endpoint, status="fetch_error")
            record["http_status"] = status_code
            return None, record
        text = getattr(resp, "text", "") or ""
        body = text.encode("utf-8")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        record = self._record(endpoint, status="downloaded")
        record["sha256"] = _sha256(body)
        self._apply_headers(record, resp)
        self._nap()
        return text, record

    def _download_pdf(self, endpoint: GmbEndpoint) -> dict:
        dest = self.out_dir / endpoint.source_name / endpoint.filename
        if dest.exists() and dest.stat().st_size > 0:
            record = self._record(endpoint, status="skipped_exists")
            record["sha256"] = _sha256(dest.read_bytes())
            return record
        try:
            resp = self._get(endpoint.url)
        except PermissionError:
            return self._record(endpoint, status="robots_blocked")
        except Exception as exc:  # noqa: BLE001
            record = self._record(endpoint, status="fetch_error")
            record["error"] = str(exc)[:500]
            return record
        status_code = getattr(resp, "status_code", 200)
        if status_code >= 400:
            record = self._record(endpoint, status="fetch_error")
            record["http_status"] = status_code
            return record
        body = _body_bytes(resp)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        record = self._record(endpoint, status="downloaded")
        record["sha256"] = _sha256(body)
        self._apply_headers(record, resp)
        self._nap()
        return record

    def _write_derived_csv(
        self, source: GmbSource, content_id: str, page_url: str, csv_bytes: bytes
    ) -> dict:
        endpoint = GmbEndpoint(
            source_name=source.name,
            document_class="traffic-table",
            filename=f"{source.name}_contentid{content_id}_traffic.csv",
            url=page_url,
            media_type="text/csv",
            note=f"derived from HTML tables on {page_url}",
            discovered_on=page_url,
        )
        dest = self.out_dir / endpoint.source_name / endpoint.filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(csv_bytes)
        record = self._record(endpoint, status="derived")
        record["sha256"] = _sha256(csv_bytes)
        return record

    # --- orchestration ---

    def _page_endpoint(self, source: GmbSource, content_id: str, page_url: str) -> GmbEndpoint:
        return GmbEndpoint(
            source_name=source.name,
            document_class=source.page_class,
            filename=f"{source.name}_contentid{content_id}.html",
            url=page_url,
            media_type="text/html",
            discovered_on=page_url,
        )

    def probe_source(self, source: GmbSource, *, dry_run: bool) -> list[dict]:
        records: list[dict] = []
        for content_id in source.content_ids:
            page_url = self.index_url(content_id)
            page_ep = self._page_endpoint(source, content_id, page_url)

            if dry_run:
                records.append(self._record(page_ep, status="dry_run"))
                if source.kind == "traffic":
                    csv_ep = GmbEndpoint(
                        source_name=source.name,
                        document_class="traffic-table",
                        filename=f"{source.name}_contentid{content_id}_traffic.csv",
                        url=page_url,
                        media_type="text/csv",
                        note="derived from HTML tables (dry run)",
                        discovered_on=page_url,
                    )
                    records.append(self._record(csv_ep, status="dry_run"))
                continue

            html, page_record = self._download_page(page_ep)
            records.append(page_record)
            if html is None:
                continue

            if source.kind == "traffic":
                rows = parse_traffic_tables(html, source_url=page_url)
                records.append(
                    self._write_derived_csv(source, content_id, page_url, _rows_to_csv(rows))
                )
            else:
                for endpoint in discover_pdf_links(html, base_url=page_url, source=source):
                    records.append(self._download_pdf(endpoint))
        return records

    def probe_sources(self, sources: list[str], *, dry_run: bool = False) -> list[dict]:
        records: list[dict] = []
        for source in self.selected_sources(sources):
            records.extend(self.probe_source(source, dry_run=dry_run))
        if not dry_run:
            for record in records:
                self.append_manifest(record)
        return records
