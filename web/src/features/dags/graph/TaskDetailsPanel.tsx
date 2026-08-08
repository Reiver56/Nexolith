import { useCallback, useEffect, useRef } from "react";

import { getDagTaskSource } from "../../../api/client";
import type { ApiErrorCode } from "../../../api/types";
import { StatusBadge } from "../../../components/StatusBadge";
import { useApiResource } from "../../../hooks/useApiResource";
import { TaskKindIcon } from "./TaskKindIcon";
import { taskKindLabel } from "./taskKinds";

function errorMessage(code: ApiErrorCode | undefined, fallback: string): string {
  if (code === "source_access_not_allowed") {
    return "Source details are available only from a loopback-only Nexolith API.";
  }
  if (code === "task_source_too_large") {
    return "This source is larger than the 256 KiB display limit.";
  }
  if (code === "task_source_binary" || code === "task_source_invalid_encoding") {
    return "This source cannot be displayed as UTF-8 text.";
  }
  if (code === "task_source_unavailable") {
    return "This task source is unavailable or unreadable.";
  }
  if (code === "task_not_found" || code === "dag_not_found") {
    return "This task is no longer present in the registered DAG.";
  }
  return fallback;
}

export function TaskDetailsPanel({
  dagName,
  taskName,
  onClose,
}: {
  dagName: string;
  taskName: string;
  onClose: () => void;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const load = useCallback(
    (signal: AbortSignal) => getDagTaskSource(dagName, taskName, signal),
    [dagName, taskName],
  );
  const { resource, reload } = useApiResource(load, "Unable to load task details.");

  useEffect(() => {
    closeRef.current?.focus();
  }, [taskName]);

  useEffect(() => {
    const handleEscape = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", handleEscape);
    return () => {
      window.removeEventListener("keydown", handleEscape);
    };
  }, [onClose]);

  return (
    <aside className="task-details-panel" aria-labelledby="task-details-title">
      <header className="task-details-panel__header">
        <div>
          <p className="eyebrow">Task details</p>
          <h2 id="task-details-title">{taskName}</h2>
        </div>
        <button
          ref={closeRef}
          className="icon-button task-details-panel__close"
          type="button"
          onClick={onClose}
          aria-label={`Close details for ${taskName}`}
          title="Close task details"
        >
          <svg aria-hidden="true" viewBox="0 0 20 20">
            <path d="m5 5 10 10M15 5 5 15" />
          </svg>
        </button>
      </header>
      {resource.status === "loading" ? (
        <p className="task-details-panel__state" role="status">Loading task details…</p>
      ) : resource.status === "error" ? (
        <div className="task-details-panel__state" role="alert">
          <p>{errorMessage(resource.code, resource.message)}</p>
          <button className="button button--secondary button--compact" type="button" onClick={reload}>
            Try again
          </button>
        </div>
      ) : (
        <div className="task-details-panel__content">
          <dl className="task-details-list">
            <div>
              <dt>Kind</dt>
              <dd className="task-details-kind">
                <TaskKindIcon kind={resource.data.kind} />
                {taskKindLabel(resource.data.kind)}
              </dd>
            </div>
            <div>
              <dt>Latest status</dt>
              <dd>
                {resource.data.latest_status === null ? (
                  <span className="muted">No persisted status</span>
                ) : (
                  <StatusBadge value={resource.data.latest_status} />
                )}
              </dd>
            </div>
            <div>
              <dt>Dependencies</dt>
              <dd>
                {resource.data.depends_on.length === 0
                  ? "None"
                  : resource.data.depends_on.join(", ")}
              </dd>
            </div>
            <div>
              <dt>Retries</dt>
              <dd>
                {resource.data.retry.retries} · {resource.data.retry.retry_delay_seconds}s delay · {resource.data.retry.retry_backoff_multiplier}× backoff
              </dd>
            </div>
          </dl>
          <section className="task-source" aria-labelledby="task-source-title">
            <div className="task-source__heading">
              <h3 id="task-source-title">
                {resource.data.kind === "script" ? "Python source" : "Pipeline definition"}
              </h3>
              <span>{resource.data.source_size_bytes.toLocaleString()} bytes</span>
            </div>
            {resource.data.source.length === 0 ? (
              <p className="task-source__empty">The source file is empty.</p>
            ) : (
              <pre data-language={resource.data.source_language}><code>{resource.data.source}</code></pre>
            )}
          </section>
        </div>
      )}
    </aside>
  );
}
