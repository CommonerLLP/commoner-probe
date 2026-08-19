# SPDX-License-Identifier: MIT
from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote, urlsplit, urlunsplit

from .http_client import iter_capped, make_session
from .runlog import RunLog
from .textparse import extract_pdf_text

if TYPE_CHECKING:
    from .resolver import Resolver
    from .topics import TopicProfile


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def safe_filename_segment(value: object, *, collapse: bool = False, strip: bool = True) -> str:
    """Sanitize an attacker-controllable string for use in a filesystem path.

    Server-supplied fields — sansad.in's ``reportNo``/``uuid``/``qslno``, a
    NADA resource's ``data-filename``, a document basename taken from a URL —
    are interpolated into destination paths. An upstream returning ``".."`` or
    ``"../../../evil"`` for one of these would otherwise write outside the
    intended directory, or produce an empty path segment.

    Replaces every character outside ``[A-Za-z0-9._-]`` with ``_``, strips
    leading dots, and returns ``"unknown"`` rather than an empty string.

    ``collapse`` squashes each run of disallowed characters to a single ``_``,
    and ``strip`` then trims leading and trailing ones. They are separate
    because the callers genuinely wrote different names: two squashed and
    trimmed, one squashed only, so trimming for it turns ``"_report_.pdf"``
    into ``"report_.pdf"`` and orphans the file already on disk. Pick whichever
    matches the filenames the caller already wrote; the safety properties are
    the same either way.

    This is the ONE implementation. Eight near-copies of the regex existed
    across the package and only this one carried the leading-dot and empty
    defences, so ``nada._slug("..")`` returned ``".."`` and
    ``nada._slug("///")`` returned ``""``.
    """
    if value is None:
        return "unknown"
    s = str(value).strip()
    if not s:
        return "unknown"
    pattern = r"[^A-Za-z0-9._-]+" if collapse else r"[^A-Za-z0-9._-]"
    sanitized = re.sub(pattern, "_", s)
    if collapse and strip:
        sanitized = sanitized.strip("_")
    # Strip leading dots so the segment cannot become a hidden file or a
    # parent-directory reference even after sanitisation.
    sanitized = sanitized.lstrip(".")
    return sanitized or "unknown"


_tmp_counter = itertools.count()


def _private_tmp_path(dest_path: Path) -> Path:
    """A temp path no other writer will pick.

    Two runs sharing an output directory both wrote ``<name>.tmp``: one
    truncated the other's partial file, and the loser's ``unlink`` deleted work
    the winner had already renamed away from. The pid and an object id make the
    path private to this writer within this process.
    """
    return dest_path.with_suffix(f"{dest_path.suffix}.{os.getpid()}.{next(_tmp_counter)}.tmp")


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


def note_text_layer(record: dict, dest: Path, body: bytes, *, min_chars: int) -> None:
    """Record the download's hash, and whether the PDF carries a text layer.

    One copy for four adapters, which each had their own. Three of those called
    ``extract_pdf_text`` unguarded, and 0.14.0 made it RAISE where it had
    returned "" — so on a machine with neither poppler nor pdfminer the
    exception escaped a download that had already written a good file, losing
    the record and ending the crawl.

    **Extraction is advisory here; acquisition is not.** A file that cannot be
    read is still acquired, and ``text_layer`` is ``None`` — unknown, which is
    not the same claim as ``False``.
    """
    record["sha256"] = hashlib.sha256(body).hexdigest()
    try:
        text = extract_pdf_text(dest) or ""
    except Exception:  # noqa: BLE001 - see the docstring
        record["text_layer"] = None
        return
    record["text_layer"] = len(text.strip()) >= min_chars


def download_file(session, url: str, dest_path: Path, headers: dict,
                  *, log=None, timeout: int = 60) -> bool:
    """Download to a temp file, then rename into place.

    The rename is what makes the size check below trustworthy. Writing
    straight to ``dest_path`` meant a Ctrl-C or a dropped connection left a
    truncated PDF there, and the next run's ``st_size > 1000`` accepted it as
    complete — permanently. Extraction then produced partial text
    indistinguishable from a genuinely short answer.

    ``os.replace`` is atomic within a filesystem, so a reader sees either no
    file or the whole file, never a half-written one.

    A function rather than a method, because a probe that does not extend
    :class:`BaseProbe` needs the same guarantee. ``BillsProbe`` is one: it
    holds its own session and carries eight document URLs per bill.
    """
    if dest_path.exists() and dest_path.stat().st_size > 1000:
        return True
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    encoded_url = _encode_url_path(url)
    tmp_path = _private_tmp_path(dest_path)
    try:
        # stream=True or requests buffers the whole body before iter_capped
        # sees a chunk, and the ceiling fires after the allocation it exists
        # to prevent.
        r = session.get(encoded_url, headers=headers, timeout=timeout, stream=True)
        r.raise_for_status()
        with tmp_path.open("wb") as f:
            for chunk in iter_capped(r):
                f.write(chunk)
        if tmp_path.stat().st_size <= 1000:
            return False
        os.replace(tmp_path, dest_path)
        return True
    except Exception as e:
        if log is not None:
            log(f"Warning: Failed to download PDF {url}: {e}")
        return False
    finally:
        tmp_path.unlink(missing_ok=True)


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
        user_agent: str | None = None,
    ):
        self.topic = topic
        self.out_dir = out_dir
        self.pdf_dir = out_dir / "pdfs"
        self.manifest = out_dir / "manifest.jsonl"
        self.log_path = out_dir / "probe.log"
        self.sleep = sleep
        # Passed to make_session, NOT set on session.headers afterwards. The
        # session needs the identity at construction so robots.txt is fetched
        # and cached under the same User-Agent the pages will be requested
        # with. Mutating headers later changes only the outgoing request; the
        # robots check keeps using the default, and a portal that answers
        # differently for the two identities gets the wrong answer.
        self.session = make_session(user_agent=user_agent)
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
        """As :func:`download_file`, using this probe's session and log."""
        return download_file(self.session, url, dest_path, headers, log=self.log)
