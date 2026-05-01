# Third-Party Notices

Reference Gen2 is licensed under the BSD 3-Clause License. Third-party
dependencies, tools, metadata sources, and container images are licensed by
their respective copyright holders.

This repository does not relicense third-party software or metadata. Users and
deployers are responsible for complying with the licenses and terms of the
software and data sources they use with the app.

## Runtime Dependencies

The Python runtime dependencies are declared in:

- `pyproject.toml`
- `requirements.txt`
- `constraints.txt`

The app also uses the AnyStyle parser CLI at runtime.

## Metadata Sources

The current v0.2 local matching database used during development was built from:

- Crossref metadata snapshot: March 2026
- Open Library metadata dumps: February 2026

See [docs/metadata_sources.md](./docs/metadata_sources.md).

## Dependency Review

The project includes dependency/license review tooling for auditing direct and
transitive dependencies. Public releases should retain the project license and
this notice, and should refresh dependency review materials when dependencies
change.
