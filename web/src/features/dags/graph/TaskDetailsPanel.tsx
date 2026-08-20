import { useEffect, useRef } from "react";

import type { DagGraphNexoAction, DagGraphOperation, DagGraphTask } from "../../../api/types";
import { StatusBadge } from "../../../components/StatusBadge";
import { deliberateMotionDuration } from "../../../utils/motion";
import { OperationIcon } from "./OperationIcon";

function phaseLabel(operation: DagGraphOperation): string {
  if (operation.phase === "task") {
    return "Task";
  }
  if (operation.phase === "transformation") {
    return "Transformation";
  }
  return operation.phase === "source" ? "Source" : "Destination";
}

function operatorLabel(operator: DagGraphNexoAction["condition_operator"]): string {
  return operator.replaceAll("_", " ");
}

export function TaskDetailsPanel({
  task,
  open,
  onClose,
  onExited,
}: {
  task: DagGraphTask;
  open: boolean;
  onClose: () => void;
  onExited: () => void;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    closeRef.current?.focus();
    const frame = window.requestAnimationFrame(() => {
      closeRef.current?.focus();
    });
    return () => {
      window.cancelAnimationFrame(frame);
    };
  }, [open, task.name]);

  useEffect(() => {
    if (open) {
      return;
    }
    const timer = window.setTimeout(onExited, deliberateMotionDuration());
    return () => {
      window.clearTimeout(timer);
    };
  }, [onExited, open]);

  useEffect(() => {
    const handleEscape = (event: KeyboardEvent): void => {
      if (open && event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", handleEscape);
    return () => {
      window.removeEventListener("keydown", handleEscape);
    };
  }, [onClose, open]);

  return (
    <aside
      className="task-details-panel"
      data-state={open ? "open" : "closing"}
      aria-hidden={open ? undefined : true}
      aria-labelledby="task-details-title"
    >
      <header className="task-details-panel__header">
        <div>
          <p className="eyebrow">Task details</p>
          <h2 id="task-details-title">{task.name}</h2>
        </div>
        <button
          ref={closeRef}
          className="icon-button task-details-panel__close"
          type="button"
          onClick={onClose}
          aria-label={`Close details for ${task.name}`}
          title="Close task details"
        >
          <svg aria-hidden="true" viewBox="0 0 20 20">
            <path d="m5 5 10 10M15 5 5 15" />
          </svg>
        </button>
      </header>
      <div className="task-details-panel__content">
        <dl className="task-details-list">
          <div>
            <dt>Latest status</dt>
            <dd>
              {task.status === null ? (
                <span className="muted">No persisted status</span>
              ) : (
                <StatusBadge value={task.status} />
              )}
            </dd>
          </div>
          <div>
            <dt>Dependencies</dt>
            <dd>{task.depends_on.length === 0 ? "None" : task.depends_on.join(", ")}</dd>
          </div>
        </dl>

        <section className="task-capabilities" aria-labelledby="task-operations-title">
          <h3 id="task-operations-title">Operations</h3>
          <ol className="task-capabilities__list">
            {task.operations.map((operation, index) => (
              <li key={`${operation.kind}-${operation.phase}-${String(index)}`}>
                <OperationIcon kind={operation.kind} />
                <div>
                  <strong>{operation.label}</strong>
                  <span>{phaseLabel(operation)}</span>
                  {operation.kind === "sql" ? <span>{operation.backend}</span> : null}
                  {operation.kind === "nexo_function" ? (
                    <code>{operation.identifier}</code>
                  ) : null}
                </div>
                {operation.kind === "python" && operation.preview !== null ? (
                  <pre data-language="python" aria-label={`${operation.label} signature`}>
                    <code>{operation.preview}</code>
                  </pre>
                ) : null}
              </li>
            ))}
          </ol>
        </section>

        {task.actions.length === 0 ? null : (
          <section className="task-actions" aria-labelledby="task-actions-title">
            <div className="task-actions__heading">
              <h3 id="task-actions-title">Nexo Actions</h3>
              <span>{task.actions.length}</span>
            </div>
            <p className="task-actions__lifecycle">
              Preflight runs before the destination write. Handlers run after WriteCompleted with
              at-least-once delivery. A retry may repeat both the destination write and Action
              invocation. Handlers receive a stable idempotency key; deduplication remains the
              handler&apos;s responsibility.
            </p>
            <ul className="task-actions__list">
              {task.actions.map((action) => (
                <li key={action.identifier}>
                  <OperationIcon kind={action.kind} />
                  <div>
                    <strong>{action.label}</strong>
                    <code>{action.identifier}</code>
                    <span>
                      Match {action.match}: {action.condition_field ?? "protected field"}{" "}
                      {operatorLabel(action.condition_operator)}
                    </span>
                    {action.idempotency_fields.length === 0 ? null : (
                      <span>Idempotency fields: {action.idempotency_fields.join(", ")}</span>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </aside>
  );
}
