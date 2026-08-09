import { render, screen, waitFor, within } from "@testing-library/react";
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
  const attribution = screen.getByRole("link", { name: /React Flow/i });
  expect(attribution).toHaveAttribute("href", "https://reactflow.dev");
});

test("keeps graph nodes and route motion stable when equivalent polling data arrives", async () => {
  const { requests } = installApiMock();
  const user = userEvent.setup();
  renderAt();

  const canvas = await screen.findByTestId("dag-graph-canvas");
  const routeView = document.querySelector(".route-view");
  const taskNode = canvas.querySelector('[data-testid="rf__node-task:extract"] .task-node');
  expect(routeView).not.toBeNull();
  expect(taskNode).not.toBeNull();
  const readsBefore = requests.filter(
    (request) => request.method === "GET" && request.path.endsWith("/graph"),
  ).length;

  await user.click(screen.getByRole("button", { name: /^Refresh$/ }));
  await waitFor(() => {
    expect(
      requests.filter(
        (request) => request.method === "GET" && request.path.endsWith("/graph"),
      ).length,
    ).toBeGreaterThan(readsBefore);
  });

  expect(document.querySelector(".route-view")).toBe(routeView);
  expect(canvas.querySelector('[data-testid="rf__node-task:extract"] .task-node')).toBe(taskNode);
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
      { name: "first", kind: "script", depends_on: ["second"], status: null },
      { name: "second", kind: "pipeline", depends_on: ["first"], status: null },
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
  expect(screen.getByText(/refreshes every 10 seconds/)).toBeInTheDocument();
});

test("shows persisted live run progress and schedule state", async () => {
  installApiMock({
    "/api/v1/dags/billing-close/graph": {
      body: {
        ...dagGraph,
        enabled: false,
        latest_run: { ...dagGraph.latest_run, status: "running", ended_at: null },
      },
    },
  });
  renderAt();

  expect(await screen.findByText("Paused")).toBeInTheDocument();
  expect(screen.getAllByText("Running").length).toBeGreaterThan(0);
  expect(screen.getByRole("region", { name: "Live DAG status" })).toHaveTextContent(
    "2 of 3 completed",
  );
  expect(screen.getAllByText("enrich").length).toBeGreaterThan(0);
  expect(screen.getByText(/refreshes every second/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Resume schedule" })).toBeInTheDocument();
});

test("labels event-driven DAGs honestly and omits interval schedule controls", async () => {
  installApiMock({
    "/api/v1/dags/event-only/graph": {
      body: { ...dagGraph, name: "event-only", schedule: null },
    },
  });
  renderAt("event-only");

  expect(await screen.findByText("Event-driven")).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: /^(Pause|Resume) schedule$/i }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Trigger run" })).toBeInTheDocument();
});

test("rejects graph data with missing schedule state", async () => {
  const withoutSchedule: Record<string, unknown> = { ...dagGraph };
  delete withoutSchedule.schedule;
  installApiMock({ "/api/v1/dags/malformed/graph": { body: withoutSchedule } });
  renderAt("malformed");

  expect(await screen.findByText("Nexolith returned malformed graph data.")).toBeInTheDocument();
});

test("shows truthful task icons without labelling ambiguous pipelines as SQL", async () => {
  installApiMock();
  renderAt();

  expect((await screen.findAllByRole("img", { name: "Pipeline task" })).length).toBeGreaterThan(0);
  expect(screen.getByRole("img", { name: "Python script task" })).toBeInTheDocument();
  expect(screen.getAllByText("Pipeline").length).toBeGreaterThan(0);
  expect(screen.queryByText(/SQL task/i)).not.toBeInTheDocument();
});

test("opens, switches, and closes task details with keyboard focus restoration", async () => {
  installApiMock();
  const user = userEvent.setup();
  renderAt();

  const extract = await screen.findByRole("button", { name: /Open details for extract/i });
  await user.click(extract);
  const closeExtract = await screen.findByRole("button", { name: "Close details for extract" });
  expect(closeExtract).toHaveFocus();
  expect(screen.getByRole("heading", { name: "Pipeline definition" })).toBeInTheDocument();
  expect(screen.getByText("name: extract-orders", { exact: false })).toBeInTheDocument();

  await user.click(closeExtract);
  expect(screen.queryByRole("heading", { name: "Pipeline definition" })).not.toBeInTheDocument();
  expect(extract).toHaveFocus();
  await user.click(extract);
  await screen.findByRole("button", { name: "Close details for extract" });

  const enrich = screen.getByRole("button", { name: /Open details for enrich/i });
  await user.click(enrich);
  expect(await screen.findByRole("button", { name: "Close details for enrich" })).toHaveFocus();
  expect(screen.getByRole("heading", { name: "Python source" })).toBeInTheDocument();
  expect(screen.getByText("def run(context):", { exact: false })).toBeInTheDocument();

  await user.keyboard("{Escape}");
  expect(screen.queryByRole("heading", { name: "Python source" })).not.toBeInTheDocument();
  expect(enrich).toHaveFocus();
});

test("renders source-like HTML as text and contains long lines in the code area", async () => {
  const unsafeSource = '<img src=x onerror="window.pwned=true">' + "x".repeat(600);
  installApiMock({
    "/api/v1/task-details?dag_name=billing-close&task_name=extract": {
      body: {
        dag_name: "billing-close",
        task_name: "extract",
        kind: "pipeline",
        depends_on: [],
        latest_status: null,
        retry: { retries: 0, retry_delay_seconds: 0, retry_backoff_multiplier: 1 },
        source_language: "yaml",
        source: unsafeSource,
        source_size_bytes: unsafeSource.length,
      },
    },
  });
  const user = userEvent.setup();
  renderAt();
  await user.click(await screen.findByRole("button", { name: /Open details for extract/i }));

  const code = await screen.findByText(unsafeSource);
  expect(code.tagName).toBe("CODE");
  expect(screen.queryAllByRole("img").length).toBeGreaterThan(0);
  expect(document.querySelector(".task-source img")).toBeNull();
  expect(code.closest("pre")).toHaveAttribute("data-language", "yaml");
});

test("shows typed loading and error states and ignores stale task responses", async () => {
  let resolveExtract: ((value: { body: unknown }) => void) | undefined;
  installApiMock({
    "/api/v1/task-details?dag_name=billing-close&task_name=extract": () =>
      new Promise((resolve) => {
        resolveExtract = resolve;
      }),
    "/api/v1/task-details?dag_name=billing-close&task_name=enrich": {
      status: 415,
      body: {
        detail: {
          code: "task_source_invalid_encoding",
          message: "The task source is not valid UTF-8.",
        },
      },
    },
  });
  const user = userEvent.setup();
  renderAt();
  await user.click(await screen.findByRole("button", { name: /Open details for extract/i }));
  expect(screen.getByRole("status")).toHaveTextContent("Loading task details");

  await user.click(screen.getByRole("button", { name: /Open details for enrich/i }));
  expect(await screen.findByText("This source cannot be displayed as UTF-8 text.")).toBeInTheDocument();

  resolveExtract?.({
    body: {
      dag_name: "billing-close",
      task_name: "extract",
      kind: "pipeline",
      depends_on: [],
      latest_status: null,
      retry: { retries: 0, retry_delay_seconds: 0, retry_backoff_multiplier: 1 },
      source_language: "yaml",
      source: "stale-source-must-not-render",
      source_size_bytes: 28,
    },
  });
  await Promise.resolve();
  expect(screen.queryByText("stale-source-must-not-render")).not.toBeInTheDocument();
});

test("shows an explicit empty source state", async () => {
  installApiMock({
    "/api/v1/task-details?dag_name=billing-close&task_name=extract": {
      body: {
        dag_name: "billing-close",
        task_name: "extract",
        kind: "pipeline",
        depends_on: [],
        latest_status: null,
        retry: { retries: 0, retry_delay_seconds: 0, retry_backoff_multiplier: 1 },
        source_language: "yaml",
        source: "",
        source_size_bytes: 0,
      },
    },
  });
  const user = userEvent.setup();
  renderAt();
  await user.click(await screen.findByRole("button", { name: /Open details for extract/i }));

  expect(await screen.findByText("The source file is empty.")).toBeInTheDocument();
  expect(document.querySelector(".task-source pre")).toBeNull();
});

test("refreshes open task details when graph polling returns new data", async () => {
  let graphRequests = 0;
  let detailRequests = 0;
  const updatedGraph: DagGraph = {
    ...dagGraph,
    tasks: dagGraph.tasks.map((task) =>
      task.name === "extract" ? { ...task, status: "running" } : task,
    ),
  };
  installApiMock({
    "/api/v1/dags/billing-close/graph": () => {
      graphRequests += 1;
      return { body: graphRequests === 1 ? dagGraph : updatedGraph };
    },
    "/api/v1/task-details?dag_name=billing-close&task_name=extract": () => {
      detailRequests += 1;
      return {
        body: {
          dag_name: "billing-close",
          task_name: "extract",
          kind: "pipeline",
          depends_on: [],
          latest_status: detailRequests === 1 ? "succeeded" : "running",
          retry: { retries: 0, retry_delay_seconds: 0, retry_backoff_multiplier: 1 },
          source_language: "yaml",
          source: detailRequests === 1 ? "first revision" : "second revision",
          source_size_bytes: 15,
        },
      };
    },
  });
  const user = userEvent.setup();
  renderAt();
  await user.click(await screen.findByRole("button", { name: /Open details for extract/i }));
  expect(await screen.findByText("first revision")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: /^Refresh$/ }));

  expect(await screen.findByText("second revision")).toBeInTheDocument();
  expect(detailRequests).toBe(2);
});

test("closes open task details when refreshed graph data removes the task", async () => {
  let graphRequests = 0;
  const withoutExtract: DagGraph = {
    ...dagGraph,
    tasks: dagGraph.tasks.filter((task) => task.name !== "extract"),
  };
  installApiMock({
    "/api/v1/dags/billing-close/graph": () => {
      graphRequests += 1;
      return { body: graphRequests === 1 ? dagGraph : withoutExtract };
    },
  });
  const user = userEvent.setup();
  renderAt();
  await user.click(await screen.findByRole("button", { name: /Open details for extract/i }));
  expect(await screen.findByRole("button", { name: "Close details for extract" })).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: /^Refresh$/ }));

  await waitFor(() => {
    expect(screen.queryByRole("button", { name: "Close details for extract" })).not.toBeInTheDocument();
  });
});
