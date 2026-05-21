VENV   := .venv
PYTHON := $(VENV)/bin/python
PIP    := $(VENV)/bin/pip

.PHONY: dev test clean help

$(PYTHON):
	python3 -m venv $(VENV)
	$(PIP) install -q -e ".[pdf,http]"

dev: $(PYTHON)

test: $(PYTHON)
	$(PYTHON) -m pytest tests/ -v

clean:
	rm -rf $(VENV) build/ *.egg-info/

help:
	@echo "Development:"
	@echo "  make dev    — create .venv and install in editable mode"
	@echo "  make test   — run pytest"
	@echo "Maintenance:"
	@echo "  make clean  — remove .venv and build artefacts"
