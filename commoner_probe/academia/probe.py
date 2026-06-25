# SPDX-License-Identifier: MIT
"""Acquire Indian HEI faculty-recruitment ads with provenance.

Topic-less probe (sibling of the dmft/mca sources) whose "endpoint table" is the
institution registry: for each institution it fetches the career page, dispatches
to a per-institution parser (registry ``parser`` field; falls back to ``generic``),
and appends one ``academic_job_posting`` record per extracted ad to
``manifest.jsonl``. Provenance, fetch/parse failures, and the empty-result case
are all recorded so coverage gaps are visible rather than silent.

Migrated from academiaindia. Reuses probe's ``make_session`` (SSRF guard /
robots / rate-limit) instead of the origin's duplicated ``fetch.py`` /
``url_safety.py``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from commoner_probe.http_client import make_session

from .parsers import get_parser
from .pdf_text import Fetcher
from .registry import load_registry, select_institutions


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class AcademicJobsProbe:
    """Crawl HEI career pages into academic_job_posting manifest records."""

    def __init__(
        self,
        out_dir: Path,
        *,
        sleep: float = 1.0,
        institutions: list[str] | None = None,
        registry_path: str | Path | None = None,
    ) -> None:
        self.out_dir = out_dir
        self.sleep = sleep
        self.manifest = out_dir / "manifest.jsonl"
        self.pdf_dir = out_dir / "pdfs"
        self._institutions_filter = set(institutions or [])
        self.registry = load_registry(registry_path)
        self.session = make_session()

    # --- helpers ---

    def selected_institutions(self) -> list[dict]:
        return select_institutions(self.registry, self._institutions_filter)

    def load_seen(self) -> set[str]:
        seen: set[str] = set()
        if not self.manifest.exists():
            return seen
        with self.manifest.open(encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("key"):
                    seen.add(rec["key"])
        return seen

    def append_manifest(self, record: dict) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _fetch_listing(self, url: str, *, robots_override: bool = False) -> tuple[str, str, int | None]:
        """Fetch a listing page. Returns ``(text, status, http_status)``.

        ``status`` is one of ``ok`` / ``robots_blocked`` / ``http_error`` /
        ``fetch_error``. A 4xx that still served a substantial body (some
        Drupal-based career portals answer the listing payload alongside a 404)
        is treated as usable (``ok``).
        """
        try:
            r = self.session.get(url, timeout=45, respect_robots=not robots_override)
        except PermissionError:
            return "", "robots_blocked", None
        except Exception:  # noqa: BLE001 — SSRF reject / network / 5xx-retry-exhausted
            return "", "fetch_error", None
        text = getattr(r, "text", "") or ""
        code = getattr(r, "status_code", 200)
        if code >= 400:
            if len(text) > 2000:
                return text, "ok", code
            return "", "http_error", code
        return text, "ok", code

    def _fetcher(self, enabled: bool) -> Fetcher | None:
        """A Fetcher (PDF download + per-position HTML sub-page helper), or None
        when download is disabled (parsers then degrade to listing-page output)."""
        if not enabled:
            return None
        return Fetcher(self.session, self.pdf_dir, self.out_dir)

    def _record(
        self, ad: dict, inst: dict, *, status: str = "ok", source_method: str | None = "official scrape"
    ) -> dict:
        return {
            "key": f"ACAD|{inst['id']}|{ad['id']}",
            "kind": "academic_job_posting",
            "record_type": "academic_job_posting",
            "source_family": "academia-india",
            "institution_id": inst["id"],
            "institution_name": inst.get("name"),
            "institution_short_name": inst.get("short_name"),
            "institution_type": inst.get("type"),
            "state": inst.get("state"),
            "parser": inst.get("parser") or "generic",
            "ad_number": ad.get("ad_number"),
            "title": ad.get("title") or "(untitled)",
            "department": ad.get("department"),
            "discipline": ad.get("discipline"),
            "post_type": ad.get("post_type") or "Unknown",
            "contract_status": ad.get("contract_status") or "Unknown",
            "category_breakdown": ad.get("category_breakdown"),
            "number_of_posts": ad.get("number_of_posts"),
            "pay_scale": ad.get("pay_scale"),
            "publication_date": ad.get("publication_date"),
            "closing_date": ad.get("closing_date"),
            "original_url": ad.get("original_url") or "",
            "info_url": ad.get("info_url"),
            "apply_url": ad.get("apply_url"),
            "publications_required": ad.get("publications_required"),
            "unit_eligibility": ad.get("unit_eligibility"),
            "annexure_pdf_url": ad.get("annexure_pdf_url"),
            "reservation_note": ad.get("reservation_note"),
            "general_eligibility": ad.get("general_eligibility"),
            "raw_text_excerpt": ad.get("raw_text_excerpt"),
            "parse_confidence": ad.get("parse_confidence"),
            "pdf_path": ad.get("pdf_path"),
            "pdf_parsed": bool(ad.get("pdf_parsed") or ad.get("_pdf_parsed")),
            "fetch_status": status,
            "source_method": source_method,
            "snapshot_fetched_at": ad.get("snapshot_fetched_at"),
            "probed_at": _now_iso(),
        }

    def _status_record(
        self, inst: dict, *, status: str, original_url: str = "", error: str | None = None
    ) -> dict:
        """A coverage record for an institution with no ads (dry-run / error / empty)."""
        rec = self._record(
            {
                "id": f"_status_{status}",
                "title": f"({status})",
                "original_url": original_url,
                "info_url": original_url or None,
                "post_type": "Unknown",
                "contract_status": "Unknown",
                "snapshot_fetched_at": _now_iso(),
            },
            inst,
            status=status,
            source_method=None,
        )
        if error:
            rec["error"] = error[:500]
        return rec

    # --- crawl ---

    def _fallback_records(self, inst: dict, parser, pdf, fetched_at) -> list[dict] | None:
        """Parse the registry ``fallback_pdf_url`` (if any) directly. Returns ads or None.

        Keeps an institution visible when its listing page is down but a known
        rolling-ad PDF exists (e.g. IIT Madras / facapp.iitm.ac.in). The parser
        receives an empty HTML body and the PDF URL.
        """
        fb = inst.get("fallback_pdf_url")
        if not fb:
            return None
        try:
            ads = parser("", fb, fetched_at, pdf)
        except Exception:  # noqa: BLE001
            return None
        if not ads:
            return None
        return [self._record(ad, inst, status="ok", source_method="fallback PDF") for ad in ads]

    def probe_institution(self, inst: dict, *, pdf, dry_run: bool) -> list[dict]:
        url = inst.get("career_page_url_guess")
        if not url:
            return [self._status_record(inst, status="no_url")]
        if dry_run:
            return [self._status_record(inst, status="dry_run", original_url=url)]

        fetched_at = datetime.now(timezone.utc)
        parser = get_parser(inst.get("parser"))

        text, status, http_status = self._fetch_listing(url)
        source_method = "official scrape"
        # Public-interest robots override: registry-opted-in official sources
        # retry past a blanket robots disallow.
        if status == "robots_blocked" and inst.get("robots_override") is True:
            text, status, http_status = self._fetch_listing(url, robots_override=True)
            source_method = "public-interest override"

        if status == "ok" and text:
            try:
                ads = parser(text, url, fetched_at, pdf)
            except Exception as exc:  # noqa: BLE001
                fb = self._fallback_records(inst, parser, pdf, fetched_at)
                return fb if fb is not None else [
                    self._status_record(inst, status="parse_error", original_url=url, error=str(exc))
                ]
            if ads:
                return [self._record(ad, inst, status="ok", source_method=source_method) for ad in ads]
            fb = self._fallback_records(inst, parser, pdf, fetched_at)
            return fb if fb is not None else [self._status_record(inst, status="no_ads", original_url=url)]

        # Fetch failed. Try the fallback PDF unless robots blocked us without an override.
        if status != "robots_blocked" or inst.get("robots_override") is True:
            fb = self._fallback_records(inst, parser, pdf, fetched_at)
            if fb is not None:
                return fb
        if status == "robots_blocked":
            return [self._status_record(inst, status="robots_blocked", original_url=url)]
        return [self._status_record(
            inst, status="fetch_error", original_url=url,
            error=status + (f" (HTTP {http_status})" if http_status else ""),
        )]

    def probe(self, *, download: bool = False, dry_run: bool = False) -> list[dict]:
        import time

        seen = self.load_seen()
        pdf = self._fetcher(download and not dry_run)
        out: list[dict] = []
        for inst in self.selected_institutions():
            records = self.probe_institution(inst, pdf=pdf, dry_run=dry_run)
            for rec in records:
                if rec["key"] in seen:
                    continue
                seen.add(rec["key"])
                if not dry_run:
                    self.append_manifest(rec)
                out.append(rec)
            if not dry_run and self.sleep:
                time.sleep(self.sleep)
        return out
