# SPDX-License-Identifier: MIT
"""ADR/MyNeta Lok Sabha 2024 candidate affidavit acquisition (myneta.info).

myneta.info self-describes as "an open data repository platform of
Association for Democratic Reforms (ADR)" (verified live 2026-07-08);
robots.txt only disallows print-view URLs (``*print=true``), so ordinary
page fetches are unrestricted. Candidate detail pages carry a Google
Charts gauge literal (``['Cases', N]``) that is the site's own
authoritative declared-criminal-case count — more robust than counting
rows in the "Cases where Pending"/"Cases where Convicted" tables, which
also contain a placeholder "No Case" row when empty.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from .http_client import make_session

BASE_URL = "https://myneta.info/LokSabha2024/"

_CONSTITUENCY_LINK_RE = re.compile(
    r"href=index\.php\?action=show_candidates&constituency_id=(\d+)\s+"
    r"title='Date of Election ([\d-]+)'>([^<]+)</a>"
)
_CANDIDATE_LINK_RE = re.compile(r"candidate\.php\?candidate_id=(\d+)")
_TAG_RE = re.compile(r"<[^>]+>")

_NAME_RE = re.compile(r"<h2>([^<]+?)\s*(?:<font[^>]*>\(([^)]+)\)</font>)?\s*</h2>")
_PARTY_RE = re.compile(r"<b>Party:</b>\s*([^<]+)")
_AGE_RE = re.compile(r"<b>Age:</b>\s*(\d+)")
_SELF_PROFESSION_RE = re.compile(r"<b>Self Profession:</b>\s*([^<]+)")
_SPOUSE_PROFESSION_RE = re.compile(r"<b>Spouse Profession:</b>\s*([^<]+)")
_EDUCATION_RE = re.compile(r"Category:\s*([^<]+?)\s*<br>")
_ASSETS_RE = re.compile(r"Assets:\s*</td><td>\s*<b>Rs&nbsp;([\d,]+)</b>")
_LIABILITIES_RE = re.compile(r"Liabilities:\s*</td><td>\s*<b>Rs&nbsp;([\d,]+)</b>")
_CASES_GAUGE_RE = re.compile(r"\['Cases',\s*(\d+)\]")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(_TAG_RE.sub(" ", text))).strip()


def _rupees(match: re.Match | None) -> int | None:
    return int(match.group(1).replace(",", "")) if match else None


class MyNetaProbe:
    """Acquire ADR/MyNeta Lok Sabha 2024 candidate affidavit summaries."""

    def __init__(self, out_dir: Path, *, sleep: float = 1.0, base_url: str = BASE_URL) -> None:
        self.out_dir = out_dir
        self.sleep = sleep
        self.base_url = base_url
        self.manifest = out_dir / "manifest.jsonl"
        self.session = make_session(rate_limit_sec=sleep)

    def discover_constituencies(self) -> list[dict[str, Any]]:
        r = self.session.get(self.base_url, timeout=60)
        r.raise_for_status()
        return [
            {
                "constituency_id": int(m.group(1)),
                "election_date": m.group(2),
                "name": _clean(m.group(3)),
            }
            for m in _CONSTITUENCY_LINK_RE.finditer(r.text)
        ]

    def list_candidates(self, constituency_id: int) -> list[int]:
        url = urljoin(self.base_url, f"index.php?action=show_candidates&constituency_id={constituency_id}")
        r = self.session.get(url, timeout=45)
        r.raise_for_status()
        return sorted({int(m) for m in _CANDIDATE_LINK_RE.findall(r.text)})

    def fetch_candidate(self, candidate_id: int) -> dict[str, Any]:
        url = urljoin(self.base_url, f"candidate.php?candidate_id={candidate_id}")
        r = self.session.get(url, timeout=45)
        r.raise_for_status()
        html = r.text

        name_m = _NAME_RE.search(html)
        party_m = _PARTY_RE.search(html)
        age_m = _AGE_RE.search(html)
        self_prof_m = _SELF_PROFESSION_RE.search(html)
        spouse_prof_m = _SPOUSE_PROFESSION_RE.search(html)
        edu_m = _EDUCATION_RE.search(html)
        assets_m = _ASSETS_RE.search(html)
        liab_m = _LIABILITIES_RE.search(html)
        cases_m = _CASES_GAUGE_RE.search(html)

        return {
            "key": f"MYNETA|LS2024|{candidate_id}",
            "kind": "myneta_candidate",
            "record_type": "myneta_candidate",
            "source": "myneta.info",
            "election": "LokSabha2024",
            "candidate_id": candidate_id,
            "name": _clean(name_m.group(1)) if name_m else None,
            "winner_status": name_m.group(2) if name_m and name_m.group(2) else None,
            "party": _clean(party_m.group(1)) if party_m else None,
            "age": int(age_m.group(1)) if age_m else None,
            "self_profession": _clean(self_prof_m.group(1)) if self_prof_m else None,
            "spouse_profession": _clean(spouse_prof_m.group(1)) if spouse_prof_m else None,
            "education_category": _clean(edu_m.group(1)) if edu_m else None,
            "assets_rupees": _rupees(assets_m),
            "liabilities_rupees": _rupees(liab_m),
            "criminal_cases_declared": int(cases_m.group(1)) if cases_m else None,
            "source_url": url,
            "probed_at": _now(),
        }

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

    def append_manifest(self, record: dict[str, Any]) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def probe(
        self,
        *,
        constituency_ids: list[int] | None = None,
        max_records: int | None = None,
        dry_run: bool = False,
    ) -> list[dict[str, Any]]:
        constituencies = self.discover_constituencies()
        if constituency_ids:
            wanted = set(constituency_ids)
            constituencies = [c for c in constituencies if c["constituency_id"] in wanted]

        seen = self.load_seen()
        records: list[dict[str, Any]] = []
        for con in constituencies:
            candidate_ids = self.list_candidates(con["constituency_id"])
            if self.sleep:
                time.sleep(self.sleep)
            for candidate_id in candidate_ids:
                key = f"MYNETA|LS2024|{candidate_id}"
                if key in seen:
                    continue
                if dry_run:
                    records.append({
                        "key": key,
                        "candidate_id": candidate_id,
                        "constituency_id": con["constituency_id"],
                        "constituency_name": con["name"],
                        "status": "dry_run",
                    })
                    continue
                record = self.fetch_candidate(candidate_id)
                record["constituency_id"] = con["constituency_id"]
                record["constituency_name"] = con["name"]
                self.append_manifest(record)
                records.append(record)
                seen.add(key)
                if self.sleep:
                    time.sleep(self.sleep)
                if max_records is not None and len(records) >= max_records:
                    return records
        return records
