import { useCallback, useEffect, useRef, useState } from "react";

import { ApiClientError } from "../api/client";
import type { ApiErrorCode } from "../api/types";

export type PollingResource<Data> =
  | { status: "loading" }
  | { status: "error"; message: string; code?: ApiErrorCode }
  | {
      status: "success";
      data: Data;
      refreshing: boolean;
      refreshError?: string;
    };

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function safeError(error: unknown, fallback: string): { message: string; code?: ApiErrorCode } {
  if (error instanceof ApiClientError) {
    return {
      message: error.message,
      ...(error.code === undefined ? {} : { code: error.code }),
    };
  }
  return { message: fallback };
}

export function usePollingResource<Data>(
  load: (signal: AbortSignal) => Promise<Data>,
  fallbackMessage: string,
  intervalMs = 5_000,
): { resource: PollingResource<Data>; refresh: () => void } {
  const [resource, setResource] = useState<PollingResource<Data>>({ status: "loading" });
  const refreshRef = useRef<(background: boolean) => void>(() => undefined);

  useEffect(() => {
    let disposed = false;
    let inFlight = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;
    let latestData: Data | undefined;

    const clearTimer = (): void => {
      if (timer !== undefined) {
        clearTimeout(timer);
        timer = undefined;
      }
    };

    const schedule = (): void => {
      clearTimer();
      if (!disposed && !document.hidden) {
        timer = setTimeout(() => {
          run(true);
        }, intervalMs);
      }
    };

    const run = (background: boolean): void => {
      if (disposed || inFlight || document.hidden) {
        return;
      }
      clearTimer();
      inFlight = true;
      controller = new AbortController();
      if (background && latestData !== undefined) {
        setResource({ status: "success", data: latestData, refreshing: true });
      }
      void load(controller.signal)
        .then(
          (data) => {
            if (disposed) {
              return;
            }
            latestData = data;
            setResource({ status: "success", data, refreshing: false });
          },
          (error: unknown) => {
            if (disposed || isAbortError(error)) {
              return;
            }
            const failure = safeError(error, fallbackMessage);
            if (latestData !== undefined) {
              setResource({
                status: "success",
                data: latestData,
                refreshing: false,
                refreshError: failure.message,
              });
            } else {
              setResource({ status: "error", ...failure });
            }
          },
        )
        .finally(() => {
          if (disposed) {
            return;
          }
          inFlight = false;
          controller = undefined;
          schedule();
        });
    };

    const handleVisibility = (): void => {
      clearTimer();
      if (document.hidden) {
        controller?.abort();
      } else {
        run(latestData !== undefined);
      }
    };

    refreshRef.current = run;
    document.addEventListener("visibilitychange", handleVisibility);
    run(false);
    return () => {
      disposed = true;
      clearTimer();
      controller?.abort();
      document.removeEventListener("visibilitychange", handleVisibility);
      refreshRef.current = () => undefined;
    };
  }, [fallbackMessage, intervalMs, load]);

  const refresh = useCallback(() => {
    refreshRef.current(true);
  }, []);
  return { resource, refresh };
}
