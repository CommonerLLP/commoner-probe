# SPDX-License-Identifier: MIT
from __future__ import annotations

import hashlib
import json
import re
import socket
import time
from datetime import datetime, timedelta
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
    "User-Agent": "commoner-probe/0.5.0 (+https://github.com/CommonerLLP/commoner-probe; public-interest research; rate-limited)",
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


def month_windows(from_date: str, to_date: str) -> list[tuple[str, str]]:
    """Split an ISO date range into calendar-month-clipped (start, end) windows."""
    start = datetime.strptime(from_date[:10], "%Y-%m-%d").date()
    end = datetime.strptime(to_date[:10], "%Y-%m-%d").date()
    windows: list[tuple[str, str]] = []
    cur = start
    while cur <= end:
        if cur.month == 12:
            nxt = cur.replace(year=cur.year + 1, month=1, day=1)
        else:
            nxt = cur.replace(month=cur.month + 1, day=1)
        windows.append((cur.isoformat(), min(end, nxt - timedelta(days=1)).isoformat()))
        cur = nxt
    return windows


def geo_fence_hint(exc: Exception, host: str = "elibrary.sansad.in") -> str | None:
    """Return a human-readable geo-fence explanation for *exc*, or None.

    sansad.in / elibrary.sansad.in are geo-fenced at the DNS level: from
    non-India egress the hostnames return NXDOMAIN. That surfaces either as
    a resolution error, or — because ``url_safety.is_safe_url`` also calls
    ``getaddrinfo`` — as a misleading "rejected by SSRF guard" ValueError.
    """
    text = f"{type(exc).__name__}: {exc}".lower()
    dns_markers = (
        "getaddrinfo",
        "name or service not known",
        "nodename nor servname",
        "nameresolution",
        "temporary failure in name resolution",
    )
    hint = (
        f"{host} unreachable ({type(exc).__name__}: {exc}). "
        "The Parliament Digital Library is geo-fenced to India at the DNS "
        "level; run this command from an India-egress host."
    )
    if any(m in text for m in dns_markers):
        return hint
    if "ssrf guard" in text:
        try:
            socket.getaddrinfo(host, None)
        except socket.gaierror:
            return hint
    return None


class SansadProbe(BaseProbe):
    def __init__(
        self,
        topic: TopicProfile | None,
        out_dir: Path,
        *,
        sleep: float = 0.25,
        topic_path: Path | str | None = None,
        resolver=None,
        member_name: str | None = None,
        enumerate_all: bool = False,
    ):
        # Allow missing topic if member_name is given
        if topic is None:
            # We mock a minimal topic profile for the BaseProbe init
            from .topics import TopicProfile
            name = "full-enumeration" if enumerate_all else "member-driven"
            topic = TopicProfile(name=name, description="", search_groups=[], lok_sabha_ministries=[], rajya_sabha_ministry_likes=[])
        super().__init__(
            topic,
            out_dir,
            sleep=sleep,
            topic_path=topic_path,
            resolver=resolver,
        )
        self.member_name = member_name
        self.enumerate_all = enumerate_all
        self.windows_path = out_dir / "_windows.jsonl"
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
        entity_ids = []
        for name in askers:
            info = self.roster.lookup(name)
            ctx = None
            if info:
                ctx = {
                    "name": info.name,
                    "party": info.party,
                    "party_name": info.party_name,
                    "house": info.house,
                    "state": info.state,
                }
                details.append(ctx)
            else:
                details.append({"name": name, "party": None})

            if self.resolver:
                result = self.resolver.resolve(name, context=ctx, kind_hint="mp")
                entity_ids.append(result.entity_id if result.status == "resolved" else None)
            else:
                entity_ids.append(None)

        rec["asker_details"] = details
        rec["asker_entity_ids"] = entity_ids
        rec.setdefault("responder_entity_id", None)
        rec.setdefault("responder_role_at_event", None)

    def ls_search_page(
        self,
        query: str,
        ministry: str,
        page: int,
        size: int = 100,
        date_range: tuple[str, str] | None = None,
    ) -> dict:
        params = [
            ("query", query),
            ("dsoType", "item"),
            ("page", str(page)),
            ("size", str(size)),
            ("f.category", f"{LS_CATEGORY_QA},equals"),
        ]
        if ministry:
            params.append(("f.ministry", f"{ministry},equals"))
        if date_range:
            # Live-verified 2026-07-08: the eLibrary DSpace instance exposes a
            # `dateIssued` range facet ([YYYY-MM-DD TO YYYY-MM-DD] filters to
            # exact days) and a `dc.date.issued` sort option. The explicit sort
            # keeps empty-query pagination stable — Solr's default score sort
            # is constant (hence unordered) when there is no query text.
            params.append(("f.dateIssued", f"[{date_range[0]} TO {date_range[1]}],equals"))
            params.append(("sort", "dc.date.issued,ASC"))
        url = f"{LS_API_BASE}/discover/search/objects?" + urlencode(params)
        r = self.session.get(url, headers=HEADERS, timeout=45)
        r.raise_for_status()
        return r.json()

    def ls_search_all(
        self,
        query: str,
        ministry: str,
        limit: int | None,
        date_range: tuple[str, str] | None = None,
    ) -> Iterator[dict]:
        page = 0
        yielded = 0
        while True:
            data = self.ls_search_page(query, ministry, page=page, date_range=date_range)
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

    def _ls_record(self, item: dict, *, run_id: str, group: str, query: str, ministry: str) -> dict:
        md = item.get("metadata", {})
        date = md_value(md, "dc.date.issued")
        qtype = md_value(md, "dc.identifier.questiontype")
        qno = md_value(md, "dc.identifier.questionnumber")
        return {
            "key": stable_key("Lok Sabha", qtype, qno, date),
            "run_id": run_id,
            "kind": "qa",
            "house": "Lok Sabha",
            "uuid": item.get("uuid"),
            "handle": item.get("handle"),
            "title": md_value(md, "dc.title"),
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

    def _ls_attach_pdf(self, rec: dict) -> None:
        pdf_url = self.ls_pdf_url(rec["uuid"])
        if not pdf_url:
            return
        qtype_seg = safe_filename_segment((rec["qtype"] or "U").upper()[:1])
        qno_seg = safe_filename_segment(rec["qno"] or "X")
        uuid_seg = safe_filename_segment(rec["uuid"][:8].replace("-", ""))
        fname = f"{qtype_seg}{qno_seg}_{uuid_seg}.pdf"
        pdf_path = self.pdf_dir / "ls" / fname
        if self.write_pdf(pdf_url, pdf_path, HEADERS):
            rec["pdf_url"] = pdf_url
            rec["pdf_path"] = str(pdf_path.relative_to(self.out_dir))

    def _rs_record(self, row: dict, *, run_id: str, found_via: str) -> dict:
        date = rs_date_iso(row.get("ans_date"))
        qtype = (row.get("qtype") or "").strip()
        qno = str(row.get("qno") or "").split(".")[0]
        return {
            "key": stable_key("Rajya Sabha", qtype, qno, date),
            "run_id": run_id,
            "kind": "qa",
            "house": "Rajya Sabha",
            "qslno": row.get("qslno"),
            "ses_no": row.get("ses_no"),
            "title": (row.get("qtitle") or "").strip(),
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
            "found_via_query": found_via,
            "status": (row.get("status") or "").strip(),
            "probed_at": now(),
        }

    def _rs_attach_pdf(self, rec: dict) -> None:
        if not rec.get("pdf_url"):
            return
        qtype_seg = safe_filename_segment((rec["qtype"] or "U").upper()[:1])
        qno_seg = safe_filename_segment(rec["qno"] or "X")
        qslno_seg = safe_filename_segment(rec.get("qslno"))
        fname = f"{qtype_seg}{qno_seg}_{qslno_seg}.pdf"
        pdf_path = self.pdf_dir / "rs" / fname
        if self.write_pdf(rec["pdf_url"], pdf_path, RS_HEADERS):
            rec["pdf_path"] = str(pdf_path.relative_to(self.out_dir))

    def load_window_states(self) -> dict[str, dict]:
        states: dict[str, dict] = {}
        if not self.windows_path.exists():
            return states
        with self.windows_path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("window_id"):
                    states[rec["window_id"]] = rec
        return states

    def record_window(self, rec: dict) -> None:
        self._append_jsonl(self.windows_path, rec)

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
        searches = [("member", self.member_name)] if self.member_name else self.topic.searches(max_buckets)
        ministries = [""] if self.member_name else self.topic.lok_sabha_ministries
        for group, query in searches:
            for ministry in ministries:
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
                        if not self.member_name and self.topic.filter_fn is not None and not self.topic.filter_fn(title, query):
                            bkt_no_match += 1
                            continue
                        rec = self._ls_record(item, run_id=run_id, group=group, query=query, ministry=ministry)
                        if (
                            self.topic.record_filter_fn is not None
                            and not self.topic.record_filter_fn(rec)
                        ):
                            bkt_no_match += 1
                            continue
                        if download:
                            self._ls_attach_pdf(rec)
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

    def rs_search_session(self, ses_no: int, ministry_like: str, member_name: str | None = None) -> list[dict]:
        if member_name:
            where = f"ses_no={ses_no} and name like '{member_name}%'"
            if ministry_like:
                where += f" and min_name like '{ministry_like}%'"
        elif ministry_like:
            where = f"ses_no={ses_no} and min_name like '{ministry_like}%'"
        else:
            # Live-verified 2026-07-08: a bare ses_no whereclause returns the
            # entire session in one response (session 267 = 4,371 rows / 5.3MB),
            # no pagination — the member-less enumeration path.
            where = f"ses_no={ses_no}"
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
        ministries = [""] if self.member_name else self.topic.rajya_sabha_ministry_likes
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
                    records = self.rs_search_session(ses_no, ministry, member_name=self.member_name)
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
                    rec = self._rs_record(row, run_id=run_id, found_via=ministry)
                    if (
                        self.topic.record_filter_fn is not None
                        and not self.topic.record_filter_fn(rec)
                    ):
                        bkt_no_match += 1
                        continue
                    if download:
                        self._rs_attach_pdf(rec)
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
                    no_match=bkt_no_match, kept=bkt_kept,
                    skipped_seen=bkt_skipped_seen,
                    elapsed_ms=round((time.monotonic() - bkt_t0) * 1000, 1),
                    error=None,
                )
        self.runlog.finish(added=added)
        return added

    def probe_ls_all(
        self,
        seen: set[str],
        *,
        from_date: str,
        to_date: str,
        qtype_filter: str | None,
        max_records: int | None,
        download: bool,
        reset_windows: frozenset[str] = frozenset(),
    ) -> int:
        run_id = self.runlog.start(
            kind="qa",
            scope={
                "house": "ls",
                "mode": "all",
                "from_date": from_date,
                "to_date": to_date,
                "qtype": qtype_filter,
                "max_records": max_records,
                "download": download,
            },
            topic_name=self.topic.name,
            topic_path=self.topic_path,
            classifier_config=self.topic.classifier_config,
        )
        states = self.load_window_states()
        added = 0
        for w_from, w_to in month_windows(from_date, to_date):
            window_id = f"ls:{w_from}..{w_to}"
            prior = states.get(window_id)
            if (
                window_id not in reset_windows
                and prior is not None
                and prior.get("status") == "complete"
                and prior.get("qtype") == qtype_filter
            ):
                self.log(f"LS window {window_id} already complete — skipping")
                continue
            self.log(f"LS window {window_id} (member-less enumeration)")
            bkt_t0 = time.monotonic()
            bkt_raw = bkt_after_date = bkt_kept = bkt_skipped_seen = 0
            bkt_error: str | None = None
            try:
                for item in self.ls_search_all("", "", None, date_range=(w_from, w_to)):
                    bkt_raw += 1
                    md = item.get("metadata", {})
                    date = md_value(md, "dc.date.issued")
                    qtype = md_value(md, "dc.identifier.questiontype")
                    qno = md_value(md, "dc.identifier.questionnumber")
                    if qtype_filter and (qtype or "").strip().lower() != qtype_filter:
                        continue
                    if not date_in_range(date, w_from, w_to):
                        continue
                    bkt_after_date += 1
                    if not item.get("uuid"):
                        continue
                    key = stable_key("Lok Sabha", qtype, qno, date)
                    if key in seen:
                        bkt_skipped_seen += 1
                        continue
                    rec = self._ls_record(item, run_id=run_id, group="all", query="", ministry="")
                    if download:
                        self._ls_attach_pdf(rec)
                        time.sleep(self.sleep)
                    rec.setdefault("language_classified", ["en"])
                    self._enrich_askers(rec)
                    self.append(rec)
                    seen.add(key)
                    added += 1
                    bkt_kept += 1
                    if max_records is not None and added >= max_records:
                        self.runlog.record_bucket(
                            kind="ls_qa_all", window=window_id, from_date=w_from, to_date=w_to,
                            raw_returned=bkt_raw, after_date_filter=bkt_after_date,
                            kept=bkt_kept, skipped_seen=bkt_skipped_seen,
                            elapsed_ms=round((time.monotonic() - bkt_t0) * 1000, 1),
                            error=None,
                        )
                        self.log(f"LS window {window_id} stopped at max_records — window left incomplete")
                        self.runlog.finish(added=added)
                        return added
            except Exception as exc:  # noqa: BLE001
                bkt_error = f"{type(exc).__name__}: {exc}"
                self.log(f"LS window {window_id} failed: {exc}")
                self.runlog.record_error(where=f"ls/{window_id}", exc=exc)
            self.runlog.record_bucket(
                kind="ls_qa_all", window=window_id, from_date=w_from, to_date=w_to,
                raw_returned=bkt_raw, after_date_filter=bkt_after_date,
                kept=bkt_kept, skipped_seen=bkt_skipped_seen,
                elapsed_ms=round((time.monotonic() - bkt_t0) * 1000, 1),
                error=bkt_error,
            )
            self.record_window({
                "window_id": window_id,
                "house": "ls",
                "from_date": w_from,
                "to_date": w_to,
                "qtype": qtype_filter,
                "status": "suspect" if bkt_error else "complete",
                "kept": bkt_kept,
                "errors": 1 if bkt_error else 0,
                "run_id": run_id,
                "recorded_at": now(),
            })
        self.runlog.finish(added=added)
        return added

    def probe_rs_all(
        self,
        seen: set[str],
        *,
        sessions: Iterable[int],
        from_date: str | None,
        to_date: str | None,
        qtype_filter: str | None,
        max_records: int | None,
        download: bool,
        reset_windows: frozenset[str] = frozenset(),
    ) -> int:
        sessions_list = list(sessions)
        run_id = self.runlog.start(
            kind="qa",
            scope={
                "house": "rs",
                "mode": "all",
                "sessions": sessions_list,
                "from_date": from_date,
                "to_date": to_date,
                "qtype": qtype_filter,
                "max_records": max_records,
                "download": download,
            },
            topic_name=self.topic.name,
            topic_path=self.topic_path,
            classifier_config=self.topic.classifier_config,
        )
        states = self.load_window_states()
        added = 0
        for ses_no in sessions_list:
            window_id = f"rs:{ses_no}"
            prior = states.get(window_id)
            if (
                window_id not in reset_windows
                and prior is not None
                and prior.get("status") == "complete"
                and prior.get("qtype") == qtype_filter
                and prior.get("from_date") == from_date
                and prior.get("to_date") == to_date
            ):
                self.log(f"RS window {window_id} already complete — skipping")
                continue
            self.log(f"RS window {window_id} (member-less enumeration)")
            bkt_t0 = time.monotonic()
            bkt_raw = bkt_after_date = bkt_kept = bkt_skipped_seen = 0
            bkt_error: str | None = None
            try:
                for row in self.rs_search_session(ses_no, "", member_name=None):
                    bkt_raw += 1
                    date = rs_date_iso(row.get("ans_date"))
                    qtype = (row.get("qtype") or "").strip()
                    if qtype_filter and qtype.lower() != qtype_filter:
                        continue
                    if not date_in_range(date, from_date, to_date):
                        continue
                    bkt_after_date += 1
                    qno = str(row.get("qno") or "").split(".")[0]
                    key = stable_key("Rajya Sabha", qtype, qno, date)
                    if key in seen:
                        bkt_skipped_seen += 1
                        continue
                    rec = self._rs_record(row, run_id=run_id, found_via="")
                    if download:
                        self._rs_attach_pdf(rec)
                        time.sleep(self.sleep)
                    rec.setdefault("language_classified", ["en"])
                    self._enrich_askers(rec)
                    self.append(rec)
                    seen.add(key)
                    added += 1
                    bkt_kept += 1
                    if max_records is not None and added >= max_records:
                        self.runlog.record_bucket(
                            kind="rs_qa_all", window=window_id, session=ses_no,
                            raw_returned=bkt_raw, after_date_filter=bkt_after_date,
                            kept=bkt_kept, skipped_seen=bkt_skipped_seen,
                            elapsed_ms=round((time.monotonic() - bkt_t0) * 1000, 1),
                            error=None,
                        )
                        self.log(f"RS window {window_id} stopped at max_records — window left incomplete")
                        self.runlog.finish(added=added)
                        return added
            except Exception as exc:  # noqa: BLE001
                bkt_error = f"{type(exc).__name__}: {exc}"
                self.log(f"RS window {window_id} failed: {exc}")
                self.runlog.record_error(where=f"rs/{window_id}", exc=exc)
            self.runlog.record_bucket(
                kind="rs_qa_all", window=window_id, session=ses_no,
                raw_returned=bkt_raw, after_date_filter=bkt_after_date,
                kept=bkt_kept, skipped_seen=bkt_skipped_seen,
                elapsed_ms=round((time.monotonic() - bkt_t0) * 1000, 1),
                error=bkt_error,
            )
            self.record_window({
                "window_id": window_id,
                "house": "rs",
                "ses_no": ses_no,
                "from_date": from_date,
                "to_date": to_date,
                "qtype": qtype_filter,
                "status": "suspect" if bkt_error else "complete",
                "kept": bkt_kept,
                "errors": 1 if bkt_error else 0,
                "run_id": run_id,
                "recorded_at": now(),
            })
        self.runlog.finish(added=added)
        return added

    # --- Tabled papers / title-search mode ---

    #: Terminal statuses for tabled-paper resume. "metadata_only" is
    #: deliberately NOT always-terminal: a --no-download pass followed by
    #: a downloads-enabled rerun must still fetch bitstreams for that item
    #: (the 2026-07-03 indiacode.py resume-staleness lesson).
    _TABLED_TERMINAL_STATUSES = frozenset({"downloaded", "no_bitstream_found"})

    def load_seen_statuses(self) -> dict[str, str]:
        """Map manifest key -> last recorded status (tabled-paper resume)."""
        seen: dict[str, str] = {}
        if not self.manifest.exists():
            return seen
        with self.manifest.open(encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("key"):
                    seen[rec["key"]] = rec.get("status", "")
        return seen

    def search_titles(
        self,
        query: str,
        *,
        title_scoped: bool = True,
        size: int = 100,
        max_pages: int | None = None,
    ) -> Iterator[dict]:
        """Generic eLibrary discover search — no Q&A category filter.

        Reaches the DSpace records the Q&A-profiled paths cannot, e.g.
        Papers Laid on the Table. ``title_scoped`` wraps the query in a
        Solr ``title:(...)`` clause so matches stay on the record title
        rather than full text.
        """
        page = 0
        while True:
            q = f"title:({query})" if title_scoped else query
            params = [
                ("query", q),
                ("dsoType", "item"),
                ("page", str(page)),
                ("size", str(size)),
            ]
            url = f"{LS_API_BASE}/discover/search/objects?" + urlencode(params)
            r = self.session.get(url, headers=HEADERS, timeout=45)
            r.raise_for_status()
            result = r.json().get("_embedded", {}).get("searchResult", {})
            objects = result.get("_embedded", {}).get("objects", [])
            if not objects:
                return
            for obj in objects:
                item = obj.get("_embedded", {}).get("indexableObject")
                if item:
                    yield item
            page += 1
            if page >= result.get("page", {}).get("totalPages", 0):
                return
            if max_pages is not None and page >= max_pages:
                return
            time.sleep(self.sleep)

    def fetch_item_bitstreams(self, item_uuid: str, dest_dir: Path) -> tuple[list[dict], bool]:
        """Download every PDF bitstream of an item.

        Returns ``(provenance_rows, complete)``. ``complete`` is False
        when any listing request failed or any PDF download failed —
        callers must record a retryable status in that case, because an
        empty/partial result from a transient failure is otherwise
        indistinguishable from an item that genuinely has no PDF.

        Unlike ``ls_pdf_url`` (first PDF only), tabled papers can carry
        several bitstreams per item, so all are walked. PDFs stream to
        local disk via ``write_pdf`` — never buffered in memory.
        """
        r = self.session.get(f"{LS_API_BASE}/core/items/{item_uuid}/bundles", headers=HEADERS, timeout=30)
        if r.status_code != 200:
            return [], False
        bundles = r.json().get("_embedded", {}).get("bundles", [])
        original = next((b for b in bundles if b.get("name") == "ORIGINAL"), None)
        if not original:
            return [], True
        bitstreams_url = original.get("_links", {}).get("bitstreams", {}).get("href")
        if not bitstreams_url:
            return [], True
        r2 = self.session.get(bitstreams_url, headers=HEADERS, timeout=30)
        if r2.status_code != 200:
            return [], False
        downloads: list[dict] = []
        complete = True
        for b in r2.json().get("_embedded", {}).get("bitstreams", []):
            name = b.get("name") or ""
            if not name.lower().endswith(".pdf"):
                continue
            content_url = b.get("_links", {}).get("content", {}).get("href")
            if not content_url:
                continue
            stem = safe_filename_segment(name[:-4])[:80]
            uuid_seg = safe_filename_segment((b.get("uuid") or "")[:8])
            dest = dest_dir / f"{stem}_{uuid_seg}.pdf"
            if not self.write_pdf(content_url, dest, HEADERS):
                complete = False
                continue
            sha = hashlib.sha256()
            with dest.open("rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    sha.update(chunk)
            downloads.append({
                "bitstream_uuid": b.get("uuid"),
                "name": name,
                "url": content_url,
                "dest": str(dest.relative_to(self.out_dir)),
                "sha256": sha.hexdigest(),
                "bytes": dest.stat().st_size,
            })
        return downloads, complete

    def _tabled_record(self, item: dict, *, run_id: str, query: str) -> dict:
        md = item.get("metadata", {})
        return {
            "key": f"TABLED|{item.get('uuid')}",
            "run_id": run_id,
            "kind": "tabled_paper",
            "record_type": "tabled_paper",
            "uuid": item.get("uuid"),
            "handle": item.get("handle"),
            "title": md_value(md, "dc.title"),
            "date_issued": md_value(md, "dc.date.issued"),
            "uri": md_value(md, "dc.identifier.uri"),
            "source": "elibrary.sansad.in",
            "query": query,
            "status": "metadata_only",
            "downloads": [],
            "probed_at": now(),
        }

    def probe_tabled(
        self,
        *,
        query: str,
        title_filter: str | None = None,
        title_scoped: bool = True,
        size: int = 100,
        max_pages: int | None = None,
        max_records: int | None = None,
        download: bool = True,
    ) -> int:
        run_id = self.runlog.start(
            kind="tabled_paper",
            scope={
                "query": query,
                "title_filter": title_filter,
                "title_scoped": title_scoped,
                "size": size,
                "max_pages": max_pages,
                "max_records": max_records,
                "download": download,
            },
            topic_name=self.topic.name,
            topic_path=self.topic_path,
            classifier_config=self.topic.classifier_config,
        )
        seen = self.load_seen_statuses()
        filt = re.compile(title_filter, re.IGNORECASE) if title_filter else None
        added = 0
        bkt_t0 = time.monotonic()
        bkt_raw = bkt_kept = bkt_skipped_seen = bkt_no_match = 0
        bkt_error: str | None = None
        try:
            for item in self.search_titles(query, title_scoped=title_scoped, size=size, max_pages=max_pages):
                bkt_raw += 1
                item_uuid = item.get("uuid")
                if not item_uuid:
                    continue
                title = md_value(item.get("metadata", {}), "dc.title")
                if filt and not filt.search(title or ""):
                    bkt_no_match += 1
                    continue
                key = f"TABLED|{item_uuid}"
                prior = seen.get(key)
                if prior in self._TABLED_TERMINAL_STATUSES:
                    bkt_skipped_seen += 1
                    continue
                if prior in ("metadata_only", "download_error") and not download:
                    bkt_skipped_seen += 1
                    continue
                rec = self._tabled_record(item, run_id=run_id, query=query)
                if download:
                    downloads, complete = self.fetch_item_bitstreams(item_uuid, self.pdf_dir / "tabled")
                    rec["downloads"] = downloads
                    if not complete:
                        # Transient listing/download failure — retryable,
                        # never terminal (distinct from true no-PDF items).
                        rec["status"] = "download_error"
                    elif downloads:
                        rec["status"] = "downloaded"
                    else:
                        rec["status"] = "no_bitstream_found"
                self.append(rec)
                seen[key] = rec["status"]
                added += 1
                bkt_kept += 1
                if max_records is not None and added >= max_records:
                    break
                time.sleep(self.sleep)
        except Exception as exc:  # noqa: BLE001
            bkt_error = f"{type(exc).__name__}: {exc}"
            self.runlog.record_error(where=f"tabled/{query}", exc=exc)
            hint = geo_fence_hint(exc)
            if hint:
                raise SystemExit(hint) from exc
            raise
        finally:
            # Bucket + finish run on every exit path — a partial run's
            # manifest rows must never carry a run_id absent from
            # _runs.jsonl.
            self.runlog.record_bucket(
                kind="tabled_paper", query=query, title_filter=title_filter,
                raw_returned=bkt_raw, no_match=bkt_no_match, kept=bkt_kept,
                skipped_seen=bkt_skipped_seen,
                elapsed_ms=round((time.monotonic() - bkt_t0) * 1000, 1),
                error=bkt_error,
            )
            self.runlog.finish(added=added)
        return added
