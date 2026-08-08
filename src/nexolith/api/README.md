# Query and action API

NXL-111 and NXL-112 provide the v0.3.4 FastAPI foundation over Nexolith's existing application,
DAG, state, and scheduler boundaries. Install it with `pip install "nexolith[api]"` or
`uv sync --extra api --extra dev`, then run `nexolith api start`. The API server remains a
foreground process at `127.0.0.1:8765` by default. `/docs` and `/openapi.json` expose the typed
contract.

## Contract

| Method | Path | Purpose | Operation ID |
|---|---|---|---|
| `GET` | `/api/v1` | API/package versions and capabilities | `get_api_info` |
| `GET` | `/api/v1/dags` | Registered DAG summaries | `list_dags` |
| `GET` | `/api/v1/dags/{dag_name}` | Current registered DAG structure | `get_dag` |
| `POST` | `/api/v1/dags/registrations` | Register or explicitly refresh a DAG | `register_dag` |
| `POST` | `/api/v1/dags/{dag_name}/runs` | Execute a registered DAG synchronously | `trigger_dag_run` |
| `GET` | `/api/v1/runs` | Bounded recent run history | `list_runs` |
| `GET` | `/api/v1/runs/{run_id}` | Run, task, and retry-attempt history | `get_run` |
| `GET` | `/api/v1/scheduler` | Identity-verified scheduler status | `get_scheduler_status` |
| `POST` | `/api/v1/scheduler/start` | Start and verify a scheduler process | `start_scheduler` |
| `POST` | `/api/v1/scheduler/stop` | Stop only the verified scheduler owner | `stop_scheduler` |

All requests, responses, parameters, and errors have explicit models. Stable operation IDs and
tags support automated TypeScript generation in NXL-113; no generated schema or handwritten
frontend interfaces are committed. Backward-incompatible changes to paths, operation IDs,
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
mutation. NXL-113 must deliberately configure its exact frontend origin instead of adding wildcard
CORS.

Responses omit raw persisted exceptions, connection details, environment values, process
arguments, source paths, owner creation timestamps, usernames, and home directories. Errors use
fixed typed summaries without tracebacks or internal exception text.
