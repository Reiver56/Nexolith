# Nexolith React monitoring UI

NXL-113 establishes the v0.3.4 browser monitoring foundation. It is a source-only React SPA with
three stable views:

| Route | View |
|---|---|
| `/` | Redirect to `/dags` |
| `/dags` | Registered DAG state, schedule, priority, severity, and trigger summary |
| `/runs` | Bounded newest-first run history |
| `/runs/:runId` | Run, task, and retry-attempt history |

The UI is intentionally read-only. It does not register or trigger DAGs, edit YAML, start or stop
the scheduler, render a React Flow graph, stream logs, or retry mutation requests. NXL-114 owns the
graph; NXL-115 owns action controls and confirmations.

## Stack and boundaries

- React 19 and React DOM provide the view layer.
- TypeScript strict mode and generated OpenAPI declarations keep the Python/TypeScript contract
  singular.
- Vite 8 provides the development server and production build; a small internal History API router
  avoids a routing dependency for three routes.
- Vitest, jsdom, and Testing Library cover DOM behavior and accessibility semantics.
- ESLint with `typescript-eslint` and React hooks rules provides static analysis.
- `openapi-typescript` is build tooling only. React and React DOM are the only runtime dependencies.

Node 24.18.0 LTS is pinned in the repository `.node-version`; npm 11 and the committed lockfile are
required. No remote fonts, CDN assets, analytics, runtime scripts, component framework, global
state framework, React Flow dependency, or frontend secret is used.

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
or external network. They verify loading/empty/error states, status semantics, route validation,
keyboard access, cancellation, responsive table metadata, secret/path exclusion, and the absence
of mutating HTTP methods.

The production output is temporary verification material under `web/dist/`; it is not committed,
packaged in the Python wheel, published, or deployed by this story. The source distribution keeps
the versioned `web/` sources so a source checkout remains reproducible, while excluding
`node_modules`, build output, coverage, caches, and temporary OpenAPI exports.
