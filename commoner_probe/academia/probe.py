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

    def fetch_html(self, url: str) -> str:
        r = self.session.get(url, timeout=45)
        r.raise_for_status()
        return r.text

    def _fetcher(self, enabled: bool) -> Fetcher | None:
        """A Fetcher (PDF download + per-position HTML sub-page helper), or None
        when download is disabled (parsers then degrade to listing-page output)."""
        if not enabled:
            return None
        return Fetcher(self.session, self.pdf_dir, self.out_dir)

    def _record(self, ad: dict, inst: dict, *, status: str = "ok") -> dict:
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
        )
        if error:
            rec["error"] = error[:500]
        return rec

    # --- crawl ---

    def probe_institution(self, inst: dict, *, pdf, dry_run: bool) -> list[dict]:
        url = inst.get("career_page_url_guess")
        if not url:
            return [self._status_record(inst, status="no_url")]
        if dry_run:
            return [self._status_record(inst, status="dry_run", original_url=url)]
        try:
            html = self.fetch_html(url)
        except Exception as exc:  # noqa: BLE001 — record, don't abort the whole crawl
            return [self._status_record(inst, status="fetch_error", original_url=url, error=str(exc))]
        fetched_at = datetime.now(timezone.utc)
        parser = get_parser(inst.get("parser"))
        try:
            ads = parser(html, url, fetched_at, pdf)
        except Exception as exc:  # noqa: BLE001
            return [self._status_record(inst, status="parse_error", original_url=url, error=str(exc))]
        if not ads:
            return [self._status_record(inst, status="no_ads", original_url=url)]
        return [self._record(ad, inst, status="ok") for ad in ads]

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
