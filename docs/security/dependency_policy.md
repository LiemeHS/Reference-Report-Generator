# Open-Source Dependency Policy

This document is the normative dependency and license policy for
`Reference_Gen2`.

## Policy Goal

All project dependencies must be open source, or they must be explicitly held
for review before they are allowed into the project.

This policy applies to:

- direct Python dependencies declared in `pyproject.toml`
- minimal runtime dependencies declared in `requirements.txt`
- transitive Python dependencies resolved in the active environment
- dev, test, and build tooling dependencies
- required external tools used by the workflow, starting with `anystyle`

## Allowed Default License Classes

The project currently allows these common open-source licenses by default:

- `MIT`
- `BSD-2-Clause`
- `BSD-3-Clause`
- `Apache-2.0`
- `ISC`
- `Python-2.0`
- `PSF`
- `MPL-2.0`

## Review-Required Cases

A dependency must be reviewed instead of silently accepted when:

- license metadata is missing or unknown
- the detected license is copyleft or restricted, such as `GPL`, `LGPL`, or `AGPL`
- the package exposes a custom or non-standard license string
- the package exposes compound prose such as "dependency licenses" that requires
  manual review
- the dependency appears outside the documented manifests or external-tool registry
- the dependency is expected by the project but is not installed in the active environment

“Missing metadata” is not treated as proof that a dependency is non-open-source,
but it is treated as unresolved and must remain visible in the audit output.

Simple SPDX `OR` expressions are allowed only when every listed license choice
is already in the allowed default license classes. Unknown terms in an `OR`
expression keep the dependency in review.

## Dependency Boundary

Current known dependency surfaces:

- Python package dependencies from `pyproject.toml`
- minimal runtime dependencies from `requirements.txt`
- `citeproc-py`, used by Phase 6A candidate citation rendering
- `fastapi`, used only by the thin Phase 7 HTTP adapter
- `python-multipart`, used by the Phase 7 FastAPI upload endpoint for
  multipart PDF/DOCX submissions
- local packaging/build tooling used in setup workflows, including `setuptools`
  and `wheel`
- the separately installed `anystyle` CLI used by Phase 3 parsing
- frontend v1 static HTML/CSS/JS served from the FastAPI app, with no frontend
  package manager, build chain, CDN, remote fonts, analytics, or third-party
  scripts

The current environment review snapshot is tracked in
[`dependency_review.md`](./dependency_review.md).

Future additions such as DB connectors, React, Tailwind, frontend build tools,
ASGI servers, deployment images, or worker infrastructure must be added to the
same audit surface when introduced.

## Audit Workflow

Use the dependency audit helper:

```bash
.venv/bin/python scripts/dependency_audit.py --json-output dependency_audit_report.json
```

The audit produces:

- a human-readable summary on stdout
- a machine-readable JSON report when `--json-output` is supplied

The JSON report records, for each dependency:

- name
- ecosystem
- scope
- version
- source
- detected license
- homepage
- audit status
- audit reason

## Merge Guardrail

New dependencies should not be merged unless:

1. they are captured by the dependency inventory
2. their license status is visible in the audit report
3. they are either allowed by policy or explicitly held for review

The initial workflow is intentionally “inventory + warnings” rather than
hard-fail enforcement. The repo can tighten this later once the dependency list
and review process are stable.

## Published Vulnerability Workflow

License inventory is not a substitute for published-vulnerability review.
Run the separate vulnerability audit to check installed Python packages and the
AnyStyle Ruby runtime chain against machine-readable advisory data:

```bash
.venv/bin/python scripts/vulnerability_audit.py --json-output vulnerability_audit_report.json
```

The vulnerability audit is expected to:

- query a machine-readable advisory source for active version-specific findings
- record latest-known package versions for upgrade review
- surface unpinned direct dependency declarations as supply-chain review items
- fail CI on known unfixed `high`/`critical` runtime advisories
- include dependency-root path visibility for advisory packages (via
  `scripts/dependency_paths.py`) so remediation is actionable.

The maintained human-readable snapshot for this surface lives in
[`vulnerability_review.md`](./vulnerability_review.md).
