# Contributing to Nexolith

Thank you for helping make Nexolith better.

## Set up the project

Fork and clone the repository, then install Python 3.12, 3.13, or 3.14, uv, and the development
dependencies:

```bash
uv sync --extra dev
```

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

