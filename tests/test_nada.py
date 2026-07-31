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
    rows, found = nada.NadaClient(sleep=0, session=session).search(query="NSS", max_studies=1)
    assert len(rows) == 1
    assert found == 129, "the total is what lets a bound say how much is left"


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


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------

PDF = b"%PDF-1.5\n%stub payload\n"


def _routes(*, docs: bool = True, rm_status: int = 200):
    routes = {
        f"/api/catalog/{IDNO}/variables": (200, json.dumps({"total": 2, "variables": []})),
        f"/api/catalog/{IDNO}/data_files": (200, json.dumps({"datafiles": {}})),
        f"/api/catalog/{IDNO}": (200, _fx("study_1.json")),
        "/related-materials": (rm_status, _fx("related_materials_1.html")),
    }
    if docs:
        routes["/download/"] = (200, PDF)
    return routes


def _acquire_one(tmp_path, **kwargs):
    """Acquire study 1 into tmp_path through the stubbed transport."""
    session = _StubSession(_routes())
    probe = nada.NadaProbe(tmp_path, sleep=0, session=session)
    return probe.acquire_study(IDNO, catalog_id=1, **kwargs)


def _manifest_rows(tmp_path) -> list[dict]:
    path = Path(tmp_path) / "manifest.jsonl"
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def test_filename_comes_from_the_attribute_not_the_content_type(tmp_path):
    """Downloads serve Content-Type: application/octet-stream even for PDFs, so
    the extension must come from data-filename / Content-Disposition."""
    out = _acquire_one(tmp_path)
    res = out["resources"][0]
    assert res["filename"].endswith(".pdf")
    assert (Path(tmp_path) / res["path"]).read_bytes().startswith(b"%PDF")
    assert res["sha256"] and res["bytes"] == len(PDF)


def test_a_re_run_refreshes_checked_at_but_keeps_when_the_bytes_arrived(tmp_path):
    """checked_at moves, fetched_at does not: the bytes were retrieved in the
    first run and that is when they were retrieved. Nine adapters here overwrite
    fetched_at on a re-run, where it silently comes to mean 'when we looked'."""
    first = _acquire_one(tmp_path)["resources"][0]
    again = _acquire_one(tmp_path)["resources"][0]
    assert again["fetch_status"] == "skipped_exists"
    assert again["fetched_at"] == first["fetched_at"]
    assert again["checked_at"] >= first["checked_at"]


def test_a_file_on_disk_with_no_manifest_row_is_skipped_without_a_fetch_time(tmp_path):
    """The other skipped_exists case: bytes are present but this tool has no
    record of fetching them, so fetched_at stays null rather than asserting a
    time nobody observed."""
    _acquire_one(tmp_path)
    (Path(tmp_path) / "manifest.jsonl").unlink()
    again = _acquire_one(tmp_path)["resources"][0]
    assert again["fetch_status"] == "skipped_exists"
    assert again["fetched_at"] is None
    assert again["checked_at"]


def test_re_running_acquisition_does_not_duplicate_manifest_rows(tmp_path):
    """Found by running it live: a second pass appended a second row for every
    study and every document, so a consumer streaming the corpus counted each
    artefact twice. Seven other adapters here use load_seen() for exactly this."""
    _acquire_one(tmp_path)
    first = len(_manifest_rows(tmp_path))
    _acquire_one(tmp_path)
    assert len(_manifest_rows(tmp_path)) == first


def test_a_re_run_still_reports_the_documents_as_skipped(tmp_path):
    """Not appending a row must not mean the caller loses the count."""
    _acquire_one(tmp_path)
    again = _acquire_one(tmp_path)
    assert again["resources"]
    assert all(r["fetch_status"] == "skipped_exists" for r in again["resources"])


def test_listed_is_not_terminal_so_a_bigger_bound_fetches_the_rest(tmp_path):
    """This is what makes a small --max-docs-per-study safe rather than
    punitive: documents left `listed` by the bound are picked up on a re-run
    with a larger one, while `downloaded` ones are not re-fetched."""
    _acquire_one(tmp_path, max_docs=1)
    first = [r for r in _manifest_rows(tmp_path) if r["kind"] == "nada_resource"]
    assert sum(r["fetch_status"] == "downloaded" for r in first) == 1
    assert sum(r["fetch_status"] == "listed" for r in first) >= 1

    _acquire_one(tmp_path, max_docs=3)
    second = [r for r in _manifest_rows(tmp_path) if r["kind"] == "nada_resource"]
    assert len(second) == len(first), "still one row per document"
    # 1 from the first run plus 3 more: the bound is per run, not a running total.
    assert sum(r["fetch_status"] == "downloaded" for r in second) == 4


def test_a_downloaded_row_does_carry_fetched_at(tmp_path):
    res = _acquire_one(tmp_path)["resources"][0]
    assert res["fetch_status"] == "downloaded"
    assert res["fetched_at"] and res["checked_at"]


def test_a_failed_download_does_not_stop_the_run(tmp_path):
    session = _StubSession({**_routes(docs=False), "/download/": (500, "boom")})
    probe = nada.NadaProbe(tmp_path, sleep=0, session=session)
    out = probe.acquire_study(IDNO, catalog_id=1)
    assert out["resources"], "the resources were still listed"
    assert all(r["fetch_status"] == "failed" for r in out["resources"])
    assert all(r["error"] for r in out["resources"])
    assert out["study"]["resources_found"] == len(out["resources"])


def test_max_docs_per_study_bounds_the_downloads(tmp_path):
    out = _acquire_one(tmp_path, max_docs=1)
    downloaded = [r for r in out["resources"] if r["fetch_status"] == "downloaded"]
    assert len(downloaded) == 1
    assert len(out["resources"]) > 1, "the rest are still listed, just not fetched"


def test_no_download_docs_lists_without_fetching(tmp_path):
    session = _StubSession(_routes(docs=False))
    probe = nada.NadaProbe(tmp_path, sleep=0, session=session)
    out = probe.acquire_study(IDNO, catalog_id=1, download_docs=False)
    assert all(r["fetch_status"] == "listed" for r in out["resources"])
    assert all(r["path"] is None and r["sha256"] is None for r in out["resources"])
    assert not any("/download/" in u for u in session.urls)


def test_an_unavailable_resource_page_still_writes_a_study_row(tmp_path):
    session = _StubSession(_routes(rm_status=500))
    probe = nada.NadaProbe(tmp_path, sleep=0, session=session)
    out = probe.acquire_study(IDNO, catalog_id=1)
    assert out["study"]["resources_status"] == "unavailable"
    assert out["study"]["error"]
    assert out["resources"] == []
    assert any(r["kind"] == "nada_study" for r in _manifest_rows(tmp_path))


def test_methodology_length_is_recorded_on_the_study_row(tmp_path):
    study = _acquire_one(tmp_path)["study"]
    assert study["sampling_procedure_chars"] > 500


def test_volatile_source_counters_are_not_recorded(tmp_path):
    study = _acquire_one(tmp_path)["study"]
    assert "total_views" not in study
    assert "total_downloads" not in study


def test_both_kinds_are_registered_with_validate():
    """An unregistered kind makes `validate` abstain and print "ok" — how
    `census` and `niti-annual-report` shipped with vacuous validation."""
    from commoner_probe.validate import _pick_schema_name

    assert _pick_schema_name({"kind": "nada_study"}) == "manifest_nada_study"
    assert _pick_schema_name({"kind": "nada_resource"}) == "manifest_nada_resource"


def test_the_written_corpus_validates(tmp_path):
    from commoner_probe.validate import validate_corpus

    _acquire_one(tmp_path)
    assert validate_corpus(tmp_path, log=lambda _m: None)


def test_a_corrupted_row_fails_validation(tmp_path):
    """The check census and niti lacked: prove the schema can actually reject.
    A validator that cannot fail is not a validator."""
    from commoner_probe.validate import validate_corpus

    _acquire_one(tmp_path)
    manifest = Path(tmp_path) / "manifest.jsonl"
    rows = _manifest_rows(tmp_path)
    for row in rows:
        if row["kind"] == "nada_resource":
            row["fetch_status"] = "teleported"
            break
    manifest.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert not validate_corpus(tmp_path, log=lambda _m: None)


def test_an_unseen_resource_type_still_validates(tmp_path):
    """resource_type is an open set by design — a new <legend> must not fail a
    corpus that was valid yesterday."""
    from commoner_probe.validate import validate_corpus

    _acquire_one(tmp_path)
    manifest = Path(tmp_path) / "manifest.jsonl"
    rows = _manifest_rows(tmp_path)
    for row in rows:
        if row["kind"] == "nada_resource":
            row["resource_type"] = "Some Legend Nobody Has Seen"
    manifest.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert validate_corpus(tmp_path, log=lambda _m: None)


def test_records_round_trip_through_the_corpus_streams(tmp_path):
    from commoner_probe.corpus import Corpus

    _acquire_one(tmp_path)
    studies = list(Corpus(tmp_path).manifest_nada_studies())
    resources = list(Corpus(tmp_path).manifest_nada_resources())
    assert len(studies) == 1
    assert studies[0].idno == IDNO
    assert resources and resources[0].resource_type
    assert resources[0].checked_at


def test_every_written_field_survives_the_typed_api(tmp_path):
    """_from_dict drops unknown keys, so a field the writer emits but the
    dataclass omits vanishes for typed consumers. That has shipped three times
    in this package (user_agent, then status, then text_source)."""
    from commoner_probe.corpus import Corpus

    raw = _acquire_one(tmp_path)
    typed_study = next(iter(Corpus(tmp_path).manifest_nada_studies()))
    typed_resource = next(iter(Corpus(tmp_path).manifest_nada_resources()))
    assert not set(raw["study"]) - set(vars(typed_study))
    assert not set(raw["resources"][0]) - set(vars(typed_resource))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _run_cli(argv: list[str]):
    from commoner_probe import cli

    args = cli.build_parser().parse_args(argv)
    return args.func(args)


def _nada_subparser():
    from commoner_probe import cli

    parser = cli.build_parser()
    actions = [a for a in parser._actions if hasattr(a, "choices") and a.choices]
    return actions[0].choices["nada"]


class _FakeProbe:
    """Stands in for NadaProbe so the CLI can be exercised without a network."""

    last: "_FakeProbe | None" = None

    def __init__(self, out_dir, **kwargs):
        self.out_dir = out_dir
        self.kwargs = kwargs
        self.acquired: list[str] = []
        self.client = self
        self.sleep = 0
        _FakeProbe.last = self

    # NadaClient surface the CLI uses
    def collections(self):
        return [{"repositoryid": "ASI", "title": "Annual Survey of Industries"}]

    def search(self, *, collection=None, query=None, max_studies):
        rows = [{"idno": f"STUDY-{i}", "id": str(i), "title": f"Study {i}"} for i in range(1, 6)]
        return rows[:max_studies], len(rows)

    def resources(self, catalog_id):
        return [], "ok", None

    def acquire_study(self, idno, *, catalog_id, download_docs=True, max_docs=25):
        self.acquired.append(idno)
        return {
            "study": {"idno": idno, "resources_status": "ok", "resources_found": 0},
            "resources": [],
        }


@pytest.fixture()
def fake_probe(monkeypatch):
    monkeypatch.setattr(nada, "NadaProbe", _FakeProbe)
    monkeypatch.setattr(nada, "NadaClient", lambda *a, **k: _FakeProbe("x"))
    return _FakeProbe


def test_enumeration_without_max_studies_is_refused(tmp_path, fake_probe):
    """No invocation walks a government catalogue because a flag was forgotten."""
    with pytest.raises(SystemExit) as exc:
        _run_cli(["nada", "--out", str(tmp_path), "--query", "NSS"])
    assert "--max-studies" in str(exc.value)


def test_study_mode_does_not_require_max_studies(tmp_path, fake_probe):
    _run_cli(["nada", "--out", str(tmp_path), "--study", IDNO])
    assert _FakeProbe.last.acquired == [IDNO]


def test_hitting_the_brake_reports_what_remains_and_how_to_continue(tmp_path, fake_probe, capsys):
    """A bound that stops silently teaches nothing."""
    _run_cli(["nada", "--out", str(tmp_path), "--query", "NSS", "--max-studies", "2"])
    err = capsys.readouterr().err
    assert "3 more" in err
    assert "commoner-probe nada" in err and "--max-studies" in err


def test_the_continue_command_suggests_a_step_not_the_whole_catalogue(
    tmp_path, monkeypatch, capsys
):
    """Found live: against a 40,254-study catalogue the brake helpfully
    suggested `--max-studies 40254`, which is the opposite of the point. The
    suggestion must be the next step, with the total named separately."""

    class _BigCatalogue(_FakeProbe):
        def search(self, *, collection=None, query=None, max_studies):
            rows = [{"idno": f"S-{i}", "id": str(i)} for i in range(max_studies)]
            return rows, 40254

    monkeypatch.setattr(nada, "NadaProbe", _BigCatalogue)
    _run_cli(["nada", "--out", str(tmp_path), "--query", "NSS", "--max-studies", "2"])
    err = capsys.readouterr().err
    assert "40252 more" in err
    assert "--max-studies 40254" not in err
    assert "--max-studies 4" in err, "suggest the next step: double what was asked"


def test_help_carries_worked_examples():
    epilog = _nada_subparser().epilog or ""
    assert epilog.count("commoner-probe nada") >= 4


def test_help_states_that_microdata_is_login_gated():
    """So nobody hunts for a flag that deliberately does not exist."""
    text = _nada_subparser().format_help().lower()
    assert "login" in text


def test_help_shows_the_sleep_default():
    text = _nada_subparser().format_help()
    assert "--sleep" in text and "2.0" in text


def test_a_tls_failure_is_a_message_not_a_traceback(tmp_path, monkeypatch):
    """Found live against censusindia.gov.in, which serves an incomplete
    certificate chain: the CLI dumped a urllib3 traceback. An operator needs to
    be told what to do, not shown a stack."""

    class _TlsFails(_FakeProbe):
        def acquire_study(self, *a, **k):
            raise OSError(
                "HTTPSConnectionPool(host='censusindia.gov.in', port=443): "
                "certificate verify failed: unable to get local issuer certificate"
            )

    monkeypatch.setattr(nada, "NadaProbe", _TlsFails)
    with pytest.raises(SystemExit) as exc:
        _run_cli(["nada", "--out", str(tmp_path), "--study", IDNO])
    message = str(exc.value)
    assert "certificate" in message
    assert "REQUESTS_CA_BUNDLE" in message, "say how to fix it, not just that it broke"


def test_a_plain_network_failure_is_also_a_message(tmp_path, monkeypatch):
    class _NetFails(_FakeProbe):
        def acquire_study(self, *a, **k):
            raise OSError("Connection timed out")

    monkeypatch.setattr(nada, "NadaProbe", _NetFails)
    with pytest.raises(SystemExit) as exc:
        _run_cli(["nada", "--out", str(tmp_path), "--study", IDNO])
    assert "REQUESTS_CA_BUNDLE" not in str(exc.value), "do not suggest an irrelevant fix"


def test_out_is_required_for_anything_that_writes(fake_probe):
    with pytest.raises(SystemExit) as exc:
        _run_cli(["nada", "--study", IDNO])
    assert "--out" in str(exc.value)


# ---------------------------------------------------------------------------
# Extraction pass
# ---------------------------------------------------------------------------


def _probe_for(tmp_path):
    return nada.NadaProbe(tmp_path, sleep=0, session=_StubSession({}))


def test_extraction_records_empty_not_a_zero_char_success(tmp_path, monkeypatch):
    """extract_pdf_text returns "" both for 'no text in this PDF' and for
    'every extractor failed'. A bare character count would print like success."""
    _acquire_one(tmp_path)
    monkeypatch.setattr(nada.textparse, "extract_pdf_text", lambda p: "")
    _probe_for(tmp_path).extract_text(ocr=False)
    row = next(r for r in _manifest_rows(tmp_path) if r["kind"] == "nada_resource")
    assert row["text_status"] == "empty"
    assert row["ocr_used"] is False


def test_ocr_rung_marks_recovered_and_records_that_it_was_used(tmp_path, monkeypatch):
    _acquire_one(tmp_path)
    monkeypatch.setattr(nada.textparse, "extract_pdf_text", lambda p: "")
    monkeypatch.setattr(nada.textparse, "ocr_pdf_text", lambda p, **k: "recovered text")
    _probe_for(tmp_path).extract_text(ocr=True)
    row = next(r for r in _manifest_rows(tmp_path) if r["kind"] == "nada_resource")
    assert row["text_status"] == "ocr_recovered"
    assert row["ocr_used"] is True
    assert row["text_chars"] == len("recovered text")


def test_ocr_that_also_returns_nothing_is_failed(tmp_path, monkeypatch):
    _acquire_one(tmp_path)
    monkeypatch.setattr(nada.textparse, "extract_pdf_text", lambda p: "")
    monkeypatch.setattr(nada.textparse, "ocr_pdf_text", lambda p, **k: "")
    _probe_for(tmp_path).extract_text(ocr=True)
    row = next(r for r in _manifest_rows(tmp_path) if r["kind"] == "nada_resource")
    assert row["text_status"] == "failed"


def test_a_successful_extraction_writes_the_text_and_its_path(tmp_path, monkeypatch):
    _acquire_one(tmp_path)
    monkeypatch.setattr(nada.textparse, "extract_pdf_text", lambda p: "questionnaire text")
    _probe_for(tmp_path).extract_text()
    row = next(r for r in _manifest_rows(tmp_path) if r["kind"] == "nada_resource")
    assert row["text_status"] == "extracted"
    assert row["text_chars"] == len("questionnaire text")
    assert (Path(tmp_path) / row["text_path"]).read_text() == "questionnaire text"


def test_extraction_makes_no_network_calls(tmp_path, monkeypatch):
    _acquire_one(tmp_path)
    monkeypatch.setattr(nada.textparse, "extract_pdf_text", lambda p: "some text")
    session = _StubSession({})  # any request raises AssertionError
    nada.NadaProbe(tmp_path, sleep=0, session=session).extract_text()
    assert session.urls == []


def test_extraction_is_rerunnable_without_duplicating_rows(tmp_path, monkeypatch):
    """Rows are updated in place by key. Appending would double the manifest on
    every re-run."""
    _acquire_one(tmp_path)
    monkeypatch.setattr(nada.textparse, "extract_pdf_text", lambda p: "some text")
    probe = _probe_for(tmp_path)
    probe.extract_text()
    before = len(_manifest_rows(tmp_path))
    probe.extract_text()
    assert len(_manifest_rows(tmp_path)) == before


def test_extraction_leaves_the_study_rows_untouched(tmp_path, monkeypatch):
    _acquire_one(tmp_path)
    before = [r for r in _manifest_rows(tmp_path) if r["kind"] == "nada_study"]
    monkeypatch.setattr(nada.textparse, "extract_pdf_text", lambda p: "some text")
    _probe_for(tmp_path).extract_text()
    after = [r for r in _manifest_rows(tmp_path) if r["kind"] == "nada_study"]
    assert before == after


def test_the_extracted_corpus_still_validates(tmp_path, monkeypatch):
    from commoner_probe.validate import validate_corpus

    _acquire_one(tmp_path)
    monkeypatch.setattr(nada.textparse, "extract_pdf_text", lambda p: "some text")
    _probe_for(tmp_path).extract_text()
    assert validate_corpus(tmp_path, log=lambda _m: None)


def test_the_ddi_metadata_is_stored_on_disk_with_its_hash(tmp_path):
    study = _acquire_one(tmp_path)["study"]
    stored = Path(tmp_path) / study["metadata_path"]
    assert stored.exists()
    assert json.loads(stored.read_text())["idno"] == IDNO
    assert study["metadata_sha256"]
