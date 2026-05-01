#!/usr/bin/env bash

set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"

if [ ! -x "$PYTHON_BIN" ]; then
  echo "[missing] Python interpreter: $PYTHON_BIN"
  echo "       expected .venv/bin/python from an existing virtual environment."
  exit 1
fi

echo "[step] upgrading packaging tooling"
"$PYTHON_BIN" -m pip install --upgrade pip setuptools wheel

echo "[step] reinstalling constrained project dependencies"
"$PYTHON_BIN" -m pip install -c constraints.txt -e ".[test]" --upgrade --force-reinstall

echo "[step] validating web stack compatibility"
"$PYTHON_BIN" -m scripts.web_stack_preflight

echo "[ok] environment refreshed to the pinned safe stack"
