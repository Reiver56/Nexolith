import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { App } from "../App";
import { dagGraph, runDetail, schedulerStopped } from "./fixtures";
import { installApiMock } from "./mockApi";

function renderAt(path: string): ReturnType<typeof render> {
  window.history.replaceState(null, "", path);
  return render(<App />);
}

function requestCount(
  requests: ReturnType<typeof installApiMock>["requests"],
  method: string,
  path: string,
): number {
  return requests.filter((request) => request.method === method && request.path === path).length;
}

test("registers a DAG only after confirmation and refreshes the registry", async () => {
  const { requests } = installApiMock();
  const user = userEvent.setup();
  renderAt("/dags");
  await screen.findByRole("heading", { name: "DAGs" });

  const opener = screen.getByRole("button", { name: "Register DAG" });
  await user.click(opener);
  const cancelled = screen.getByRole("alertdialog", { name: "Register this DAG?" });
  expect(within(cancelled).getByRole("button", { name: "Cancel" })).toHaveFocus();
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  expect(opener).toHaveFocus();
  expect(requestCount(requests, "POST", "/api/v1/dags/registrations")).toBe(0);

  const readsBefore = requestCount(requests, "GET", "/api/v1/dags");
  await user.click(opener);
  const dialog = screen.getByRole("alertdialog", { name: "Register this DAG?" });
  await user.type(within(dialog).getByRole("textbox", { name: "DAG YAML path" }), "workflows/new-orders.yaml");
  await user.click(within(dialog).getByRole("checkbox"));
  await user.click(within(dialog).getByRole("button", { name: "Register DAG" }));

  expect(await screen.findByText("new-orders was registered.")).toBeInTheDocument();
  await waitFor(() => {
    expect(requestCount(requests, "GET", "/api/v1/dags")).toBeGreaterThan(readsBefore);
  });
  const request = requests.find(
    (candidate) => candidate.method === "POST" && candidate.path === "/api/v1/dags/registrations",
  );
  expect(request?.body).toEqual({ source_path: "workflows/new-orders.yaml", force: true });
  expect(request?.headers.get("content-type")).toBe("application/json");
  expect(request?.headers.get("accept")).toBe("application/json");
});

test("triggers a DAG only after confirmation and opens the authoritative run", async () => {
  const { requests } = installApiMock({
    "/api/v1/runs/24": { body: { ...runDetail, id: 24 } },
  });
  const user = userEvent.setup();
  renderAt("/dags");
  const table = await screen.findByRole("table");
  const billingRow = screen.getByText("billing-close").closest("tr");
  if (billingRow === null) {
    throw new Error("billing-close row missing");
  }
  const opener = within(billingRow).getByRole("button", { name: "Trigger run" });

  await user.click(opener);
  let dialog = screen.getByRole("alertdialog", { name: "Trigger billing-close?" });
  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(requestCount(requests, "POST", "/api/v1/dags/billing-close/runs")).toBe(0);

  await user.click(opener);
  dialog = screen.getByRole("alertdialog", { name: "Trigger billing-close?" });
  await user.click(within(dialog).getByRole("button", { name: "Trigger run" }));
  await screen.findByRole("heading", { name: "billing-close" });
  expect(window.location.pathname).toBe("/runs/24");
  expect(requestCount(requests, "POST", "/api/v1/dags/billing-close/runs")).toBe(1);
  expect(
    requests.find((request) => request.path === "/api/v1/dags/billing-close/runs")?.body,
  ).toEqual({ confirm: true });
  expect(table).not.toBeInTheDocument();
});

test("pauses and resumes scheduling after accessible confirmation", async () => {
  const pausedGraph = { ...dagGraph, enabled: false };
  let graphReads = 0;
  const { requests } = installApiMock({
    "/api/v1/dags/billing-close/graph": () => {
      graphReads += 1;
      return { body: graphReads === 1 ? dagGraph : pausedGraph };
    },
  });
  const user = userEvent.setup();
  renderAt("/dags/billing-close/graph");
  const pause = await screen.findByRole("button", { name: "Pause schedule" });

  await user.click(pause);
  let dialog = screen.getByRole("alertdialog", { name: "Pause schedule for billing-close?" });
  expect(within(dialog).getByText(/Active runs and manual, API, or event-triggered runs/)).toBeInTheDocument();
  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(requestCount(requests, "POST", "/api/v1/dag-schedules/pause")).toBe(0);

  await user.click(pause);
  dialog = screen.getByRole("alertdialog", { name: "Pause schedule for billing-close?" });
  await user.click(within(dialog).getByRole("button", { name: "Pause schedule" }));
  expect(await screen.findByRole("button", { name: "Resume schedule" })).toBeInTheDocument();
  const pauseRequest = requests.find(
    (request) => request.method === "POST" && request.path === "/api/v1/dag-schedules/pause",
  );
  expect(pauseRequest?.body).toEqual({ confirm: true, dag_name: "billing-close" });
  expect(pauseRequest?.headers.get("content-type")).toBe("application/json");

  await user.click(screen.getByRole("button", { name: "Resume schedule" }));
  dialog = screen.getByRole("alertdialog", { name: "Resume schedule for billing-close?" });
  await user.click(within(dialog).getByRole("button", { name: "Resume schedule" }));
  expect(requestCount(requests, "POST", "/api/v1/dag-schedules/resume")).toBe(1);
});

test("starts the scheduler only after confirmation and refreshes backend status", async () => {
  const { requests } = installApiMock({ "/api/v1/scheduler": { body: schedulerStopped } });
  const user = userEvent.setup();
  renderAt("/scheduler");
  const opener = await screen.findByRole("button", { name: "Start scheduler" });

  await user.click(opener);
  expect(screen.getByRole("alertdialog", { name: "Start the scheduler?" })).toBeInTheDocument();
  await user.keyboard("{Escape}");
  expect(requestCount(requests, "POST", "/api/v1/scheduler/start")).toBe(0);

  await user.click(opener);
  const dialog = screen.getByRole("alertdialog", { name: "Start the scheduler?" });
  await user.click(within(dialog).getByRole("button", { name: "Start scheduler" }));
  expect(await screen.findByText("Scheduler started and verified.")).toBeInTheDocument();
  expect(requestCount(requests, "POST", "/api/v1/scheduler/start")).toBe(1);
  expect(requestCount(requests, "GET", "/api/v1/scheduler")).toBeGreaterThan(2);
});

test("stops the scheduler only after confirmation and refreshes backend status", async () => {
  const { requests } = installApiMock();
  const user = userEvent.setup();
  renderAt("/scheduler");
  const opener = await screen.findByRole("button", { name: "Stop scheduler" });

  await user.click(opener);
  let dialog = screen.getByRole("alertdialog", { name: "Stop the scheduler?" });
  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(requestCount(requests, "POST", "/api/v1/scheduler/stop")).toBe(0);

  await user.click(opener);
  dialog = screen.getByRole("alertdialog", { name: "Stop the scheduler?" });
  await user.click(within(dialog).getByRole("button", { name: "Stop scheduler" }));
  expect(await screen.findByText("Scheduler stopped and verified.")).toBeInTheDocument();
  expect(requestCount(requests, "POST", "/api/v1/scheduler/stop")).toBe(1);
  expect(requestCount(requests, "GET", "/api/v1/scheduler")).toBeGreaterThan(2);
});

test("renders typed protected action failures without exposing backend internals", async () => {
  installApiMock({
    "POST /api/v1/dags/billing-close/runs": {
      status: 503,
      body: {
        detail: {
          code: "state_unavailable",
          message: "C:\\Users\\operator\\secret-state.db",
        },
      },
    },
  });
  const user = userEvent.setup();
  renderAt("/dags/billing-close/graph");
  await screen.findByTestId("dag-graph-canvas");
  await user.click(screen.getByRole("button", { name: "Trigger run" }));
  const dialog = screen.getByRole("alertdialog", { name: "Trigger billing-close?" });
  await user.click(within(dialog).getByRole("button", { name: "Trigger run" }));
  expect(
    await within(dialog).findByText("Nexolith returned a protected error response."),
  ).toBeInTheDocument();
  expect(dialog).not.toHaveTextContent("C:\\Users\\operator");
});

test("prevents duplicate submissions and traps keyboard focus", async () => {
  let release: ((value: { body: unknown; status: number }) => void) | undefined;
  const pending = new Promise<{ body: unknown; status: number }>((resolve) => {
    release = resolve;
  });
  let submissions = 0;
  const { requests } = installApiMock({
    "POST /api/v1/dags/registrations": () => {
      submissions += 1;
      return pending;
    },
  });
  const user = userEvent.setup();
  renderAt("/dags");
  const opener = await screen.findByRole("button", { name: "Register DAG" });
  await user.click(opener);
  const dialog = screen.getByRole("alertdialog", { name: "Register this DAG?" });
  const input = within(dialog).getByRole("textbox", { name: "DAG YAML path" });
  const confirm = within(dialog).getByRole("button", { name: "Register DAG" });
  confirm.focus();
  await user.tab();
  expect(input).toHaveFocus();
  await user.type(input, "orders.yaml");
  await user.click(confirm);
  expect(within(dialog).getByRole("button", { name: "Working…" })).toBeDisabled();
  await user.click(within(dialog).getByRole("button", { name: "Working…" }));
  expect(submissions).toBe(1);
  expect(requestCount(requests, "POST", "/api/v1/dags/registrations")).toBe(1);

  release?.({
    status: 201,
    body: { dag_name: "orders", status: "created", enabled: true, schedule: null },
  });
  expect(await screen.findByText("orders was registered.")).toBeInTheDocument();
});

test.each(["percent%name", "slash/name", "space name", "caffè-東京", "query?#name"])(
  "preserves encoded DAG names when triggering %s",
  async (name) => {
    const encoded = encodeURIComponent(name);
    const { requests } = installApiMock({
      [`/api/v1/dags/${encoded}/graph`]: { body: { ...dagGraph, name } },
      [`POST /api/v1/dags/${encoded}/runs`]: {
        body: { run_id: 24, dag_name: name, status: "succeeded" },
        status: 201,
      },
      "/api/v1/runs/24": { body: { ...runDetail, id: 24, dag_name: name } },
    });
    const user = userEvent.setup();
    renderAt(`/dags/${encoded}/graph`);
    await screen.findByRole("heading", { name, level: 1 });
    await user.click(screen.getByRole("button", { name: "Trigger run" }));
    const dialog = screen.getByRole("alertdialog", { name: `Trigger ${name}?` });
    await user.click(within(dialog).getByRole("button", { name: "Trigger run" }));
    await waitFor(() => {
      expect(window.location.pathname).toBe("/runs/24");
    });
    expect(
      requests.some(
        (request) => request.method === "POST" && request.path === `/api/v1/dags/${encoded}/runs`,
      ),
    ).toBe(true);
  },
);

test("the loopback development proxy preserves the API same-origin action check", () => {
  const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
  const viteConfig = readFileSync(resolve(webRoot, "vite.config.ts"), "utf8");
  expect(viteConfig).toContain(
    'const API_PROXY_TARGET = process.env.NEXOLITH_API_URL ?? "http://127.0.0.1:8765"',
  );
  expect(viteConfig).toContain("changeOrigin: true");
  expect(viteConfig).toContain("headers: { Origin: API_PROXY_TARGET }");
  expect(viteConfig).toContain('host: "127.0.0.1"');
});
