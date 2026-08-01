# HTTP politeness fix + NADA platform adapter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the shared HTTP client honour 429 / `Retry-After` with jittered backoff, then add `commoner-probe nada` — a bounded NADA platform adapter that enumerates survey studies and acquires their questionnaires, methodology and documents.

**Architecture:** Task 1 changes `http_client.RetrySession._request` only; every adapter inherits it. Tasks 2–8 add one new module `commoner_probe/nada.py` (a client + an HTML resource parser + a probe), two manifest kinds registered end to end, and a CLI subcommand whose bounds are required rather than defaulted.

**Tech Stack:** Python 3.11+, stdlib `html.parser` and `urllib`, optional `requests`, `pytest`, `ruff`, JSON Schema 2020-12.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-07-31-nada-adapter-design.md`. Where this plan and the spec disagree, the spec wins; fix the plan.
- **Branch:** `feat/nada-adapter`. Never commit to `master`.
- **`pyproject.toml` declares `dependencies = []`.** `nada.py` may import only the stdlib and this package. It must never import `partial-recall` or any Layer 1 module.
- **Run everything from the repo `.venv`:** `.venv/bin/pytest`, `.venv/bin/ruff`. System `python3` is a different interpreter and its results do not count.
- **No `git commit` without Commoner's explicit word in that turn** (repo rule). Commit steps below are written for when that word is given.
- **No Co-Authored-By trailer, no AI attribution line, ever.**
- **Stage by explicit path.** Never `git add -A`.
- **Every test is verified to fail before its implementation exists.** A test that passes on the unmodified tree proves nothing.
- **HTML parsing uses `html.parser.HTMLParser`, never regex.** PR #72 replaced a non-greedy regex in `visible_text()` because it stopped at the first close tag and leaked nested content. Do not reintroduce that.
- Line length and style: match the surrounding file; `ruff` must be clean.

---

## File Structure

| file | responsibility |
|---|---|
| `commoner_probe/http_client.py` | modify `_request` retry loop + module docstring (Task 1) |
| `commoner_probe/nada.py` | NADA client, resource-page parser, probe (Tasks 2–4, 7) |
| `commoner_probe/records.py` | `ManifestNadaStudyRecord`, `ManifestNadaResourceRecord` (Task 5) |
| `commoner_probe/corpus.py` | two stream methods (Task 5) |
| `commoner_probe/validate.py` | two `_pick_schema_name` branches (Task 5) |
| `commoner_probe/schemas/manifest_nada_study.schema.json` | study schema (Task 5) |
| `commoner_probe/schemas/manifest_nada_resource.schema.json` | resource schema (Task 5) |
| `commoner_probe/cli.py` | `nada` subcommand, bounds, epilog (Task 6) |
| `tests/test_http_client.py` | retry tests (Task 1) |
| `tests/test_nada.py` | all adapter tests (Tasks 2–7) |
| `tests/fixtures/nada/` | captured live responses (Task 2) |
| `CHANGELOG.md`, `docs/SCHEMAS.md` | folded into Tasks 1 and 6 |

---

## Task 1: HTTP client honours 429 and `Retry-After`

The retry loop backs off on 5xx and network errors only. A 429 returns to the caller like a success and the next request goes out on the ordinary schedule; `Retry-After` is never read; the backoff has no jitter. This is the change every other task depends on.

**Files:**
- Modify: `commoner_probe/http_client.py:18-19` (docstring), `:269-280` (retry loop), and add module constants near `:65-67`
- Test: `tests/test_http_client.py`

**Interfaces:**
- Consumes: nothing
- Produces: `http_client.RETRY_AFTER_MAX_SEC` (float, 30.0), `http_client._retry_delay(attempt: int, resp) -> float`, and `RetrySession._request` retrying on status 429 as well as 5xx. Behaviour for every other status is unchanged.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_http_client.py`:

```python
import commoner_probe.http_client as hc


class _FakeSession:
    """Returns a scripted sequence of responses; records calls."""

    def __init__(self, statuses, headers=None):
        self._statuses = list(statuses)
        self._headers = headers or [{}] * len(statuses)
        self.calls = 0
        self.headers = {}

    def request(self, method, url, **kwargs):
        i = self.calls
        self.calls += 1
        return _FakeResp(self._statuses[i], self._headers[i])


class _FakeResp:
    def __init__(self, status_code, headers):
        self.status_code = status_code
        self.headers = headers


def _session_with(monkeypatch, statuses, headers=None):
    """A RetrySession whose transport is scripted and whose sleeps are recorded."""
    slept = []
    monkeypatch.setattr(hc.time, "sleep", lambda s: slept.append(s))
    sess = hc.RetrySession(rate_limit_sec=0)
    sess._session = _FakeSession(statuses, headers)
    return sess, slept


def test_429_is_retried_not_returned_as_success(monkeypatch):
    """429 means the portal is asking for a slower rate. Returning it to the
    caller unretried is how a polite crawler becomes a banned one."""
    sess, slept = _session_with(monkeypatch, [429, 200])
    resp = sess.get("https://example.gov.in/x", respect_robots=False)
    assert resp.status_code == 200
    assert sess._session.calls == 2
    assert slept, "a 429 must back off before retrying"


def test_retry_after_seconds_is_honoured(monkeypatch):
    sess, slept = _session_with(
        monkeypatch, [429, 200], headers=[{"Retry-After": "7"}, {}]
    )
    sess.get("https://example.gov.in/x", respect_robots=False)
    assert slept[0] == 7.0


def test_retry_after_beyond_cap_raises_instead_of_blocking(monkeypatch):
    """A portal asking for ten minutes is telling us to stop, not to sleep
    through it holding the process."""
    sess, slept = _session_with(monkeypatch, [429], headers=[{"Retry-After": "600"}])
    with pytest.raises(RuntimeError, match="Retry-After"):
        sess.get("https://example.gov.in/x", respect_robots=False)
    assert not slept


def test_backoff_is_jittered(monkeypatch):
    """Deterministic 2**attempt makes every client retry in lockstep."""
    delays = {hc._retry_delay(2, None) for _ in range(50)}
    assert len(delays) > 1, "backoff must be jittered"
    assert all(2.0 <= d <= 4.0 for d in delays), delays


def test_non_429_client_errors_are_not_retried(monkeypatch):
    """Regression guard: 404 must still return immediately."""
    sess, _ = _session_with(monkeypatch, [404, 200])
    resp = sess.get("https://example.gov.in/x", respect_robots=False)
    assert resp.status_code == 404
    assert sess._session.calls == 1


def test_persistent_429_raises_after_max_retries(monkeypatch):
    sess, _ = _session_with(monkeypatch, [429] * hc.MAX_RETRIES)
    with pytest.raises(RuntimeError, match="429"):
        sess.get("https://example.gov.in/x", respect_robots=False)
```

Add `import pytest` at the top of the file if absent.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_http_client.py -k "429 or retry_after or jitter" -v`

Expected: FAIL. `test_429_is_retried_not_returned_as_success` fails with `sess._session.calls == 1`; `_retry_delay` raises `AttributeError`.

- [ ] **Step 3: Implement**

Near the existing constants (`http_client.py:65-67`) add:

```python
RETRY_AFTER_MAX_SEC = 30.0
#: Statuses worth retrying. 429 is the portal asking for a slower rate; not
#: retrying it and continuing at the normal cadence is how a polite crawler
#: becomes a blocked one.
RETRYABLE_STATUSES = frozenset({429})
```

Add `import random` to the module imports.

Add above `class RetrySession`:

```python
def _retry_delay(attempt: int, resp: Any) -> float:
    """Seconds to wait before retry *attempt*.

    Honours ``Retry-After`` when the server sends it. Otherwise exponential
    backoff with equal jitter — half the window fixed, half random — so that
    concurrent clients do not retry in lockstep. Full jitter is not used: a
    delay that can round to zero is not a backoff.
    """
    after = None
    if resp is not None:
        raw = getattr(resp, "headers", {}) or {}
        after = raw.get("Retry-After") or raw.get("retry-after")
    if after is not None:
        try:
            seconds = float(str(after).strip())
        except ValueError:
            seconds = None  # HTTP-date form; fall through to backoff
        if seconds is not None:
            if seconds > RETRY_AFTER_MAX_SEC:
                raise RuntimeError(
                    f"server asked for Retry-After: {seconds:g}s, above the "
                    f"{RETRY_AFTER_MAX_SEC:g}s cap — stopping rather than blocking"
                )
            return max(0.0, seconds)
    base = min(30.0, 2.0 ** attempt)
    return base / 2 + random.uniform(0, base / 2)
```

Replace the retry loop body (`:269-280`) with:

```python
            for attempt in range(MAX_RETRIES):
                try:
                    resp = self._session.request(method, url, **kwargs)
                    retryable = (
                        500 <= resp.status_code < 600
                        or resp.status_code in RETRYABLE_STATUSES
                    )
                    if retryable:
                        last_exc = RuntimeError(f"HTTP {resp.status_code} {url}")
                        time.sleep(_retry_delay(attempt, resp))
                        continue
                    return resp
                except requests.RequestException as exc:
                    last_exc = exc
                    time.sleep(_retry_delay(attempt, None))
            raise last_exc or RuntimeError(f"max retries exceeded for {url}")
```

Update the module docstring at `:18-19` to state what is now true:

```
- Exponential backoff with equal jitter on 5xx, 429 and network errors: up to
  MAX_RETRIES attempts, sleep capped at 30s. `Retry-After` is honoured when
  present; a value above RETRY_AFTER_MAX_SEC raises rather than blocking the
  process. Government portals 429/503 without warning.
```

- [ ] **Step 4: Run the full http_client suite**

Run: `.venv/bin/pytest tests/test_http_client.py -v && .venv/bin/ruff check commoner_probe/http_client.py`

Expected: all PASS, ruff clean. The pre-existing robots tests must still pass untouched.

- [ ] **Step 5: Confirm nothing else regressed**

Run: `.venv/bin/pytest -q`

Expected: the full suite passes (963 baseline plus the new tests).

- [ ] **Step 6: Note the stdlib fallback limitation**

Add one line to the module docstring's stdlib-fallback bullet: the `StdlibSession` path has no retry and therefore no 429 handling; it is for zero-dependency installs only. Do not implement retry there — it is out of scope and would duplicate `requests`.

- [ ] **Step 7: CHANGELOG + commit** (needs Commoner's word)

Add under `## Unreleased` → `### Fixed`:

```markdown
- HTTP client: 429 responses are now retried with backoff instead of being
  returned to the caller as if successful, `Retry-After` is honoured (capped
  at 30s, above which the request raises rather than blocking), and the
  exponential backoff carries equal jitter.
```

```bash
git add commoner_probe/http_client.py tests/test_http_client.py CHANGELOG.md
git commit -m "fix: retry 429 and honour Retry-After in the shared HTTP client"
```

---

## Task 2: NADA client — collections, search, study metadata

**Files:**
- Create: `commoner_probe/nada.py`, `tests/test_nada.py`, `tests/fixtures/nada/`
- Test: `tests/test_nada.py`

**Interfaces:**
- Consumes: `http_client.make_session`
- Produces:
  - `nada.DEFAULT_BASE_URL = "https://microdata.gov.in/NADA"`
  - `nada.NadaApiError(RuntimeError)`
  - `nada.NadaClient(base_url: str = DEFAULT_BASE_URL, *, sleep: float = 2.0, session=None)`
  - `NadaClient.collections() -> list[dict]`
  - `NadaClient.search(*, collection: str | None = None, query: str | None = None, max_studies: int) -> list[dict]`
  - `NadaClient.study(idno: str) -> dict` (returns the `dataset` object)
  - `NadaClient.variables(idno) -> dict`, `NadaClient.data_files(idno) -> dict`

- [ ] **Step 1: Capture fixtures from the live source**

These exact calls were verified on 2026-07-31. Run them and save the bodies:

```bash
mkdir -p tests/fixtures/nada
UA="commoner-probe/0.11.0 (research)"
B="https://microdata.gov.in/NADA/index.php"
ID="DDI-IND-MOSPI-NSSO-68Rnd-Sch1.0-July2011-June2012"
curl -s -A "$UA" "$B/api/catalog/collections"        -o tests/fixtures/nada/collections.json
curl -s -A "$UA" "$B/api/catalog/search?ps=3"        -o tests/fixtures/nada/search_ps3.json
curl -s -A "$UA" "$B/api/catalog/$ID"                -o tests/fixtures/nada/study_1.json
curl -s -A "$UA" "$B/api/catalog/1"                  -o tests/fixtures/nada/study_numeric_id_error.json
curl -s -A "$UA" "$B/catalog/1/related-materials"    -o tests/fixtures/nada/related_materials_1.html
curl -s -A "$UA" "$B/catalog/2/related-materials"    -o tests/fixtures/nada/related_materials_2.html
```

Then shrink `related_materials_*.html` by hand to the `<div class="resources">` block plus one enclosing tag — the full pages are 117 KB and the tests only read that block. Keep every `<fieldset>` and at least two resources per legend.

- [ ] **Step 2: Write the failing tests**

```python
import json
from pathlib import Path

import pytest

from commoner_probe import nada

FIX = Path(__file__).parent / "fixtures" / "nada"
IDNO = "DDI-IND-MOSPI-NSSO-68Rnd-Sch1.0-July2011-June2012"


class _StubSession:
    """Maps URL substrings to (status, body). Records every URL requested."""

    def __init__(self, routes):
        self.routes = routes
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        for needle, (status, body) in self.routes.items():
            if needle in url:
                return _StubResp(status, body)
        raise AssertionError(f"unexpected URL: {url}")


class _StubResp:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.headers = {}
        self.content = body.encode() if isinstance(body, str) else body
        self.text = body if isinstance(body, str) else body.decode()

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_study_is_fetched_by_idno_not_numeric_id():
    """`/api/catalog/1` returns HTTP 400 IDNO-NOT-FOUND; the DDI idno is the key."""
    session = _StubSession({
        f"/api/catalog/{IDNO}": (200, (FIX / "study_1.json").read_text()),
    })
    client = nada.NadaClient(sleep=0, session=session)
    study = client.study(IDNO)
    assert study["idno"] == IDNO
    assert any(IDNO in u for u in session.urls)


def test_study_payload_that_answers_a_different_idno_is_rejected():
    """Unknown API subroutes return the study payload with HTTP 200 rather than
    an error, so accepting any 200 body stores the wrong object and reports
    success (verified: /resources and /related_materials both do this)."""
    payload = json.loads((FIX / "study_1.json").read_text())
    payload["dataset"]["idno"] = "SOMETHING-ELSE"
    session = _StubSession({"/api/catalog/": (200, json.dumps(payload))})
    client = nada.NadaClient(sleep=0, session=session)
    with pytest.raises(nada.NadaApiError, match="idno"):
        client.study(IDNO)


def test_study_failure_payload_raises_with_the_idno_named():
    session = _StubSession({
        "/api/catalog/": (400, (FIX / "study_numeric_id_error.json").read_text()),
    })
    client = nada.NadaClient(sleep=0, session=session)
    with pytest.raises(nada.NadaApiError, match="IDNO-NOT-FOUND"):
        client.study("1")


def test_search_is_bounded_by_max_studies():
    session = _StubSession({
        "/api/catalog/search": (200, (FIX / "search_ps3.json").read_text()),
    })
    client = nada.NadaClient(sleep=0, session=session)
    rows = client.search(max_studies=2)
    assert len(rows) == 2


def test_collections_are_listed():
    session = _StubSession({
        "/api/catalog/collections": (200, (FIX / "collections.json").read_text()),
    })
    client = nada.NadaClient(sleep=0, session=session)
    cols = client.collections()
    assert {"repositoryid", "title"} <= set(cols[0])


def test_methodology_prose_is_reachable_from_the_study_payload():
    """`sampling_procedure` is the written sample design — the reason this
    adapter reads the API at all rather than only downloading PDFs."""
    session = _StubSession({
        f"/api/catalog/{IDNO}": (200, (FIX / "study_1.json").read_text()),
    })
    study = nada.NadaClient(sleep=0, session=session).study(IDNO)
    method = study["metadata"]["study_desc"]["method"]["data_collection"]
    assert len(method["sampling_procedure"]) > 500
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_nada.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'commoner_probe.nada'`.

- [ ] **Step 4: Implement the client**

Create `commoner_probe/nada.py` with an SPDX header and a module docstring that records the source contract facts 1–10 from the spec verbatim, including that microdata itself is login-gated and deliberately out of scope.

```python
DEFAULT_BASE_URL = "https://microdata.gov.in/NADA"


class NadaApiError(RuntimeError):
    pass


class NadaClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, *, sleep: float = 2.0, session=None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api = f"{self.base_url}/index.php/api/catalog"
        self.pages = f"{self.base_url}/index.php/catalog"
        self.session = session or make_session()
        self.sleep = sleep

    def _get_json(self, url: str, params: dict | None = None) -> dict:
        r = self.session.get(url, params=params, timeout=60)
        try:
            payload = r.json()
        except ValueError as exc:
            raise NadaApiError(f"{url} did not return JSON (HTTP {r.status_code})") from exc
        if payload.get("status") == "failed":
            raise NadaApiError(f"{url}: {payload.get('message')}")
        return payload

    def study(self, idno: str) -> dict:
        payload = self._get_json(f"{self.api}/{idno}")
        dataset = payload.get("dataset")
        if not isinstance(dataset, dict):
            raise NadaApiError(f"{idno}: response carried no dataset object")
        if dataset.get("idno") != idno:
            # Unknown subroutes 200 with the study payload; a body that answers
            # a different question must never be accepted as this one's answer.
            raise NadaApiError(
                f"{idno}: response carried idno {dataset.get('idno')!r} — "
                "refusing a payload that does not answer the request"
            )
        return dataset
```

`search()` pages with `ps` and `page`, stops at `max_studies`, and sleeps `self.sleep` between pages. `collections()` reads `payload["collections"]`. `variables()` and `data_files()` call their routes and return the payload; both tolerate an absent listing by returning `{}`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_nada.py -v && .venv/bin/ruff check commoner_probe/nada.py`

Expected: PASS, ruff clean.

- [ ] **Step 6: Commit** (needs Commoner's word)

```bash
git add commoner_probe/nada.py tests/test_nada.py tests/fixtures/nada
git commit -m "feat: NADA catalogue client (collections, bounded search, study by idno)"
```

---

## Task 3: Resource-page parser

**Files:**
- Modify: `commoner_probe/nada.py`
- Test: `tests/test_nada.py`

**Interfaces:**
- Consumes: Task 2's `NadaClient`
- Produces: `nada.parse_resources(html: str) -> list[dict]` returning dicts with keys `resource_id`, `resource_type`, `title`, `filename`, `url`; and `NadaClient.resources(catalog_id: int) -> tuple[list[dict], str, str | None]` returning `(resources, status, error)` where status is `"ok"` or `"unavailable"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_resources_are_grouped_by_legend():
    html = (FIX / "related_materials_1.html").read_text()
    rows = nada.parse_resources(html)
    assert rows, "study 1 lists resources"
    assert any(r["resource_type"] == "Questionnaires" for r in rows)
    q = next(r for r in rows if r["resource_type"] == "Questionnaires")
    assert q["resource_id"].isdigit()
    assert q["filename"].endswith(".pdf")
    assert q["url"].endswith(f"/download/{q['resource_id']}")


def test_an_unseen_legend_is_kept_not_rejected():
    """resource_type is an open set: study 1 has Questionnaires, studies 2 and
    150 have Reports / Technical documents / Other Materials. A future legend
    must not be dropped or coerced."""
    html = """<div class="resources"><fieldset><legend>Brand New Type</legend>
      <span class="resource-info" id="99">A title</span>
      <a href="https://x/NADA/index.php/catalog/1/download/99" data-filename="a.pdf"></a>
      </fieldset></div>"""
    rows = nada.parse_resources(html)
    assert rows[0]["resource_type"] == "Brand New Type"


def test_the_study_payload_is_not_mistaken_for_a_resource_list():
    """Feeding parse_resources the JSON study payload must not read as
    'this study has zero documents'."""
    with pytest.raises(nada.NadaApiError):
        nada.parse_resources((FIX / "study_1.json").read_text())


def test_a_500_on_the_resource_page_is_unavailable_not_zero():
    """Study 40 returns HTTP 500 while 1, 2 and 150 return 200. 'The page
    errored' and 'this study has no documents' are different facts."""
    session = _StubSession({"/catalog/40/related-materials": (500, "<html>error</html>")})
    client = nada.NadaClient(sleep=0, session=session)
    rows, status, error = client.resources(40)
    assert rows == []
    assert status == "unavailable"
    assert error


def test_a_page_with_no_resources_is_ok_and_empty():
    session = _StubSession({
        "/catalog/7/related-materials": (200, '<div class="resources"></div>'),
    })
    rows, status, error = nada.NadaClient(sleep=0, session=session).resources(7)
    assert (rows, status, error) == ([], "ok", None)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_nada.py -k resource -v`

Expected: FAIL — `parse_resources` does not exist.

- [ ] **Step 3: Implement with `HTMLParser`**

Add a `_ResourceParser(HTMLParser)` that tracks the current `<legend>` text as `resource_type`, captures `<span class="resource-info" id=...>` text as the title, and reads the `<a>` `href` plus `data-filename`. Pair each span with the next anchor inside the same block. Do not use regex — a non-greedy pattern stops at the first close tag, which is the exact defect PR #72 removed from `visible_text()`.

`parse_resources` raises `NadaApiError` when the input contains no `<fieldset` and no `class="resources"` marker, so a JSON payload or an error page cannot read as an empty list.

`NadaClient.resources(catalog_id)` fetches `{self.pages}/{catalog_id}/related-materials`, returns `([], "unavailable", str(err))` on any 4xx/5xx or parse failure, and `(rows, "ok", None)` otherwise.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_nada.py -v && .venv/bin/ruff check commoner_probe/nada.py`

Expected: PASS, ruff clean.

- [ ] **Step 5: Commit** (needs Commoner's word)

```bash
git add commoner_probe/nada.py tests/test_nada.py
git commit -m "feat: parse NADA related-materials into typed resource rows"
```

---

## Task 4: Probe — downloads, manifest rows, `checked_at` vs `fetched_at`

**Files:**
- Modify: `commoner_probe/nada.py`
- Test: `tests/test_nada.py`

**Interfaces:**
- Consumes: Tasks 2 and 3
- Produces: `nada.NadaProbe(out_dir: Path, *, base_url=DEFAULT_BASE_URL, sleep=2.0, session=None)` with `acquire_study(idno, *, catalog_id, download_docs=True, max_docs=25) -> dict` returning `{"study": rec, "resources": [rec, ...]}`, and `NadaProbe.run(...) -> dict` returning counts `{"studies", "resources", "downloaded", "skipped", "failed", "unavailable"}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_filename_comes_from_the_attribute_not_the_content_type(tmp_path):
    """Downloads serve Content-Type: application/octet-stream even for PDFs."""
    pdf = b"%PDF-1.5\n%stub\n"
    session = _StubSession({
        f"/api/catalog/{IDNO}": (200, (FIX / "study_1.json").read_text()),
        "/related-materials": (200, (FIX / "related_materials_1.html").read_text()),
        "/download/": (200, pdf),
    })
    probe = nada.NadaProbe(tmp_path, sleep=0, session=session)
    out = probe.acquire_study(IDNO, catalog_id=1)
    res = out["resources"][0]
    assert res["filename"].endswith(".pdf")
    assert res["content_type"] == "application/octet-stream"
    assert (tmp_path / res["path"]).read_bytes().startswith(b"%PDF")
    assert res["sha256"] and res["bytes"] == len(pdf)


def test_skipped_exists_carries_checked_at_and_no_fetched_at(tmp_path):
    """Nine adapters write fetched_at on skipped_exists rows, where it means
    'when we looked'. This one does not become the tenth."""
    pdf = b"%PDF-1.5\n%stub\n"
    session = _StubSession({
        f"/api/catalog/{IDNO}": (200, (FIX / "study_1.json").read_text()),
        "/related-materials": (200, (FIX / "related_materials_1.html").read_text()),
        "/download/": (200, pdf),
    })
    probe = nada.NadaProbe(tmp_path, sleep=0, session=session)
    probe.acquire_study(IDNO, catalog_id=1)
    again = probe.acquire_study(IDNO, catalog_id=1)
    res = again["resources"][0]
    assert res["fetch_status"] == "skipped_exists"
    assert res["fetched_at"] is None
    assert res["checked_at"]


def test_a_failed_download_does_not_stop_the_run(tmp_path):
    session = _StubSession({
        f"/api/catalog/{IDNO}": (200, (FIX / "study_1.json").read_text()),
        "/related-materials": (200, (FIX / "related_materials_1.html").read_text()),
        "/download/": (500, "boom"),
    })
    probe = nada.NadaProbe(tmp_path, sleep=0, session=session)
    out = probe.acquire_study(IDNO, catalog_id=1)
    assert all(r["fetch_status"] == "failed" for r in out["resources"])
    assert all(r["error"] for r in out["resources"])
    assert out["study"]["resources_found"] == len(out["resources"])


def test_max_docs_per_study_bounds_the_downloads(tmp_path):
    pdf = b"%PDF-1.5\n"
    session = _StubSession({
        f"/api/catalog/{IDNO}": (200, (FIX / "study_1.json").read_text()),
        "/related-materials": (200, (FIX / "related_materials_1.html").read_text()),
        "/download/": (200, pdf),
    })
    probe = nada.NadaProbe(tmp_path, sleep=0, session=session)
    out = probe.acquire_study(IDNO, catalog_id=1, max_docs=1)
    downloaded = [r for r in out["resources"] if r["fetch_status"] == "downloaded"]
    assert len(downloaded) == 1


def test_no_download_docs_lists_without_fetching(tmp_path):
    session = _StubSession({
        f"/api/catalog/{IDNO}": (200, (FIX / "study_1.json").read_text()),
        "/related-materials": (200, (FIX / "related_materials_1.html").read_text()),
    })
    probe = nada.NadaProbe(tmp_path, sleep=0, session=session)
    out = probe.acquire_study(IDNO, catalog_id=1, download_docs=False)
    assert all(r["fetch_status"] == "listed" for r in out["resources"])
    assert all(r["path"] is None and r["sha256"] is None for r in out["resources"])
    assert not any("/download/" in u for u in session.urls)


def test_volatile_source_counters_are_not_recorded(tmp_path):
    session = _StubSession({
        f"/api/catalog/{IDNO}": (200, (FIX / "study_1.json").read_text()),
        "/related-materials": (200, (FIX / "related_materials_1.html").read_text()),
        "/download/": (200, b"%PDF-1.5\n"),
    })
    probe = nada.NadaProbe(tmp_path, sleep=0, session=session)
    study = probe.acquire_study(IDNO, catalog_id=1)["study"]
    assert "total_views" not in study and "total_downloads" not in study
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_nada.py -k "filename or skipped or failed_download or max_docs or no_download or volatile" -v`

Expected: FAIL — `NadaProbe` does not exist.

- [ ] **Step 3: Implement `NadaProbe`**

Write `metadata/{slug}.json`, `variables/{slug}.json`, `data_files/{slug}.json`, and `docs/{slug}/{filename}` where `slug` is `re.sub(r"[^A-Za-z0-9._-]+", "_", idno)`. Append both record kinds to `manifest.jsonl`. Emit exactly the fields listed in the spec's two tables — every field always present, `None` where not applicable, because `records._from_dict` drops undeclared keys and a field absent from the writer silently vanishes for typed consumers.

Set `fetched_at` only on a real fetch; set `checked_at` on every row. Downloads use `stream=True` and write via a temporary file plus `Path.replace()` so an interrupted download cannot leave a truncated PDF that a later run records as `skipped_exists`.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_nada.py -v && .venv/bin/ruff check commoner_probe/nada.py`

Expected: PASS, ruff clean.

- [ ] **Step 5: Commit** (needs Commoner's word)

```bash
git add commoner_probe/nada.py tests/test_nada.py
git commit -m "feat: NADA probe writes study and resource manifest rows"
```

---

## Task 5: Schemas, dataclasses, corpus streams, validate registration

An unregistered kind makes `validate` abstain and print "ok" — how `census` and `niti-annual-report` shipped with vacuous validation. `tests/test_census.py::test_every_manifest_kind_this_package_emits_is_registered_with_validate` already walks emitted kinds and will fail until this task lands.

**Files:**
- Create: `commoner_probe/schemas/manifest_nada_study.schema.json`, `commoner_probe/schemas/manifest_nada_resource.schema.json`
- Modify: `commoner_probe/records.py`, `commoner_probe/corpus.py`, `commoner_probe/validate.py`, `docs/SCHEMAS.md`
- Test: `tests/test_nada.py`

**Interfaces:**
- Consumes: Task 4's record shapes
- Produces: `records.ManifestNadaStudyRecord`, `records.ManifestNadaResourceRecord`, `Corpus.manifest_nada_studies()`, `Corpus.manifest_nada_resources()`

- [ ] **Step 1: Write the failing tests**

```python
def test_both_kinds_are_registered_with_validate():
    from commoner_probe.validate import _pick_schema_name

    assert _pick_schema_name({"kind": "nada_study"}) == "manifest_nada_study"
    assert _pick_schema_name({"kind": "nada_resource"}) == "manifest_nada_resource"


def test_a_corrupted_row_fails_validation(tmp_path):
    """The check census and niti lacked: prove the schema can reject."""
    from commoner_probe import validate

    good = _acquire_one(tmp_path)["resources"][0]
    bad = dict(good, fetch_status="teleported")
    assert validate.validate_record(good)
    assert not validate.validate_record(bad)


def test_records_round_trip_through_the_corpus_stream(tmp_path):
    from commoner_probe.corpus import Corpus

    _acquire_one(tmp_path)
    studies = list(Corpus(tmp_path).manifest_nada_studies())
    resources = list(Corpus(tmp_path).manifest_nada_resources())
    assert len(studies) == 1
    assert resources and resources[0].resource_type
    assert resources[0].checked_at


def test_every_written_field_survives_the_typed_api(tmp_path):
    """_from_dict drops unknown keys, so a field the writer emits but the
    dataclass omits vanishes for typed consumers. That has shipped three times."""
    from commoner_probe.corpus import Corpus

    raw = _acquire_one(tmp_path)["resources"][0]
    typed = next(iter(Corpus(tmp_path).manifest_nada_resources()))
    missing = set(raw) - set(vars(typed))
    assert not missing, f"declared nowhere in the dataclass: {sorted(missing)}"
```

Add a module-level `_acquire_one(tmp_path)` helper that runs the Task 4 stub flow and returns the `acquire_study` result, so the tests above do not repeat the stub wiring.

Adjust `validate.validate_record` in the test to whatever the module's actual public entry point is — read `commoner_probe/validate.py` and use the real name rather than assuming this one.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_nada.py -k "registered or corrupted or round_trip or typed_api" -v` and `.venv/bin/pytest tests/test_census.py -k every_manifest_kind -v`

Expected: both FAIL — the census walk test names `nada_study` and `nada_resource` as unregistered.

- [ ] **Step 3: Implement**

Write both schemas following `manifest_orgi_census.schema.json`: `$schema` 2020-12, a `$id` under `.../schemas/v1/`, a `description` that states what one record is, a `required` list covering every always-present field, `const` for `kind` and `record_type`, and a `pattern` on `key`.

`fetch_status` is `{"enum": ["downloaded", "skipped_exists", "listed", "failed"]}`; `resources_status` is `{"enum": ["ok", "unavailable"]}`; `text_status` is `{"enum": ["extracted", "ocr_recovered", "empty", "failed"]}` and nullable. **`resource_type` is `{"type": "string"}` with no enum** — an enum would reject an unseen `<legend>` on a corpus that already validates.

Add both dataclasses to `records.py` with the standing docstring convention, declaring every field the writer emits. Add both stream methods to `corpus.py` following `manifest_orgi_census`. Add both branches to `validate._pick_schema_name`. Document both kinds in `docs/SCHEMAS.md`.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_nada.py tests/test_census.py -v && .venv/bin/ruff check commoner_probe`

Expected: PASS, including the census walk test.

- [ ] **Step 5: Commit** (needs Commoner's word)

```bash
git add commoner_probe/schemas/manifest_nada_study.schema.json \
        commoner_probe/schemas/manifest_nada_resource.schema.json \
        commoner_probe/records.py commoner_probe/corpus.py \
        commoner_probe/validate.py docs/SCHEMAS.md tests/test_nada.py
git commit -m "feat: register nada_study and nada_resource with schemas, records and validate"
```

---

## Task 6: CLI — required bounds, teaching brake messages, worked examples

**Files:**
- Modify: `commoner_probe/cli.py`
- Test: `tests/test_nada.py`

**Interfaces:**
- Consumes: Tasks 2–5
- Produces: the `nada` subcommand and a handler `_cmd_nada(args) -> int`

- [ ] **Step 1: Write the failing tests**

```python
def test_enumeration_without_max_studies_is_refused(capsys):
    from commoner_probe import cli

    rc = cli.main(["nada", "--out", "x", "--query", "NSS"])
    assert rc != 0
    assert "--max-studies" in capsys.readouterr().err


def test_study_mode_does_not_require_max_studies(tmp_path, monkeypatch):
    from commoner_probe import cli

    monkeypatch.setattr(cli, "_nada_probe_factory", _stub_probe_factory)
    assert cli.main(["nada", "--out", str(tmp_path), "--study", IDNO]) == 0


def test_hitting_the_brake_prints_what_remains_and_how_to_continue(capsys):
    """A bound that stops silently teaches nothing."""
    ...  # run a stubbed enumeration of 5 studies with --max-studies 2
    out = capsys.readouterr().out
    assert "3 more" in out or "remaining" in out
    assert "--max-studies" in out


def test_help_carries_worked_examples():
    from commoner_probe import cli

    parser = cli.build_parser()
    epilog = _subparser(parser, "nada").epilog or ""
    assert "commoner-probe nada" in epilog
    assert epilog.count("commoner-probe nada") >= 4


def test_help_states_that_microdata_is_login_gated():
    """So nobody hunts for a flag that deliberately does not exist."""
    from commoner_probe import cli

    text = _subparser(cli.build_parser(), "nada").format_help()
    assert "login" in text.lower()
```

Write `_subparser(parser, name)` and `_stub_probe_factory` as local helpers. Read `cli.py` for the real parser-construction entry point and use its actual name rather than assuming `build_parser`.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_nada.py -k "cli or brake or help or max_studies or login" -v`

Expected: FAIL — `invalid choice: 'nada'`.

- [ ] **Step 3: Implement the subcommand**

Register `nada` with `formatter_class=argparse.RawDescriptionHelpFormatter` and an epilog:

```
Examples:
  # 1. What collections does this instance carry?
  commoner-probe nada --list-collections

  # 2. See what a bounded search would fetch, without fetching it
  commoner-probe nada --out data/nada --query NSS --max-studies 3 --dry-run

  # 3. Acquire those three studies with their questionnaires and reports
  commoner-probe nada --out data/nada --query NSS --max-studies 3

  # 4. Extract text from what was downloaded (second pass, no network)
  commoner-probe nada --out data/nada --extract-text

Another NADA instance:
  commoner-probe nada --base-url https://censusindia.gov.in/nada \
      --out data/census-nada --max-studies 5
```

The description states that the surface is a NADA (National Data Archive) instance, that `microdata.gov.in/NADA` and `censusindia.gov.in/nada` are verified, and that the microdata files themselves are login-gated and out of scope.

Every flag carries a help line, and every flag with a default shows it via `%(default)s` — including `--sleep`, which currently prints neither in the adapters that have it.

The handler refuses an enumeration run without `--max-studies`, returning non-zero with the reason on stderr. On hitting a brake it prints the remaining count and the exact command to continue, for example:

```
Stopped at the --max-studies bound: 2 of 5 matching studies acquired, 3 more available.
Continue with:  commoner-probe nada --out data/nada --query NSS --max-studies 5
```

Exit non-zero only when no study was acquired at all (convention); a partially degraded run exits 0 and prints the counts.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_nada.py -v && .venv/bin/python -m commoner_probe.cli nada --help`

Expected: PASS, and the help shows four examples with defaults visible.

- [ ] **Step 5: Docs-sync**

Run: `.venv/bin/pytest tests/test_docs_sync.py -v`. If `README.md` lists subcommands, add `nada` there in this same task — a public claim and its code change together.

- [ ] **Step 6: Commit** (needs Commoner's word)

```bash
git add commoner_probe/cli.py tests/test_nada.py README.md
git commit -m "feat: commoner-probe nada CLI with required bounds and worked examples"
```

---

## Task 7: Extraction pass

**Files:**
- Modify: `commoner_probe/nada.py`, `commoner_probe/cli.py`
- Test: `tests/test_nada.py`

**Interfaces:**
- Consumes: Task 4's manifest rows
- Produces: `NadaProbe.extract_text(*, ocr: bool = False) -> dict` returning counts, updating rows in place by `key`

- [ ] **Step 1: Write the failing tests**

```python
def test_extraction_records_empty_not_a_zero_char_success(tmp_path, monkeypatch):
    """extract_pdf_text returns "" both for 'no text' and 'every extractor
    failed'. A bare char count would print like success."""
    _acquire_one(tmp_path)
    monkeypatch.setattr(nada.textparse, "extract_pdf_text", lambda p: "")
    probe = nada.NadaProbe(tmp_path, sleep=0, session=_StubSession({}))
    probe.extract_text(ocr=False)
    row = next(r for r in _manifest_rows(tmp_path) if r["kind"] == "nada_resource")
    assert row["text_status"] == "empty"
    assert row["ocr_used"] is False


def test_ocr_rung_marks_recovered_and_records_that_it_was_used(tmp_path, monkeypatch):
    _acquire_one(tmp_path)
    monkeypatch.setattr(nada.textparse, "extract_pdf_text", lambda p: "")
    monkeypatch.setattr(nada.textparse, "ocr_pdf_text", lambda p, **k: "recovered text")
    probe = nada.NadaProbe(tmp_path, sleep=0, session=_StubSession({}))
    probe.extract_text(ocr=True)
    row = next(r for r in _manifest_rows(tmp_path) if r["kind"] == "nada_resource")
    assert row["text_status"] == "ocr_recovered"
    assert row["ocr_used"] is True
    assert row["text_chars"] == len("recovered text")


def test_ocr_that_also_returns_nothing_is_failed(tmp_path, monkeypatch):
    _acquire_one(tmp_path)
    monkeypatch.setattr(nada.textparse, "extract_pdf_text", lambda p: "")
    monkeypatch.setattr(nada.textparse, "ocr_pdf_text", lambda p, **k: "")
    probe = nada.NadaProbe(tmp_path, sleep=0, session=_StubSession({}))
    probe.extract_text(ocr=True)
    row = next(r for r in _manifest_rows(tmp_path) if r["kind"] == "nada_resource")
    assert row["text_status"] == "failed"


def test_extraction_makes_no_network_calls(tmp_path, monkeypatch):
    _acquire_one(tmp_path)
    monkeypatch.setattr(nada.textparse, "extract_pdf_text", lambda p: "some text")
    session = _StubSession({})   # any request raises AssertionError
    nada.NadaProbe(tmp_path, sleep=0, session=session).extract_text()
    assert session.urls == []


def test_extraction_is_rerunnable_without_duplicating_rows(tmp_path, monkeypatch):
    _acquire_one(tmp_path)
    monkeypatch.setattr(nada.textparse, "extract_pdf_text", lambda p: "some text")
    probe = nada.NadaProbe(tmp_path, sleep=0, session=_StubSession({}))
    probe.extract_text()
    before = len(_manifest_rows(tmp_path))
    probe.extract_text()
    assert len(_manifest_rows(tmp_path)) == before
```

Add `_manifest_rows(tmp_path)` reading `manifest.jsonl` into a list of dicts.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_nada.py -k extract -v`

Expected: FAIL — `NadaProbe` has no `extract_text`.

- [ ] **Step 3: Implement**

`extract_text` reads `manifest.jsonl`, selects `nada_resource` rows whose `fetch_status` is `downloaded` or `skipped_exists`, runs `textparse.extract_pdf_text`, falls through to `textparse.ocr_pdf_text` only when `ocr=True` and the first result is empty, writes `text/{slug}/{resource_id}.txt`, and rewrites the manifest with rows updated in place by `key` — read all rows, update, write to a temporary file, `replace()`. Never append, or a re-run duplicates every row.

Do not record which rung inside `extract_pdf_text` succeeded: it returns a bare string, so the adapter cannot observe it, and a label must not assert more than was checked. `ocr_used` is observable because the probe calls that rung itself.

Wire `--extract-text` and `--ocr` in `cli.py` as a mode that makes no network calls.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_nada.py -v && .venv/bin/ruff check commoner_probe`

Expected: PASS, ruff clean.

- [ ] **Step 5: Commit** (needs Commoner's word)

```bash
git add commoner_probe/nada.py commoner_probe/cli.py tests/test_nada.py
git commit -m "feat: opt-in text extraction pass over downloaded NADA documents"
```

---

## Task 8: Live verification

A green mocked suite says nothing about the source. Every fixture above was captured once; the source can have moved since.

**Files:** none changed unless a defect is found.

- [ ] **Step 1: Full suite and lint**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check commoner_probe tests`

Expected: PASS, clean.

- [ ] **Step 2: Dry-run against the live instance**

Run: `.venv/bin/python -m commoner_probe.cli nada --out /tmp/nada-verify --query NSS --max-studies 3 --dry-run`

Expected: three studies listed with their document counts; nothing written to disk beyond the directory.

- [ ] **Step 3: Acquire three NSS studies**

Run: `.venv/bin/python -m commoner_probe.cli nada --out /tmp/nada-verify --query NSS --max-studies 3`

Then check, and record the actual numbers rather than asserting they are fine:

```bash
jq -r 'select(.kind=="nada_study") | [.idno, .resources_status, .resources_found, .sampling_procedure_chars] | @tsv' /tmp/nada-verify/manifest.jsonl
jq -r 'select(.kind=="nada_resource") | [.resource_type, .fetch_status, .bytes] | @tsv' /tmp/nada-verify/manifest.jsonl | sort | uniq -c
```

Expected: `sampling_procedure_chars` greater than zero on at least one study; at least one `Questionnaires` row with `fetch_status: downloaded`.

- [ ] **Step 4: Prove a downloaded questionnaire is a real PDF**

```bash
find /tmp/nada-verify/docs -name '*.pdf' | head -1 | xargs file
find /tmp/nada-verify/docs -name '*.pdf' | head -1 | xargs shasum -a 256
```

Expected: `PDF document`, and the sha256 matches that row's `sha256` in the manifest. Compare them explicitly; do not eyeball.

- [ ] **Step 5: Verify the `unavailable` path against the study that actually fails**

Study 40's related-materials page returned HTTP 500 on 2026-07-31. Acquire it by idno and confirm the study row carries `resources_status: "unavailable"` with an error, and that a study row was still written. If study 40 now returns 200, find another failing study or record that the case could not be reproduced live — do not claim the path was verified when it was not.

- [ ] **Step 6: Extraction pass**

Run: `.venv/bin/python -m commoner_probe.cli nada --out /tmp/nada-verify --extract-text`

Expected: at least one row at `text_status: "extracted"` with a non-trivial `text_chars`. Open one `.txt` and confirm it reads as questionnaire text, not mojibake.

- [ ] **Step 7: Validate the corpus**

Run: `.venv/bin/python -m commoner_probe.cli validate --out /tmp/nada-verify`

Expected: passes. Then corrupt one row's `fetch_status` in a copy and confirm it **fails** — a validator that cannot fail is not a validator.

- [ ] **Step 8: Re-run to confirm resume is cheap**

Run the Step 3 command again. Expected: `skipped_exists` on every document, no re-download, `fetched_at` null on those rows, exit 0.

- [ ] **Step 9: Record the numbers**

Append the measured counts to `TODO.md` archive and `memory/session-log.md`, and note explicitly anything that could not be verified live.

**No bulk run.** Acquiring the full 187-study catalogue, or anything on the Census instance's 40,254, is a separate decision by Commoner.

---

## Self-review

**Spec coverage:** module/boundary → Task 2; six source-contract facts → Tasks 2 (2, 3, 9), 3 (4, 5, 6), 4 (7), 2 (8, 10); bounded-by-construction → Tasks 4 (`--max-docs-per-study`) and 6 (required `--max-studies`, brake message, `--dry-run`); politeness gap → Task 1; help text → Task 6; outputs and both manifest tables → Tasks 4 and 5; `checked_at`/`fetched_at` → Task 4; extraction pass and its four statuses → Task 7; registration → Task 5; error-handling table → Tasks 2, 3, 4, 6; testing list → Tasks 2–7; live verification → Task 8.

**Two things the implementer must read rather than assume:** the real name of `validate`'s record-checking entry point (Task 5 Step 1) and of `cli.py`'s parser builder (Task 6 Step 1). Both are written here as plausible names, and both must be replaced with what the modules actually export.
