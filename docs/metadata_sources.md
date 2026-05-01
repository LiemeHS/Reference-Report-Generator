# Metadata Sources

Reference Gen2 matches parsed references against a local database built from
open bibliographic metadata sources.

## Sources Used For The Current Database

The current local database used during v0.2 development was built from:

- Crossref metadata snapshot: March 2026
- Open Library metadata dumps: February 2026

These sources are used for bibliographic matching and review support. The app
does not need to send uploaded documents or pasted reference lists to Crossref
or Open Library during normal report generation.

## Planned Updates

Future versions are expected to improve the database maintenance workflow by:

- moving hosted deployments from SQLite to PostgreSQL
- adding scheduled Crossref metadata refreshes
- documenting repeatable Open Library import/update steps
- recording exact source snapshot dates and import commands for each public
  database release

## Attribution And Removal Requests

This repository is intended to contain source code, documentation, tests, and
configuration for the Reference Gen2 application. If copyrighted material,
incorrect attribution, or content requiring removal is found in the repository,
please open an issue or contact the maintainer with the file path and relevant
details so it can be attributed, corrected, or removed.
