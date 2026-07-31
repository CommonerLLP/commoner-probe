"""Tests for the NADA platform adapter.

Fixtures are real responses from microdata.gov.in captured 2026-07-31; see
`tests/fixtures/nada/README.md` for exactly which are verbatim captures and
which two are hand-written. No test here touches the network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from commoner_probe import nada

FIX = Path(__file__).parent / "fixtures" / "nada"
IDNO = "DDI-IND-MOSPI-NSSO-68Rnd-Sch1.0-July2011-June2012"


class _StubResp:
    def __init__(self, status_code: int, body) -> None:
        self.status_code = status_code
        self.headers: dict = {}
        self.content = body.encode("utf-8") if isinstance(body, str) else body
        self.text = self.content.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=8192):
        for i in range(0, len(self.content), chunk_size):
            yield self.content[i:i + chunk_size]


class _StubSession:
    """Maps a URL substring to (status, body); records every URL requested."""

    def __init__(self, routes: dict) -> None:
        self.routes = routes
        self.urls: list[str] = []
        self.params: list[dict] = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        self.params.append(kwargs.get("params") or {})
        for needle, (status, body) in self.routes.items():
            if needle in url:
                return _StubResp(status, body)
        raise AssertionError(f"unexpected URL: {url}")


def _fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


def test_study_is_fetched_by_idno_not_numeric_id():
    """`/api/catalog/1` answers HTTP 400 IDNO-NOT-FOUND; the DDI idno is the key."""
    session = _StubSession({f"/api/catalog/{IDNO}": (200, _fx("study_1.json"))})
    study = nada.NadaClient(sleep=0, session=session).study(IDNO)
    assert study["idno"] == IDNO
    assert any(IDNO in u for u in session.urls)


def test_a_payload_answering_a_different_idno_is_refused():
    """Unknown API subroutes return the study payload with HTTP 200 rather than
    an error — verified: /resources and /related_materials both return bodies
    byte-identical to the bare study route. Accepting any 200 body stores the
    wrong object and reports success."""
    payload = json.loads(_fx("study_1.json"))
    payload["dataset"]["idno"] = "SOMETHING-ELSE"
    session = _StubSession({"/api/catalog/": (200, json.dumps(payload))})
    with pytest.raises(nada.NadaApiError, match="idno"):
        nada.NadaClient(sleep=0, session=session).study(IDNO)


def test_a_failure_payload_raises_with_the_message_named():
    session = _StubSession({
        "/api/catalog/": (400, _fx("study_numeric_id_error.json")),
    })
    with pytest.raises(nada.NadaApiError, match="IDNO-NOT-FOUND"):
        nada.NadaClient(sleep=0, session=session).study("1")


def test_a_body_that_is_not_json_raises_rather_than_reading_as_empty():
    session = _StubSession({"/api/catalog/": (200, "<html>maintenance</html>")})
    with pytest.raises(nada.NadaApiError, match="JSON"):
        nada.NadaClient(sleep=0, session=session).study(IDNO)


def test_search_is_bounded_by_max_studies():
    session = _StubSession({"/api/catalog/search": (200, _fx("search_nss.json"))})
    rows = nada.NadaClient(sleep=0, session=session).search(query="NSS", max_studies=1)
    assert len(rows) == 1


def test_search_passes_the_collection_and_query_filters():
    session = _StubSession({"/api/catalog/search": (200, _fx("search_nss.json"))})
    nada.NadaClient(sleep=0, session=session).search(
        collection="PLFS", query="NSS", max_studies=2
    )
    sent = session.params[0]
    assert sent["collection"] == "PLFS"
    assert sent["sk"] == "NSS"
    assert sent["ps"] == 2, "page size must not exceed the caller's bound"


def test_collections_are_listed():
    session = _StubSession({
        "/api/catalog/collections": (200, _fx("collections_trimmed.json")),
    })
    cols = nada.NadaClient(sleep=0, session=session).collections()
    assert {"repositoryid", "title"} <= set(cols[0])
    assert cols[0]["repositoryid"] == "ASI"


def test_methodology_prose_is_reachable_from_the_study_payload():
    """`sampling_procedure` is the written sample design — the reason this
    adapter reads the API at all rather than only downloading PDFs."""
    session = _StubSession({f"/api/catalog/{IDNO}": (200, _fx("study_1.json"))})
    study = nada.NadaClient(sleep=0, session=session).study(IDNO)
    collection = study["metadata"]["study_desc"]["method"]["data_collection"]
    assert len(collection["sampling_procedure"]) > 500


def test_resources_are_grouped_by_their_legend():
    rows = nada.parse_resources(_fx("related_materials_1.html"))
    assert rows, "study 1 lists resources"
    types = {r["resource_type"] for r in rows}
    assert "Questionnaires" in types
    q = next(r for r in rows if r["resource_type"] == "Questionnaires")
    assert q["resource_id"].isdigit()
    assert q["filename"].endswith(".pdf")
    assert q["url"].endswith(f"/download/{q['resource_id']}")
    assert q["title"]


def test_a_second_study_yields_its_own_legend_set():
    """Study 150 has no Questionnaires block at all — the legend set varies per
    study, so nothing may assume a fixed vocabulary."""
    rows = nada.parse_resources(_fx("related_materials_150.html"))
    types = {r["resource_type"] for r in rows}
    assert "Other Materials" in types
    assert "Questionnaires" not in types


def test_an_unseen_legend_is_kept_not_rejected():
    html = """<div class="resources"><fieldset><legend>Brand New Type</legend>
      <span class="resource-info" id="99">A title</span>
      <a href="https://x/NADA/index.php/catalog/1/download/99" data-filename="a.pdf"></a>
      </fieldset></div>"""
    rows = nada.parse_resources(html)
    assert rows[0]["resource_type"] == "Brand New Type"
    assert rows[0]["filename"] == "a.pdf"


def test_the_study_payload_is_not_mistaken_for_an_empty_resource_list():
    """Feeding the JSON study payload to the parser must raise, not read as
    'this study has zero documents'."""
    with pytest.raises(nada.NadaApiError):
        nada.parse_resources(_fx("study_1.json"))


def test_a_500_on_the_resource_page_is_unavailable_not_zero():
    """Study 40 returned HTTP 500 while 1, 2 and 150 returned 200. 'The page
    errored' and 'no documents' must not collapse into one record."""
    session = _StubSession({"/catalog/40/related-materials": (500, "<html>error</html>")})
    rows, status, error = nada.NadaClient(sleep=0, session=session).resources(40)
    assert rows == []
    assert status == "unavailable"
    assert error


def test_a_page_with_no_resources_is_ok_and_empty():
    session = _StubSession({
        "/catalog/7/related-materials": (200, '<div class="resources"></div>'),
    })
    rows, status, error = nada.NadaClient(sleep=0, session=session).resources(7)
    assert (rows, status, error) == ([], "ok", None)


def test_an_unparseable_resource_page_is_unavailable_not_empty():
    session = _StubSession({"/catalog/9/related-materials": (200, "<html>login</html>")})
    rows, status, error = nada.NadaClient(sleep=0, session=session).resources(9)
    assert rows == []
    assert status == "unavailable"
    assert error


def test_base_url_selects_the_instance():
    """The adapter is a platform adapter: censusindia.gov.in/nada runs the same
    software and must be reachable by pointing base_url at it."""
    client = nada.NadaClient("https://censusindia.gov.in/nada", sleep=0, session=_StubSession({}))
    assert client.api.startswith("https://censusindia.gov.in/nada/index.php/api/catalog")
    assert client.pages.startswith("https://censusindia.gov.in/nada/index.php/catalog")
