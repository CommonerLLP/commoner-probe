# Contributing to `commoner-probe`

Thanks for your interest in improving this project.

## Report issues

1. Open a GitHub issue with a clear title and reproduction details.
2. Include the command(s) you ran, expected behavior, and actual behavior.
3. Add sample input/output paths or snippets when possible.

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[pdf,http,dev]"
```

## Quality checks

Run these before opening a pull request:

```bash
python -m ruff check commoner_probe tests
pytest -q
python -m build --no-isolation
python -m twine check dist/*
```

## Pull requests

1. Keep PRs focused and small when possible.
2. Add or update tests for behavior changes.
3. Update docs (`README.md`, `docs/SCHEMAS.md`) if user-facing behavior changes.
4. Reference related issues in the PR description.
5. Ensure CI passes on Python 3.10, 3.11, and 3.12.
