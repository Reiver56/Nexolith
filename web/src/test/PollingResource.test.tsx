import { act, render, screen } from "@testing-library/react";
import { useCallback } from "react";
import { afterEach, vi } from "vitest";

import { usePollingResource } from "../hooks/usePollingResource";

function Probe({ loader }: { loader: (signal: AbortSignal) => Promise<string> }) {
  const load = useCallback((signal: AbortSignal) => loader(signal), [loader]);
  const { resource } = usePollingResource(load, "Safe fallback.");
  if (resource.status === "loading") {
    return <span>loading</span>;
  }
  if (resource.status === "error") {
    return <span>{resource.message}</span>;
  }
  return (
    <div>
      <span>{resource.data}</span>
      {resource.refreshing ? <span>refreshing</span> : null}
      {resource.refreshError === undefined ? null : <span>{resource.refreshError}</span>}
    </div>
  );
}

afterEach(() => {
  vi.useRealTimers();
});

test("waits for each request to settle before scheduling the next poll", async () => {
  vi.useFakeTimers();
  let resolveFirst: ((value: string) => void) | undefined;
  const loader = vi.fn(
    () =>
      new Promise<string>((resolve) => {
        resolveFirst = resolve;
      }),
  );
  render(<Probe loader={loader} />);
  expect(loader).toHaveBeenCalledTimes(1);

  await act(() => vi.advanceTimersByTimeAsync(20_000));
  expect(loader).toHaveBeenCalledTimes(1);
  await act(async () => {
    resolveFirst?.("first result");
    await Promise.resolve();
  });
  expect(screen.getByText("first result")).toBeInTheDocument();

  await act(() => vi.advanceTimersByTimeAsync(4_999));
  expect(loader).toHaveBeenCalledTimes(1);
  await act(() => vi.advanceTimersByTimeAsync(1));
  expect(loader).toHaveBeenCalledTimes(2);
});

test("aborts an active request while hidden and refreshes when visible again", async () => {
  let hidden = false;
  vi.spyOn(document, "hidden", "get").mockImplementation(() => hidden);
  const signals: AbortSignal[] = [];
  const loader = vi.fn((signal: AbortSignal) => {
    signals.push(signal);
    return new Promise<string>((_resolve, reject) => {
      signal.addEventListener("abort", () => {
        reject(new DOMException("Request aborted.", "AbortError"));
      });
    });
  });
  render(<Probe loader={loader} />);
  expect(loader).toHaveBeenCalledTimes(1);

  hidden = true;
  await act(async () => {
    document.dispatchEvent(new Event("visibilitychange"));
    await Promise.resolve();
  });
  expect(signals[0]?.aborted).toBe(true);
  hidden = false;
  await act(async () => {
    document.dispatchEvent(new Event("visibilitychange"));
    await Promise.resolve();
  });
  expect(loader).toHaveBeenCalledTimes(2);
});

test("preserves last good data when a background refresh fails", async () => {
  vi.useFakeTimers();
  const loader = vi
    .fn<(signal: AbortSignal) => Promise<string>>()
    .mockResolvedValueOnce("trusted graph")
    .mockRejectedValueOnce(new Error("private implementation detail"));
  render(<Probe loader={loader} />);
  await act(async () => {
    await Promise.resolve();
  });
  expect(screen.getByText("trusted graph")).toBeInTheDocument();

  await act(() => vi.advanceTimersByTimeAsync(5_000));
  expect(screen.getByText("trusted graph")).toBeInTheDocument();
  expect(screen.getByText("Safe fallback.")).toBeInTheDocument();
});
