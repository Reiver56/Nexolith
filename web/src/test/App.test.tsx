import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { App } from "../App";
import { schedulerStopped } from "./fixtures";
import { installApiMock } from "./mockApi";

function renderAt(path: string): ReturnType<typeof render> {
  window.history.replaceState(null, "", path);
  return render(<App />);
}

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
  installApiMock();
  const overview = renderAt("/");
  expect(await screen.findByRole("heading", { name: "DAGs" })).toBeInTheDocument();
  expect(window.location.pathname).toBe("/dags");
  overview.unmount();

  renderAt("/does-not-exist");
  const main = screen.getByRole("main");
  expect(within(main).getByRole("heading", { name: "Page not found" })).toBeInTheDocument();
});
