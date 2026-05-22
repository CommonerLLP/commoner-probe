# Plan: Release `sansad-crawler` on PyPI

## Context

The package is at v0.2.0 with a working CLI, test suite (256 passing), JSON
Schemas, a `Corpus` loader API, and a researcher-facing README. The
`pyproject.toml` packaging skeleton is already correct (PEP 621, setuptools
build backend, console script, optional extras, package-data for schemas).

This plan covers the remaining steps to reach a clean, credible PyPI 1.0.0
release. It is structured as a linear checklist rather than parallelisable
phases — each step should be verified before the next.

---

## What is already done

- [x] `pyproject.toml` uses PEP 621 metadata: name, version, description,
  `readme`, `requires-python`, `authors`, `license`, `keywords`, `classifiers`.
- [x] `[project.scripts]` registers the `sansad-crawl` entry-point.
- [x] `setuptools.packages.find` scoped to `sansad_crawler*`.
- [x] `[tool.setuptools.package-data]` ships `sansad_crawler/schemas/*.json`.
- [x] Optional extras declared: `[pdf]`, `[http]`, `[all]`, `[dev]`, `[pandas]`.
- [x] `requires-python = ">=3.10"` with matching classifiers.
- [x] `sansad_crawler/__init__.py` exposes `__version__ = "0.2.0"`.
- [x] `__main__.py` so `python -m sansad_crawler` works.
- [x] `CHANGELOG.md` with v0.2.0 and v0.1.0 entries.
- [x] Test suite: 256 passing, 1 skipped.
- [x] `README.md` renders as researcher-facing documentation.

---

## Blockers — must fix before any upload

### 1. Add a `LICENSE` file

`pyproject.toml` declares `PolyForm-Noncommercial-1.0.0` but there is no
`LICENSE` file in the repo. PyPI will accept the upload but:
- GitHub will not show the license badge.
- `pip` 24+ SPDX scanners flag it as missing.
- Downstream tooling (Dependabot, OSS scorecards) treats the package as
  unlicensed.

**Action**: Copy the canonical PolyForm Noncommercial 1.0.0 text from
https://polyformproject.org/licenses/noncommercial/1.0.0/ into `LICENSE`.

**Note on license choice**: PolyForm-Noncommercial is *not* OSI-approved.
This means the package will be excluded from PyPI's "Open Source" filter and
some institutional package managers will block it. If the goal is maximum
research reach, consider switching to Apache-2.0 or AGPL-3.0 — both allow
free academic use while protecting against commercial redistribution. Decide
this before uploading; the license is effectively permanent once the package
has dependents.

### 2. Add `[project.urls]`

Without project URLs the PyPI page shows no homepage, source, or issue
tracker. This makes the package look abandoned.

Add to `pyproject.toml`:

```toml
[project.urls]
Homepage = "https://github.com/CommonerLLP/sansad-crawler"
Source = "https://github.com/CommonerLLP/sansad-crawler"
Issues = "https://github.com/CommonerLLP/sansad-crawler/issues"
Documentation = "https://github.com/CommonerLLP/sansad-crawler/blob/master/README.md"
Changelog = "https://github.com/CommonerLLP/sansad-crawler/blob/master/CHANGELOG.md"
```

### 3. Verify the package name is unclaimed on PyPI

```bash
pip index versions sansad-crawler 2>&1 | head -5
```

If it returns "No matching distribution found", the name is available. Reserve
it early — even a `0.0.1` upload secures it.

---

## Strongly recommended polish (before 1.0.0)

### 4. Fix the install command in README

The README "Install" section shows:

```bash
pip install "sansad-crawler[all]"   # for users
```

After the package is on PyPI this becomes the canonical install command. Before
that, the development install is `pip install -e ".[pdf,http,dev]"`. Make sure
the README is updated to reflect the PyPI URL once the package is published.

### 5. Ship example topic files inside the package

The README quickstart references:

```bash
sansad-crawl crawl --topic examples/topics/libraries.json ...
```

After `pip install sansad-crawler[all]`, the `examples/` directory does not
exist. Researchers will hit a "file not found" error on the very first command.

**Two options**:

**Option A (simpler)**: Move the example topic files into the package as
installed data and expose a CLI helper:

```
sansad_crawler/
  example_topics/
    libraries.json
    home_affairs_starred.json
    affirmative_action.json
```

Add to `pyproject.toml`:
```toml
[tool.setuptools.package-data]
"sansad_crawler.example_topics" = ["*.json"]
```

Add a `sansad-crawl init-topic --name libraries --out ./my_topic.json`
subcommand (or just `sansad-crawl topics` to list/copy examples).

**Option B (document it)**: Keep examples as git-only and add a prominent
README note: *"Example topic files are in the git repo at `examples/topics/`;
download them from GitHub or write your own."*

Option A is better for researchers who install via pip.

### 6. Add `MANIFEST.in` or confirm sdist content

Run `python -m build --sdist` and inspect the tarball:

```bash
python -m build --sdist
tar tzf dist/sansad_crawler-0.2.0.tar.gz | sort
```

Verify that `sansad_crawler/schemas/*.json` ships (it should, via
`package-data`). Verify that `docs/SCHEMAS.md`, `CHANGELOG.md`, `LICENSE`, and
`README.md` are included. Exclude agent-only files (`.beads/`, `.ai/`,
`AGENTS.md`, `CLAUDE.md`) if you don't want them in the tarball.

A minimal `MANIFEST.in`:

```
include LICENSE
include CHANGELOG.md
include README.md
recursive-include docs *.md
prune .beads
prune .ai
prune .claude
```

### 7. Single-source the version

Currently `__version__` in `sansad_crawler/__init__.py` and `version` in
`pyproject.toml` are maintained separately. Every version bump must touch two
files.

**Option A** — read from package metadata at runtime (zero config change):

```python
# sansad_crawler/__init__.py
from importlib.metadata import version as _pkg_version
__version__ = _pkg_version("sansad-crawler")
```

**Option B** — use `setuptools-scm` to derive version from git tags
(eliminates manual version bumps entirely):

```toml
# pyproject.toml
[build-system]
requires = ["setuptools>=64", "setuptools-scm>=8"]
build-backend = "setuptools.build_meta"

[tool.setuptools_scm]
```

Option A is simpler and has no new dependencies. Option B is better for
long-term maintenance.

### 8. Add lint configuration

`pyproject.toml` currently has no `[tool.ruff]` or `[tool.mypy]` section.
Without this, the next contributor will silently reformat the codebase or
introduce type errors. A minimal `ruff` config prevents the most common issues:

```toml
[tool.ruff]
target-version = "py310"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "W", "I"]   # pycodestyle + pyflakes + isort
ignore = ["E501"]                # handled by line-length
```

### 9. Add CI (GitHub Actions)

Without CI, regressions land silently. A minimal workflow file at
`.github/workflows/ci.yml`:

```yaml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.10", "3.11", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: pip install -e ".[pdf,http,dev]"
      - run: pytest -q
```

For automated PyPI publishing on `v*` tags, add a `release.yml` using OIDC
trusted publishing (no API token stored as a secret):

```yaml
name: Release
on:
  push:
    tags: ["v*"]
jobs:
  publish:
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install build
      - run: python -m build
      - uses: pypa/gh-action-pypi-publish@release/v1
```

Set up the "pypi" environment in GitHub repo settings and configure the OIDC
trusted publisher on PyPI (no API keys needed).

### 10. Add `CONTRIBUTING.md` and `CODE_OF_CONDUCT.md`

Table stakes for an open-source civic-tech project. A one-page `CONTRIBUTING.md`
covering: how to file issues, how to run tests (`make test`), how to open a PR,
and the development install command. The Contributor Covenant is a standard
`CODE_OF_CONDUCT.md` that most open-source researchers expect.

---

## Version strategy

The current version is `0.2.0`. Options:

| Approach | First PyPI version | When to use |
|---|---|---|
| Ship now as `0.2.0` | `0.2.0` | Package is functionally complete for current scope |
| Polish first, then tag | `1.0.0` | Prefer a single "stable" signal for researchers |

Recommendation: fix the three blockers (#1 LICENSE, #2 URLs, #3 name check),
do a test upload to TestPyPI as `0.2.0`, then decide whether to call it
`1.0.0` after the polish items are done. Research libraries benefit from a
clear 1.0.0 signal — it tells users the schema is stable.

---

## Release sequence

```bash
# 1. Fix blockers: add LICENSE, [project.urls], single-source version

# 2. Run full test suite
make test

# 3. Build distributions
pip install build twine
python -m build
# Produces: dist/sansad_crawler-0.2.0-py3-none-any.whl
#           dist/sansad_crawler-0.2.0.tar.gz

# 4. Pre-flight check
twine check dist/*               # Validates README rendering and metadata

# 5. Test upload to TestPyPI
twine upload --repository testpypi dist/*

# 6. Verify the TestPyPI install
pip install -i https://test.pypi.org/simple/ "sansad-crawler[all]"
sansad-crawl --help              # Should list all 6 subcommands
sansad-crawl stats --out examples/corpora/committees-smoke/

# 7. Real release (or via GitHub Actions + OIDC trusted publishing)
twine upload dist/*
git tag v0.2.0
git push --tags

# 8. Verify on PyPI
pip install "sansad-crawler[all]"
```

---

## Open decisions (resolve before uploading)

1. **License**: PolyForm-NC vs Apache-2.0 vs AGPL-3.0. Affects who can use it.
2. **Version**: Ship as `0.2.0` or polish to `1.0.0` first?
3. **Example topics**: Bundle inside the package (Option A) or document
   as git-only (Option B)?
4. **Version sourcing**: `importlib.metadata` (Option A) or `setuptools-scm`
   (Option B)?

---

## Risks

- **Name squatting**: `sansad-crawler` is currently unclaimed on PyPI. If
  there is any delay, verify it's still available before building.
- **PolyForm-NC interpretability**: Some institution package managers and
  academic software lists automatically reject non-OSI licenses. If the
  audience is primarily academic researchers, this may reduce adoption.
- **Schema stability**: The package ships JSON Schemas as part of the wheel.
  Any breaking change to the manifest field set will require a semver-major
  bump. The README should state the stability guarantee explicitly before
  1.0.0.
