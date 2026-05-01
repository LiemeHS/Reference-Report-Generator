#!/usr/bin/env bash

set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-}"
REPORT_DIR="${REPORT_DIR:-.}"
VERBOSE="${VERBOSE:-0}"

if [ -z "$PYTHON_BIN" ]; then
  if [ -x ".venv/bin/python" ]; then
    PYTHON_BIN=".venv/bin/python"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  fi
fi

if [ -z "$PYTHON_BIN" ] || ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "[missing] Python interpreter: $PYTHON_BIN"
  echo "       install dependencies in a virtual environment, or set PYTHON_BIN explicitly."
  exit 1
fi

if [ "$VERBOSE" != "0" ]; then
  set -x
fi

run_step() {
  local label="$1"
  shift

  echo "[step] $label"
  "$@"
}

run_step "supply-chain hardening checks" bash -c '
  set -euo pipefail

  if grep -RInE "(^|[[:space:]])(FROM|image:)[[:space:]][^#[:space:]]+:latest([[:space:]]|$)" Dockerfile docker-compose.local.yml docker-compose.public.yml; then
    echo "[error] container image tags must not use :latest."
    exit 1
  fi

  if grep -nE "^FROM python:[0-9]+\\.[0-9]+([^0-9.]|$)" Dockerfile; then
    echo "[error] Python runtime image must pin a patch-level tag."
    exit 1
  fi

  if ! grep -qE "gem install --no-document anystyle -v [0-9]+\\.[0-9]+\\.[0-9]+" Dockerfile; then
    echo "[error] AnyStyle Ruby gem must be installed with an explicit version."
    exit 1
  fi
'

run_step "preflight: validate fastapi/starlette stack" \
  "$PYTHON_BIN" -m scripts.web_stack_preflight

run_step "dependency audit (license and manifest surface)" \
  "$PYTHON_BIN" scripts/dependency_audit.py --json-output "$REPORT_DIR/dependency_audit_report.json"

echo "[step] published-vulnerability audit"
VULNERABILITY_EXIT_CODE=0
"$PYTHON_BIN" scripts/vulnerability_audit.py --json-output "$REPORT_DIR/vulnerability_audit_report.json" || \
  VULNERABILITY_EXIT_CODE=$?

run_step "dependency path tracing for vulnerable packages" \
  bash -c "[ -f \"$REPORT_DIR/vulnerability_audit_report.json\" ] && \"${PYTHON_BIN}\" scripts/dependency_paths.py \
    --from-vuln-report \"$REPORT_DIR/vulnerability_audit_report.json\" \
    --json-output \"$REPORT_DIR/dependency_paths_report.json\" \
    --max-paths 5 \
    --max-depth 8 || true"

run_step "phase 7 ASGI smoke tests" \
  "$PYTHON_BIN" -m pytest tests/test_web_stack_preflight.py tests/test_phase7_fastapi_adapter.py

if [ "$VULNERABILITY_EXIT_CODE" != "0" ]; then
  echo "[error] published vulnerability audit detected blocking findings."
  exit "$VULNERABILITY_EXIT_CODE"
fi

echo "[ok] safety audit finished"
