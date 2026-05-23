VENV   := .venv
PYTHON := $(VENV)/bin/python
PIP    := $(VENV)/bin/pip

.PHONY: dev test lint hooks clean help

$(PYTHON):
	python3 -m venv $(VENV)
	$(PIP) install -q -e ".[pdf,http,dev]"

dev: $(PYTHON)

lint: $(PYTHON)
	$(PYTHON) -m ruff check sansad_crawler/

hooks:
	cp scripts/pre-commit.sh .git/hooks/pre-commit
	chmod +x .git/hooks/pre-commit
	@echo "pre-commit hook installed."

test: $(PYTHON)
	$(PYTHON) -m pytest tests/ -v

clean:
	rm -rf $(VENV) build/ *.egg-info/

help:
	@echo "Development:"
	@echo "  make dev    — create .venv and install in editable mode (includes dev deps)"
	@echo "  make test   — run pytest"
	@echo "  make lint   — run ruff on sansad_crawler/"
	@echo "  make hooks  — install pre-commit hook (run once per clone)"
	@echo "Maintenance:"
	@echo "  make clean  — remove .venv and build artefacts"
