import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { render, screen, within } from "@testing-library/react";

import { App } from "../App";
import { useApiResource } from "../hooks/useApiResource";
import { installApiMock } from "./mockApi";

function renderAt(path: string): ReturnType<typeof render> {
  window.history.replaceState(null, "", path);
  return render(<App />);
}

function rowAt(rows: HTMLElement[], index: number): HTMLElement {
  const row = rows[index];
  if (row === undefined) {
    throw new Error(`Expected table row ${String(index)}.`);
  }
  return row;
}

test("renders newest-first run history and semantic statuses", async () => {
  installApiMock();
  renderAt("/runs");

  const table = await screen.findByRole("table");
  const rows = within(table).getAllByRole("row").slice(1);
  const first = rowAt(rows, 0);
  const second = rowAt(rows, 1);
  expect(within(first).getByRole("link", { name: /#12/ })).toBeInTheDocument();
  expect(within(first).getByText("Interrupted")).toBeInTheDocument();
  expect(within(second).getByRole("link", { name: /#11/ })).toBeInTheDocument();
  expect(within(second).getByText("Failed")).toBeInTheDocument();
  expect(within(second).getByText("Cross Dag")).toBeInTheDocument();
});

test("renders the run-list empty state", async () => {
  installApiMock({ "/api/v1/runs": { body: [] } });
  renderAt("/runs");
  expect(await screen.findByRole("heading", { name: "No run history yet" })).toBeInTheDocument();
});

test("renders run tasks and attempts without raw persisted errors", async () => {
  installApiMock();
  renderAt("/runs/12");

  expect(await screen.findByRole("heading", { name: "billing-close" })).toBeInTheDocument();
  expect(screen.getByText("Interrupted")).toBeInTheDocument();
  expect(screen.getAllByText("Failed").length).toBeGreaterThan(0);
  expect(screen.getByText("Skipped")).toBeInTheDocument();
  expect(screen.getByText("Blocked")).toBeInTheDocument();
  expect(screen.getByText("Attempt 1")).toBeInTheDocument();
  expect(screen.getByText("Attempt 2")).toBeInTheDocument();
  expect(screen.getByText("Execution was interrupted before completion.")).toBeInTheDocument();
  expect(screen.getAllByText("Task attempt failed.")).toHaveLength(2);
  expect(document.body).not.toHaveTextContent("NXL_UI_SECRET_SENTINEL");
  expect(document.body).not.toHaveTextContent("C:\\Users\\operator");
  expect(document.body).not.toHaveTextContent("postgresql://");
  expect(document.body).not.toHaveTextContent("/home/operator");
});

test("handles unknown and malformed run IDs without unsafe requests", async () => {
  const unknown = installApiMock({
    "/api/v1/runs/99": {
      status: 404,
      body: { detail: { code: "run_not_found", message: "Run not found." } },
    },
  });
  const unknownView = renderAt("/runs/99");
  expect(await screen.findByRole("heading", { name: "Run not found" })).toBeInTheDocument();
  expect(unknown.requests.some((request) => request.path === "/api/v1/runs/99")).toBe(true);
  unknownView.unmount();

  const malformed = installApiMock();
  renderAt("/runs/not-a-number");
  expect(await screen.findByRole("heading", { name: "Invalid run identifier" })).toBeInTheDocument();
  expect(
    malformed.requests.some((request) => request.path.includes("not-a-number")),
  ).toBe(false);
});

test("aborts an in-flight read when its component unmounts", () => {
  let observedSignal: AbortSignal | undefined;
  const load = (signal: AbortSignal): Promise<string> => {
    observedSignal = signal;
    return new Promise(() => undefined);
  };

  function Probe() {
    useApiResource(load, "Unavailable");
    return null;
  }

  const view = render(<Probe />);
  expect(observedSignal?.aborted).toBe(false);
  view.unmount();
  expect(observedSignal?.aborted).toBe(true);
});

test("production actions use the generated contract without handwritten response schemas", () => {
  const sourceRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
  const client = readFileSync(resolve(sourceRoot, "api", "client.ts"), "utf8");
  const aliases = readFileSync(resolve(sourceRoot, "api", "types.ts"), "utf8");
  const styles = readFileSync(resolve(sourceRoot, "styles", "app.css"), "utf8");

  expect(client).toContain('method: "POST"');
  expect(client).toContain("/scheduler/start");
  expect(client).toContain("/scheduler/stop");
  expect(client).toContain("/dags/registrations");
  expect(client).not.toMatch(/method:\s*["'](?:PUT|PATCH|DELETE)["']/i);
  expect(aliases).toContain('import type { components, operations } from "./schema"');
  expect(aliases).not.toMatch(/interface\s+(?:Dag|Run|Scheduler)/);
  expect(styles).toContain("@media (max-width: 44rem)");
  expect(styles).toContain("content: attr(data-label)");
});
