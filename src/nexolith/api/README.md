# Query and action API

The v0.3.4 FastAPI layer builds on Nexolith's existing application, DAG, state, and scheduler
boundaries. Install it with `pip install "nexolith[api]"` or
`uv sync --extra api --extra dev`, then run `nexolith api start`. The API server remains a
foreground process at `127.0.0.1:8765` by default. `/docs` and `/openapi.json` expose the typed
contract.

## Contract

| Method | Path | Purpose | Operation ID |
|---|---|---|---|
| `GET` | `/api/v1` | API/package versions and capabilities | `get_api_info` |
| `GET` | `/api/v1/dags` | Registered DAG summaries | `list_dags` |
| `GET` | `/api/v1/dags/{dag_name}` | Current registered DAG structure | `get_dag` |
| `GET` | `/api/v1/dags/{dag_name}/graph` | Current dependencies and latest task statuses | `get_dag_graph` |
| `GET` | `/api/v1/task-details?dag_name=…&task_name=…` | Bounded source for one registered task | `get_dag_task_source` |
| `POST` | `/api/v1/dags/registrations` | Register or explicitly refresh a DAG | `register_dag` |
| `POST` | `/api/v1/dags/{dag_name}/runs` | Execute a registered DAG synchronously | `trigger_dag_run` |
| `GET` | `/api/v1/runs` | Bounded recent run history | `list_runs` |
| `GET` | `/api/v1/runs/{run_id}` | Run, task, and retry-attempt history | `get_run` |
| `GET` | `/api/v1/scheduler` | Identity-verified scheduler status | `get_scheduler_status` |
| `POST` | `/api/v1/scheduler/start` | Start and verify a scheduler process | `start_scheduler` |
| `POST` | `/api/v1/scheduler/stop` | Stop only the verified scheduler owner | `stop_scheduler` |

All requests, responses, parameters, and errors have explicit models. Stable operation IDs and
tags support automated TypeScript generation. `scripts/export_openapi.py` calls the
application factory without starting a server or opening state; `npm run api:generate` from
`web/` commits the resulting TypeScript declarations, never an intermediate OpenAPI JSON file.
Frontend code imports generated types instead of handwritten shadow interfaces, and CI fails on
drift. Backward-incompatible changes to paths, operation IDs,
parameters, enums, fields, or errors must be deliberate, documented, and reviewed.

Registration calls the shared `register_dag()` action. A first registration is `201 created`;
existing rows are `200 unchanged` unless `force` requests a refresh. Refreshing preserves a
disabled row, validates through the normal DAG loader, and executes no task. Responses never echo
the stored absolute source path.

Triggering resolves the registered source and calls `execute_dag()` with `trigger_reason="api"`.
It runs synchronously in a worker thread and returns the real persisted run ID and terminal status;
task failure is a typed action result. Streaming and asynchronous job submission are future work.
Nexolith, middleware, and Uvicorn do not retry actions. Clients **cannot safely retry after losing
a response**: every accepted trigger request may create a distinct run and durable idempotency is
not implemented.

## Scheduler lifecycle

Start launches `[current Python, -m, nexolith, scheduler, start]` without a shell. The child uses
the existing foreground scheduler command, atomic pidfile acquisition, lifetime OS lock, and
PID-plus-creation-time identity. The API waits a bounded interval for verified ownership before
reporting success. Same-process requests are serialized and the OS lock still arbitrates other
processes. Standard streams and unnecessary handles are closed; a small daemon reaper only waits
on the direct child so POSIX does not retain a zombie. The scheduler remains an independent process
and survives API-server shutdown.

Stop calls the same UI-independent control function used by the CLI. It verifies PID and creation
time, uses the existing Windows stable handle or Linux pidfd termination, never signals by PID
alone, never unlinks as an observer, and never removes a replacement claim. Legacy, corrupt,
reused, or unverifiable ownership fails closed. A bounded wait distinguishes `stopped` from
`uncertain`; Windows reports that remote termination is forced rather than graceful.

## State, browser, and trust boundaries

`create_app()` performs no state or scheduler I/O. Each DAG request creates, uses, and closes one
`StateStore` in the same worker thread, so SQLite's thread check remains enabled and blocking work
does not stall the event loop. FastAPI and Uvicorn remain isolated in the `api` optional extra.

There is **no authentication or authorization**. Keep the server on localhost or a trusted network.
Non-loopback binding prints a warning; wildcard binds also require explicit repeated
`--trusted-host` values. The API accepts only configured Host names, sends no wildcard CORS policy,
requires JSON for every action, rejects cross-origin browser actions by default, and exposes no GET
mutation. The development server uses same-origin relative paths and proxies `/api` to the local
backend instead of adding wildcard CORS. Its loopback-only proxy rewrites the upstream Host/Origin
pair together so the API still sees one trusted origin.

Task source is more sensitive than operational metadata. The source-details endpoint is enabled
only when the API itself is bound to a loopback address; non-loopback and wildcard binds reject it,
even when trusted hosts are configured. The client supplies only a DAG name and task name. The
server resolves the current registered DAG, finds that exact task, and reads its declared script or
pipeline definition in the request worker thread. It never accepts or returns a filesystem path.
Files must be UTF-8 text without NUL bytes and no larger than 256 KiB; unreadable, binary,
invalidly encoded, and oversized sources produce fixed typed errors. Source text is returned
verbatim, not logged or persisted, and is not described as secret-redacted: registered local code
can itself contain sensitive values, so this endpoint remains suitable only for a trusted local
operator.

Responses omit raw persisted exceptions, connection details, environment values, process
arguments, source paths, owner creation timestamps, usernames, and home directories. Errors use
fixed typed summaries without tracebacks or internal exception text.

The graph query performs one read-only aggregation for the browser: current task identities and
dependencies come from the registered source, while task statuses come from the latest persisted
run for that DAG. A status is `null` when no trustworthy persisted task status exists. Historical
task rows absent from the current definition remain visible in `unmapped_task_history` instead of
being presented as current graph nodes. Cross-DAG triggers remain DAG-level relationships. The
response includes no task parameters, scripts, source paths, or persisted error text.
The task `kind` is the only additional graph field used for recognizable script/pipeline icons;
source content stays out of the polling response and is fetched only after task selection.
