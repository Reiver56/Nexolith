import { readFileSync } from "node:fs";

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { App } from "../App";
import { schedulerStopped } from "./fixtures";
import { installApiMock } from "./mockApi";

function renderAt(path: string): ReturnType<typeof render> {
  window.history.replaceState(null, "", path);
  return render(<App />);
}

test("declares the repository-owned Nexolith favicon", () => {
  const html = readFileSync("index.html", "utf-8");
  expect(html).toContain('rel="icon"');
  expect(html).toContain('href="/nexo-icon.png"');
});

test("uses decorative Nexolith artwork for the product mark", () => {
  installApiMock();
  renderAt("/dags");

  const productLink = screen.getByRole("link", { name: "Nexolith monitoring home" });
  expect(within(productLink).getByText("Nexolith")).toBeInTheDocument();
  expect(within(productLink).getByText("Monitor")).toBeInTheDocument();

  const productMark = productLink.querySelector<HTMLImageElement>("img.product-mark");
  expect(productMark).not.toBeNull();
  expect(productMark).toHaveAttribute("alt", "");
  expect(productMark).toHaveAttribute("aria-hidden", "true");
  expect(productMark).toHaveAttribute("width", "32");
  expect(productMark).toHaveAttribute("height", "32");
  expect(productMark?.src).toContain("nexo-monitor-icon.png");
  expect(productLink.querySelector("svg.product-mark")).not.toBeInTheDocument();
});

test("renders the application shell and keyboard-accessible navigation", async () => {
  installApiMock();
  const user = userEvent.setup();
  renderAt("/dags");

  const heading = await screen.findByRole("heading", { name: "DAGs" });
  expect(heading).toHaveFocus();
  expect(screen.getByRole("link", { name: "Skip to content" })).toHaveAttribute(
    "href",
    "#main-content",
  );

  const runsLink = screen.getByRole("link", { name: "Runs" });
  runsLink.focus();
  await user.keyboard("{Enter}");
  expect(await screen.findByRole("heading", { name: "Runs" })).toBeInTheDocument();
  expect(window.location.pathname).toBe("/runs");
  expect(runsLink).toHaveAttribute("aria-current", "page");
});

test("shows running, stopped, unknown, and unreachable system states", async () => {
  installApiMock();
  const running = renderAt("/dags");
  expect(await screen.findByText("Scheduler running")).toBeInTheDocument();
  running.unmount();

  installApiMock({ "/api/v1/scheduler": { body: schedulerStopped } });
  const stopped = renderAt("/dags");
  expect(await screen.findByText("Scheduler stopped")).toBeInTheDocument();
  stopped.unmount();

  installApiMock({
    "/api/v1/scheduler": {
      status: 503,
      body: {
        detail: {
          code: "scheduler_identity_unavailable",
          message: "Scheduler identity cannot be verified.",
        },
      },
    },
  });
  const unknown = renderAt("/dags");
  expect(await screen.findByText("Scheduler unknown")).toBeInTheDocument();
  unknown.unmount();

  installApiMock({ "/api/v1": new TypeError("connection details must not render") });
  renderAt("/dags");
  expect(await screen.findByText("API unreachable")).toBeInTheDocument();
});

test("redirects the overview and provides a useful unknown-route view", async () => {
  const { requests } = installApiMock();
  const overview = renderAt("/");
  expect(await screen.findByRole("heading", { name: "DAGs" })).toBeInTheDocument();
  expect(window.location.pathname).toBe("/dags");
  expect(
    requests.filter((request) => request.method === "GET" && request.path === "/api/v1/dags"),
  ).toHaveLength(1);
  overview.unmount();

  const unknown = renderAt("/does-not-exist");
  const main = screen.getByRole("main");
  expect(within(main).getByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  unknown.unmount();

  renderAt("/runs/%");
  expect(screen.getByRole("heading", { name: "Page not found" })).toBeInTheDocument();
});

test.each(["percent%name", "slash/name", "space name", "caffè-東京", "query?#name"])(
  "round-trips encoded DAG names through the graph route",
  async (name) => {
    installApiMock({
      [`/api/v1/dags/${encodeURIComponent(name)}/graph`]: {
        body: {
          name,
          enabled: true,
          schedule: null,
          trigger: null,
          tasks: [],
          latest_run: null,
          unmapped_task_history: [],
        },
      },
    });
    renderAt(`/dags/${encodeURIComponent(name)}/graph`);
    expect(await screen.findByRole("heading", { name, level: 1 })).toBeInTheDocument();
  },
);

test("rejects malformed graph route encoding without making a graph request", () => {
  const { requests } = installApiMock();
  renderAt("/dags/%/graph");
  expect(screen.getByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  expect(requests.some((request) => request.path.includes("/graph"))).toBe(false);
});
