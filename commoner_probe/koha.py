# SPDX-License-Identifier: MIT
"""Acquire held-item and held-biblio metadata from a Koha public REST API."""

from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .checkpoint import checkpointed_run, load_checkpoint
from .http_client import make_session

_PORTAL_RE = re.compile(r"^[a-z0-9-]+$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _response_json(response: Any) -> Any:
    if hasattr(response, "json"):
        return response.json()
    body = getattr(response, "content", None)
    if body is not None:
        return json.loads(body)
    return json.loads(response.text)


@dataclass
class KohaRunResult:
    """Records and coverage state produced by one Koha invocation."""

    records: list[dict[str, Any]] = field(default_factory=list)
    failed_units: list[tuple[str, str]] = field(default_factory=list)
    held_items_total_first: int | None = None
    held_items_total_last: int | None = None
    derived_pages: int | None = None
    items_added: int = 0
    biblios_added: int = 0
    truncated: bool = False
    dry_run: bool = False

    @property
    def total_changed(self) -> bool:
        return (
            self.held_items_total_first is not None
            and self.held_items_total_last is not None
            and self.held_items_total_first != self.held_items_total_last
        )

    @property
    def report(self) -> str:
        parts = [
            f"added {self.items_added} held item(s)",
            f"added {self.biblios_added} held biblio(s)",
        ]
        if self.truncated:
            parts.append("TRUNCATED by --max-records")
        if self.total_changed:
            parts.append(
                "held-item total changed "
                f"from {self.held_items_total_first} to {self.held_items_total_last}"
            )
        if self.failed_units:
            failed = ", ".join(unit for unit, _ in self.failed_units[:5])
            parts.append(f"PARTIAL: {len(self.failed_units)} failed unit(s): {failed}")
        return ". ".join(parts) + "."


class KohaProbe:
    """Enumerate Koha held items and optionally deepen them with MARC."""

    def __init__(
        self,
        out_dir: Path,
        *,
        base_url: str,
        portal_name: str,
        per_page: int = 1000,
        embeds: list[str] | tuple[str, ...] = ("biblio",),
        sleep: float = 1.0,
        session: Any | None = None,
        log: Callable[[str], None] | None = print,
        emit: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if not _PORTAL_RE.fullmatch(portal_name):
            raise ValueError("portal_name must contain only lowercase letters, digits, and hyphens")
        if not 1 <= per_page <= 1000:
            raise ValueError("per_page must be between 1 and 1000")
        cleaned_embeds = list(dict.fromkeys(value.strip() for value in embeds if value.strip()))
        if "biblio" not in cleaned_embeds:
            cleaned_embeds.insert(0, "biblio")

        self.out_dir = Path(out_dir)
        self.base_url = base_url.rstrip("/")
        self.portal_name = portal_name
        self.per_page = per_page
        self.embeds = tuple(cleaned_embeds)
        self.session = session or make_session(rate_limit_sec=sleep)
        self.log_callback = log
        self.emit_callback = emit
        self.manifest = self.out_dir / "manifest.jsonl"
        self.run_log = self.out_dir / "probe.log"
        self.items_checkpoint = self.out_dir / f"_checkpoint_koha_{portal_name}_items.jsonl"
        self.marc_checkpoint = self.out_dir / f"_checkpoint_koha_{portal_name}_marc.jsonl"

    def _log(self, message: str, *, persist: bool = True) -> None:
        line = f"{_now_iso()} {message}"
        if self.log_callback:
            self.log_callback(line)
        if persist:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            with self.run_log.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def _page_url(self, page: int) -> str:
        return f"{self.base_url}/api/v1/public/items?_per_page={self.per_page}&_page={page}"

    def _fetch_page(self, page: int) -> tuple[list[dict[str, Any]], int, str]:
        url = self._page_url(page)
        response = self.session.get(
            url,
            timeout=120,
            headers={"x-koha-embed": ",".join(self.embeds)},
        )
        response.raise_for_status()
        rows = _response_json(response)
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError(f"page {page} did not return a JSON array of objects")
        raw_total = (getattr(response, "headers", None) or {}).get("X-Total-Count")
        if raw_total is None:
            raise ValueError(f"page {page} omitted X-Total-Count")
        try:
            total = int(raw_total)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"page {page} returned invalid X-Total-Count {raw_total!r}") from exc
        return rows, total, url

    def _fetch_marc(self, biblio_id: int) -> tuple[dict[str, Any], str]:
        url = f"{self.base_url}/api/v1/public/biblios/{biblio_id}"
        response = self.session.get(
            url,
            timeout=60,
            headers={"Accept": "application/marc-in-json"},
        )
        response.raise_for_status()
        marc = _response_json(response)
        if not isinstance(marc, dict):
            raise ValueError(f"biblio {biblio_id} did not return a MARC object")
        return marc, url

    def _manifest_rows(self, kind: str) -> Iterator[dict[str, Any]]:
        if not self.manifest.exists():
            return
        with self.manifest.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("kind") == kind and row.get("portal_name") == self.portal_name:
                    yield row

    def _append_records(self, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _emit_records(
        self,
        result: KohaRunResult,
        records: list[dict[str, Any]],
    ) -> None:
        if self.emit_callback:
            for record in records:
                self.emit_callback(record)
            return
        result.records.extend(records)

    def _item_record(
        self,
        row: dict[str, Any],
        *,
        page: int,
        source_url: str,
        total_count: int,
    ) -> dict[str, Any]:
        item_id = int(row["item_id"])
        biblio_id = int(row["biblio_id"])
        biblio = row.get("biblio")
        if not isinstance(biblio, dict):
            raise ValueError(f"item {item_id} omitted embedded biblio metadata")
        embedded = {
            name: row[name]
            for name in self.embeds
            if name != "biblio" and name in row
        }
        return {
            "key": f"KOHA|{self.portal_name}|item|{item_id}",
            "kind": "koha_item",
            "record_type": "koha_item",
            "source": self.base_url,
            "source_url": source_url,
            "portal_name": self.portal_name,
            "item_id": item_id,
            "biblio_id": biblio_id,
            "barcode": row.get("external_id", row.get("barcode")),
            "callnumber": row.get("callnumber"),
            "home_library_id": row.get("home_library_id"),
            "withdrawn": row.get("withdrawn"),
            "lost_status": row.get("lost_status"),
            "biblio": biblio,
            "embedded": embedded,
            "page": page,
            "source_total_count": total_count,
            "status": "metadata_only",
            "probed_at": _now_iso(),
        }

    def _existing_page_state(
        self, item_rows: Iterable[dict[str, Any]]
    ) -> tuple[set[str], dict[int, set[str]], dict[int, int]]:
        seen: set[str] = set()
        page_keys: dict[int, set[str]] = {}
        page_totals: dict[int, int] = {}
        for row in item_rows:
            key = row.get("key")
            page = row.get("page")
            if isinstance(key, str):
                seen.add(key)
            if isinstance(key, str) and isinstance(page, int):
                page_keys.setdefault(page, set()).add(key)
                if isinstance(row.get("source_total_count"), int):
                    page_totals[page] = row["source_total_count"]
        return seen, page_keys, page_totals

    def _enumerate_items(self, result: KohaRunResult, *, max_records: int | None) -> None:
        seen, page_keys, page_totals = self._existing_page_state(
            self._manifest_rows("koha_item")
        )
        checkpoint = load_checkpoint(self.items_checkpoint)
        done_pages = {page for page in checkpoint.done if isinstance(page, int)}
        terminal_pages = [
            page for page in done_pages if len(page_keys.get(page, set())) < self.per_page
        ]
        terminal_page = min(terminal_pages) if terminal_pages else None
        if 1 in page_totals:
            result.held_items_total_first = page_totals[1]
            result.derived_pages = math.ceil(page_totals[1] / self.per_page)
        if terminal_page is not None and terminal_page in page_totals:
            result.held_items_total_last = page_totals[terminal_page]

        safety_page = (
            math.ceil(result.held_items_total_first / self.per_page) + 1
            if result.held_items_total_first is not None
            else None
        )
        page = 1
        with checkpointed_run(
            self.items_checkpoint,
            interval=0.0,
            log=lambda message: self._log(message),
        ) as run:
            while terminal_page is None or page <= terminal_page:
                if page in run.done:
                    page += 1
                    continue
                try:
                    rows, total, source_url = self._fetch_page(page)
                    if result.held_items_total_first is None:
                        result.held_items_total_first = total
                        result.derived_pages = math.ceil(total / self.per_page)
                    safety_page = max(safety_page or 0, math.ceil(total / self.per_page) + 1)
                    candidates = [
                        self._item_record(
                            row,
                            page=page,
                            source_url=source_url,
                            total_count=total,
                        )
                        for row in rows
                        if f"KOHA|{self.portal_name}|item|{int(row['item_id'])}" not in seen
                    ]
                except Exception as exc:  # noqa: BLE001 - one failed page must not erase the run
                    reason = f"{type(exc).__name__}: {exc}"[:300]
                    result.failed_units.append((f"page:{page}", reason))
                    self._log(f"held-item page {page} failed: {reason}. The page remains retryable.")
                    if safety_page is None:
                        break
                    page += 1
                    if page > safety_page:
                        break
                    continue

                capacity = None if max_records is None else max_records - result.items_added
                selected = candidates if capacity is None else candidates[: max(0, capacity)]
                self._append_records(selected)
                self._emit_records(result, selected)
                result.items_added += len(selected)
                seen.update(record["key"] for record in selected)
                page_keys.setdefault(page, set()).update(record["key"] for record in selected)
                page_totals[page] = total

                page_complete = len(selected) == len(candidates)
                if page_complete:
                    run.mark(page)
                    if len(rows) < self.per_page:
                        terminal_page = page
                        result.held_items_total_last = total
                        self._log(f"held-item enumeration reached short page {page}.")
                        break
                else:
                    result.truncated = True
                    self._log(
                        f"TRUNCATED: --max-records stopped after {result.items_added} held item(s)."
                    )
                    break

                page += 1
                if safety_page is not None and page > safety_page:
                    reason = "pagination did not reach a short page within the live count safety bound"
                    result.failed_units.append(("pagination", reason))
                    self._log(f"PARTIAL: {reason}.")
                    break

    def _deepen_marc(self, result: KohaRunResult) -> None:
        holdings: Counter[int] = Counter()
        seen_items: set[str] = set()
        for row in self._manifest_rows("koha_item"):
            key = row["key"]
            if key in seen_items:
                continue
            seen_items.add(key)
            holdings[int(row["biblio_id"])] += 1
        existing = {
            row["key"] for row in self._manifest_rows("koha_biblio") if isinstance(row.get("key"), str)
        }
        with checkpointed_run(
            self.marc_checkpoint,
            interval=0.0,
            log=lambda message: self._log(message),
        ) as run:
            for biblio_id in sorted(holdings):
                key = f"KOHA|{self.portal_name}|biblio|{biblio_id}"
                if biblio_id in run.done:
                    continue
                if key in existing:
                    run.mark(biblio_id)
                    continue
                try:
                    marc, source_url = self._fetch_marc(biblio_id)
                except Exception as exc:  # noqa: BLE001 - one absent MARC row must not abort the run
                    reason = f"{type(exc).__name__}: {exc}"[:300]
                    result.failed_units.append((f"biblio:{biblio_id}", reason))
                    self._log(
                        f"held-biblio {biblio_id} MARC failed: {reason}. The biblio remains retryable."
                    )
                    continue
                record = {
                    "key": key,
                    "kind": "koha_biblio",
                    "record_type": "koha_biblio",
                    "source": self.base_url,
                    "source_url": source_url,
                    "portal_name": self.portal_name,
                    "biblio_id": biblio_id,
                    "holdings_count": holdings[biblio_id],
                    "marc": marc,
                    "status": "metadata_only",
                    "probed_at": _now_iso(),
                }
                self._append_records([record])
                self._emit_records(result, [record])
                result.biblios_added += 1
                existing.add(key)
                run.mark(biblio_id)

    def _dry_run(self) -> KohaRunResult:
        rows, total, source_url = self._fetch_page(1)
        records = [
            self._item_record(row, page=1, source_url=source_url, total_count=total)
            for row in rows[:5]
        ]
        return KohaRunResult(
            records=records,
            held_items_total_first=total,
            held_items_total_last=total if len(rows) < self.per_page else None,
            derived_pages=math.ceil(total / self.per_page),
            dry_run=True,
        )

    def probe(
        self,
        *,
        marc: bool = False,
        max_records: int | None = None,
        dry_run: bool = False,
    ) -> KohaRunResult:
        if max_records is not None and max_records < 1:
            raise ValueError("max_records must be positive")
        if dry_run:
            return self._dry_run()

        result = KohaRunResult()
        has_existing_items = next(self._manifest_rows("koha_item"), None) is not None
        if marc and has_existing_items:
            self._log("MARC-only upgrade over existing held items. Enumeration is unchanged.")
        else:
            self._enumerate_items(result, max_records=max_records)
        if marc:
            self._deepen_marc(result)
        self._log(result.report)
        return result
