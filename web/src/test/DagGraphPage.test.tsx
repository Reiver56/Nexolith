import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { App } from "../App";
import type { DagGraph } from "../api/types";
import { dagGraph } from "./fixtures";
import { installApiMock } from "./mockApi";

function renderAt(name = "billing-close"): void {
  window.history.replaceState(null, "", `/dags/${encodeURIComponent(name)}/graph`);
  render(<App />);
}

test("renders current structure, latest statuses, run context, and accessible fallback", async () => {
  const { requests } = installApiMock();
  renderAt();

  expect(await screen.findByRole("heading", { name: "billing-close", level: 1 })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Run #12" })).toHaveAttribute("href", "/runs/12");
  expect(screen.getByText("Interrupted")).toBeInTheDocument();
  expect(screen.getByTestId("dag-graph-canvas")).toBeInTheDocument();
  const summary = screen.getByRole("heading", { name: "Dependency summary" }).closest("section");
  if (summary === null) {
    throw new Error("Expected the dependency summary section.");
  }
  expect(within(summary).getByText("Depends on enrich, extract")).toBeInTheDocument();
  expect(within(summary).getByText(/inventory sync, warehouse-refresh/)).toBeInTheDocument();
  expect(within(summary).getByText(/retired-task \(skipped\)/)).toBeInTheDocument();
  expect(requests.some((request) => request.path === "/api/v1/dags/billing-close/graph" && request.method === "GET")).toBe(true);
  expect(requests.every((request) => request.method === "GET")).toBe(true);
});

test("provides keyboard-operated view controls without edit actions", async () => {
  installApiMock();
  const user = userEvent.setup();
  renderAt();
  const reset = await screen.findByRole("button", { name: "Reset layout" });
  reset.focus();
  await user.keyboard("{Enter}");
  expect(reset).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /save|delete|connect|run dag/i })).not.toBeInTheDocument();
  expect(screen.getByText(/React Flow/i)).toBeInTheDocument();
});

test("shows no-history and empty-DAG states without fabricating statuses", async () => {
  const empty: DagGraph = { ...dagGraph, tasks: [], latest_run: null, unmapped_task_history: [] };
  installApiMock({ "/api/v1/dags/empty/graph": { body: empty } });
  renderAt("empty");
  expect(await screen.findByRole("heading", { name: "No tasks to map" })).toBeInTheDocument();
  expect(screen.getByText("No persisted runs")).toBeInTheDocument();
  expect(screen.queryByTestId("dag-graph-canvas")).not.toBeInTheDocument();
});

test("renders typed not-found and safe malformed-data errors", async () => {
  installApiMock({
    "/api/v1/dags/missing/graph": {
      status: 404,
      body: { detail: { code: "dag_not_found", message: "DAG not found." } },
    },
  });
  const missing = render(<App />);
  window.history.replaceState(null, "", "/dags/missing/graph");
  window.dispatchEvent(new PopStateEvent("popstate"));
  expect(await screen.findByRole("heading", { name: "DAG not found" })).toBeInTheDocument();
  missing.unmount();

  installApiMock({ "/api/v1/dags/bad/graph": { body: { name: "bad", tasks: "secret" } } });
  renderAt("bad");
  expect(await screen.findByText("Nexolith returned malformed graph data.")).toBeInTheDocument();
});

test("contains layout failures and API failures behind safe messages", async () => {
  const cyclic: DagGraph = {
    ...dagGraph,
    tasks: [
      { name: "first", depends_on: ["second"], status: null },
      { name: "second", depends_on: ["first"], status: null },
    ],
  };
  installApiMock({ "/api/v1/dags/cyclic/graph": { body: cyclic } });
  const layoutFailure = render(<App />);
  window.history.replaceState(null, "", "/dags/cyclic/graph");
  window.dispatchEvent(new PopStateEvent("popstate"));
  expect(await screen.findByText(/could not be arranged safely/)).toBeInTheDocument();
  layoutFailure.unmount();

  installApiMock({
    "/api/v1/dags/offline/graph": new TypeError("NXL_UI_SECRET_SENTINEL private path"),
  });
  renderAt("offline");
  expect(await screen.findByText("Unable to reach this DAG graph.")).toBeInTheDocument();
  expect(screen.queryByText(/NXL_UI_SECRET_SENTINEL/)).not.toBeInTheDocument();
});

test("shows graph freshness without obscuring the last good data", async () => {
  installApiMock();
  renderAt();
  expect(await screen.findByTestId("dag-graph-canvas")).toBeInTheDocument();
  expect(screen.getByText(/refreshes every 5 seconds/)).toBeInTheDocument();
});
