# Changelog

All notable user-facing changes to Nexolith are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and Nexolith
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Changes targeting releases after 0.3.0 will be recorded here.

### Added

- Added a full-screen interactive session on capable terminals: a bordered, titled Nexo pixel-art
  panel as a persistent header, a scrollable output log, and a tab-completing input line (slash
  commands and `/open` path completion), using `prompt_toolkit`'s alternate screen buffer. Falls
  back to the existing plain-text line loop — unchanged, byte-identical — whenever `NO_COLOR`, no
  real TTY, a narrow terminal, or `prompt_toolkit` cannot acquire a terminal for full-screen mode.
  Every command dispatches through the same logic in both modes; only presentation differs
  (NXL-69).
- Added blue Discord-toned divider lines separating the full-screen layout's header, status,
  output, and input regions, and made command/path tab-completion visible as a live menu while
  typing instead of only on Tab/Enter (NXL-69).
- Added an in-place step timeline for `/validate` and `/run` in the full-screen session, with a
  small activity dot that pulses only when a real pipeline lifecycle event arrives (no timer, no
  background thread), replaced by a bordered summary panel (status, rows read, rows written,
  duration) on completion. `/validate` configuration errors now additionally show a small, bounded
  excerpt of the pipeline YAML with the offending line highlighted, when it can be reliably
  located; otherwise falls back to today's plain-text-only error message (NXL-69).

## [0.3.0] - 2026-08-01

### Added

- Added a persistent interactive session. Running `nexolith` without a subcommand now opens a
  `nexolith> ` prompt behind a short Nexo splash; `/help` lists the available commands, `/exit`
  closes the session, and EOF or `Ctrl+C` exit cleanly without a traceback (NXL-36).
- Added `/open <path>`, `/open`, and `/clear` to the interactive session for selecting, showing,
  and clearing the active pipeline. The selected filename appears in the prompt, and a failed or
  deleted pipeline leaves the previous selection in place instead of breaking the session (NXL-37).
- Added `/validate` and `/run` to the interactive session. Both reread the active pipeline through
  the shared application layer and print plain-text lifecycle events as they happen, followed by
  the real status, row counts, and duration; failures report the error without leaking connector
  details and return to a usable prompt, and `Ctrl+C` interrupts only the current operation
  (NXL-38).
- Documented the stdout, stderr, and exit-code contract for the scriptable `--version`,
  `diagnostics`, `validate`, and `run` commands, and added regression tests pinning that
  contract so interactive-session work cannot silently change automation behavior (NXL-40).
- Confirmed `/help`, `/open`, `/validate`, `/run`, and `/exit` are all discoverable through
  `/help` and individually tested end to end. Documented that context-aware completion and
  persistent command history are deferred to v0.3.1, and `/logs` is deferred until persistent
  execution history ships in v0.5.0 (NXL-39).

### Fixed

- Fixed the Nexo mascot not rendering on PyPI. The README image now uses a stable absolute URL
  pinned to the `v0.2.0` release tag instead of a repository-relative path, so it renders
  correctly on both GitHub and in the PyPI-rendered package description; the mascot remains
  centered (NXL-32).

## [0.2.0] - 2026-07-31

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
