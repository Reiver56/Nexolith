import { useCallback, useEffect, useRef, useState } from "react";

import { ApiClientError, getSchedulerStatus, startScheduler, stopScheduler } from "../../api/client";
import { announceBackendStateChanged } from "../../api/events";
import type { SchedulerStatus } from "../../api/types";
import { ActionFeedback } from "../../components/ActionFeedback";
import type { Feedback } from "../../components/ActionFeedback";
import { ErrorState, LoadingState, RefreshButton } from "../../components/AsyncStates";
import { ConfirmationDialog } from "../../components/ConfirmationDialog";
import { PageHeader } from "../../components/PageHeader";
import { StatusBadge } from "../../components/StatusBadge";
import { usePollingResource } from "../../hooks/usePollingResource";
import { formatTimestamp } from "../../utils/format";

type SchedulerAction = "start" | "stop";

function actionError(error: unknown): string {
  if (error instanceof ApiClientError) {
    return error.message;
  }
  return "Nexolith could not change scheduler state.";
}

function SchedulerActions({
  status,
  refresh,
}: {
  status: SchedulerStatus;
  refresh: () => void;
}) {
  const [action, setAction] = useState<SchedulerAction | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const controller = useRef<AbortController | null>(null);

  useEffect(() => () => controller.current?.abort(), []);

  async function confirm(): Promise<void> {
    if (action === null) {
      return;
    }
    setBusy(true);
    setError(undefined);
    controller.current = new AbortController();
    try {
      if (action === "start") {
        await startScheduler(controller.current.signal);
        setFeedback({ tone: "success", message: "Scheduler started and verified." });
      } else {
        const result = await stopScheduler(controller.current.signal);
        setFeedback(
          result.state === "uncertain"
            ? {
                tone: "warning",
                message: "The stop request completed, but shutdown could not be verified. Check the refreshed status before acting again.",
              }
            : {
                tone: "success",
                message:
                  result.state === "not_running"
                    ? "Scheduler was already stopped."
                    : "Scheduler stopped and verified.",
              },
        );
      }
      setAction(null);
      refresh();
      announceBackendStateChanged();
    } catch (caught: unknown) {
      if (!(caught instanceof DOMException && caught.name === "AbortError")) {
        setError(actionError(caught));
      }
    } finally {
      setBusy(false);
      controller.current = null;
    }
  }

  const actionLabel = status.running ? "Stop scheduler" : "Start scheduler";
  return (
    <>
      <section className="scheduler-actions" aria-labelledby="scheduler-actions-title">
        <div>
          <p className="eyebrow">Explicit control</p>
          <h2 id="scheduler-actions-title">Scheduler action</h2>
          <p>
            {status.running
              ? "Stop the verified scheduler process after its current work reaches a safe boundary."
              : "Start one detached scheduler process for this Nexolith state database."}
          </p>
        </div>
        <button
          className={`button ${status.running ? "button--danger" : "button--primary"}`}
          type="button"
          onClick={() => {
            setFeedback(null);
            setError(undefined);
            setAction(status.running ? "stop" : "start");
          }}
        >
          {actionLabel}
        </button>
      </section>
      <ActionFeedback feedback={feedback} />
      <ConfirmationDialog
        open={action !== null}
        title={action === "stop" ? "Stop the scheduler?" : "Start the scheduler?"}
        description={
          action === "stop"
            ? "Nexolith will terminate only the identity-verified scheduler. Running work may be interrupted and reconciled on the next start."
            : "Nexolith will launch and verify a detached scheduler that can trigger enabled DAGs and their external side effects."
        }
        confirmLabel={action === "stop" ? "Stop scheduler" : "Start scheduler"}
        busy={busy}
        {...(error === undefined ? {} : { error })}
        onCancel={() => {
          if (!busy) {
            setAction(null);
            setError(undefined);
          }
        }}
        onConfirm={() => void confirm()}
      />
    </>
  );
}

function LoadedScheduler({
  status,
  refresh,
  refreshing,
  refreshError,
}: {
  status: SchedulerStatus;
  refresh: () => void;
  refreshing: boolean;
  refreshError?: string;
}) {
  return (
    <>
      <PageHeader
        eyebrow="Runtime control"
        title="Scheduler"
        description="Observe and explicitly control the scheduler responsible for due and cross-DAG runs."
        action={<RefreshButton onClick={refresh} />}
      />
      <p className="scheduler-refresh-state" aria-live="polite">
        {refreshing
          ? "Refreshing scheduler status…"
          : refreshError ?? "Scheduler status refreshes every 5 seconds while this tab is visible."}
      </p>
      <section className="surface scheduler-overview" aria-label="Scheduler status">
        <div>
          <span className="graph-context__label">State</span>
          <StatusBadge
            value={status.running ? "running-enabled" : "disabled"}
            label={status.running ? "Running" : "Stopped"}
          />
        </div>
        <div>
          <span className="graph-context__label">Started</span>
          <strong>
            {status.running && status.started_at !== null
              ? formatTimestamp(status.started_at)
              : "Not running"}
          </strong>
        </div>
        <div>
          <span className="graph-context__label">Identity</span>
          <strong>{status.running ? "Verified by backend" : "No active owner"}</strong>
        </div>
      </section>
      <SchedulerActions status={status} refresh={refresh} />
    </>
  );
}

export function SchedulerPage() {
  const load = useCallback((signal: AbortSignal) => getSchedulerStatus(signal), []);
  const { resource, refresh } = usePollingResource(
    load,
    "Unable to reach scheduler status.",
  );

  if (resource.status === "loading") {
    return <LoadingState label="Loading scheduler status" />;
  }
  if (resource.status === "error") {
    return <ErrorState message={resource.message} retry={refresh} />;
  }
  return (
    <LoadedScheduler
      status={resource.data}
      refresh={refresh}
      refreshing={resource.refreshing}
      {...(resource.refreshError === undefined ? {} : { refreshError: resource.refreshError })}
    />
  );
}
