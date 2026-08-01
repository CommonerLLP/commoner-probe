# SPDX-License-Identifier: MIT
"""NADA platform adapter — survey documentation, questionnaires and methodology.

NADA (National Data Archive) is World Bank software, not one ministry's portal.
This module talks to *an instance*, selected by ``base_url``; two are verified:

- ``https://microdata.gov.in/NADA``   — MoSPI, 187 studies (NSS, ASI, PLFS, ...)
- ``https://censusindia.gov.in/nada`` — ORGI, 40,254 studies

Contract (live-verified 2026-07-31 against microdata.gov.in from US egress; no
India relay required):

1. ``robots.txt`` 404s on microdata.gov.in, and this package fails open on 404
   (only 401/403 mean disallow-all), so the crawl is permitted with the robots
   check left on.
2. **Study routes key on the DDI ``idno``, not the numeric id.**
   ``/api/catalog/1`` returns HTTP 400 ``{"status":"failed",
   "message":"IDNO-NOT-FOUND"}``. The numeric id is used only to build the
   HTML page URLs.
3. **Unknown API subroutes return the study payload with HTTP 200.** Both
   ``/api/catalog/{idno}/resources`` and ``/related_materials`` return a body
   byte-identical to the bare study route rather than a resource list or an
   error. This module calls neither, and :meth:`NadaClient.study` refuses a
   payload whose ``idno`` is not the one requested — a 200 that answers a
   different question is not an answer.
4. **The document list exists only as HTML**, at
   ``/catalog/{numeric_id}/related-materials``: ``<fieldset><legend>`` groups,
   a ``<span class="resource-info" id="...">`` per entry, and an ``<a>`` whose
   ``data-filename`` carries the real filename.
5. **That page can fail** — study 40 returned HTTP 500 while 1, 2 and 150
   returned 200. "The page errored" and "this study has no documents" are
   different facts and get different records.
6. **Resource type is an open set**, read from the ``<legend>``: study 1 shows
   Questionnaires / Reports / Technical documents, study 150 shows Reports /
   Technical documents / Other Materials. Recorded as a free string; an enum
   would reject an unseen legend on a corpus that already validates.
7. **Downloads mislabel their content type.** ``download/6420`` serves
   ``Content-Type: application/octet-stream`` with ``Content-Disposition:
   attachment; filename="Schedule_68_1_0_type1.pdf"`` and a PDF 1.5 body.
   Filename and extension come from ``data-filename`` or the disposition
   header, never from the content type.
8. **Methodology is in the API**, at
   ``study_desc.method.data_collection.sampling_procedure``.
9. Search: ``ps`` is page size, ``page`` advances, ``collection=`` filters to a
   repository, ``sk=`` is free text.
10. Source metadata can be internally inconsistent — study 1 is titled
    "July 2011 - June 2012" while its ``time_method`` reads "July 2007-June
    2008". Both are recorded as found; neither is corrected.

**TLS on censusindia.gov.in**: that host serves the leaf certificate with no
intermediate (verified 2026-07-31 — openssl reports "unable to verify the first
certificate"). ``curl`` succeeds because it chases the AIA extension to fetch
the missing intermediate; Python does not, so ``requests`` fails verification.
Build a bundle carrying ``emSign SSL CA - G1`` and point ``REQUESTS_CA_BUNDLE``
at it. Do not disable verification.

**Microdata files themselves are login-gated** (``/catalog/{id}/get-microdata``
redirects to a login form) and are deliberately out of scope: this module
acquires no credentials and implements no login. That is a posture, not an
unfinished feature — do not "fix" it by adding one.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from . import textparse
from .base import safe_filename_segment
from .http_client import make_session

DEFAULT_BASE_URL = "https://microdata.gov.in/NADA"
#: Per-study download bound. Study 150 alone lists 63 resources, so ten studies
#: would mean 600 files nobody asked for.
DEFAULT_MAX_DOCS_PER_STUDY = 25


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _slug(value: str) -> str:
    return safe_filename_segment(value, collapse=True)


class NadaApiError(RuntimeError):
    pass


class NadaClient:
    """Thin typed wrapper over one NADA instance's catalogue routes."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        sleep: float = 2.0,
        session: Any = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api = f"{self.base_url}/index.php/api/catalog"
        self.pages = f"{self.base_url}/index.php/catalog"
        self.session = session or make_session()
        self.sleep = sleep

    def _get_json(self, url: str, params: dict | None = None) -> dict:
        resp = self.session.get(url, params=params, timeout=60)
        try:
            payload = resp.json()
        except ValueError as exc:
            raise NadaApiError(
                f"{url} did not return JSON (HTTP {resp.status_code}) — "
                "a maintenance or error page, not a catalogue response"
            ) from exc
        if isinstance(payload, dict) and payload.get("status") == "failed":
            raise NadaApiError(f"{url}: {payload.get('message')}")
        return payload

    def collections(self) -> list[dict]:
        payload = self._get_json(f"{self.api}/collections")
        return payload.get("collections") or []

    def search(
        self,
        *,
        collection: str | None = None,
        query: str | None = None,
        max_studies: int,
    ) -> tuple[list[dict], int]:
        """Enumerate studies, stopping at *max_studies*.

        Returns ``(rows, total_found)``. The total is what makes a bound
        teachable — without it the caller can only say "stopped", not "3 more
        available, continue with this command".

        Bounded by construction: there is no "fetch everything" call, because
        an unbounded walk of a government catalogue should be something an
        operator asked for rather than something they inherited from a default.
        """
        rows: list[dict] = []
        found = 0
        page = 1
        # The page size MUST stay constant for the whole walk. NADA's offset is
        # (page - 1) * ps, so shrinking ps as the bound fills up while page
        # advances addresses inconsistent windows: a walk of 7 with a server
        # capping at 3 rows went offset 0 -> 4 -> 2, skipping one study
        # entirely and returning another twice. The bound is applied by
        # truncating at the end, never by narrowing the window mid-walk.
        page_size = max(1, min(50, max_studies))
        while len(rows) < max_studies:
            params: dict[str, Any] = {"ps": page_size, "page": page}
            if collection:
                params["collection"] = collection
            if query:
                params["sk"] = query
            payload = self._get_json(f"{self.api}/search", params)
            result = payload.get("result") or {}
            try:
                found = int(result.get("found") or 0)
            except (TypeError, ValueError):
                found = 0
            batch = result.get("rows") or []
            if not batch:
                break
            rows.extend(batch)
            if len(batch) < page_size:
                # Short page. Either the catalogue ended, or the server did not
                # honour `ps` — and the two are indistinguishable from here.
                # Advancing would address offset (page)*ps and skip whatever sat
                # between, so stop. NADA honours `ps` (verified at 1, 2, 3, 200),
                # so in practice this is the end of the catalogue.
                break
            page += 1
            if len(rows) < max_studies:
                time.sleep(self.sleep)
        return rows[:max_studies], found

    def study(self, idno: str) -> dict:
        payload = self._get_json(f"{self.api}/{idno}")
        dataset = payload.get("dataset")
        if not isinstance(dataset, dict):
            raise NadaApiError(f"{idno}: response carried no dataset object")
        if dataset.get("idno") != idno:
            raise NadaApiError(
                f"{idno}: response carried idno {dataset.get('idno')!r} — refusing "
                "a payload that does not answer the request (unknown subroutes "
                "return the study body with HTTP 200)"
            )
        return dataset

    def resources(self, catalog_id: Any) -> tuple[list[dict], str, str | None]:
        """Return ``(resources, status, error)`` for one study's documents.

        ``status`` is ``"ok"`` or ``"unavailable"``. The distinction is
        load-bearing: study 40 answers HTTP 500 while its neighbours answer
        200, and a failed page reported as an empty document list is a silent
        success — the failure class this package keeps shipping.
        """
        url = f"{self.pages}/{catalog_id}/related-materials"
        try:
            resp = self.session.get(url, timeout=60)
            resp.raise_for_status()
            rows = parse_resources(resp.text)
        except (OSError, RuntimeError, NadaApiError) as exc:
            # Deliberately NOT a bare `except Exception`. A defect inside the
            # parser (AttributeError, TypeError, ...) would then be reported
            # forever as "the portal is unavailable" — unfalsifiable, and it
            # sends whoever investigates to the wrong system. Transport and
            # protocol failures are "we do not know"; our own bugs are not.
            return [], "unavailable", f"{type(exc).__name__}: {exc}"
        for row in rows:
            if row.get("url"):
                row["url"] = absolute_url(self.base_url, row["url"])
        return rows, "ok", None

    def variables(self, idno: str) -> dict | None:
        """The variable listing, or None if it could not be obtained.

        None rather than {}: an empty dict written to disk and pointed at by
        `variables_path` would make "the endpoint failed" and "this study has
        no variables" produce an identical, confident record.
        """
        try:
            return self._get_json(f"{self.api}/{idno}/variables")
        except (NadaApiError, OSError, RuntimeError):
            return None

    def data_files(self, idno: str) -> dict | None:
        """The data-file listing, or None if it could not be obtained."""
        try:
            return self._get_json(f"{self.api}/{idno}/data_files")
        except (NadaApiError, OSError, RuntimeError):
            return None


class _ResourceParser(HTMLParser):
    """Read the related-materials resource table.

    Deliberately an HTMLParser and not a regex: PR #72 replaced a non-greedy
    pattern in ``visible_text()`` because it stopped at the first close tag and
    leaked nested content. The same trap applies here.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict] = []
        self._legend: str | None = None
        self._in_legend = False
        self._in_info = False
        self._info_id: str | None = None
        self._info_text: list[str] = []
        self._pending: dict | None = None
        self.saw_container = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag == "fieldset" or "resources" in classes:
            self.saw_container = True
        if tag == "legend":
            self._in_legend = True
            self._legend = ""
            return
        if tag == "span" and "resource-info" in classes:
            self._flush()
            self._in_info = True
            self._info_id = a.get("id")
            self._info_text = []
            return
        if tag == "a" and self._pending is not None:
            href = a.get("href") or ""
            if "/download/" in href:
                # First anchor wins: the same resource is linked more than once
                # per row. `setdefault` would be wrong here — the keys exist
                # with a None value, so it would never assign.
                if self._pending["url"] is None:
                    self._pending["url"] = href
                filename = a.get("data-filename") or a.get("title")
                if filename and self._pending["filename"] is None:
                    self._pending["filename"] = filename

    def handle_endtag(self, tag: str) -> None:
        if tag == "legend":
            self._in_legend = False
            self._legend = (self._legend or "").strip()
        elif tag == "span" and self._in_info:
            self._in_info = False
            self._pending = {
                "resource_id": self._info_id or "",
                "resource_type": self._legend or "",
                "title": " ".join("".join(self._info_text).split()),
                "url": None,
                "filename": None,
            }
        elif tag == "fieldset":
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._in_legend:
            self._legend = (self._legend or "") + data
        elif self._in_info:
            self._info_text.append(data)

    def _flush(self) -> None:
        if self._pending and self._pending.get("resource_id"):
            self.rows.append(self._pending)
        self._pending = None

    def close(self) -> None:  # noqa: D102 - inherited
        super().close()
        self._flush()


def parse_resources(html: str) -> list[dict]:
    """Parse a related-materials page into resource dicts.

    Raises :class:`NadaApiError` when *html* carries no resource container at
    all — a JSON study payload or an error page must not read as "this study
    has zero documents".
    """
    parser = _ResourceParser()
    parser.feed(html)
    parser.close()
    if not parser.saw_container:
        raise NadaApiError(
            "no resource container in the response — this is not a "
            "related-materials page, and must not be read as zero documents"
        )
    return parser.rows


def absolute_url(base_url: str, url: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", url)


class NadaProbe:
    """Acquire NADA studies and their documents into a manifested corpus.

    Two manifest kinds, one row per acquired artefact: ``nada_study`` and
    ``nada_resource``. One row per file is what the provenance contract wants
    — sha256, fetch status, URL and filename are per-file properties — and it
    matches the question a consumer asks ("every questionnaire across all NSS
    rounds"), which a nested list would force them to flatten first.
    """

    def __init__(
        self,
        out_dir: Path,
        *,
        base_url: str = DEFAULT_BASE_URL,
        sleep: float = 2.0,
        session: Any = None,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.base_url = base_url.rstrip("/")
        self.client = NadaClient(self.base_url, sleep=sleep, session=session)
        self.manifest = self.out_dir / "manifest.jsonl"
        self.source = urlparse(self.base_url).netloc

    # -- writing -----------------------------------------------------------

    def _append(self, record: dict) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def load_seen(self) -> dict[str, dict]:
        """Rows already in the manifest, keyed by `key`.

        One row per artefact: a re-run updates a row in place rather than
        appending a second one, or a consumer streaming the corpus counts every
        study and document twice. `listed` is deliberately NOT terminal — that
        is what lets a small --max-docs-per-study be raised later and pick up
        the rest instead of freezing them.
        """
        if not self.manifest.exists():
            return {}
        rows: dict[str, dict] = {}
        for line in self.manifest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("key"):
                rows[row["key"]] = row
        return rows

    def _rewrite(self, rows: list[dict]) -> None:
        tmp = self.manifest.with_suffix(".jsonl.tmp")
        tmp.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
        )
        tmp.replace(self.manifest)

    def _upsert(self, record: dict, known: dict | None = None) -> None:
        """Append a new row, or replace the existing row with this key.

        *known* is the caller's already-loaded key index. Without it this read
        the whole manifest once per row, so writing N rows moved O(N^2) bytes.
        """
        existing = self.load_seen() if known is None else known
        if record["key"] not in existing:
            self._append(record)
            if known is not None:
                known[record["key"]] = record
            return
        rows = [
            json.loads(line)
            for line in self.manifest.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self._rewrite([record if r.get("key") == record["key"] else r for r in rows])

    def _write_json(self, rel: Path, payload: Any) -> str:
        path = self.out_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        blob = json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8")
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(blob)
        tmp.replace(path)
        return hashlib.sha256(blob).hexdigest()

    # -- acquisition -------------------------------------------------------

    def acquire_study(
        self,
        idno: str,
        *,
        catalog_id: Any,
        download_docs: bool = True,
        max_docs: int = DEFAULT_MAX_DOCS_PER_STUDY,
    ) -> dict:
        slug = _slug(idno)
        dataset = self.client.study(idno)
        # `--study IDNO` has no numeric id, and the resource page is addressed
        # by the numeric id — not the idno. The study payload carries it, so
        # take it from there rather than building /catalog/None/... and
        # recording every document page as unavailable (Codex, PR #98).
        if catalog_id is None:
            catalog_id = dataset.get("id")
        metadata_rel = Path("metadata") / f"{slug}.json"
        metadata_sha = self._write_json(metadata_rel, dataset)

        # Written only when actually obtained. A path pointing at a fabricated
        # empty listing would assert "we looked and there were none".
        variables = self.client.variables(idno)
        data_files = self.client.data_files(idno)
        variables_rel = Path("variables") / f"{slug}.json" if variables is not None else None
        data_files_rel = Path("data_files") / f"{slug}.json" if data_files is not None else None
        if variables_rel is not None:
            self._write_json(variables_rel, variables)
        if data_files_rel is not None:
            self._write_json(data_files_rel, data_files)

        method = (
            dataset.get("metadata", {})
            .get("study_desc", {})
            .get("method", {})
            .get("data_collection", {})
        )
        sampling = method.get("sampling_procedure") or ""

        resources, resources_status, resources_error = self.client.resources(catalog_id)

        study_record = {
            "key": f"NADA|{self.source}|{idno}",
            "kind": "nada_study",
            "record_type": "nada_study",
            "source": self.source,
            "base_url": self.base_url,
            "idno": idno,
            "catalog_id": str(catalog_id),
            "title": dataset.get("title") or "",
            "subtitle": dataset.get("subtitle"),
            "collection": dataset.get("repositoryid"),
            "authoring_entity": dataset.get("authoring_entity"),
            "nation": dataset.get("nation"),
            "year_start": dataset.get("year_start"),
            "year_end": dataset.get("year_end"),
            "study_type": dataset.get("type"),
            "metadata_path": str(metadata_rel),
            "metadata_sha256": metadata_sha,
            # A cheap, honest signal that the written sample design is present.
            # The prose itself lives in the stored DDI payload.
            "sampling_procedure_chars": len(sampling),
            "resources_status": resources_status,
            "resources_found": len(resources),
            "variables_path": str(variables_rel) if variables_rel else None,
            "variables_count": _count(variables, "variables", "total"),
            "data_files_path": str(data_files_rel) if data_files_rel else None,
            "data_files_count": _count(data_files, "datafiles", None),
            "checked_at": _now(),
            "fetched_at": _now(),
            "error": resources_error,
        }
        seen = self.load_seen()
        self._upsert(study_record, seen)
        resource_records = []
        downloaded = 0
        for resource in resources:
            key = f"NADA|{self.source}|{idno}|{resource['resource_id']}"
            prior = seen.get(key)
            # A terminal status is not proof the bytes are still there: a
            # deleted file or a partial corpus copy would otherwise be skipped
            # forever while the manifest pointed at nothing (Codex, PR #98).
            on_disk = bool(prior and prior.get("path") and (self.out_dir / prior["path"]).exists())
            if prior and on_disk and prior.get("fetch_status") in ("downloaded", "skipped_exists"):
                # Already acquired. Report it so the caller's counts are right,
                # but do not touch the manifest — one row per artefact.
                resource_records.append(
                    {**prior, "fetch_status": "skipped_exists", "checked_at": _now()}
                )
                continue
            allow = download_docs and downloaded < max_docs
            record = self._acquire_resource(idno, slug, catalog_id, resource, allow)
            if record["fetch_status"] == "downloaded":
                downloaded += 1
            self._upsert(record, seen)
            resource_records.append(record)
            if allow:
                time.sleep(self.client.sleep)

        return {"study": study_record, "resources": resource_records}

    def _acquire_resource(
        self, idno: str, slug: str, catalog_id: Any, resource: dict, allow_download: bool
    ) -> dict:
        filename = resource.get("filename") or f"{resource['resource_id']}.bin"
        rel = Path("docs") / slug / _slug(filename)
        record = {
            "key": f"NADA|{self.source}|{idno}|{resource['resource_id']}",
            "kind": "nada_resource",
            "record_type": "nada_resource",
            "source": self.source,
            "base_url": self.base_url,
            "idno": idno,
            "catalog_id": str(catalog_id),
            "resource_id": str(resource["resource_id"]),
            "resource_type": resource.get("resource_type") or "",
            "title": resource.get("title") or "",
            "filename": filename,
            "url": resource.get("url"),
            "fetch_status": "listed",
            "path": None,
            "sha256": None,
            "bytes": None,
            "content_type": None,
            "text_path": None,
            "text_chars": None,
            "text_status": None,
            "ocr_used": None,
            # fetched_at is set ONLY when bytes were actually retrieved. Nine
            # adapters here write it on skipped_exists rows, where it means
            # "when we looked" rather than "when we fetched".
            "checked_at": _now(),
            "fetched_at": None,
            "error": None,
        }
        if not allow_download or not record["url"]:
            return record

        target = self.out_dir / rel
        if target.exists():
            blob = target.read_bytes()
            record.update(
                fetch_status="skipped_exists",
                path=str(rel),
                sha256=hashlib.sha256(blob).hexdigest(),
                bytes=len(blob),
            )
            return record

        try:
            resp = self.client.session.get(record["url"], timeout=120, stream=True)
            resp.raise_for_status()
            blob = b"".join(resp.iter_content(8192))
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_bytes(blob)
            tmp.replace(target)
        except Exception as exc:  # noqa: BLE001 - one bad file must not end the run
            record["fetch_status"] = "failed"
            record["error"] = f"{type(exc).__name__}: {exc}"
            return record

        record.update(
            fetch_status="downloaded",
            path=str(rel),
            sha256=hashlib.sha256(blob).hexdigest(),
            bytes=len(blob),
            content_type=(getattr(resp, "headers", {}) or {}).get("Content-Type"),
            fetched_at=_now(),
        )
        return record


    # -- extraction --------------------------------------------------------

    def extract_text(self, *, ocr: bool = False) -> dict:
        """Extract text from documents already on disk. Makes no network calls.

        Deliberately a second pass rather than part of acquisition: extraction
        is slow and OCR slower, so coupling them means an extraction failure
        costs the fetch progress and a re-run re-hits the portal.

        `extract_pdf_text` returns "" both for "this PDF holds no text" and for
        "every extractor failed", so a bare character count would print like
        success. `text_status` separates them, and `ocr_used` is recorded only
        because this method calls that rung itself. Which extractor *inside*
        `extract_pdf_text` succeeded is not recorded: it returns a bare string,
        so the fact is not observable, and a label must not assert more than
        was checked.
        """
        rows = [
            json.loads(line)
            for line in self.manifest.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        counts = {"extracted": 0, "ocr_recovered": 0, "empty": 0, "failed": 0, "skipped": 0}
        for row in rows:
            if row.get("kind") != "nada_resource":
                continue
            if row.get("fetch_status") not in ("downloaded", "skipped_exists"):
                counts["skipped"] += 1
                continue
            pdf = self.out_dir / (row.get("path") or "")
            if not row.get("path") or not pdf.exists():
                counts["skipped"] += 1
                continue

            text = textparse.extract_pdf_text(pdf) or ""
            used_ocr = False
            if not text.strip() and ocr:
                used_ocr = True
                text = textparse.ocr_pdf_text(pdf) or ""

            if text.strip():
                status = "ocr_recovered" if used_ocr else "extracted"
            elif used_ocr:
                status = "failed"
            else:
                status = "empty"

            if text.strip():
                rel = Path("text") / _slug(row["idno"]) / f"{row['resource_id']}.txt"
                path = self.out_dir / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
                row["text_path"] = str(rel)
            row["text_chars"] = len(text)
            row["text_status"] = status
            row["ocr_used"] = used_ocr
            counts[status] += 1

        # Rewritten in place, never appended: an append would double the
        # manifest on every re-run.
        tmp = self.manifest.with_suffix(".jsonl.tmp")
        tmp.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
        )
        tmp.replace(self.manifest)
        return counts


def _count(payload: dict | None, list_key: str, total_key: str | None) -> int | None:
    """Count entries in a listing, or None when the listing was not obtained."""
    if not payload:
        return None
    if total_key and isinstance(payload.get(total_key), int):
        return payload[total_key]
    value = payload.get(list_key)
    if isinstance(value, (list, dict)):
        return len(value)
    return None
