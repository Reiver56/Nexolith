import { useCallback, useEffect, useState } from "react";

import { ApiClientError } from "../api/client";
import type { ApiErrorCode } from "../api/types";

export type AsyncResource<Data> =
  | { status: "loading" }
  | { status: "success"; data: Data }
  | { status: "error"; message: string; code?: ApiErrorCode };

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

export function useApiResource<Data>(
  load: (signal: AbortSignal) => Promise<Data>,
  fallbackMessage: string,
): { resource: AsyncResource<Data>; reload: () => void } {
  const [revision, setRevision] = useState(0);
  const [resource, setResource] = useState<AsyncResource<Data>>({ status: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    void load(controller.signal).then(
      (data) => {
        if (active) {
          setResource({ status: "success", data });
        }
      },
      (error: unknown) => {
        if (!active || isAbortError(error)) {
          return;
        }
        if (error instanceof ApiClientError) {
          setResource({
            status: "error",
            message: error.message,
            ...(error.code === undefined ? {} : { code: error.code }),
          });
          return;
        }
        setResource({ status: "error", message: fallbackMessage });
      },
    );
    return () => {
      active = false;
      controller.abort();
    };
  }, [fallbackMessage, load, revision]);

  const reload = useCallback(() => {
    setResource({ status: "loading" });
    setRevision((current) => current + 1);
  }, []);
  return { resource, reload };
}
