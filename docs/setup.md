# Development Setup

This repository standardizes on Python `3.11+`, a local `.venv`, editable
installation from `pyproject.toml`, `citeproc-py` for Phase 6A citation
rendering, FastAPI plus multipart upload support for the Phase 7 HTTP adapter,
and a separately installed `anystyle` CLI for end-to-end parsing work.

The normative dependency and license policy lives in
[`docs/security/dependency_policy.md`](./security/dependency_policy.md). That policy covers
direct, transitive, dev/test/build, and required external-tool dependencies.

## Canonical Workflow

Create and activate the environment:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

If `python3.11 -m venv .venv` fails on Debian or Ubuntu with an `ensurepip` or
`python3.x-venv` error, install the matching OS package first and then recreate
the virtual environment:

```bash
sudo apt install python3.11-venv
```

Upgrade packaging tools and install the project in editable mode:

```bash
python -m pip install --upgrade pip setuptools wheel
python -m pip install -c constraints.txt -e ".[test]"
```

Verify the package import:

```bash
python -c "import reference_gen2; print('package import ok')"
```

Run the test suite:

```bash
pytest
```

If adapter tests appear to hang at first request, verify web-stack compatibility in the
active environment before debugging phase behavior:

```bash
python -m pip show fastapi starlette httpx anyio
```

A known local failure mode is `fastapi` with a very old `starlette` (or vice versa),
which can block `fastapi.testclient.TestClient` request dispatch. `starlette 1.x`
is not safe for this project's current FastAPI line and should be treated as an
environment blocker.

You can also run:

```bash
.venv/bin/python -m scripts.web_stack_preflight
```

or:

```bash
.venv/bin/pytest tests/test_web_stack_preflight.py
```

For stall triage, run:

```bash
.venv/bin/python scripts/profile_test_runtime.py --timeout 20
```

This runs a short, per-module pass over critical suites, hard-stops on long
runs, and prints slowest-test candidates for immediate triage.

If your local stack is mismatched, repair the environment using:

```bash
bash scripts/refresh_safe_environment.sh
```

Run the full safety gate (preflight, dependency/vulnerability audit, and adapter smoke tests):

```bash
bash scripts/safety_audit.sh
```

For vulnerability triage, trace advisory packages back to declared roots in the same
environment:

```bash
.venv/bin/python scripts/dependency_audit.py --json-output dependency_audit_report.json
.venv/bin/python scripts/vulnerability_audit.py --json-output vulnerability_audit_report.json
.venv/bin/python scripts/dependency_paths.py \
  --from-vuln-report vulnerability_audit_report.json \
  --json-output dependency_paths_report.json
```

`scripts/dependency_paths.py` reads the same manifest roots used by the audit
helpers, then resolves dependency chains and prints concrete path lines such as
`fastapi -> python-multipart`.

## AnyStyle CLI Requirement

The Python environment does not install the parser backend executable. Phase 3
parsing expects a working local `anystyle` CLI.

Verify Ruby is available:

```bash
ruby --version
gem --version
```

Install AnyStyle:

```bash
gem install anystyle
```

If you do not have permission to install gems system-wide, use a user-local
install instead:

```bash
gem install --user-install anystyle
```

Verify the CLI:

```bash
anystyle --version
```

If `anystyle` is not on `PATH`, parsing features will fail at runtime unless
you explicitly point the app to the executable. For a user-local gem install,
you may also need to add the Ruby gem bin directory to `PATH`, for example:

```bash
export PATH="$HOME/.local/share/gem/ruby/3.2.0/bin:$PATH"
```

## Relevant Environment Variables

These settings are defined in
[`reference_gen2/api/settings.py`](../reference_gen2/api/settings.py):

- `REFERENCE_GEN2_ANYSTYLE_ENABLED`
  Enables or disables AnyStyle-backed parsing. Defaults to `true`.
- `REFERENCE_GEN2_ANYSTYLE_EXECUTABLE`
  Executable name or absolute path used to invoke AnyStyle. Defaults to
  `anystyle`.
- `REFERENCE_GEN2_ANYSTYLE_PARSE_ARGS`
  Extra CLI arguments inserted before `--stdout -f json parse ...`.
- `REFERENCE_GEN2_ANYSTYLE_TIMEOUT_SEC`
  Timeout for parser subprocess execution. Defaults to `15`.
- `REFERENCE_GEN2_REPORT_SERVING_TMP_DIR`
  Private directory for Phase 7 rendered report artifacts. Defaults to
  `/tmp/reference_gen2_reports`.
- `REFERENCE_GEN2_REPORT_SERVING_JOB_DIR`
  Private directory for Phase 7 job state and queued worker artifacts.
  Defaults to `/tmp/reference_gen2_jobs`.
- `REFERENCE_GEN2_REPORT_SERVING_TTL_SECONDS`
  TTL for unserved Phase 7 report artifacts. Defaults to `3600`.
- `REFERENCE_GEN2_API_EXECUTION_BACKEND`
  Phase 7 execution mode. Use `sync` for inline execution or `worker` to queue
  jobs for `reference_gen2.api.phase7_worker`. Defaults to `sync`.
- `REFERENCE_GEN2_LOCAL_DB_PATH`
  Server-owned SQLite local search database path used by the Phase 7 upload
  orchestration endpoint. This is never accepted from an HTTP request.
- `REFERENCE_GEN2_API_MAX_REQUEST_BYTES`
  Maximum HTTP request body size accepted by the Phase 7 adapter. Defaults to
  `REFERENCE_GEN2_UPLOAD_MAX_BYTES` and must not exceed it.
- `REFERENCE_GEN2_PHASE7_WORKER_POLL_SECONDS`
  Worker polling interval for queued jobs. Defaults to `2`.
- `REFERENCE_GEN2_PHASE7_WORKER_CONCURRENCY`
  Number of same-node worker loops to run in one worker process. Defaults to
  `1`; the Docker Compose worker defaults this to `2`. The global active job
  cap is still controlled by `REFERENCE_GEN2_API_MAX_CONCURRENT_JOBS`.
- `REFERENCE_GEN2_PHASE7_DB_WARMUP_ENABLED`
  Enables a bounded, one-shot, read-only SQLite warmup when the Phase 7 worker
  starts and no jobs are already queued. Defaults to `0`.
- `REFERENCE_GEN2_PHASE7_DB_WARMUP_MAX_SECONDS`
  Maximum time budget for the worker startup DB warmup. Defaults to `3`.
- `REFERENCE_GEN2_CORS_ALLOWED_ORIGINS`
  Comma-separated list of allowed frontend origins for Phase 7. Defaults to an
  empty list, which emits no permissive CORS headers.

Example override:

```bash
export REFERENCE_GEN2_ANYSTYLE_EXECUTABLE="$HOME/.local/bin/anystyle"
```

## Verification Checklist

Use the checks below to separate Python setup problems from parser-runtime
problems.

Python package health:

```bash
python -c "import reference_gen2; print('package import ok')"
```

Core test health:

```bash
pytest
```

Parser CLI availability:

```bash
anystyle --version
```

Parser import health with the venv active:

```bash
python -c "import reference_gen2.reference_parsing; print('reference parsing import ok')"
```

Optional smoke check against the real CLI:

```bash
python - <<'PY'
from reference_gen2.reference_parsing import parse_reference

result = parse_reference("Smith, J. (2020). Example title.")
print(result.parser_backend, result.parser_model_used)
PY
```

If this smoke check fails with `anystyle_unconfigured` or
`anystyle_execution_failed`, the Python package is installed but the CLI runtime
is not available yet.

## Bootstrap Helper

For a quick non-destructive check of common prerequisites, run:

```bash
bash scripts/bootstrap_dev.sh
```

The script verifies Python, venv health, package imports, `pytest`, Ruby, and
the AnyStyle CLI, and prints next steps when something is missing.

## Dependency Audit

Dependency compliance currently includes:

- Python dependencies declared in `pyproject.toml`
- the minimal runtime list in `requirements.txt`
- `citeproc-py` for Phase 6A candidate citation rendering
- `fastapi` for the Phase 7 HTTP adapter
- `python-multipart` for Phase 7 multipart PDF/DOCX uploads
- `httpx` for Phase 7 adapter tests
- frontend v1 static HTML/CSS/JS, served by FastAPI without npm, React,
  Tailwind, CDN assets, or remote fonts
- local build/setup tooling such as `setuptools` and `wheel`
- the separately installed `anystyle` CLI

Generate an audit summary plus machine-readable JSON with:

```bash
.venv/bin/python scripts/dependency_audit.py --json-output dependency_audit_report.json
```

The audit starts in inventory-and-warning mode. Unknown, missing, custom, or
copyleft license states are surfaced for review rather than silently accepted.

Generate the published-vulnerability audit with:

```bash
.venv/bin/python scripts/vulnerability_audit.py --json-output vulnerability_audit_report.json
```

That audit checks installed Python packages and the local AnyStyle runtime
chain against machine-readable advisory data, records latest-known package
versions, and surfaces unpinned direct dependencies for review.

## Phase 7 Local Run Modes

Run the API with synchronous execution:

```bash
REFERENCE_GEN2_API_EXECUTION_BACKEND=sync \
python -m uvicorn reference_gen2.api.phase7_app:app --host 127.0.0.1 --port 8000
```

Run the API plus background worker with queued execution:

```bash
REFERENCE_GEN2_API_EXECUTION_BACKEND=worker \
python -m uvicorn reference_gen2.api.phase7_app:app --host 127.0.0.1 --port 8000
```

```bash
python -m reference_gen2.api.phase7_worker
```

For local Docker usage, see [`docker-compose.local.yml`](../docker-compose.local.yml).
For public server hosting, see [`docs/public_deployment.md`](./public_deployment.md).
