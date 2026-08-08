import { vi } from "vitest";

import { apiInfo, dags, runDetail, runs, schedulerRunning } from "./fixtures";

export interface ObservedRequest {
  method: string;
  path: string;
  signal: AbortSignal | null;
}

interface RouteResponse {
  body: unknown;
  status?: number;
}

type RouteValue = RouteResponse | Error;

const defaultRoutes: Record<string, RouteValue> = {
  "/api/v1": { body: apiInfo },
  "/api/v1/dags": { body: dags },
  "/api/v1/runs": { body: runs },
  "/api/v1/runs/12": { body: runDetail },
  "/api/v1/scheduler": { body: schedulerRunning },
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
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = requestUrl(input);
    const method = init?.method ?? (input instanceof Request ? input.method : "GET");
    requests.push({ method, path: `${url.pathname}${url.search}`, signal: init?.signal ?? null });
    const route = routes[`${url.pathname}${url.search}`] ?? routes[url.pathname];
    if (route === undefined) {
      return Promise.resolve(
        new Response(
          JSON.stringify({ detail: { code: "resource_not_found", message: "Not found." } }),
          {
            status: 404,
            headers: { "content-type": "application/json" },
          },
        ),
      );
    }
    if (route instanceof Error) {
      return Promise.reject(route);
    }
    return Promise.resolve(
      new Response(JSON.stringify(route.body), {
        status: route.status ?? 200,
        headers: { "content-type": "application/json" },
      }),
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  return { requests };
}
