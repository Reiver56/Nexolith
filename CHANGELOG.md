# Changelog

All notable user-facing changes to Nexolith are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and Nexolith
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Changes targeting releases after 0.2.0 will be recorded here.

## [0.2.0] - Unreleased

### Added

- Added a tag-driven PyPI release workflow using short-lived Trusted Publishing credentials,
  protected-environment approval, one-time artifact builds, and fail-closed release validation.
- Added `nexolith diagnostics`, a deterministic and secret-safe environment report for issue
  submissions. It includes Nexolith, Python, platform, installation, dependency, SQLite, and
  PostgreSQL driver availability without exposing local paths or environment values
  ([PR #4](https://github.com/Reiver56/Nexolith/pull/4)).
- Added a service-free zero-to-first-pipeline tutorial with a CSV example that covers installation,
  validation, execution, and output inspection
  ([PR #2](https://github.com/Reiver56/Nexolith/pull/2)).
- Added an opt-in Docker Compose workflow and integration coverage for PostgreSQL connection,
  reading, writing, destination modes, and CSV-to-PostgreSQL execution
  ([PR #1](https://github.com/Reiver56/Nexolith/pull/1)).

### Changed

- Standardized expected failures under configuration, connector, transformation, and execution
  domain errors. CLI diagnostics now use stable categories and preserve original causes for API
  callers ([PR #3](https://github.com/Reiver56/Nexolith/pull/3)).
- Declared Python 3.12, 3.13, and 3.14 as the supported versions. CI now builds the distribution
  once, installs the wheel on every supported version, and verifies import plus the first CLI
  workflow ([PR #5](https://github.com/Reiver56/Nexolith/pull/5)).
- Made the Docker Compose PostgreSQL host port configurable through `POSTGRES_PORT`
  ([PR #1](https://github.com/Reiver56/Nexolith/pull/1)).

### Fixed

- Expected YAML, configuration, CSV, SQL, and transformation failures now produce actionable
  messages without an expected-error traceback. Unexpected programming errors continue to
  propagate for debugging ([PR #3](https://github.com/Reiver56/Nexolith/pull/3)).
- Explicit PostgreSQL test runs now fail clearly when `DATABASE_URL` is missing or PostgreSQL is
  unreachable, while the standard service-free suite remains independent of Docker
  ([PR #1](https://github.com/Reiver56/Nexolith/pull/1)).

### Security

- Redacted credentials, authenticated connection URLs, and raw driver details from expected CLI
  failures. Environment diagnostics do not collect or report usernames, hostnames, environment
  values, or local paths
  ([PR #3](https://github.com/Reiver56/Nexolith/pull/3),
  [PR #4](https://github.com/Reiver56/Nexolith/pull/4)).

### Breaking changes and migrations

- **CLI exit codes changed.** Scripts that previously expected exit code `1` for every failure must
  use `2` for configuration or validation failures and `3` for execution, connector, or
  transformation failures ([PR #3](https://github.com/Reiver56/Nexolith/pull/3)).
- **Python 3.15 and newer are outside the supported range.** Package metadata now requires
  `>=3.12,<3.15`. Use Python 3.12, 3.13, or 3.14 before installing Nexolith 0.2.0
  ([PR #5](https://github.com/Reiver56/Nexolith/pull/5)).
- No pipeline YAML, connector configuration, or stored-data migration is required. The
  `ComponentNotFoundError` import and the result-returning default of `PipelineRunner.run()` remain
  available for compatibility ([PR #3](https://github.com/Reiver56/Nexolith/pull/3)).

## [0.1.0] - 2026-07-31

### Added

- Initial open-source Nexolith project with declarative YAML pipelines, CSV and SQL connectors,
  built-in transformations, a Typer CLI, and service-free execution.
