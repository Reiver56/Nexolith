import { readFileSync } from "node:fs";

import { render, screen } from "@testing-library/react";
import { vi } from "vitest";

import { EmptyState, ErrorState, LoadingState } from "../components/AsyncStates";
import { deliberateMotionDuration, prefersReducedMotion } from "../utils/motion";

test("defines shared motion tokens and a narrow reduced-motion override", () => {
  const tokens = readFileSync("src/styles/tokens.css", "utf8");
  const appStyles = readFileSync("src/styles/app.css", "utf8");
  const graphStyles = readFileSync("src/styles/graph.css", "utf8");

  expect(tokens).toContain("--motion-duration-fast: 120ms");
  expect(tokens).toContain("--motion-duration-standard: 180ms");
  expect(tokens).toContain("--motion-duration-deliberate: 240ms");
  expect(tokens).toContain("--motion-ease-standard:");
  expect(tokens).toContain("--motion-ease-emphasized:");
  expect(appStyles).toContain("@media (prefers-reduced-motion: reduce)");
  expect(appStyles).toContain("animation: none !important");
  expect(appStyles).toContain("transition-duration: 0.01ms !important");
  expect(`${appStyles}\n${graphStyles}`).not.toMatch(/transition:\s*all/i);
});

test("reports reduced-motion preference through the shared runtime boundary", () => {
  const matchMedia = vi.fn((query: string) => ({
    matches: query === "(prefers-reduced-motion: reduce)",
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(() => true),
  })) as unknown as typeof window.matchMedia;
  vi.stubGlobal("matchMedia", matchMedia);

  expect(prefersReducedMotion()).toBe(true);
  expect(deliberateMotionDuration()).toBe(0);
  expect(matchMedia).toHaveBeenCalledWith("(prefers-reduced-motion: reduce)");
});

test("keeps loading, empty, and error states explicit and accessible", () => {
  const loading = render(<LoadingState label="Loading monitor data" />);
  expect(screen.getByLabelText("Loading monitor data")).toHaveAttribute("data-state", "loading");
  loading.unmount();

  const empty = render(<EmptyState title="Nothing here" message="No records were found." />);
  expect(screen.getByRole("heading", { name: "Nothing here" }).closest("section")).toHaveAttribute(
    "data-state",
    "empty",
  );
  empty.unmount();

  render(<ErrorState message="The network is unavailable." retry={() => undefined} />);
  expect(screen.getByText("The network is unavailable.").closest("section")).toHaveAttribute(
    "data-state",
    "error",
  );
  expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
});

test("keeps responsive, theme, and React Flow attribution policies explicit", () => {
  const tokens = readFileSync("src/styles/tokens.css", "utf8");
  const appStyles = readFileSync("src/styles/app.css", "utf8");
  const graphStyles = readFileSync("src/styles/graph.css", "utf8");
  const graphCanvas = readFileSync("src/features/dags/graph/DagGraphCanvas.tsx", "utf8");

  expect(tokens).toContain("@media (prefers-color-scheme: dark)");
  expect(appStyles).toContain("@media (max-width: 64rem)");
  expect(appStyles).toContain("@media (max-width: 44rem)");
  expect(appStyles).toMatch(/\.icon-button\s*\{[\s\S]*?width:\s*2\.75rem/);
  expect(appStyles).toMatch(/\.primary-nav a\s*\{[\s\S]*?min-height:\s*2\.75rem/);
  expect(graphCanvas).toContain('attributionPosition="bottom-left"');
  expect(graphCanvas).not.toContain("hideAttribution");
  expect(graphStyles).toContain(".graph-canvas .react-flow__attribution");
  expect(graphStyles).toContain("opacity: 1");
  expect(graphStyles).not.toMatch(/react-flow__attribution[^}]*opacity:\s*0/);
});
