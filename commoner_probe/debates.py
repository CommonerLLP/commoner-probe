# SPDX-License-Identifier: MIT
"""Floor-debate probe (Lok Sabha "text of debate").

Roadmap source (README "Upcoming"): the richest longitudinal record of what MPs
say on the floor — one record per speaker turn (business type, member, verbatim
text).

============================  PROVISIONAL CONTRACT  ===========================
The live sansad.in debate API contract is NOT yet confirmed. The README names
``api_ls/debate/text-of-debate``, but that path returns HTTP 400 for every
query-parameter combination tried during black-box probing (the gateway emits a
canned "404 page not found" body), and the real request parameters + JSON field
names could not be recovered without capturing an actual browser request.

This module is therefore deliberately defensive and is wired end-to-end EXCEPT
for the live contract:

* ``LS_DEBATE_API`` is a best-guess default, overridable via the constructor /
  ``--api-url`` so it can be corrected without code changes.
* Pagination assumes the committee API's ``{"records": [...],
  "_metadata": {"totalPages": N}}`` envelope (the only sansad.in shape verified
  in this codebase).
* Field extraction tries several plausible key names per field and tolerates
  misses (everything but the structural core is nullable in the schema).
* The dedup ``key`` hashes the raw record, so it is stable across re-runs
  regardless of which fields turn out to be canonical.

TO FINALISE (see bead sansad-crawler-5ht): capture one real response from the
debates page Network tab, then (1) set ``LS_DEBATE_API`` + page params in
``debate_page``, (2) map the real field names in ``_record``, (3) tighten the
schema ``required`` list, (4) replace the FakeSession fixture in
tests/test_debates.py with the captured shape.
==============================================================================
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Iterator
from urllib.parse import urlencode

from .base import BaseProbe, now, safe_filename_segment
from .committees import parse_ls_date
from .sansad import date_in_range
from .topics import TopicProfile

# PROVISIONAL — unverified. See module docstring / bead sansad-crawler-5ht.
LS_DEBATE_API = "https://sansad.in/api_ls/debate/text-of-debate"
DEFAULT_LOK_SABHA = 18

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "commoner-probe/0.4.1 (github.com/CommonerLLP/commoner-probe; public-interest research; rate-limited)",
    "Referer": "https://sansad.in/ls",
}


def _first(raw: dict, *keys: str) -> object | None:
    """Return the first present, non-empty value among ``keys`` (provisional
    field-name tolerance — the canonical names are unconfirmed)."""
    for k in keys:
        v = raw.get(k)
        if v not in (None, ""):
            return v
    return None


def debate_key(ls_no: int, date: str, raw: dict) -> str:
    """Stable dedup key. Hashes the raw record so it survives field-name churn
    while the live contract is unconfirmed.
    """
    digest = hashlib.sha1(
        json.dumps(raw, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:12]
    return f"DEBATE|{ls_no}|{date or 'NA'}|{digest}"


class DebateProbe(BaseProbe):
    """Probes Lok Sabha floor debates. Sibling of CommitteeProbe.

    Topic-based: the topic profile travels with the run for provenance, and
    ``topic.filter_fn`` (if set) filters records by (title, text). Server-side
    search filtering is deferred until the live API params are known.
    """

    def __init__(
        self,
        topic: TopicProfile,
        out_dir: Path,
        *,
        sleep: float = 0.25,
        lok_sabha_no: int = DEFAULT_LOK_SABHA,
        topic_path: Path | str | None = None,
        api_url: str = LS_DEBATE_API,
    ) -> None:
        super().__init__(topic, out_dir, sleep=sleep, topic_path=topic_path)
        self.lok_sabha_no = lok_sabha_no
        self.api_url = api_url

    def debate_page(self, ls_no: int, page: int, size: int = 200) -> dict:
        # PROVISIONAL param names — adjust once the live contract is captured.
        params = {"loksabhaNo": ls_no, "page": page, "size": size}
        url = f"{self.api_url}?{urlencode(params)}"
        r = self.session.get(url, headers=HEADERS, timeout=45)
        r.raise_for_status()
        return r.json()

    def debate_all(self, ls_no: int) -> Iterator[dict]:
        page = 1
        while True:
            data = self.debate_page(ls_no, page)
            records = data.get("records") or []
            if not records:
                return
            yield from records
            meta = data.get("_metadata") or {}
            total_pages = int(meta.get("totalPages") or 0)
            if page >= total_pages:
                return
            page += 1
            time.sleep(self.sleep)

    def _record(self, raw: dict, *, ls_no: int, run_id: str) -> dict:
        raw_date = _first(raw, "debateDate", "date", "sittingDate", "dateOfDebate")
        date = parse_ls_date(raw_date) if isinstance(raw_date, str) else ""
        verbatim = _first(raw, "debateText", "verbatimText", "text", "speech")
        title = _first(raw, "debateTitle", "title", "subject", "businessTitle")
        return {
            "key": debate_key(ls_no, date, raw),
            "run_id": run_id,
            "kind": "floor_debate",
            "house": "Lok Sabha",
            "ls_no": ls_no,
            "date": date or None,
            "business_type": _first(raw, "businessType", "typeOfBusiness", "business"),
            "member_name": _first(raw, "memberName", "member", "speaker", "name"),
            "member_party": _first(raw, "party", "partyName"),
            "constituency": _first(raw, "constituency", "constituencyName"),
            "debate_title": title,
            "verbatim_text": verbatim,
            "language_classified": ["en"],
            "source": "sansad.in/api_ls/debate",
            "pdf_url": _first(raw, "url", "pdfUrl", "fileUrl"),
            "pdf_path": None,
            "probed_at": now(),
        }

    def probe(
        self,
        seen: set[str],
        *,
        ls_no: int | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        max_records: int | None = None,
        download: bool = False,
    ) -> int:
        ls_no = ls_no if ls_no is not None else self.lok_sabha_no
        run_id = self.runlog.start(
            kind="floor_debate",
            scope={
                "house": "ls",
                "ls_no": ls_no,
                "from_date": from_date,
                "to_date": to_date,
                "max_records": max_records,
                "download": download,
                "api_url": self.api_url,
            },
            topic_name=self.topic.name,
            topic_path=self.topic_path,
        )
        added = 0
        self.log(f"LS debates ls={ls_no} run={run_id[:8]} (PROVISIONAL contract)")
        try:
            for raw in self.debate_all(ls_no):
                rec = self._record(raw, ls_no=ls_no, run_id=run_id)
                if rec["key"] in seen or not date_in_range(rec["date"] or "", from_date, to_date):
                    continue
                # Optional thematic filter via the topic profile.
                if self.topic.filter_fn and not self.topic.filter_fn(
                    rec.get("debate_title") or "", rec.get("verbatim_text") or ""
                ):
                    continue
                if download and rec.get("pdf_url"):
                    fname = f"{safe_filename_segment(ls_no)}_{safe_filename_segment(rec['key'].rsplit('|', 1)[-1])}.pdf"
                    pdf_path = self.pdf_dir / "debates" / fname
                    if self.write_pdf(rec["pdf_url"], pdf_path, HEADERS):
                        rec["pdf_path"] = str(pdf_path.relative_to(self.out_dir))
                self.append(rec)
                seen.add(rec["key"])
                added += 1
                if max_records is not None and added >= max_records:
                    break
                time.sleep(self.sleep)
        except Exception as exc:  # noqa: BLE001 — provisional endpoint may 4xx; record + finish cleanly
            self.log(f"LS debates failed (likely unverified endpoint): {exc}")
            self.runlog.record_error(where="debates/ls", exc=exc)
        self.runlog.finish(added=added)
        return added
