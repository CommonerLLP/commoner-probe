VENV   := .venv
PYTHON := $(VENV)/bin/python
PIP    := $(VENV)/bin/pip

.PHONY: dev test lint hooks prune clean help

$(PYTHON):
	python3 -m venv $(VENV)
	$(PIP) install -q -e ".[pdf,http,dev]"

dev: $(PYTHON)

lint: $(PYTHON)
	$(PYTHON) -m ruff check commoner_probe tests scripts

hooks:
	cp scripts/pre-commit.sh .git/hooks/pre-commit
	chmod +x .git/hooks/pre-commit
	@echo "pre-commit hook installed."

test: $(PYTHON)
	$(PYTHON) -m pytest tests/ -v

verify-release:    ## prove a published version installs from PyPI: make verify-release VERSION=0.12.0
	@test -n "$(VERSION)" || (echo "usage: make verify-release VERSION=x.y.z" && exit 2)
	python3 scripts/verify_release.py $(VERSION)

prune:             ## delete local branches already merged into master
	git fetch --prune
	git branch --merged master | grep -vE '^\*|master' | xargs -r git branch -d

clean:
	rm -rf $(VENV) build/ *.egg-info/

help:
	@echo "Development:"
	@echo "  make dev    — create .venv and install in editable mode (includes dev deps)"
	@echo "  make test   — run pytest"
	@echo "  make lint   — run ruff on commoner_probe/, tests/, and scripts/"
	@echo "  make hooks  — install pre-commit hook (run once per clone)"
	@echo "Release:"
	@echo "  make verify-release VERSION=x.y.z — prove that version installs from PyPI"
	@echo "Maintenance:"
	@echo "  make prune  — delete local branches already merged into master"
	@echo "  make clean  — remove .venv and build artefacts"
