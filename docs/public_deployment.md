# Public No-Auth Deployment

This guide describes the open public deployment mode for Reference Gen2. It is
for a single-node public website without login.

Use this mode only when anonymous public submissions are intentional.

## Architecture

Public mode runs:

- `caddy`: public HTTPS reverse proxy on ports 80/443
- `api`: internal FastAPI service
- `worker`: internal queued job runner
- `phase7_runtime`: private shared Docker volume for uploads, reports, jobs,
  and rate-limit/security state

The public Caddyfile has no `forward_auth` block. It proxies directly to the
internal API service and preserves the expected host/proxy headers.

## Required Setup

1. Point DNS for `APP_DOMAIN` to the server.
2. Open ports 80 and 443.
3. Put the reference SQLite database on the host, for example
   `/data/db.sqlite`.
4. Create `.env` from the public template:

```bash
cp .env.public.example .env
chmod 600 .env
```

5. Generate app secrets:

```bash
openssl rand -hex 32
openssl rand -hex 32
```

6. Set the required values in `.env`.

## Trusted Proxy CIDR

Production startup requires `REFERENCE_GEN2_API_TRUSTED_PROXY_CIDRS` so the app
only trusts forwarded headers from the real Caddy path.

The public Compose file defines a deterministic internal-only subnet:

```text
REFERENCE_GEN2_INTERNAL_SUBNET=172.28.0.0/24
REFERENCE_GEN2_API_TRUSTED_PROXY_CIDRS=172.28.0.0/24
```

If `172.28.0.0/24` conflicts with another local Docker network, change both
values to a different narrow subnet before starting the stack. Do not use broad
private ranges like `10.0.0.0/8`, `172.16.0.0/12`, or `192.168.0.0/16`.

## Malware Scanning Decision

Public production submissions must make an explicit scanner decision:

- configure a scanner through `REFERENCE_GEN2_SECURITY_SCAN_ENABLED=1` and
  `REFERENCE_GEN2_SECURITY_SCAN_EXECUTABLE`, or
- set `REFERENCE_GEN2_SECURITY_SCAN_ACCEPT_RISK=1`.

The public compose path does not include ClamAV by default. ClamAV is the
obvious open-source scanner, but it has a large memory footprint and should be
added as a separate deployment decision when the server can afford it.

## Start Public Mode

```bash
docker compose -f docker-compose.public.yml config
docker compose -f docker-compose.public.yml build
docker compose -f docker-compose.public.yml up -d
docker compose -f docker-compose.public.yml ps
```

View logs:

```bash
docker compose -f docker-compose.public.yml logs -f caddy api worker
```

## Smoke Test

Open:

```text
https://APP_DOMAIN/
```

Then submit a short pasted reference list and confirm:

- a job is created
- job status completes
- the report opens from the same browser session
- a different browser/session cannot access that report

For a local security sanity check before exposing DNS, run a ZAP baseline
against a localhost or staging instance.

## Operational Notes

- `.env`, `.env.*`, SQLite databases, `.venv`, `.git`, caches,
  logs, and manual outputs are excluded from Docker build context by
  `.dockerignore`.
- CORS remains disabled by default. Public browser use is same-origin.
- Reports and jobs are short-lived and no-store.
- The deployment is single-node. It is not a horizontally scaled architecture.
- Tune queue, worker, rate-limit, and challenge settings to the server size.

## Updating

Pull or merge changes into the same repo, then rebuild:

```bash
docker compose -f docker-compose.public.yml build
docker compose -f docker-compose.public.yml up -d
```

Make changes in source control and regenerate/redeploy from the repository.
