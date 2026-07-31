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

**Microdata files themselves are login-gated** (``/catalog/{id}/get-microdata``
redirects to a login form) and are deliberately out of scope: this module
acquires no credentials and implements no login. That is a posture, not an
unfinished feature — do not "fix" it by adding one.
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

from .http_client import make_session

DEFAULT_BASE_URL = "https://microdata.gov.in/NADA"


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
    ) -> list[dict]:
        """Enumerate studies, stopping at *max_studies*.

        Bounded by construction: there is no "fetch everything" call, because
        an unbounded walk of a government catalogue should be something an
        operator asked for rather than something they inherited from a default.
        """
        rows: list[dict] = []
        page = 1
        while len(rows) < max_studies:
            params: dict[str, Any] = {"ps": min(50, max_studies - len(rows)), "page": page}
            if collection:
                params["collection"] = collection
            if query:
                params["sk"] = query
            payload = self._get_json(f"{self.api}/search", params)
            result = payload.get("result") or {}
            batch = result.get("rows") or []
            if not batch:
                break
            rows.extend(batch)
            page += 1
        return rows[:max_studies]

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
        except Exception as exc:  # noqa: BLE001 - any failure is "we do not know"
            return [], "unavailable", f"{type(exc).__name__}: {exc}"
        for row in rows:
            if row.get("url"):
                row["url"] = absolute_url(self.base_url, row["url"])
        return rows, "ok", None

    def variables(self, idno: str) -> dict:
        try:
            return self._get_json(f"{self.api}/{idno}/variables")
        except NadaApiError:
            return {}

    def data_files(self, idno: str) -> dict:
        try:
            return self._get_json(f"{self.api}/{idno}/data_files")
        except NadaApiError:
            return {}


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
