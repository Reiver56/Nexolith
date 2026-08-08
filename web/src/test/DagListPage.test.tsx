import { render, screen, within } from "@testing-library/react";

import { App } from "../App";
import { installApiMock } from "./mockApi";

function renderDags(): void {
  window.history.replaceState(null, "", "/dags");
  render(<App />);
}

function rowAt(rows: HTMLElement[], index: number): HTMLElement {
  const row = rows[index];
  if (row === undefined) {
    throw new Error(`Expected table row ${String(index)}.`);
  }
  return row;
}

test("renders DAGs in stable order with textual priority and severity", async () => {
  installApiMock();
  renderDags();

  const table = await screen.findByRole("table");
  const rows = within(table).getAllByRole("row").slice(1);
  const first = rowAt(rows, 0);
  const second = rowAt(rows, 1);
  expect(within(first).getByText("billing-close")).toBeInTheDocument();
  expect(within(first).getByText("Critical")).toBeInTheDocument();
  expect(within(first).getByText("High")).toBeInTheDocument();
  expect(within(first).getByText("After warehouse-refresh")).toBeInTheDocument();
  expect(within(second).getByText("Disabled")).toBeInTheDocument();
  expect(within(second).getByText("Low")).toBeInTheDocument();
  expect(within(second).getByText("Medium")).toBeInTheDocument();
  expect(within(second).getByText("30m")).toBeInTheDocument();
  expect(within(first).getByText("billing-close").closest("th")).toHaveAttribute(
    "data-label",
    "DAG",
  );
});

test("renders the DAG empty state", async () => {
  installApiMock({ "/api/v1/dags": { body: [] } });
  renderDags();
  expect(await screen.findByRole("heading", { name: "No registered DAGs" })).toBeInTheDocument();
});

test("renders a typed safe DAG error without leaking a path", async () => {
  installApiMock({
    "/api/v1/dags": {
      status: 503,
      body: {
        detail: {
          code: "state_unavailable",
          message: "C:\\Users\\operator\\NXL_UI_SECRET_SENTINEL.db",
        },
      },
    },
  });
  renderDags();
  expect(await screen.findByText("Nexolith returned a protected error response.")).toBeInTheDocument();
  expect(screen.queryByText(/NXL_UI_SECRET_SENTINEL/)).not.toBeInTheDocument();
});
