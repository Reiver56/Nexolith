# Changelog

All notable user-facing changes to Nexolith are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and Nexolith
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Everything below has landed since `0.3.0` but is not yet released, grouped by the milestone each
change was built for. A version bump (`0.3.1`–`0.3.4`, or a combined release) is a separate,
not-yet-authorized step; this file records what shipped, not when it is cut.

### v0.4.0 — Nexo Functions and Actions

#### Added

- Added a typed `nexofunction.<name>` registry for reusable destination operations, with deterministic
  built-ins and trusted project-local discovery from a configuration-relative `nexofunctions/`
  directory. Local definitions intentionally override built-ins; duplicate or malformed local
  registrations fail before writing. Initial built-ins provide atomic SQLite/PostgreSQL `upsert`
  against declared unique conflict keys and `truncate_write` through the existing schema-preserving
  SQL truncate semantics. This does not add package plugins or sandboxing.
- Added declarative post-write `nexoaction.<name>` hooks with typed conditions, deterministic
  trusted-local discovery, and opaque stable idempotency keys. Handlers receive only matched rows;
  failures fail the pipeline and existing DAG retries can invoke both the destination write and
  action again. Nexolith does not persist action receipts, so the guarantee is at-least-once with
  handler-owned deduplication, not exactly-once delivery.
- Added safe typed Nexo Function and Nexo Action metadata to Monitor's DAG graph and task drawer.
  Functions, Actions, Python, and SQL operations now have distinct local icons and labels; Actions
  remain attached to their owning task and explain their preflight, post-write, at-least-once
  lifecycle without exposing raw configuration, connection details, or idempotency-key values.

### v0.3.4 — Web UI Foundations

#### Added

- Added an optional, versioned FastAPI monitoring backend for registered DAGs, persisted run/task/
  attempt history, and PID-plus-creation-time-verified scheduler status. The API opens one SQLite
  state store per request, exposes only explicit read models and GET operations, redacts persisted
  error details, and includes stable OpenAPI operation IDs for future generated TypeScript clients.
- Added explicit typed POST actions to register DAGs, synchronously trigger registered DAG runs,
  and start/stop the scheduler through shared CLI-independent services. Scheduler start remains a
  separate identity-verified process; stop preserves stable-handle/pidfd and replacement-marker
  guarantees. Action requests require JSON, enforce trusted Host/same-origin browser boundaries,
  are never retried automatically, and retain secret-safe deterministic OpenAPI models.
- Added the first React monitoring UI with a restrained responsive shell, registered-DAG and
  newest-first run lists, run/task/retry-attempt detail, API/scheduler status, accessible
  loading/empty/error states, and no action controls. TypeScript types are generated
  deterministically from the local FastAPI OpenAPI contract and checked for drift in a dedicated
  pinned-Node frontend CI job.
- Added a read-only interactive DAG dependency graph with deterministic layered layout, latest-run
  task statuses, distinct cross-DAG trigger boundaries, accessible text summaries, responsive
  pan/zoom/reset controls, and visibility-aware non-overlapping polling. A minimal typed graph GET
  endpoint keeps the browser contract complete without exposing source paths or error details.
- Connected the React interface to typed DAG registration/run-trigger and scheduler start/stop
  endpoints. Consequential actions use accessible confirmation dialogs, prevent duplicate
  submission, never retry automatically, refresh authoritative state after success, preserve
  encoded DAG names, and keep protected backend details out of rendered feedback.
- Added task source viewing to the DAG dependency graph: selecting a task fetches its declared
  script or pipeline definition through a dedicated, loopback-only endpoint (disabled on any
  non-loopback API bind, even with trusted hosts configured) and displays it verbatim. Source text
  is never logged, persisted, or included in the graph's own polling response — it's fetched only
  after a task is selected, and only file existence/size/encoding are validated server-side, not
  redacted for secrets, since registered local code can itself contain sensitive values.
- Added `nexolith dev`, a unified local-development command that locates the `web/` checkout,
  verifies the declared Node.js/npm versions and installed frontend dependencies, then starts and
  supervises both the API and the Vite dev server together, printing both URLs once both are
  actually ready. `Ctrl+C`, a readiness timeout, or either process exiting unexpectedly stops both
  owned process trees. Loopback defaults (`127.0.0.1:8765` API, `127.0.0.1:5173` Vite) are
  overridable; a non-loopback bind opts in explicitly and prints a warning, and wildcard/multicast
  binds are rejected. Replaces the previous two-terminal `nexolith api start` + `cd web && npm run
  dev` workflow for local development.
- Added per-DAG schedule pause/resume: a new API action pair
  (`POST /api/v1/dag-schedules/{pause,resume}`) and matching React controls suspend or restore only
  a DAG's own *interval*-triggered runs — a paused DAG still responds normally to a manual trigger,
  an API-triggered run, or a cross-DAG `on_success_of` trigger from an upstream DAG. Previously the
  only way to stop scheduled execution was disabling the DAG outright, which also silently blocked
  cross-DAG triggers with no way to tell the two apart from the outside.
- Polished Monitor's interaction and motion design: per-route transition animation on navigation, a
  refined motion token scale (distinct fast/standard/deliberate durations and easing curves) used
  consistently across the UI, minimum 44px touch targets on interactive controls, explicit
  busy/pending indicators (disabled state, `aria-busy`) on in-flight actions and refresh, and
  confirmation-dialog focus handling that restores focus to the triggering element on close.

#### Fixed

- Allowed the API and frontend development servers to share a numeric port when they bind to
  genuinely distinct specific addresses, while still rejecting equivalent and `localhost`-aliased
  bind targets.
- Fixed the DAG dependency graph page reloading its route unnecessarily on navigation, alongside
  the transition-animation work above.

### v0.3.1 — CLI Aesthetics

#### Added

- Added a colored Nexo pixel-art startup splash and prompt idle state, degrading automatically by
  terminal capability: the Kitty graphics protocol at full fidelity where supported, ANSI truecolor
  (falling back to a real 256-color tier when truecolor isn't detected) block art otherwise, and
  today's plain-text splash on a narrow, non-color, non-TTY, or encoding-unsafe terminal — never a
  crash, always a graceful degrade (NXL-69, NXL-72).
- Added a full-screen interactive session on capable terminals: a bordered, titled Nexo panel as a
  persistent header, blue Discord-toned divider lines separating header/status/output/input, a
  scrollable output log, and a tab-completing input line (slash commands and `/open` path
  completion, visible as a live menu while typing). Falls back to the existing plain-text line
  loop — unchanged, byte-identical — whenever `NO_COLOR`, no real TTY, a narrow terminal, or
  `prompt_toolkit` cannot acquire a terminal for full-screen mode. Every command dispatches through
  the same logic in both modes; only presentation differs (NXL-69).
- Added an in-place step timeline for `/validate` and `/run` in the full-screen session, with a
  small activity dot that pulses only when a real pipeline lifecycle event arrives, replaced by a
  bordered summary/validation panel on completion. `/validate` configuration errors additionally
  show a small, bounded excerpt of the pipeline YAML with the offending line highlighted, when it
  can be reliably located (NXL-69).

#### Fixed

- Fixed `/validate` leaving the full-screen status area stuck on its in-progress timeline forever
  on success, instead of showing a terminal validation panel the way `/run` already did.
- Fixed trackpad/mouse-wheel scrolling in the full-screen session hijacking the input field's
  command history instead of scrolling the output log.

### v0.3.2 — Operational Pipelines

#### Added

- Added a declarative DAG format (`workflow.yaml`) for describing multiple existing pipelines as
  named, dependency-ordered tasks, with structural validation and cycle detection (NXL-74).
- Added a DAG executor: runs a DAG's tasks in dependency order through the same pipeline engine
  `run`/`validate` already use, recording live, crash-recoverable run/task state, and propagating a
  failure to every downstream task as `skipped` (NXL-76).
- Added a scheduler daemon: polls enabled, scheduled DAGs and triggers due ones automatically on an
  interval schedule (`30s`/`5m`/`2h`/`1d`), with a skip-missed-occurrences catch-up policy and safe
  shutdown (NXL-77).
- Added `nexolith scheduler start`/`stop`/`status` and `nexolith runs list`/`show` to the CLI for
  running and observing the scheduler daemon and DAG run history (NXL-78).
- Added configurable per-task retry (delay and backoff multiplier) and a DAG-level failure
  propagation policy (`skip`, the prior-only behavior, or `block`, which also blocks independent,
  unrelated tasks), with a per-attempt breakdown shown in `runs show` for any task that was
  retried (NXL-79).

#### Fixed

- Fixed `runs show` crashing with `UnicodeEncodeError` on a terminal/encoding that can't render its
  Unicode status markers (e.g. a legacy Windows code page), including when `NO_COLOR` was set
  (NXL-80).

### v0.3.3 — Advanced Pipeline Capabilities

#### Added

- Added `query_file` on SQL pipeline sources, so a query can live in its own `.sql` file instead of
  being inlined in the pipeline YAML (NXL-81).
- Added parameterized SQL queries: named parameters declared on the source and safely bound via
  SQLAlchemy Core (never string interpolation), overridable per DAG task at run time (NXL-82).
- Added in-process Python transform job steps (`python_job`) for row-level logic the declarative
  transform set can't express (NXL-83, ADR-7).
- Added autonomous script job steps (`script:`) — a DAG task that runs as its own subprocess with
  its own interpreter/venv, for engines like PySpark or a fan-out shape a single pipeline can't
  express (NXL-88, ADR-7).
- Added cross-DAG triggers (`trigger.on_success_of`): a DAG can start automatically as soon as
  another DAG it depends on succeeds, instead of only on its own schedule (NXL-85).
- Added DAG priority (`low`/`normal`/`high`/`critical`) for resolving scheduling contention among
  multiple DAGs due at once (NXL-86).
- Added DAG severity classification (`low`/`medium`/`high`/`critical`, default `medium`), snapshotted
  per run and shown in `runs show`/`runs list` (NXL-87).
- Added `nexolith dag register` (classic CLI) and `/register` (interactive session) to enable a DAG
  for scheduled execution without running it first (NXL-103).
- Added a `truncate` SQL destination write mode: clears a table's rows in place before writing,
  preserving schema and constraints — unlike `replace`, which drops and recreates the table
  (NXL-96).
- Brought the full-screen interactive session up to parity with the classic CLI: `/runs`,
  `/scheduler status`/`stop`, DAG file recognition for `/open`/`/validate`/`/run`, and `/register`
  (NXL-99); `/open` with no argument now discovers DAG files recursively when nothing is open
  (NXL-107); `/clear` now clears the scrollable output log, and the prior pipeline/DAG-context-clearing
  behavior moved to `/close` (NXL-105).
- Added new worked examples: a self-contained medallion bronze/silver/gold pipeline pattern
  combining declarative, Python-job, and script-job steps; a Postgres-backed FonoLink stress test
  (10 pipelines, 3 cross-DAG-triggered DAGs, real fraud/churn signal); and a `query_file`-based SQL
  enrichment chain demo. See `examples/` for details.

#### Fixed

- Fixed full-screen validation panels rendering a short right border when a pipeline name contains
  wide Unicode characters such as CJK text.
- Fixed `/open` path completion returning no matches when multiple spaces separate the command from
  its path argument.
- Fixed abandoned `running` DAG runs permanently blocking future interval and cross-DAG scheduling
  after an unclean scheduler stop. Runs now record their owner process; a fresh scheduler marks rows
  whose owner is known to be gone as `interrupted`, preserving honest history in `runs list`/`show`
  while making the DAG eligible again (NXL-116).
- Fixed operating-system PID reuse making an abandoned run look genuinely active forever. Run
  ownership now combines PID with process creation time on Windows and supported POSIX platforms;
  dead or identity-mismatched owners are interrupted, while legacy or unverifiable identities stay
  running with a diagnostic warning to avoid duplicate side effects (NXL-120).
- Fixed concurrent `scheduler start` commands both passing a read-before-write PID check and
  launching duplicate schedulers. PID ownership now combines atomic exclusive marker creation with
  a crash-released operating-system lease held across the scheduler lifetime; acquisition, stale
  recovery, and owner cleanup share that lease, so an exiting scheduler cannot erase a replacement
  claim while `status` and `stop` remain read-only observers (NXL-117, NXL-121).
- Fixed scheduler PID reuse making `status` trust, and `stop` terminate, an unrelated process.
  Scheduler markers now persist PID plus process creation time; startup safely recovers dead or
  identity-mismatched markers, while legacy and unverifiable markers fail closed. Remote stop uses
  one identity-bound Windows process handle or Linux pidfd and never falls back to PID-only
  signaling (NXL-121).
- Fixed unexpected ordinary task exceptions terminating scheduler polling and leaving attempt,
  task, and DAG rows `running`. They now follow configured retries and failure policy with
  secret-safe terminal history, while process-level interruptions still propagate for restart
  reconciliation (NXL-122).
- **The classic CLI's `validate`/`run` commands didn't recognize DAG files at all** — only plain
  pipeline YAML — despite DAG support existing since v0.3.2. Both commands now detect and
  validate/run DAG files correctly.
- **SQL destination type inference silently downgraded `Decimal`/`datetime`/`date` columns to
  `String`** when writing a fresh table (`replace`/`fail`-then-create) from aggregated or timestamp
  values, breaking `SUM`/`MIN`/`MAX` and other SQL-level aggregation with no warning at write time.
  Now infers real `Numeric`/`DateTime`/`Date` column types (NXL-97).
- Fixed CSV pipeline source/destination paths only ever resolving against the process's working
  directory instead of the pipeline's own directory, unlike every other path-bearing field
  (`query_file`, `python_job`'s `file:`, a DAG's `pipeline:`/`script:`) (NXL-89).
- Fixed cross-DAG trigger reactions only being recorded when the scheduler itself triggered a run —
  a manually-run downstream DAG could be silently re-triggered by the scheduler later against stale
  data (NXL-94).
- Fixed `runs show`/`run` panel width having no ceiling: a long error message could produce a panel
  wider than any real terminal, which then line-wrapped into a misaligned-looking result. Panel
  width is now capped to the terminal and long values wrap cleanly (NXL-93).
- Fixed `runs show`/`run` reading as if nothing happened on a failed DAG run that still had
  independent, unrelated tasks succeed with real, persisted side effects (NXL-95).
- Fixed the full-screen session printing raw 24-bit color escape codes as literal text on terminals
  that signal color support but not truecolor, and colorized text leaking into the output log (which
  cannot interpret any ANSI escape codes at all) — structural highlighting there (borders, labels,
  recognized commands) now goes through a proper syntax-highlighting lexer instead (NXL-100,
  NXL-104, NXL-106).
- Fixed two real sources of visual corruption in the full-screen session's scrollable output log: an
  unaccounted-for scrollbar column that could overflow a maximally-wide result panel by one
  character, and a stale terminal width that was never refreshed after a real window resize;
  redraw bursts are also now throttled as an additional, lower-confidence mitigation. Occasional
  full-screen rendering corruption can still occur on Windows — see README's "Known limitations".

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
  ([PR #4](https://github.com/nexolith-labs/Nexolith/pull/4)).
- Added a service-free zero-to-first-pipeline tutorial with a CSV example that covers installation,
  validation, execution, and output inspection
  ([PR #2](https://github.com/nexolith-labs/Nexolith/pull/2)).
- Added an opt-in Docker Compose workflow and integration coverage for PostgreSQL connection,
  reading, writing, destination modes, and CSV-to-PostgreSQL execution
  ([PR #1](https://github.com/nexolith-labs/Nexolith/pull/1)).

### Changed

- Standardized expected failures under configuration, connector, transformation, and execution
  domain errors. CLI diagnostics now use stable categories and preserve original causes for API
  callers ([PR #3](https://github.com/nexolith-labs/Nexolith/pull/3)).
- Declared Python 3.12, 3.13, and 3.14 as the supported versions. CI now builds the distribution
  once, installs the wheel on every supported version, and verifies import plus the first CLI
  workflow ([PR #5](https://github.com/nexolith-labs/Nexolith/pull/5)).
- Made the Docker Compose PostgreSQL host port configurable through `POSTGRES_PORT`
  ([PR #1](https://github.com/nexolith-labs/Nexolith/pull/1)).

### Fixed

- Expected YAML, configuration, CSV, SQL, and transformation failures now produce actionable
  messages without an expected-error traceback. Unexpected programming errors continue to
  propagate for debugging ([PR #3](https://github.com/nexolith-labs/Nexolith/pull/3)).
- Explicit PostgreSQL test runs now fail clearly when `DATABASE_URL` is missing or PostgreSQL is
  unreachable, while the standard service-free suite remains independent of Docker
  ([PR #1](https://github.com/nexolith-labs/Nexolith/pull/1)).

### Security

- Redacted credentials, authenticated connection URLs, and raw driver details from expected CLI
  failures. Environment diagnostics do not collect or report usernames, hostnames, environment
  values, or local paths
  ([PR #3](https://github.com/nexolith-labs/Nexolith/pull/3),
  [PR #4](https://github.com/nexolith-labs/Nexolith/pull/4)).

### Breaking changes and migrations

- **CLI exit codes changed.** Scripts that previously expected exit code `1` for every failure must
  use `2` for configuration or validation failures and `3` for execution, connector, or
  transformation failures ([PR #3](https://github.com/nexolith-labs/Nexolith/pull/3)).
- **Python 3.15 and newer are outside the supported range.** Package metadata now requires
  `>=3.12,<3.15`. Use Python 3.12, 3.13, or 3.14 before installing Nexolith 0.2.0
  ([PR #5](https://github.com/nexolith-labs/Nexolith/pull/5)).
- No pipeline YAML, connector configuration, or stored-data migration is required. The
  `ComponentNotFoundError` import and the result-returning default of `PipelineRunner.run()` remain
  available for compatibility ([PR #3](https://github.com/nexolith-labs/Nexolith/pull/3)).

## [0.1.0] - 2026-07-31

### Added

- Initial open-source Nexolith project with declarative YAML pipelines, CSV and SQL connectors,
  built-in transformations, a Typer CLI, and service-free execution.
