# Read-only monitoring API

NXL-111 introduces the first v0.3.4 Web UI foundation: an optional FastAPI adapter over Nexolith's
existing DAG, state, and scheduler read boundaries. Install it with `pip install "nexolith[api]"`
or `uv sync --extra api --extra dev`, then run `nexolith api start`. The server stays in the
foreground and defaults to `127.0.0.1:8765`; `/docs` serves interactive documentation and
`/openapi.json` serves the machine-readable contract.

## Contract

All application routes use the stable `/api/v1` prefix:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1` | API and package version metadata |
| `GET` | `/api/v1/dags` | Registered DAG summaries with current safe declarations |
| `GET` | `/api/v1/dags/{dag_name}` | Current DAG structure from the registered source |
| `GET` | `/api/v1/runs` | Bounded recent run history |
| `GET` | `/api/v1/runs/{run_id}` | Run, task, and retry-attempt history |
| `GET` | `/api/v1/scheduler` | PID-and-creation-time-verified scheduler status |

Every route has an explicit response model, typed parameters and error responses, a stable
`operation_id`, and a stable tag. The schema is intended to feed automated TypeScript type/client
generation in NXL-113 without handwritten duplicate frontend interfaces. Generated OpenAPI JSON
is deliberately not committed: semantic tests check paths, methods, operation IDs, models, tags,
and security exclusions without a noisy snapshot.

Backward-incompatible changes to paths, operation IDs, parameters, enum values, response fields,
or error models must be deliberate, documented, and reviewed as contract changes. The API and
package versions contain no machine-local data, and equivalent application factories produce the
same schema.

## Lifecycle and boundaries

`create_app()` is injectable and performs no state or scheduler I/O. Each request creates, queries,
and closes its own `StateStore` within one worker-thread invocation. SQLite's default thread check
remains enabled, no connection is shared globally, and lock waits or file I/O do not stall the
async event loop. DAG declarations are read fresh from their registered files, while
run/task/attempt history comes directly from the existing state queries.
Scheduler status uses the shared read-only PID-file query and verifies both PID and process creation
time. It never signals a process, deletes a stale marker, or performs cleanup.

DAG detail retains the registered name used by the route and reports the name currently declared
in the source separately, so a changed file cannot silently blur registry identity.

FastAPI and Uvicorn are isolated behind the `api` extra. Importing Nexolith, running pipelines, or
using another CLI command does not import them or make a network call.

## Read-only and security guarantees

NXL-111 exposes no `POST`, `PUT`, `PATCH`, or `DELETE` operation. It cannot trigger/register a DAG,
change schedules, start/stop the scheduler, edit configuration, or delete state. NXL-112 actions,
the NXL-113–NXL-115 React application, authentication, HTTPS, CORS, streaming, and deployment are
separate work.

Responses omit registered source paths, task file paths and parameters, run owner identities,
process creation times, command lines, environment values, and raw persisted exceptions. Terminal
errors become fixed summaries. Missing/invalid DAG sources, unknown resources, request validation,
state failures, and unverifiable scheduler identity use typed, stable JSON errors without
tracebacks or internal exception text.

There is no authentication or authorization in NXL-111. Keep the server on localhost or another
trusted network; a non-loopback bind is an explicit operator decision and produces a CLI warning.
