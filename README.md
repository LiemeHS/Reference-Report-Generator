# Reference Gen2

Reference Gen2 is a self-hosted web app for checking bibliography and reference
lists. Users can upload a PDF/DOCX or paste reference text, and the app returns
a private, short-lived HTML report with parsing, matching, and review status.

The app is designed for people who need to inspect reference lists against a
local SQLite reference database without sending documents to a third-party API.

## Version

Current public version: **v0.2**.

This version focuses on a working self-hosted reference-checking workflow:
document upload, bibliography extraction, reference parsing, local database
matching, report generation, and Docker-based public hosting.

Planned improvements include:

- moving the reference database from SQLite to PostgreSQL for larger hosted
  deployments
- adding scheduled metadata refreshes from Crossref so the local reference
  database can stay current over time
- improving database update tooling for repeatable imports and maintenance
- expanding public deployment documentation as the hosted setup matures

## Metadata Sources

The current v0.2 local matching database was built from Crossref metadata from
March 2026 and Open Library metadata dumps from February 2026. Report generation
uses the local database; uploaded documents and pasted reference lists do not
need to be sent to Crossref or Open Library during normal use.

More detail: [docs/metadata_sources.md](./docs/metadata_sources.md).

## What It Does

- Accepts PDF and DOCX uploads.
- Accepts pasted bibliography/reference text.
- Extracts the reference section from documents.
- Segments references into individual entries.
- Parses reference fields with AnyStyle.
- Matches parsed references against a local SQLite database.
- Renders a sanitized browser report with match status and review details.
- Supports public no-login hosting with Docker Compose.

## How It Works

Reference Gen2 has a small web frontend served by FastAPI. Submitted work is
processed through a Python pipeline:

1. Validate and extract document text.
2. Detect the bibliography/reference section.
3. Split the section into individual references.
4. Parse reference metadata.
5. Match references against the local database.
6. Render a short-lived HTML report.

For hosted use, the API can queue work for a same-node worker process. Reports
are protected with session ownership, so a report URL alone is not enough to
open another user's report.

## Docker Compose Options

This repository supports local and public Docker Compose usage.

| Mode | Use Case | Main Files |
| --- | --- | --- |
| Public no-auth | Open website where anonymous users can submit references | `docker-compose.public.yml`, `.env.public.example`, `docker/caddy/Caddyfile.public` |

## Quick Start With Docker Compose

This is the easiest way to try the app locally. It starts the FastAPI app on
`http://127.0.0.1:8000` without Caddy or HTTPS.

Requirements:

- Docker
- Docker Compose plugin

Create a small mock database:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -c constraints.txt -e ".[test]"
.venv/bin/python scripts/create_mock_localdb.py --overwrite
```

Build and start the local Docker app:

```bash
docker compose -f docker-compose.local.yml build
docker compose -f docker-compose.local.yml up -d
docker compose -f docker-compose.local.yml ps
```

Open:

```text
http://127.0.0.1:8000/
```

Stop it:

```bash
docker compose -f docker-compose.local.yml down
```

To use a real reference database instead of the mock database:

```bash
REFERENCE_GEN2_LOCAL_DB_HOST_PATH=/path/to/db.sqlite \
  docker compose -f docker-compose.local.yml up -d
```

## Local Development Without Docker

Requirements:

- Python 3.11 or newer
- Ruby and the `anystyle` CLI for real parsing
- SQLite reference database for full matching behavior

Create the Python environment:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -c constraints.txt -e ".[test]"
```

Install AnyStyle:

```bash
gem install anystyle
anystyle --version
```

Create a small mock database for local testing:

```bash
.venv/bin/python scripts/create_mock_localdb.py --overwrite
```

Run the local web app:

```bash
export REFERENCE_GEN2_LOCAL_DB_PATH=local_mock_refs.db
python -m uvicorn reference_gen2.api.phase7_app:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000/
```

Run tests:

```bash
.venv/bin/pytest -q
```

For the full local setup guide, see [docs/setup.md](./docs/setup.md).

## Public Hosting

Public hosting exposes the app without login. It is intended for a server with
a real domain, HTTPS via Caddy, and a local SQLite reference database.

Create the local environment file:

```bash
cp .env.public.example .env
chmod 600 .env
```

Fill in at least:

```env
APP_DOMAIN=refgen.example.com
ACME_EMAIL=admin@example.com
REFERENCE_GEN2_LOCAL_DB_HOST_PATH=/data/db.sqlite
REFERENCE_GEN2_REPORT_SERVING_OWNERSHIP_SECRET=<generate>
REFERENCE_GEN2_API_RATE_LIMIT_SECRET=<generate>
REFERENCE_GEN2_API_ALLOWED_HOSTS=refgen.example.com
REFERENCE_GEN2_API_POST_ALLOWED_ORIGINS=https://refgen.example.com
```

Generate secrets with:

```bash
openssl rand -hex 32
openssl rand -hex 32
```

Start public mode:

```bash
docker compose -f docker-compose.public.yml config
docker compose -f docker-compose.public.yml build
docker compose -f docker-compose.public.yml up -d
docker compose -f docker-compose.public.yml ps
```

The public stack publishes only Caddy on ports 80/443. The API and worker stay
on an internal Docker network.

Detailed guide: [docs/public_deployment.md](./docs/public_deployment.md).

## Security Notes

Public production mode requires explicit security settings for:

- allowed host must be configured
- POST origin allowlist must be configured
- trusted proxy CIDRs must be explicit
- broad private proxy ranges are rejected
- upload scanner policy must be explicit
- Docker build context excludes local secrets, databases, caches, and runtime state

The public deployment does not include ClamAV by default. The app can call an
external upload scanner, but the default public template sets
`REFERENCE_GEN2_SECURITY_SCAN_ACCEPT_RISK=1`. Change that before launch if you
want uploads scanned by an external tool.

Dependency policy documentation is available in [docs/security/dependency_policy.md](./docs/security/dependency_policy.md).

<<<<<<< HEAD
=======
## License

Reference Gen2 is licensed under the BSD 3-Clause License. See
[LICENSE](./LICENSE).

Third-party dependencies, tools, metadata sources, and container images keep
their own licenses and terms. See [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md).

>>>>>>> f727102 (Update public release files)
## Development And Attribution

This tool was created with AI-assisted development, including Codex and OpenAI
models 5.4 and 5.5, plus Claude Sonnet models.

If copyrighted information or material requiring attribution inadvertently made
it into this repository, please open an issue or contact the maintainer with the
file path and relevant details so it can be attributed, corrected, or removed.

## Repository Map

- [reference_gen2/](./reference_gen2): application package
- [reference_gen2/api/](./reference_gen2/api): FastAPI app, worker, static UI
- [reference_gen2/services/](./reference_gen2/services): document/report pipeline services
- [docker/](./docker): Caddy deployment files
- [scripts/](./scripts): setup, audit, profiling, and helper scripts
- [tests/](./tests): unit and integration tests
- [docs/](./docs): setup, architecture, security, and deployment docs

## Useful Commands

Run environment safety checks:

```bash
bash scripts/safety_audit.sh
```

Check the web stack dependency versions:

```bash
.venv/bin/python -m scripts.web_stack_preflight
```

Audit Python dependencies:

```bash
.venv/bin/python scripts/dependency_audit.py --json-output dependency_audit_report.json
.venv/bin/python scripts/vulnerability_audit.py --json-output vulnerability_audit_report.json
```

## More Documentation

- [Local setup](./docs/setup.md)
- [Metadata sources](./docs/metadata_sources.md)
- [Public deployment](./docs/public_deployment.md)
- [Phase boundaries](./docs/phase_boundaries.md)
- [Dependency policy](./docs/security/dependency_policy.md)
