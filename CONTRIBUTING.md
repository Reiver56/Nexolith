# Contributing to Nexolith

Thank you for helping make Nexolith better.

## Set up the project

Fork and clone the repository, then install Python 3.12, 3.13, or 3.14, uv, and the development
dependencies:

```bash
uv sync --extra api --extra dev
```

Frontend work additionally uses the Node 24 LTS version pinned in `.node-version`. Install the
locked dependencies from `web/` with `npm ci`; never commit `node_modules`, `dist`, coverage, Vite
cache files, or temporary OpenAPI exports.

Create a focused branch from an up-to-date `main`:

```bash
git switch -c fix/short-description
```

## Quality checks

Run the complete local check suite before submitting a pull request:

```bash
uv run ruff format .
uv run ruff check .
uv run mypy src
uv run pytest
```

Code should be typed, small, readable, and separated by responsibility. Prefer explicit
domain errors, avoid global mutable state, never execute pipeline content as code, and add
tests for changed behavior. Ruff is the source of truth for formatting and lint rules.

For frontend changes, also run from `web/`:

```bash
npm ci
npm run api:check
npm run lint
npm run typecheck
npm test
npm run build
```

The OpenAPI document is a typed compatibility contract for generated clients. Every route must
declare stable operation IDs, tags, response and error models, and typed parameters. Do not commit
a generated schema snapshot or frontend interfaces that duplicate it. Backward-incompatible
schema changes must be intentional, called out in the pull request, and explicitly reviewed.
`scripts/export_openapi.py` exports the local application-factory schema without a server, database,
or network call; `npm run api:generate` updates the committed `web/src/api/schema.d.ts`, and
`npm run api:check` fails on drift. Frontend code must consume that generated contract rather than
maintaining duplicate response interfaces. Monitoring components must not call POST, PUT, PATCH,
or DELETE endpoints unless a later story explicitly authorizes actions.

## Issues and proposals

Search existing issues before filing a report. Bug reports should include a minimal pipeline,
the command used, expected and actual behavior, Nexolith and Python versions, and sanitized
logs. Feature proposals should explain the user problem and why it belongs in the focused
core. Do not include credentials or security vulnerabilities in public issues; follow
`SECURITY.md`.

## Pull requests

Keep pull requests focused and explain both what changed and why. Add or update tests and
documentation, complete the pull request template, and ensure CI passes. Maintainers may ask
for changes to preserve the small, modular architecture. Use clear commit messages; merge
strategy is determined by the maintainers.

## Changelog

Update `CHANGELOG.md` for every user-facing addition, behavior change, fix, deprecation, removal,
or security improvement. Group entries by Keep a Changelog category, link the relevant issue or
pull request when useful, and state breaking changes plus required migrations explicitly.

Version headings must use the exact form `## [X.Y.Z] - status-or-date`, without a leading `v`.
Release automation maps a tag such as `v0.2.0` to the `[0.2.0]` heading and fails when that section
is absent. Use `Unreleased` while preparing a version, then replace it with an ISO date such as
`2026-07-31` when publishing the release.

## Releases

Maintainers must follow [RELEASING.md](RELEASING.md). Releases originate from reviewed `master`
commits and exact `vX.Y.Z` tags. Never store a PyPI password or API token in GitHub: publication
uses the protected `pypi` environment and PyPI Trusted Publishing.

