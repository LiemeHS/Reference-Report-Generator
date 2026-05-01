#!/usr/bin/env bash

set -u

echo "Reference Gen2 environment check"
echo

status=0

check_command() {
  local command_name="$1"
  local label="$2"

  if command -v "$command_name" >/dev/null 2>&1; then
    local resolved
    resolved="$(command -v "$command_name")"
    echo "[ok] $label: $resolved"
  else
    echo "[missing] $label"
    if [ "$command_name" = "anystyle" ]; then
      echo "         if installed with --user-install, add ~/.local/share/gem/ruby/*/bin to PATH"
    fi
    status=1
  fi
}

check_python_version() {
  if ! command -v python3 >/dev/null 2>&1; then
    echo "[missing] python3"
    status=1
    return
  fi

  local version_output
  version_output="$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')" || {
    echo "[error] unable to query python3 version"
    status=1
    return
  }

  local major minor
  major="$(python3 -c 'import sys; print(sys.version_info[0])')"
  minor="$(python3 -c 'import sys; print(sys.version_info[1])')"

  if [ "$major" -gt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -ge 11 ]; }; then
    echo "[ok] python3 version: $version_output"
  else
    echo "[missing] python3.11+ required, found: $version_output"
    status=1
  fi
}

check_web_stack() {
  if [ -x ".venv/bin/python" ]; then
    if .venv/bin/python -m scripts.web_stack_preflight >/dev/null 2>&1; then
      echo "[ok] web stack compatibility preflight"
    else
      echo "[missing] web stack compatibility preflight"
      echo "         fastapi/starlette versions are not matching this project's known-safe pairing"
      echo "         run:"
      echo "           .venv/bin/python -m pip install -c constraints.txt -e \".[test]\""
      echo "           .venv/bin/python -m scripts.web_stack_preflight"
      status=1
    fi
  fi
}

check_venv() {
  if [ -x ".venv/bin/python" ]; then
    echo "[ok] local virtual environment: .venv"
  else
    echo "[missing] local virtual environment .venv"
    echo "         create it with: python3.11 -m venv .venv"
    echo "         on Debian/Ubuntu you may need: sudo apt install python3.11-venv"
    status=1
  fi
}

check_import() {
  if [ ! -x ".venv/bin/python" ]; then
    return
  fi

  if .venv/bin/python -c 'import reference_gen2; print("package import ok")' >/dev/null 2>&1; then
    echo "[ok] package import through .venv"
  else
    echo "[missing] package import through .venv"
    echo "         install with: .venv/bin/python -m pip install -e \".[test]\""
    status=1
  fi

  if .venv/bin/python -c 'import reference_gen2.reference_parsing; print("reference parsing import ok")' >/dev/null 2>&1; then
    echo "[ok] reference_parsing import through .venv"
  else
    echo "[missing] reference_parsing import through .venv"
    status=1
  fi
}

check_command "pytest" "pytest on PATH"
check_python_version
check_venv
check_import
check_web_stack
check_command "ruby" "Ruby"
check_command "gem" "RubyGems"
check_command "anystyle" "AnyStyle CLI"

echo

if [ "$status" -eq 0 ]; then
  echo "Environment looks ready."
  echo "Suggested next steps:"
  echo "  source .venv/bin/activate"
  echo "  pytest"
  echo "  anystyle --version"
else
  echo "Environment is not fully ready yet."
  echo "Recommended setup sequence:"
  echo "  python3.11 -m venv .venv"
  echo "  source .venv/bin/activate"
  echo "  python -m pip install --upgrade pip setuptools wheel"
  echo "  python -m pip install -c constraints.txt -e \".[test]\""
  echo "  bash scripts/refresh_safe_environment.sh"
  echo "  gem install --user-install anystyle"
  echo "  export PATH=\"\$HOME/.local/share/gem/ruby/*/bin:\$PATH\""
fi

exit "$status"
