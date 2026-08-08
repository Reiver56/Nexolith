# Nexolith React monitoring UI

NXL-113 establishes the v0.3.4 browser monitoring foundation and NXL-114 adds its read-only DAG
graph. It is a source-only React SPA with these stable views:

| Route | View |
|---|---|
| `/` | Redirect to `/dags` |
| `/dags` | Registered DAG state, schedule, priority, severity, and trigger summary |
| `/dags/:dagName/graph` | Interactive task dependencies and latest persisted task status |
| `/runs` | Bounded newest-first run history |
| `/runs/:runId` | Run, task, and retry-attempt history |

The UI is intentionally read-only. It does not register or trigger DAGs, edit YAML, start or stop
the scheduler, stream logs, or retry mutation requests. The graph permits view-only pan, zoom,
selection, and reset interactions; it never changes dependencies or persisted state. NXL-115 owns
action controls and confirmations.

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

Polling starts only after the preceding request settles, pauses and aborts while the document is
hidden, and preserves the last good graph if a background refresh fails. Automatic refresh never
resets the operator's viewport; the explicit Reset layout control does.

## Local development

From the repository root, install and start the optional API:

```bash
uv sync --extra api --extra dev
uv run nexolith api start
```

In another terminal:

```bash
cd web
npm ci
npm run dev
```

Open `http://127.0.0.1:5173`. Vite proxies relative `/api` requests to
`http://127.0.0.1:8765`, preserving the backend's Host/origin posture without wildcard CORS.
Direct route refreshes work in Vite's development fallback. A future production host must provide
an equivalent SPA fallback; NXL-113 does not deploy or serve these files through FastAPI.

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
deduplication, task/cross-DAG separation, status semantics, encoded route names, keyboard access,
poll cancellation/non-overlap, responsive metadata, secret/path exclusion, and the absence of
mutating HTTP methods.

The production output is temporary verification material under `web/dist/`; it is not committed,
packaged in the Python wheel, published, or deployed by this story. The source distribution keeps
the versioned `web/` sources so a source checkout remains reproducible, while excluding
`node_modules`, build output, coverage, caches, and temporary OpenAPI exports.
