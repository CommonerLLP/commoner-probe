# SPDX-License-Identifier: MIT
"""Per-run audit log: makes categorical apparatus travel with the corpus.

Every crawl appends one record to ``<out>/_runs.jsonl``. Each record pins:

* a run id (uuid4) so individual records in ``manifest.jsonl`` can be linked
  to the run that produced them via the ``run_id`` field;
* the topic profile's content hash and classifier configuration (with
  secrets redacted), so a future reader can verify which categorical
  apparatus produced which records;
* scope (committees, houses, date filters, Lok Sabha number);
* outcome (added, errors).

This is the architectural answer to two pressures:

* Suchman (*Do Categories Have Politics?*): tag rules and anchors are
  theories of speech, not neutral filters. The theory must be inseparable
  from the data it produced.
* Power (*Making Things Auditable*): "audit-grade" only means anything if
  the apparatus that did the auditing is itself inspectable.

JSONL purity in ``manifest.jsonl`` is preserved — runs go to a sibling file.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from importlib.metadata import version as _importlib_version
    TOOL_VERSION = _importlib_version("commoner-probe")
except Exception:
    TOOL_VERSION = "0.0.0"

# Substrings that, when present in a dict-key (case-insensitive), trigger
# redaction in `_redact()` before the value is written to the runlog.
# Substring-matching (rather than exact-name matching) catches the long
# tail of credential-naming conventions: `api_key`, `apiKey`, `apikey`,
# `OPENAI_API_KEY`, `secret`, `client_secret`, `access_token`,
# `bearer_token`, `password`, `auth`, `credential`, etc. The runlog is
# committed/distributed by sister projects, so missed redactions become
# permanent leaks.
_REDACT_SUBSTRINGS: frozenset[str] = frozenset({
    "key", "secret", "token", "password", "auth", "bearer", "credential",
})


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _is_secret_key(key: str) -> bool:
    """True if the key (case-insensitive) contains any redact substring."""
    lowered = key.lower()
    return any(s in lowered for s in _REDACT_SUBSTRINGS)


def _redact(obj: Any) -> Any:
    """Deep-redact any dict key matching a credential substring.

    Lists/scalars are passed through unchanged; only dict values whose
    key matches a credential substring are replaced with ``"<redacted>"``.
    """
    if isinstance(obj, dict):
        return {
            k: ("<redacted>" if _is_secret_key(k) else _redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


def topic_hash(topic_path: Path) -> str:
    """Stable content hash of the topic-profile JSON on disk.

    Hashes raw bytes (not parsed JSON) so whitespace-only edits show up too —
    the Power critique: every variation of the apparatus is a different
    apparatus and should be traceable as such.
    """
    h = hashlib.sha256(topic_path.read_bytes())
    return f"sha256:{h.hexdigest()}"


@dataclass
class Run:
    run_id: str
    kind: str  # 'committee_report' | 'qa' | ...
    scope: dict[str, Any]
    topic_name: str
    topic_path: str
    topic_hash: str
    classifier_mode: str
    classifier_config_redacted: dict[str, Any]
    tool_version: str
    started_at: str
    ended_at: str | None = None
    added: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)
    # Per-bucket attempt log: one entry per (query, ministry) for QA crawls;
    # one per committee for committee crawls. Schema is deliberately
    # free-form so different crawler types can record what's relevant
    # (raw_returned, after_date_filter, kept, skipped_seen, elapsed_ms,
    # error). Surfaced 2026-05-08 by user audit: empty-result crawls
    # were undebuggable from the run log.
    bucket_attempts: list[dict[str, Any]] = field(default_factory=list)
    # Run-level outcome, derived at finish(): 'complete' | 'partial' |
    # 'failed'. Added for the zero-hour case: a crawl whose every bucket
    # raised still wrote added: 0 and exited 0, so a totally-failed run and
    # a genuinely quiet member produced indistinguishable artefacts.
    status: str = "complete"


class RunLog:
    """Append-only ``_runs.jsonl`` writer. One instance per crawl invocation."""

    def __init__(self, out_dir: Path) -> None:
        self.path = out_dir / "_runs.jsonl"
        self._run: Run | None = None
        self._t0: float = 0.0
        #: Status of every run finished on this instance, in order. One
        #: invocation can finish several runs (sansad finishes one per
        #: house), and the command's exit code has to see all of them.
        self.statuses: list[str] = []

    def start(
        self,
        *,
        kind: str,
        scope: dict[str, Any],
        topic_name: str,
        topic_path: Path | str | None,
        classifier_mode: str = "",
        classifier_config: dict[str, Any] | None = None,
    ) -> str:
        """Open a run; returns the run_id (callers stamp it on each record)."""
        topic_path_str = str(topic_path) if topic_path else ""
        thash = topic_hash(Path(topic_path)) if topic_path else "sha256:unknown"
        self._run = Run(
            run_id=uuid.uuid4().hex,
            kind=kind,
            scope=scope,
            topic_name=topic_name,
            topic_path=topic_path_str,
            topic_hash=thash,
            classifier_mode=classifier_mode,
            classifier_config_redacted=_redact(classifier_config or {}),
            tool_version=TOOL_VERSION,
            started_at=_now(),
        )
        self._t0 = time.monotonic()
        return self._run.run_id

    def record_error(self, where: str, exc: BaseException) -> None:
        if self._run is None:
            return
        self._run.errors.append({"where": where, "error": f"{type(exc).__name__}: {exc}"})

    def record_bucket(self, **fields: Any) -> None:
        """Append one bucket-attempt row to the active run.

        ``fields`` is free-form: callers pass whichever keys are relevant
        to their crawler kind. Conventional keys for QA crawls:

          group, query, ministry, raw_returned, after_date_filter,
          kept, skipped_seen, elapsed_ms, error

        Conventional keys for committee crawls:

          committee_slug, house, pages_fetched, raw_returned, kept,
          elapsed_ms, error

        No-op if no run is open.
        """
        if self._run is None:
            return
        self._run.bucket_attempts.append(dict(fields))

    def _derive_status(self) -> str:
        """Run outcome from what the crawl actually recorded.

        Bucket attempts are the evidence when a crawler logs them: every
        bucket erroring is a failed run, some erroring is partial. A
        crawler that logs no buckets falls back to ``record_error``.
        ``added`` is deliberately not consulted — a quiet member and a
        broken crawl both add nothing, which is the confusion this
        field exists to end.
        """
        assert self._run is not None
        buckets = self._run.bucket_attempts
        if buckets:
            failed = sum(1 for b in buckets if b.get("error"))
            if failed == len(buckets):
                return "failed"
            return "partial" if failed else "complete"
        return "failed" if self._run.errors else "complete"

    def finish(self, *, added: int) -> str:
        """Close the run, append it, and return its status."""
        if self._run is None:
            return ""
        self._run.ended_at = _now()
        self._run.added = added
        self._run.status = self._derive_status()
        self.statuses.append(self._run.status)
        payload = {
            **self._run.__dict__,
            "elapsed_ms": round((time.monotonic() - self._t0) * 1000, 1),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        status = self._run.status
        self._run = None
        return status
