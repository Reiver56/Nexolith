# Nexolith React operations UI

The source-only React SPA provides monitoring and explicit operational controls through these
stable views:

| Route | View |
|---|---|
| `/` | Redirect to `/dags` |
| `/dags` | Registered DAG state, schedule, priority, severity, and trigger summary |
| `/dags/:dagName/graph` | Interactive dependencies, statuses, and on-demand source details |
| `/runs` | Bounded newest-first run history |
| `/runs/:runId` | Run, task, and retry-attempt history |
| `/scheduler` | Live scheduler status with explicit start and stop controls |

The DAG views can register a YAML definition and explicitly trigger a registered run. The scheduler
view can start or stop the verified scheduler process. Every consequential action uses an
accessible custom confirmation dialog, prevents duplicate submission, never retries automatically,
and refreshes authoritative backend state after success. The UI does not edit YAML, schedules, or
graph dependencies and does not stream logs.

## Stack and boundaries

- React 19 and React DOM provide the view layer.
- React Flow renders the controlled dependency canvas, default attribution included. Nexolith owns
  deterministic layout and never enables node dragging, connecting, deletion, or persistence.
- TypeScript strict mode and generated OpenAPI declarations keep the Python/TypeScript contract
  singular.
- Vite 8 provides the development server and production build; a small internal History API router
  avoids a routing dependency for the small route set.
- Vitest, jsdom, and Testing Library cover DOM behavior and accessibility semantics.
- ESLint with `typescript-eslint` and React hooks rules provides static analysis.
- `openapi-typescript` is build tooling only. React, React DOM, and React Flow are the runtime UI
  dependencies.

Node 24.18.0 LTS is pinned in the repository `.node-version`; npm 11 and the committed lockfile are
required. No remote fonts, CDN assets, analytics, runtime scripts, component framework, global
state framework, automatic layout dependency, or frontend secret is used.

The graph requests one typed `GET /api/v1/dags/{dag_name}/graph` response. Current task names and
dependencies always come from the current DAG definition; statuses come only from its latest
persisted run. Historical tasks no longer present in the definition are disclosed below the graph,
not drawn as current nodes. Cross-DAG `on_success_of` relationships remain distinct DAG-level
nodes and dashed edges. Equivalent inputs produce the same layered layout.

Script and pipeline nodes use repository-owned, colour-independent SVG symbols with accessible
labels. Pipelines remain neutrally labelled because a pipeline definition may combine CSV, SQL,
transformations, and Python jobs. Selecting a task opens a read-only desktop side panel or mobile
sheet. The panel fetches only that registered task through `GET /api/v1/task-details`, aborts and
ignores stale selections, restores focus when closed, and renders source as escaped plain text in
an internally scrolling code region. Escape closes it. Source is discarded when the panel closes
and never joins graph polling data.

The API enables source details only on a loopback bind. Returned source is not secret-redacted and
must be treated as trusted-local content. The UI never requests a path, displays backend path
metadata, executes source-like HTML, edits source, or adds run/save controls to the panel.

Polling starts only after the preceding request settles, pauses and aborts while the document is
hidden, and preserves the last good graph if a background refresh fails. Automatic refresh never
resets the operator's viewport; the explicit Reset layout control does.

Action transport stays in `src/api/client.ts` and uses generated OpenAPI request and response
types. Requests remain same-origin JSON POSTs. Error messages are filtered before rendering, and
the UI never displays scheduler PIDs, process identities, internal exceptions, credentials, or
backend paths. A successfully triggered run navigates to its persisted run-detail route instead of
inventing an optimistic state.

## Local development

Install dependencies explicitly from the source checkout:

```powershell
uv sync --extra api --extra dev
npm.cmd --prefix "<checkout>\web" ci
```

Then start both servers with one supervised command. `nexolith dev` works from any directory;
the `uv` fallback is run from the checkout:

```bash
nexolith dev
# When the project environment is not already active:
uv run --extra api nexolith dev
```

After the API extra is already synchronized, `uv run nexolith dev` also works.

The command waits for stable API and web endpoints before printing their URLs. `Ctrl+C` stops both
owned process trees, and an unexpected exit stops the remaining peer and returns a non-zero status.
It never installs or updates dependencies. Use `--api-host`, `--api-port`, `--web-host`, and
`--web-port` to change the bindings; specific non-loopback IPs explicitly expose the development
services and produce a warning. `--open` opens the browser after readiness and is off by default.

Open `http://127.0.0.1:5173`. Vite proxies relative `/api` requests to
`http://127.0.0.1:8765` and rewrites the proxy Host/Origin pair to that loopback target so action
requests remain same-origin from the API's perspective, without wildcard CORS. Vite itself binds
only to loopback.
Direct route refreshes work in Vite's development fallback. A future production host must provide
an equivalent SPA fallback; this source-only frontend is not deployed or served through FastAPI.

## OpenAPI generation

The FastAPI application remains the source of truth. Run:

```bash
npm run api:generate
npm run api:check
```

The generator asks `uv` to run `scripts/export_openapi.py`, which calls `create_app().openapi()`
without a network server, state store, scheduler, or database. `openapi-typescript` consumes that
JSON in memory and writes the deterministic, committed `src/api/schema.d.ts`. No `openapi.json`
snapshot is written. Application aliases in `src/api/types.ts` reference generated components;
do not create handwritten response interfaces.

Backward-incompatible API schema changes must be deliberate and reviewed. Regenerate types after
any accepted change and commit the result in the same pull request.

## Checks

```bash
npm ci
npm run api:check
npm run lint
npm run typecheck
npm test
npm run build
```

Tests use deterministic `fetch` fixtures; they require no database, PostgreSQL, Docker, scheduler,
or external network. They verify loading/empty/error states, deterministic layout and edge
deduplication, task/cross-DAG separation, status semantics, encoded route and action names,
confirmation/cancellation, focus trapping and restoration, duplicate-submission prevention,
authoritative refresh, typed safe errors, and poll cancellation/non-overlap.

The production output is temporary verification material under `web/dist/`; it is not committed,
packaged in the Python wheel, published, or deployed by this story. The source distribution keeps
the versioned `web/` sources so a source checkout remains reproducible, while excluding
`node_modules`, build output, coverage, caches, and temporary OpenAPI exports.
The existing `assets/nexo-icon.png` bytes are copied into Vite's public directory and emitted as
the application favicon; no repository asset directory is exposed wholesale. The production
build verifies the icon is emitted as a standalone public asset. Navigation uses a separate
96-pixel, transparent, optimized copy derived from the same artwork; Vite fingerprints that asset
without changing the existing favicon behavior.
