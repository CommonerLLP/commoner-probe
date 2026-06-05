# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Iterator

if TYPE_CHECKING:
    from .members import MPRoster
from urllib.parse import urlencode

from .base import BaseProbe, now, safe_filename_segment
from .topics import TopicProfile

LS_API_BASE = "https://elibrary.sansad.in/server/api"
RS_API_SEARCH = "https://rsdoc.nic.in/Question/Search_Questions"
LS_CATEGORY_QA = "Part 1(Questions And Answers)"

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "commoner-probe/0.3.0 (+https://github.com/CommonerLLP/commoner-probe; public-interest research; rate-limited)",
}
RS_HEADERS = {
    **HEADERS,
    "Origin": "https://sansad.in",
    "Referer": "https://sansad.in/",
}




def stable_key(house: str, qtype: str | None, qno: str | None, date: str | None) -> str:
    h = "LS" if house.lower().startswith("lok") else "RS"
    qt = (qtype or "U").strip().upper()[:1] or "U"
    qn = str(qno or "X").strip().split(".")[0]
    return f"{h}|{qt}|{qn}|{(date or '')[:10]}"


def date_in_range(value: str | None, from_date: str | None, to_date: str | None) -> bool:
    if not value:
        return True
    d = value[:10]
    return not ((from_date and d < from_date) or (to_date and d > to_date))


def md_value(metadata: dict, key: str, default: str = "") -> str:
    arr = metadata.get(key) or []
    if arr and isinstance(arr, list) and isinstance(arr[0], dict):
        return arr[0].get("value", default)
    return default


def md_values(metadata: dict, key: str) -> list[str]:
    arr = metadata.get(key) or []
    return [v.get("value", "") for v in arr if isinstance(v, dict) and v.get("value")]


def rs_date_iso(value: str | None) -> str:
    if not value:
        return ""
    try:
        return datetime.strptime(value.strip()[:10], "%d.%m.%Y").strftime("%Y-%m-%d")
    except ValueError:
        return value[:10]


class SansadProbe(BaseProbe):
    def __init__(
        self,
        topic: TopicProfile,
        out_dir: Path,
        *,
        sleep: float = 0.25,
        topic_path: Path | str | None = None,
        resolver=None,
    ):
        super().__init__(
            topic,
            out_dir,
            sleep=sleep,
            topic_path=topic_path,
            resolver=resolver,
        )
        self._roster: MPRoster | None = None

    @property
    def roster(self):
        """Lazy-loaded MP roster for enrichment."""
        if self._roster is None:
            from .members import MPRoster

            self._roster = MPRoster(self.session)
            self.log("Fetching MP rosters (LS + RS)...")
            try:
                self._roster.load_ls()
                self._roster.load_rs()
            except Exception as e:
                self.log(f"Warning: Failed to load MP rosters: {e}")
        return self._roster

    def _enrich_askers(self, rec: dict) -> None:
        """Add party/house details (v0.4.0) and stable entity_ids (v0.5.0).

        ``asker_details`` carries party/party_name/house from the in-memory
        MPRoster — backwards-compatible with v0.4.0 consumers.
        ``asker_entity_ids`` is the v0.5.0 schema commitment: a parallel list
        same length as ``askers``, with stable entity_ids when the resolver
        could match confidently and ``None`` otherwise. Always present on
        every QA record so consumers can rely on its shape regardless of
        whether ``--with-entities`` was used.
        """
        askers = rec.get("askers") or []
        details = []
        for name in askers:
            info = self.roster.lookup(name)
            if info:
                details.append(
                    {
                        "name": info.name,
                        "party": info.party,
                        "party_name": info.party_name,
                        "house": info.house,
                    }
                )
            else:
                details.append({"name": name, "party": None})
        rec["asker_details"] = details
        # v0.5.0 schema: parallel entity_id list, plus null responder fields
        # reserved for the Phase 1 (answer-text extraction) populator.
        rec["asker_entity_ids"] = self.resolve_askers(askers)
        rec.setdefault("responder_entity_id", None)
        rec.setdefault("responder_role_at_event", None)

    def ls_search_page(self, query: str, ministry: str, page: int, size: int = 100) -> dict:
        params = [
            ("query", query),
            ("dsoType", "item"),
            ("page", str(page)),
            ("size", str(size)),
            ("f.ministry", f"{ministry},equals"),
            ("f.category", f"{LS_CATEGORY_QA},equals"),
        ]
        url = f"{LS_API_BASE}/discover/search/objects?" + urlencode(params)
        r = self.session.get(url, headers=HEADERS, timeout=45)
        r.raise_for_status()
        return r.json()

    def ls_search_all(self, query: str, ministry: str, limit: int | None) -> Iterator[dict]:
        page = 0
        yielded = 0
        while True:
            data = self.ls_search_page(query, ministry, page=page)
            result = data.get("_embedded", {}).get("searchResult", {})
            objects = result.get("_embedded", {}).get("objects", [])
            if not objects:
                return
            for obj in objects:
                item = obj.get("_embedded", {}).get("indexableObject")
                if not item:
                    continue
                yield item
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
            meta = result.get("page", {})
            if page + 1 >= meta.get("totalPages", 0):
                return
            page += 1
            time.sleep(self.sleep)

    def ls_pdf_url(self, item_uuid: str) -> str | None:
        r = self.session.get(f"{LS_API_BASE}/core/items/{item_uuid}/bundles", headers=HEADERS, timeout=30)
        if r.status_code != 200:
            return None
        bundles = r.json().get("_embedded", {}).get("bundles", [])
        original = next((b for b in bundles if b.get("name") == "ORIGINAL"), None)
        if not original:
            return None
        bitstreams_url = original.get("_links", {}).get("bitstreams", {}).get("href")
        if not bitstreams_url:
            return None
        r2 = self.session.get(bitstreams_url, headers=HEADERS, timeout=30)
        if r2.status_code != 200:
            return None
        bitstreams = r2.json().get("_embedded", {}).get("bitstreams", [])
        pdf = next((b for b in bitstreams if (b.get("name") or "").lower().endswith(".pdf")), None)
        return pdf.get("_links", {}).get("content", {}).get("href") if pdf else None

    def write_pdf(self, url: str, path: Path, headers: dict) -> bool:
        if path.exists() and path.stat().st_size > 1000:
            return True
        path.parent.mkdir(parents=True, exist_ok=True)
        r = self.session.get(url, headers=headers, timeout=120, stream=True)
        if r.status_code != 200:
            return False
        with path.open("wb") as f:
            for chunk in r.iter_content(chunk_size=16384):
                f.write(chunk)
        return path.exists() and path.stat().st_size > 1000

    def probe_ls(
        self,
        seen: set[str],
        *,
        from_date: str | None,
        to_date: str | None,
        qtype_filter: str | None,
        limit: int | None,
        max_buckets: int | None,
        max_records: int | None,
        download: bool,
    ) -> int:
        run_id = self.runlog.start(
            kind="qa",
            scope={
                "house": "ls",
                "from_date": from_date,
                "to_date": to_date,
                "limit": limit,
                "max_buckets": max_buckets,
                "max_records": max_records,
                "download": download,
            },
            topic_name=self.topic.name,
            topic_path=self.topic_path,
            classifier_config=self.topic.classifier_config,
        )
        added = 0
        for group, query in self.topic.searches(max_buckets):
            for ministry in self.topic.lok_sabha_ministries:
                self.log(f"LS query={query!r} ministry={ministry}")
                # Per-bucket counters for the audit trail. Surfaced 2026-05-08:
                # empty-result crawls were undebuggable from _runs.jsonl alone.
                bkt_t0 = time.monotonic()
                bkt_raw = bkt_after_date = bkt_kept = bkt_skipped_seen = bkt_no_match = 0
                bkt_error: str | None = None
                try:
                    for item in self.ls_search_all(query, ministry, limit):
                        bkt_raw += 1
                        uuid = item.get("uuid")
                        md = item.get("metadata", {})
                        date = md_value(md, "dc.date.issued")
                        qtype = md_value(md, "dc.identifier.questiontype")
                        qno = md_value(md, "dc.identifier.questionnumber")
                        if qtype_filter and (qtype or "").strip().lower() != qtype_filter:
                            continue
                        key = stable_key("Lok Sabha", qtype, qno, date)
                        if not date_in_range(date, from_date, to_date):
                            continue
                        bkt_after_date += 1
                        if not uuid:
                            continue
                        if key in seen:
                            bkt_skipped_seen += 1
                            continue
                        title = md_value(md, "dc.title")
                        if self.topic.filter_fn is not None and not self.topic.filter_fn(title, query):
                            bkt_no_match += 1
                            continue
                        rec = {
                            "key": key,
                            "run_id": run_id,
                            "kind": "qa",
                            "house": "Lok Sabha",
                            "uuid": uuid,
                            "handle": item.get("handle"),
                            "title": title,
                            "date": date,
                            "qtype": qtype,
                            "qno": qno,
                            "session": md_value(md, "dc.identifier.sessionnumber"),
                            "loksabhanumber": md_value(md, "dc.identifier.loksabhanumber"),
                            "ministry": md_value(md, "dc.relation.ministry") or ministry,
                            "askers": md_values(md, "dc.contributor.members"),
                            "uri": md_value(md, "dc.identifier.uri"),
                            "source": "elibrary.sansad.in",
                            "found_via_group": group,
                            "found_via_query": query,
                            "probed_at": now(),
                        }
                        if download:
                            pdf_url = self.ls_pdf_url(uuid)
                            if pdf_url:
                                qtype_seg = safe_filename_segment((qtype or "U").upper()[:1])
                                qno_seg = safe_filename_segment(qno or "X")
                                uuid_seg = safe_filename_segment(uuid[:8].replace("-", ""))
                                fname = f"{qtype_seg}{qno_seg}_{uuid_seg}.pdf"
                                pdf_path = self.pdf_dir / "ls" / fname
                                if self.write_pdf(pdf_url, pdf_path, HEADERS):
                                    rec["pdf_url"] = pdf_url
                                    rec["pdf_path"] = str(pdf_path.relative_to(self.out_dir))
                        rec.setdefault("language_classified", ["en"])
                        self._enrich_askers(rec)
                        self.append(rec)
                        seen.add(key)
                        added += 1
                        bkt_kept += 1
                        if max_records is not None and added >= max_records:
                            self.runlog.record_bucket(
                                kind="ls_qa", group=group, query=query, ministry=ministry,
                                raw_returned=bkt_raw, after_date_filter=bkt_after_date,
                                no_match=bkt_no_match, kept=bkt_kept, skipped_seen=bkt_skipped_seen,
                                elapsed_ms=round((time.monotonic() - bkt_t0) * 1000, 1),
                                error=None,
                            )
                            self.runlog.finish(added=added)
                            return added
                        time.sleep(self.sleep)
                except Exception as exc:  # noqa: BLE001
                    bkt_error = f"{type(exc).__name__}: {exc}"
                    self.log(f"LS failed query={query!r} ministry={ministry}: {exc}")
                    self.runlog.record_error(where=f"ls/{ministry}/{query}", exc=exc)
                finally:
                    self.runlog.record_bucket(
                        kind="ls_qa", group=group, query=query, ministry=ministry,
                        raw_returned=bkt_raw, after_date_filter=bkt_after_date,
                        no_match=bkt_no_match, kept=bkt_kept, skipped_seen=bkt_skipped_seen,
                        elapsed_ms=round((time.monotonic() - bkt_t0) * 1000, 1),
                        error=bkt_error,
                    )
        self.runlog.finish(added=added)
        return added

    def rs_search_session(self, ses_no: int, ministry_like: str) -> list[dict]:
        where = f"ses_no={ses_no} and min_name like '{ministry_like}%'"
        r = self.session.get(RS_API_SEARCH, params={"whereclause": where}, headers=RS_HEADERS, timeout=60)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):
            return data.get("data", []) or []
        return data if isinstance(data, list) else []

    def probe_rs(
        self,
        seen: set[str],
        *,
        sessions: Iterable[int],
        from_date: str | None,
        to_date: str | None,
        qtype_filter: str | None,
        limit: int | None,
        max_buckets: int | None,
        max_records: int | None,
        download: bool,
    ) -> int:
        sessions_list = list(sessions)
        run_id = self.runlog.start(
            kind="qa",
            scope={
                "house": "rs",
                "sessions": sessions_list,
                "from_date": from_date,
                "to_date": to_date,
                "limit": limit,
                "max_buckets": max_buckets,
                "max_records": max_records,
                "download": download,
            },
            topic_name=self.topic.name,
            topic_path=self.topic_path,
            classifier_config=self.topic.classifier_config,
        )
        self.log("RS: keeping all ministry-matched rows (no in-crawler classification).")
        added = 0
        ministries = self.topic.rajya_sabha_ministry_likes
        if max_buckets is not None:
            ministries = ministries[:max_buckets]
        for ses_no in sessions_list:
            for ministry in ministries:
                self.log(f"RS session={ses_no} ministry_like={ministry}%")
                # Per-bucket counters (audit trail).
                bkt_t0 = time.monotonic()
                bkt_raw = bkt_after_date = bkt_kept = bkt_skipped_seen = bkt_no_match = 0
                bkt_error: str | None = None
                try:
                    records = self.rs_search_session(ses_no, ministry)
                except Exception as exc:  # noqa: BLE001
                    bkt_error = f"{type(exc).__name__}: {exc}"
                    self.log(f"RS failed session={ses_no} ministry={ministry}: {exc}")
                    self.runlog.record_error(where=f"rs/{ses_no}/{ministry}", exc=exc)
                    self.runlog.record_bucket(
                        kind="rs_qa", session=ses_no, ministry=ministry,
                        raw_returned=0, after_date_filter=0, no_match=0,
                        kept=0, skipped_seen=0,  # exception path: counters not yet populated
                        elapsed_ms=round((time.monotonic() - bkt_t0) * 1000, 1),
                        error=bkt_error,
                    )
                    continue
                kept_for_bucket = 0
                for row in records:
                    bkt_raw += 1
                    date = rs_date_iso(row.get("ans_date"))
                    qtype = (row.get("qtype") or "").strip()
                    if qtype_filter and qtype.lower() != qtype_filter:
                        continue
                    qno = str(row.get("qno") or "").split(".")[0]
                    key = stable_key("Rajya Sabha", qtype, qno, date)
                    if not date_in_range(date, from_date, to_date):
                        continue
                    bkt_after_date += 1
                    if key in seen:
                        bkt_skipped_seen += 1
                        continue
                    title = (row.get("qtitle") or "").strip()
                    if self.topic.filter_fn is not None and not self.topic.filter_fn(title, ministry):
                        bkt_no_match += 1
                        continue
                    rec = {
                        "key": key,
                        "run_id": run_id,
                        "kind": "qa",
                        "house": "Rajya Sabha",
                        "qslno": row.get("qslno"),
                        "ses_no": row.get("ses_no"),
                        "title": title,
                        "date": date,
                        "qtype": qtype,
                        "qno": qno,
                        "ministry": (row.get("min_name") or "").strip(),
                        "askers": [row.get("name")] if row.get("name") else [],
                        "question_text": row.get("qn_text"),
                        "answer_text": row.get("ans_text"),
                        "pdf_url": row.get("files"),
                        "pdf_url_hindi": row.get("hindifiles"),
                        "source": "rsdoc.nic.in",
                        "found_via_query": ministry,
                        "status": (row.get("status") or "").strip(),
                        "probed_at": now(),
                    }
                    if download and rec.get("pdf_url"):
                        qtype_seg = safe_filename_segment((qtype or "U").upper()[:1])
                        qno_seg = safe_filename_segment(qno or "X")
                        qslno_seg = safe_filename_segment(rec.get("qslno"))
                        fname = f"{qtype_seg}{qno_seg}_{qslno_seg}.pdf"
                        pdf_path = self.pdf_dir / "rs" / fname
                        if self.write_pdf(rec["pdf_url"], pdf_path, RS_HEADERS):
                            rec["pdf_path"] = str(pdf_path.relative_to(self.out_dir))
                    rec.setdefault("language_classified", ["en"])
                    self._enrich_askers(rec)
                    self.append(rec)
                    seen.add(key)
                    added += 1
                    kept_for_bucket += 1
                    bkt_kept += 1
                    if max_records is not None and added >= max_records:
                        self.runlog.record_bucket(
                            kind="rs_qa", session=ses_no, ministry=ministry,
                            raw_returned=bkt_raw, after_date_filter=bkt_after_date,
                            no_match=bkt_no_match, kept=bkt_kept,
                            skipped_seen=bkt_skipped_seen,
                            elapsed_ms=round((time.monotonic() - bkt_t0) * 1000, 1),
                            error=None,
                        )
                        self.runlog.finish(added=added)
                        return added
                    if limit is not None and kept_for_bucket >= limit:
                        break
                    time.sleep(self.sleep)
                self.runlog.record_bucket(
                    kind="rs_qa", session=ses_no, ministry=ministry,
                    raw_returned=bkt_raw, after_date_filter=bkt_after_date,
                    no_match=0, kept=bkt_kept,
                    skipped_seen=bkt_skipped_seen,
                    elapsed_ms=round((time.monotonic() - bkt_t0) * 1000, 1),
                    error=None,
                )
        self.runlog.finish(added=added)
        return added
