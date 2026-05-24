from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote, urlsplit, urlunsplit

from .http_client import make_session
from .runlog import RunLog

if TYPE_CHECKING:
    from .resolver import Resolver
    from .topics import TopicProfile


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def safe_filename_segment(value: object) -> str:
    """Sanitize an attacker-controllable string for use in a filesystem path.

    sansad.in API responses populate fields (``reportNo``, ``uuid``,
    ``qslno``) that are interpolated into PDF destination paths. A
    malicious or compromised upstream returning ``"../../../evil"`` for
    one of these would cause ``write_pdf`` to write outside the intended
    ``pdfs/`` directory.

    Replaces every character outside ``[A-Za-z0-9._-]`` with ``_``.
    Empty / None / non-string inputs become ``"unknown"``.
    """
    if value is None:
        return "unknown"
    s = str(value).strip()
    if not s:
        return "unknown"
    sanitized = re.sub(r"[^A-Za-z0-9._-]", "_", s)
    # Defensive: strip leading dots so the segment cannot become a
    # hidden file or a parent-directory reference even after sanitisation.
    sanitized = sanitized.lstrip(".")
    return sanitized or "unknown"


def _encode_url_path(url: str) -> str:
    """Percent-encode unsafe characters in the URL path/query.

    sansad.in's committee endpoints embed committee names with literal spaces
    in the path (e.g. ``/lsscommittee/Rural Development and Panchayati Raj/``).
    Both ``urllib`` and ``requests`` reject URLs containing raw spaces or other
    unencoded control characters; we must percent-encode the path/query before
    handing the URL to the HTTP client.

    Already-encoded URLs are left unchanged because ``%`` and ``+`` are in the
    safe-set, so re-encoding is idempotent.
    """
    parts = urlsplit(url)
    encoded_path = quote(parts.path, safe="/%+")
    encoded_query = quote(parts.query, safe="=&%+")
    return urlunsplit(
        (parts.scheme, parts.netloc, encoded_path, encoded_query, parts.fragment)
    )


class BaseProbe:
    """Shared I/O logic for Sansad crawlers."""

    def __init__(
        self,
        topic: TopicProfile,
        out_dir: Path,
        *,
        sleep: float = 0.25,
        topic_path: Path | str | None = None,
        resolver: "Resolver | None" = None,
    ):
        self.topic = topic
        self.out_dir = out_dir
        self.pdf_dir = out_dir / "pdfs"
        self.manifest = out_dir / "manifest.jsonl"
        self.log_path = out_dir / "probe.log"
        self.sleep = sleep
        self.session = make_session()
        self.topic_path = topic_path
        self.runlog = RunLog(out_dir)
        # Optional name+context -> entity_id resolver. When None, records
        # carry ``asker_entity_ids`` lists with null entries — schema
        # commitment lands either way, populating it requires entity data.
        self.resolver = resolver

    def resolve_askers(self, names: list[str], context: dict | None = None) -> list[str | None]:
        """Map a list of asker names to a parallel list of entity_ids.

        Same length as input. Null entries mean ``status != "resolved"`` —
        unknown name, ambiguous match, or no resolver configured. The
        record stays honest about the gap; consumers handling weights and
        cross-session tracking skip null entities cleanly.
        """
        out: list[str | None] = []
        for nm in names or []:
            if not self.resolver:
                out.append(None)
                continue
            result = self.resolver.resolve(nm, context=context, kind_hint="mp")
            out.append(result.entity_id if result.status == "resolved" else None)
        return out

    def log(self, msg: str) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        line = f"[{now()}] {msg}"
        print(line, flush=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

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

    def append(self, rec: dict) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _load_jsonl_keys(self, path: Path) -> set[str]:
        """Return the set of ``key`` values from an arbitrary JSONL file."""
        seen: set[str] = set()
        if not path.exists():
            return seen
        with path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    seen.add(json.loads(line)["key"])
                except (json.JSONDecodeError, KeyError):
                    pass
        return seen

    def _append_jsonl(self, path: Path, rec: dict) -> None:
        """Append one record to an arbitrary JSONL file."""
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def write_pdf(self, url: str, dest_path: Path, headers: dict) -> bool:
        if dest_path.exists() and dest_path.stat().st_size > 1000:
            return True
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        encoded_url = _encode_url_path(url)
        try:
            r = self.session.get(encoded_url, headers=headers, timeout=60)
            r.raise_for_status()
            with dest_path.open("wb") as f:
                for chunk in r.iter_content(chunk_size=16384):
                    f.write(chunk)
            return dest_path.exists() and dest_path.stat().st_size > 1000
        except Exception as e:
            self.log(f"Warning: Failed to download PDF {url}: {e}")
            return False
