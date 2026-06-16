#!/bin/sh
set -e

RUFF=".venv/bin/ruff"
PYTHON=".venv/bin/python"

if [ ! -f "$RUFF" ]; then
  echo "pre-commit: ruff not found — run: make dev" >&2
  exit 1
fi

$RUFF check commoner_probe tests scripts
$PYTHON scripts/check_leaks.py --staged
