import { vi } from "vitest";

import { apiInfo, dagGraph, dags, runDetail, runs, schedulerRunning } from "./fixtures";

export interface ObservedRequest {
  method: string;
  path: string;
  signal: AbortSignal | null;
  headers: Headers;
  body: unknown;
}

interface RouteResponse {
  body: unknown;
  status?: number;
}

type RouteResult = RouteResponse | Error;
type RouteValue = RouteResult | ((request: ObservedRequest) => RouteResult | Promise<RouteResult>);

const defaultRoutes: Record<string, RouteValue> = {
  "/api/v1": { body: apiInfo },
  "/api/v1/dags": { body: dags },
  "/api/v1/dags/billing-close/graph": { body: dagGraph },
  "/api/v1/runs": { body: runs },
  "/api/v1/runs/12": { body: runDetail },
  "/api/v1/scheduler": { body: schedulerRunning },
  "POST /api/v1/dags/registrations": {
    body: {
      dag_name: "new-orders",
      status: "created",
      enabled: true,
      schedule: "5m",
    },
    status: 201,
  },
  "POST /api/v1/dags/billing-close/runs": {
    body: { run_id: 24, dag_name: "billing-close", status: "succeeded" },
    status: 201,
  },
  "POST /api/v1/scheduler/start": { body: { state: "started", pid: 4243 } },
  "POST /api/v1/scheduler/stop": {
    body: { state: "stopped", pid: 4242, forced: false },
  },
};

function requestUrl(input: RequestInfo | URL): URL {
  if (input instanceof Request) {
    return new URL(input.url);
  }
  return new URL(String(input), window.location.origin);
}

export function installApiMock(overrides: Record<string, RouteValue> = {}): {
  requests: ObservedRequest[];
} {
  const routes = { ...defaultRoutes, ...overrides };
  const requests: ObservedRequest[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = requestUrl(input);
    const method = init?.method ?? (input instanceof Request ? input.method : "GET");
    const path = `${url.pathname}${url.search}`;
    let body: unknown;
    if (typeof init?.body === "string") {
      try {
        body = JSON.parse(init.body) as unknown;
      } catch {
        body = init.body;
      }
    }
    const observed = {
      method,
      path,
      signal: init?.signal ?? null,
      headers: new Headers(init?.headers),
      body,
    };
    requests.push(observed);
    let route = routes[`${method} ${path}`] ?? routes[path] ?? routes[url.pathname];
    if (route === undefined) {
      return new Response(
        JSON.stringify({ detail: { code: "resource_not_found", message: "Not found." } }),
        {
          status: 404,
          headers: { "content-type": "application/json" },
        },
      );
    }
    if (typeof route === "function") {
      route = await route(observed);
    }
    if (route instanceof Error) {
      throw route;
    }
    return new Response(JSON.stringify(route.body), {
      status: route.status ?? 200,
      headers: { "content-type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { requests };
}
