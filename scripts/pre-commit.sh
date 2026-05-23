#!/bin/sh
set -e

RUFF=".venv/bin/ruff"

if [ ! -f "$RUFF" ]; then
  echo "pre-commit: ruff not found — run: make dev" >&2
  exit 1
fi

$RUFF check sansad_crawler/
