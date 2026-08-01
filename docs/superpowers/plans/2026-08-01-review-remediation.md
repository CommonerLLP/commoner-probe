# Review Remediation Plan — commoner-probe

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:executing-plans`
> (or `superpowers:subagent-driven-development`) to work through this
> task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Close the fifteen findings from the 2026-08-01 `xhigh` review, in
severity order, starting with the two that are already live on PyPI.

**Source of the findings:** `/code-review xhigh`, run against `master` at
`151fd95`, framed as "how organised, well-maintained, secure, safe, easy to
understand, well-commented and well-documented is this package". Six were
re-verified against the source by hand before this plan was written; those are
marked **CONFIRMED**. The other nine are marked **PLAUSIBLE** — read the named
lines before changing them, and drop any that turn out to be wrong.

---

## Global constraints

Copied verbatim from the rules already in force in this repo. Every task
inherits all of them.

- **`dependencies = []` is binding.** The core package installs with no
  third-party packages. Anything that needs `requests`, `bs4`, `lxml`, `xlrd`
  or `pdfminer` goes behind an extra. A Layer 0 acquisition path must never
  import Layer 1.
- **Pre-1.0 versioning (`ROADMAP.md:45`):** a new acquisition surface **or any
  breaking change** is a minor bump; only backwards-compatible fixes take a
  patch.
- **The release ritual (`ROADMAP.md:30`):** bump `pyproject.toml`, CHANGELOG
  entry, `pip install -e .` in the repo `.venv`, branch + PR, tag, then
  **`make verify-release VERSION=x.y.z`**, then move consumer pins. The verify
  step is not optional and a green workflow does not replace it.
- **Branch + PR for everything.** Never commit to `master`. Branch names
  `<type>/<slug>`, no agent prefix.
- **Never commit or push without explicit permission in the turn.**
- **Repo-local `.venv` only.** `.venv/bin/python -m pytest`, not system python.
- **Every check must be shown failing on the input it exists to catch.** This
  is the standing lesson from the session that produced these findings: three
  checks written that day passed CI and could not fail. After writing a test,
  construct the input it exists to catch and confirm it goes red before making
  it green. A test that has never failed has not been tested.

---

## Ordering, and why

The two external findings come first because they are on PyPI right now, in
0.12.1, in front of every installer. Everything after that is ordered by blast
radius.

| # | task | finding | release |
|---|---|---|---|
| 1 | Remove the internal credential path from the wheel | CONFIRMED | **0.12.2**, ship alone |
| 2 | Guard the zero-dependency session | CONFIRMED | 0.13.0 |
| 3 | Gate the release and test the default install | CONFIRMED ×2 | 0.13.0 |
| 4 | Make `validate` fail closed | CONFIRMED | 0.13.0 |
| 5 | Make `write_pdf` atomic | CONFIRMED | 0.13.0 |
| 6 | The nine PLAUSIBLE findings | verify each first | 0.13.0 or later |

**Two releases, not one.** Task 1 is a small, unambiguous patch and should not
wait behind the guard work. Tasks 2–5 change behaviour on paths that currently
succeed, so under `ROADMAP.md:45` they take the **minor** slot: 0.13.0. Do not
let Task 1 drift into that release — the point of splitting is that the leak
stops shipping today rather than at the end of the plan.

---

## Task 1 — Remove the internal credential path from the published wheel

**Finding (CONFIRMED, `census.py:59`).** `KEY_HINT`, a hardcoded path inside a private sibling repo,
is in the wheel on PyPI. Verified by unzipping
`dist/commoner_probe-0.12.1-py3-none-any.whl` and reading the shipped
`census.py`: the internal path and a private sibling repo's name are both present.

Two distinct problems, and the second is the worse one:

1. **Disclosure.** A published artefact names an internal repo, an internal
   secrets directory, and tells strangers "the org already keeps a registered
   data.gov.in key there". The org's own pre-publish scan rule names exactly
   this class.
2. **The search.** `resolve_api_key` (`census.py:174-181`) iterates
   `Path(__file__).resolve().parents` and reads any file at that hardcoded relative path
   it finds. Installed into site-packages, that walk covers
   `.../site-packages`, `.../python3.x`, `/usr/lib`, `/usr`, and `/` — the
   package reads and parses arbitrary files outside its own tree looking for a
   credential.

**Files:**
- Modify: `commoner_probe/census.py:55-59` (the constant and its comment),
  `commoner_probe/census.py:170-185` (`resolve_api_key`)
- Test: `tests/test_census.py`

**Interfaces:** `resolve_api_key(explicit: str | None = None) -> str` keeps its
signature. Resolution order becomes: `explicit` → `DATA_GOV_IN_KEY` →
`COMMONER_PROBE_KEY_FILE` (a full path to a `KEY=VALUE` file, read only if the
operator set it) → raise `CensusApiError`.

- [x] **Step 1: Write the failing tests**

```python
def test_resolve_api_key_does_not_walk_outside_the_package(tmp_path, monkeypatch):
    """A credential file planted in an ancestor directory must NOT be read.

    Installed into site-packages, the old parents-walk reached /usr and /.
    """
    monkeypatch.delenv("DATA_GOV_IN_KEY", raising=False)
    monkeypatch.delenv("COMMONER_PROBE_KEY_FILE", raising=False)
    planted = Path(census.__file__).resolve().parent.parent / PRIVATE_REL_DIR
    planted.mkdir(parents=True, exist_ok=True)
    (planted / KEY_FILENAME).write_text("DATA_GOV_IN_KEY=leaked\n", encoding="utf-8")
    try:
        with pytest.raises(census.CensusApiError):
            census.resolve_api_key()
    finally:
        shutil.rmtree(planted.parent, ignore_errors=True)


def test_error_message_names_no_internal_path(monkeypatch):
    monkeypatch.delenv("DATA_GOV_IN_KEY", raising=False)
    monkeypatch.delenv("COMMONER_PROBE_KEY_FILE", raising=False)
    with pytest.raises(census.CensusApiError) as exc:
        census.resolve_api_key()
    message = str(exc.value)
    for internal in FORBIDDEN:  # see tests/test_packaging.py
        assert internal not in message


def test_explicit_key_file_is_read_when_the_operator_names_one(tmp_path, monkeypatch):
    monkeypatch.delenv("DATA_GOV_IN_KEY", raising=False)
    keyfile = tmp_path / KEY_FILENAME
    keyfile.write_text('DATA_GOV_IN_KEY="abc123"\n', encoding="utf-8")
    monkeypatch.setenv("COMMONER_PROBE_KEY_FILE", str(keyfile))
    assert census.resolve_api_key() == "abc123"
```

- [x] **Step 2: Run them and confirm the first two FAIL**

Run: `.venv/bin/python -m pytest tests/test_census.py -k "walk or internal_path or key_file" -v`
Expected: the parents-walk test fails (it reads the planted file and returns
`"leaked"`), the message test fails (the message names the private path). This is the
"show it failing" step — if the first test passes before the fix, the planted
file is in the wrong place and the test proves nothing.

- [x] **Step 3: Make the change**

```python
#: Set this to the full path of a ``KEY=VALUE`` file to read the credential
#: from disk instead of the environment. Deliberately opt-in and absolute:
#: an earlier version walked every parent of ``__file__`` looking for a
#: fixed relative path, which from site-packages meant reading files under
#: /usr and /, and which named a private directory in a public artefact.
KEY_FILE_ENV = "COMMONER_PROBE_KEY_FILE"
KEY_ENV = "DATA_GOV_IN_KEY"
```

```python
def resolve_api_key(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    if os.environ.get(KEY_ENV):
        return os.environ[KEY_ENV]
    key_file = os.environ.get(KEY_FILE_ENV)
    if key_file:
        path = Path(key_file)
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith(f"{KEY_ENV}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise CensusApiError(
        f"{KEY_ENV} is not set. Register a free key at "
        f"https://data.gov.in/apis and either export {KEY_ENV}, pass "
        f"--api-key, or point {KEY_FILE_ENV} at a file containing "
        f"{KEY_ENV}=<key>."
    )
```

- [x] **Step 4: Run the tests and confirm they PASS**

Run: `.venv/bin/python -m pytest tests/test_census.py -v`

- [x] **Step 5: Prove the string is gone from a built artefact**

```bash
rm -rf dist && .venv/bin/python -m build 2>&1 | tail -2
.venv/bin/python - <<'PY'
import glob, zipfile
whl = sorted(glob.glob("dist/*.whl"))[-1]
src = zipfile.ZipFile(whl).read("commoner_probe/census.py").decode()
for needle in FORBIDDEN:  # see tests/test_packaging.py
    assert needle not in src, f"{needle} still in {whl}"
print("clean:", whl)
PY
```

Then widen it — the review found one occurrence, not necessarily the only one:

```bash
.venv/bin/python - <<'PY'
import glob, zipfile
whl = sorted(glob.glob("dist/*.whl"))[-1]
z = zipfile.ZipFile(whl)
hits = [(n, w) for n in z.namelist() if n.endswith(".py")
        for w in FORBIDDEN  # see tests/test_packaging.py
        if w in z.read(n).decode("utf-8", "replace")]
print(hits or "no internal references in the wheel")
PY
```

- [x] **Step 6: Add that scan as a test, so it cannot come back**

Put it in `tests/test_packaging.py` operating on the source tree rather than a
built wheel (no build step in the unit suite): walk `commoner_probe/**/*.py`
and assert none of the internal markers appear. Confirm it fails by
temporarily re-adding the string.

- [x] **Step 7: Release 0.12.2**

Full ritual from `ROADMAP.md:30`. CHANGELOG entry says what it is plainly: a
published artefact named an internal path and read files outside its own tree
while looking for a credential. Then `make verify-release VERSION=0.12.2`, then
move the six consumer pins — the sweep procedure is in `notes/HANDOFF.md`.

**Ask Commoner before this step:** yanking or leaving 0.12.1 on PyPI. The
disclosure is a directory name, not a secret — no key is in the wheel — so
leaving it is defensible. That is a judgement call, not mine to make.

---

## Task 2 — Guard the zero-dependency session

**Finding (CONFIRMED, `http_client.py:344`).** `dependencies = []` means the
default `pip install commoner-probe` gets `StdlibSession`, whose `_request`
(line 239) hands the URL straight to `urllib.request.urlopen`. No
`is_safe_url()`, no scheme allowlist, no robots check, no rate limit. So
`file:///etc/passwd` is honoured by urllib's file handler and
`http://169.254.169.254/latest/meta-data/` reaches cloud metadata. Every probe
in the package calls `make_session()`, so on the advertised install path the
whole crawler runs unguarded and unthrottled against government portals.

The inline comment at line 344 states the tradeoff honestly. The module
docstring at lines 6-13 asserts the opposite, unconditionally, for every
caller: "every URL is checked against `url_safety.is_safe_url()` before the
first request". One of the two has to change, and it should be the code.

**This is not a dependency constraint.** Verified: `url_safety` imports only
`ipaddress`, `socket` and `urllib.parse`; `_get_robot_parser` (line 120) uses
`urllib.robotparser` and `urllib.request`; `_rate_limit` (line 182) uses
`time`; `_retry_delay` (line 170) uses `time` and `random`. All four are
defined **above** the `try: import requests` at line 187 and are pure stdlib.
The guards are already zero-dependency; they were simply never wired into the
fallback.

**Files:**
- Modify: `commoner_probe/http_client.py` — `StdlibSession._request` (line 239)
- Test: `tests/test_http_client.py`

- [x] **Step 1: Write the failing tests** — force the fallback by monkeypatching
      `http_client.requests` to `None` and constructing `StdlibSession`
      directly. Four cases, each asserting a raise:

```python
def test_stdlib_session_refuses_file_scheme():
    with pytest.raises(ValueError):
        http_client.StdlibSession().get("file:///etc/passwd")


def test_stdlib_session_refuses_link_local():
    with pytest.raises(ValueError):
        http_client.StdlibSession().get("http://169.254.169.254/latest/meta-data/")


def test_stdlib_session_refuses_loopback():
    with pytest.raises(ValueError):
        http_client.StdlibSession().get("http://127.0.0.1:8080/")


def test_stdlib_session_honours_robots_disallow(monkeypatch):
    # stub _get_robot_parser to return a parser that disallows everything
    ...
    with pytest.raises(PermissionError):
        http_client.StdlibSession().get("https://example.gov.in/secret")
```

- [x] **Step 2: Run and confirm all four FAIL** — they will currently attempt
      the fetch. The `file:` one should *succeed at reading the file*, which is
      the demonstration.

- [x] **Step 3: Wire the four guards into `StdlibSession._request`** — the same
      order `RetrySession` uses: `is_safe_url` → robots → rate limit → request.
      Keep `_get_robot_parser(url, user_agent=self._user_agent)` so the
      identity that checks is the identity that fetches (see Task 6a).

- [x] **Step 4: Run and confirm all four PASS, and the full suite is green**

- [x] **Step 5: Correct the module docstring** — with the guards wired in, lines
      6-13 become true for both sessions. State explicitly what the fallback
      still lacks (no retry, no cache) so the next reader is not misled the
      other way.

- [x] **Step 6: Commit**

```bash
git add commoner_probe/http_client.py tests/test_http_client.py
git commit -m "fix: apply the SSRF, robots and rate-limit guards on the zero-dependency path"
```

---

## Task 3 — Gate the release, and test the install the package advertises

Two CONFIRMED findings, one branch: nothing catches a bad release, and the
install path everything is designed around is never exercised.

**3a — `.github/workflows/release.yml:23`.** The publish job is checkout →
setup-python → build + `twine check` → `pypa/gh-action-pypi-publish`, with no
`needs:` on the test job. Any push of a `v*` tag publishes. A red suite ships;
a `v0.13.0` tag on a tree whose `pyproject.toml` says `0.12.1` publishes 0.12.1
with no error. `make verify-release` exists but runs after the fact, by hand.

**3b — `.github/workflows/ci.yml:22`.** Every matrix entry installs
`-e ".[pdf,http,dev]"`, so the zero-dependency install is never tested. A new
top-level `import requests` anywhere in the package stays green on 3.10, 3.11
and 3.12, and breaks `pip install commoner-probe` for every user.

**3c — the lint gap.** `ci.yml:24` runs `ruff check commoner_probe tests`;
`Makefile:14` runs `ruff check commoner_probe tests scripts`. `scripts/` is
linted locally and unchecked upstream.

**Files:** `.github/workflows/release.yml`, `.github/workflows/ci.yml`,
`tests/test_zero_dependency_import.py` (new)

- [x] **Step 1: Add a version-consistency check to the release workflow** — the
      tag minus its `v` must equal `pyproject.toml`'s version, and the job must
      exit non-zero on mismatch. Verify by pushing a deliberately mismatched
      tag to a scratch branch, or by running the step's script locally with a
      fake `GITHUB_REF`.

- [x] **Step 2: Make `publish` depend on tests** — either a `needs:` on a test
      job in the same workflow, or duplicate the install-and-pytest steps
      ahead of the build. Confirm by tagging a commit with a deliberately
      failing test on a scratch branch: the publish step must not run.

- [x] **Step 3: Add a bare-install CI job**

```yaml
  bare-install:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install with no extras
        run: python -m pip install .
      - name: Import every module and run the CLI
        run: |
          python -c "import commoner_probe, commoner_probe.cli; print(commoner_probe.__version__)"
          commoner-probe --help > /dev/null
```

Note the CLI import is the real test: `cli.py` imports ~39 adapters at module
level (see Task 6g), so `--help` alone transitively imports nearly the whole
package. Confirm this job can fail by adding a top-level `import requests` to a
core module and watching it go red.

- [x] **Step 4: Align the lint paths** — `ci.yml` to `commoner_probe tests
      scripts`, matching the Makefile. Fix whatever `scripts/` has been hiding.

- [x] **Step 5: Commit**

---

## Task 4 — Make `validate` fail closed

**Finding (CONFIRMED, `validate.py:161`).** An unknown `kind` is skipped
(`continue  # unknown kind — skip, don't fail`), a missing schema is skipped,
`file_ok` stays `True`, and line 191 then prints a count of **non-blank lines**
rather than of validated records. So `1500 records — ok` can mean zero were
validated, `validate_corpus` returns `True`, and `commoner-probe validate`
exits 0 in CI.

This is the repo's signature defect, and the fix keeps being applied one branch
at a time — `dc06d85` is literally "one more unvalidated manifest kind". Fix
the mechanism, not the instance.

**Files:** `commoner_probe/validate.py`, `tests/test_validate.py`

- [x] **Step 1: Write the failing test** — a manifest containing one record with
      `kind: "definitely-not-a-registered-kind"` must make `validate_corpus`
      return `False` and must not print `ok`.

- [x] **Step 2: Run it and confirm it FAILS** (currently prints
      `1 records — ok` and returns `True`).

- [x] **Step 3: Fail closed on an unrecognised kind** — an unknown kind is an
      error, not a skip. Report the kind, the line number and the file.

- [x] **Step 4: Count what was validated, not what was read** — return
      `(ok, validated_count, skipped_count)` from `_validate_file` and print
      `N of M records validated`. A number that counts lines cannot detect the
      failure it is meant to detect.

- [x] **Step 5: Derive the kind→schema mapping instead of hand-writing it** —
      build it from the schema files at import time so a new kind cannot be
      forgotten. If a schema genuinely has no kind, list it explicitly in one
      place with a comment saying why.

- [x] **Step 6: Run the whole suite plus `commoner-probe validate` against a
      real corpus on disk** — the count line must now show validated ≠ read
      where kinds are genuinely unregistered, and that gap is the finding.

- [x] **Step 7: Commit**

---

## Task 5 — Make `write_pdf` atomic

**Finding (CONFIRMED, `base.py:156`).** `write_pdf` streams to the final path
and resumes on `exists() and st_size > 1000`. An interrupt inside the
`iter_content` loop (lines 163-165) leaves a truncated PDF that every later run
treats as complete. `answers.extract_answers` then extracts partial text, which
is indistinguishable from a genuinely short ministry answer.

Five modules in this package already do this correctly — `answers.py`,
`atr_linkage.py`, `dchb_town.py`, `nada.py`, `questions_list.py` all write
`.tmp` then rename. The shared base class used by the sansad and committee
crawlers, the repo's core surface, does not.

**Files:** `commoner_probe/base.py:155-170`, `tests/test_base.py`

- [x] **Step 1: Write the failing test** — a stub session whose `iter_content`
      yields two chunks and then raises. Assert that after the exception no
      file exists at `dest_path`, and that a second call with a working session
      produces the complete file.

- [x] **Step 2: Run it and confirm it FAILS** — currently a truncated file is
      left behind and the second call returns `True` without refetching.

- [x] **Step 3: Write to `dest_path.with_suffix(dest_path.suffix + ".tmp")`,
      `os.replace` on success, and remove the temp file in a `finally` on
      failure.** Match the idiom the five existing writers use.

- [x] **Step 4: Run and confirm PASS**

- [x] **Step 5: Consider the resume heuristic separately** — `st_size > 1000`
      is a guess about PDFs, and with atomic writes it is no longer load-
      bearing for correctness. Leave it, but note in the docstring that it now
      only skips *completed* files. Do not widen scope here.

- [x] **Step 6: Commit, then release 0.13.0** with Tasks 2–5, full ritual.

---

## Task 6 — The nine PLAUSIBLE findings

**Read the named lines and confirm each before changing anything.** These came
from the review agent, not from my own read. Any that turn out to be wrong get
struck from this plan with a note, not quietly dropped.

- [x] **6a — `dchb_town.py:276`, zip-bomb guard trusts metadata.** The guard
      tests `info.file_size`, which is attacker-controlled central-directory
      metadata, then calls `archive.read()`, which decompresses fully into
      memory before any size is verified. The comment at lines 65-66 promises
      the opposite. Fix: read incrementally through `archive.open(member)` with
      a running byte ceiling. Also `district_from_zip` (line 191) and
      `ingest_district_zip` (line 421), which have no cap at all. Test with a
      real crafted archive, not a mock.

- [x] **6b — `neva.py:235`, robots checked under the wrong identity.**
      `StateAssemblyCrawler` mutates `self.session.headers["User-Agent"]` after
      construction, so pages go out as `NEVA_UA` while robots.txt is fetched
      and cached under the default `USER_AGENT`. `http_client.py:110-113`
      states the invariant this breaks. Fix: pass `user_agent` into
      `make_session()` rather than mutating headers. Grep for the same
      post-construction mutation elsewhere.

- [ ] **6c — `textparse.py:138`, `extract_pdf_text` returns `""` on any
      error.** A bare `except Exception` makes "the toolchain is missing" and
      "this PDF has no words" the same result — the failure `OcrUnavailable`'s
      own docstring (lines 36-40) exists to prevent. The OCR rung the CLAUDE.md
      contract describes is also not wired in. Decide deliberately: either wire
      OCR in and raise on toolchain failure, or correct the documented
      contract. Do not leave them disagreeing.

- [x] **6d — `http_client.py:337`, `__getattr__` forwards past the guards.**
      Only `get` and `post` are wrapped; `head`, `put`, `delete`, `patch` and
      `request` reach the bare session. The comment at lines 295-298 records
      that this trap was already hit once for `post`. Fix: wrap the remaining
      verbs, or make `__getattr__` refuse unknown request methods loudly.
      Same task: `_last_request_by_domain` and `_robot_parsers` are unbounded
      module dicts, and `_rate_limit` reads-sleeps-writes with no lock, so the
      1 req/s promise does not hold under concurrency. Either add a lock or
      document the session as single-threaded.

- [x] **6e — `nada.py:88`, eight filename sanitisers that disagree.** Only
      `base.safe_filename_segment` handles a leading dot and empty input.
      `nada._slug("..")` returns `".."`; `nada._slug("///")` returns `""`.
      Duplicated at `dchb_town.py:262`, `cag.py:246`, `cag.py:269`,
      `niti.py:183`, `doe.py:99`, `ddg.py:429`, `academia/pdf_text.py:77`.
      Fix: one function, callers updated, a test per hostile input.

- [ ] **6f — no response-size cap anywhere.** `nada.py:584` passes
      `stream=True` and then immediately `b"".join`s every chunk, defeating
      streaming. Twelve call sites listed in the finding. Fix: a shared
      `read_capped(resp, max_bytes)` in `http_client`, with a default ceiling
      and a per-caller override, raising rather than truncating.

- [ ] **6g — `cli.py:2176`, no exception handling and eager imports.** Domain
      errors and Ctrl-C reach the user as raw tracebacks, discarding the
      carefully written messages those exceptions carry (`census.py:181`,
      `http_client.py:303`, `courts.py:113`) and disclosing local paths. Fix: a
      `try/except` in `main()` mapping known exception types to exit codes and
      one-line messages, with `--traceback` to opt back in. Separately, the 39
      top-level imports at `cli.py:9-38` mean `--help` imports every adapter
      and one bad module takes down all 33 subcommands — move to per-subcommand
      lazy imports. Note this interacts with Task 3's bare-install job: lazy
      imports make that job weaker, so keep an explicit import-everything step.

- [x] **6h — `validate.py:171`, a Validator built per record.** The schema dict
      is cached and then the saving is thrown away by constructing
      `Validator(schema)` inside the loop. Cache the validator instead. Also
      line 191 and eleven identical lines re-read each file in full just to
      count records — Task 4 removes the need.

- [x] **6i — `validate.py:197`, twelve copy-pasted blocks.** The same file
      already shows the table-driven form at lines 288-301 and 304-320. Folding
      197-285 into a `{filename: schema}` dict removes ~90 lines and one class
      of maintenance error. Do this **after** Task 4, which touches the same
      region.

---

## Status — 2026-08-01

Tasks 1-5 are implemented, plus 6a, 6b, 6d, 6e, 6h and 6i. Seven commits on
six branches off `master`; nothing pushed, nothing released.

Every fix was demonstrated failing first, on the input it exists to catch, not
assumed from the review:

| finding | the demonstration |
|---|---|
| census key walk | a planted ancestor file made `resolve_api_key()` return `"leaked-by-ancestor-walk"` |
| stdlib session | `StdlibSession().get("file:///etc/hosts")` returned 213 bytes of /etc/hosts |
| release tag gate | ran on both inputs: `v0.12.1` exit 0, `v0.13.0` exit 1 |
| validate | `examples/corpora/smoke` reported "3 records — ok" having validated ZERO |
| write_pdf | after an interrupted download the retry returned True with no request made |
| slug | `nada._slug("..")` returned `".."`, `_slug("///")` returned `""` |
| NeVA UA | outgoing `neva-ua/1.0`, robots identity `commoner-probe/0.12.1` |
| zip bomb | forged header declared 1000 bytes; peak RSS grew 432 MB |

Widening Task 1's scan found the leak class was larger than the one occurrence
reported: one private sibling repo appeared 23 times and another twice,
one pointing at a gitignored internal note. `docs/_archive/` turned out to be
gitignored, so the sensitive material there was never public.

Two fixes went beyond what the review named, because fixing only the named
site would have left the defect live: `sansad.py` overrides `write_pdf` with
the identical non-atomic write, and `dchb_town` had two more uncapped
`archive.read` calls with no size check at all.

1136 passed, ruff clean, and a fresh wheel carries none of the eight internal
markers. The bare install was re-verified after the new cross-module imports:
74/74 modules import with no extras present.

### Left to do

- **6c, 6f, 6g** below — 6c is an open decision (see below), 6f and 6g are
  the two largest remaining items.
- **Release 0.12.2 and 0.13.0.** Both need explicit permission; nothing has
  been pushed, tagged or published.
- **Found while sweeping, not yet filed:** several modules pass a hardcoded
  `User-Agent: commoner-probe/0.5.0` in per-request `headers=` dicts
  (indiacode, questions_list, committees, sansad, bills). That is the same
  identity-mismatch shape as 6b — robots is evaluated for the session's UA
  while the request goes out as another — and the version string is six
  releases stale. Practical impact is likely nil, since robotparser matches on
  the `commoner-probe` token either way, but it should be checked rather than
  assumed.

---

## Finding 16 — `_is_sample_key` may not recognise anything, and no test could tell

Found on 2026-08-01 while answering "are there secrets on PyPI?" — not from
the review. **Not fixed; it needs an input this repo does not hold.**

`census._is_sample_key` guards a 1,128-district crawl: it refuses to run a
corpus pass on the shared, rate-limited public key from data.gov.in, which
would throttle part-way and read as "the source is flaky". It compares
`sha256(key)` against the shipped `SAMPLE_KEY_SHA256`.

Both tests that touch it do:

```python
monkeypatch.setattr(census, "_is_sample_key", lambda key: True)
```

They stub the predicate. So they prove the CALLER refuses a sample key, and
prove nothing about whether the predicate can recognise one. If the constant
is the digest of nothing, the guard never fires and every test still passes —
the same shape as the three checks this plan already fixed.

What is known: the constant is a digest, so it discloses nothing and is not a
secret. What is NOT known: whether it is the digest of the key data.gov.in
actually publishes. `https://www.data.gov.in/apis` returned **403** to an
automated fetch on 2026-08-01, so it could not be settled from here.

Done in the meantime: `test_is_sample_key_compares_a_digest_not_a_prefix`
exercises the predicate rather than stubbing it, pinning that an exact match
is True, a different key is False, and a key sharing the `579b464db66e` prefix
every data.gov.in key starts with is NOT treated as the sample — a prefix test
here would refuse the org's registered credential, which is how the first cut
of this module failed.

**To close it:** open `https://www.data.gov.in/apis` in a browser, copy the
published sample key, and check `sha256(key).hexdigest()` against
`SAMPLE_KEY_SHA256`. If they differ, the constant is wrong and the guard has
never fired. That is one minute of a human's time and cannot be done headless.

---

## What this plan does not do

- **No new acquisition surface.** No new adapters, no new subcommands. This is
  remediation.
- **No restructuring of the package layout.** The 75 modules stay where they
  are.
- **No epilog sweep and no `fetched_at` backfill.** Both are in
  `TODO.md` §Future and neither is urgent.
- **No change to `dependencies = []`.** Every fix here is achievable without
  it; Task 2 documents why.

## Open decisions for Commoner

1. **Yank 0.12.1 from PyPI, or leave it?** No secret is in the wheel — only a
   directory name — so leaving it is defensible. Task 1 Step 7.
2. **Is Task 2 a minor or a patch?** It makes the stdlib path *refuse* URLs it
   previously fetched and *sleep* where it previously did not. My read of
   `ROADMAP.md:45` is that this is a breaking change and therefore 0.13.0. Say
   so if you read it differently.
3. **Task 6c: wire OCR in, or correct the documented contract?** Wiring it in
   is the bigger change and adds an optional-extra dependency edge.
