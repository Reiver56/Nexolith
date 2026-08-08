import { useCallback } from "react";

import { getRun } from "../../api/client";
import type { RunDetail, TaskAttempt, TaskRun } from "../../api/types";
import { ErrorState, LoadingState, RefreshButton } from "../../components/AsyncStates";
import { PageHeader } from "../../components/PageHeader";
import { StatusBadge } from "../../components/StatusBadge";
import { useApiResource } from "../../hooks/useApiResource";
import { AppLink } from "../../router";
import { formatTimestamp, humanize } from "../../utils/format";

function terminalSummary(status: RunDetail["status"]): string | null {
  if (status === "failed") {
    return "DAG execution did not complete successfully.";
  }
  if (status === "interrupted") {
    return "Execution was interrupted before completion.";
  }
  return null;
}

function taskSummary(status: TaskRun["status"]): string | null {
  const summaries: Partial<Record<TaskRun["status"], string>> = {
    failed: "Task execution failed.",
    skipped: "Skipped because an upstream dependency failed.",
    blocked: "Blocked by the DAG failure policy.",
    pending: "Waiting to become eligible.",
  };
  return summaries[status] ?? null;
}

function Attempt({ attempt }: { attempt: TaskAttempt }) {
  return (
    <li className="attempt-row">
      <div>
        <span className="attempt-row__number">Attempt {attempt.attempt_number}</span>
        <StatusBadge value={attempt.status} />
      </div>
      <div className="attempt-row__time">
        <time dateTime={attempt.started_at}>{formatTimestamp(attempt.started_at)}</time>
        <span aria-hidden="true">→</span>
        {attempt.ended_at === null ? (
          <span>In progress</span>
        ) : (
          <time dateTime={attempt.ended_at}>{formatTimestamp(attempt.ended_at)}</time>
        )}
      </div>
      {attempt.status === "failed" ? <p>Task attempt failed.</p> : null}
    </li>
  );
}

function TaskHistory({ task }: { task: TaskRun }) {
  const summary = taskSummary(task.status);
  return (
    <article className="task-card">
      <header className="task-card__header">
        <div>
          <p className="eyebrow">Task</p>
          <h3>{task.name}</h3>
        </div>
        <StatusBadge value={task.status} />
      </header>
      <dl className="detail-grid detail-grid--compact">
        <div>
          <dt>Started</dt>
          <dd>{task.started_at === null ? "Not started" : formatTimestamp(task.started_at)}</dd>
        </div>
        <div>
          <dt>Finished</dt>
          <dd>{task.ended_at === null ? "Not finished" : formatTimestamp(task.ended_at)}</dd>
        </div>
      </dl>
      {summary === null ? null : <p className="safe-summary">{summary}</p>}
      <div className="attempts">
        <h4>Attempt history</h4>
        {task.attempts.length === 0 ? (
          <p className="muted">No execution attempt was recorded.</p>
        ) : (
          <ol>
            {task.attempts.map((attempt) => (
              <Attempt key={attempt.attempt_number} attempt={attempt} />
            ))}
          </ol>
        )}
      </div>
    </article>
  );
}

function RunNotFound({ malformed = false }: { malformed?: boolean }) {
  return (
    <section className="not-found" aria-live="polite">
      <p className="eyebrow">Run unavailable</p>
      <h1 tabIndex={-1}>{malformed ? "Invalid run identifier" : "Run not found"}</h1>
      <p>
        {malformed
          ? "Run identifiers are positive whole numbers. No API request was sent."
          : "This run does not exist or is no longer available in the current state store."}
      </p>
      <AppLink className="button button--primary" to="/runs">
        Return to runs
      </AppLink>
    </section>
  );
}

function ValidRunDetailPage({ runId }: { runId: number }) {
  const load = useCallback((signal: AbortSignal) => getRun(runId, signal), [runId]);
  const { resource, reload } = useApiResource(load, "Unable to reach this run.");

  if (resource.status === "loading") {
    return <LoadingState label={`Loading run ${String(runId)}`} />;
  }
  if (resource.status === "error") {
    if (resource.code === "run_not_found") {
      return <RunNotFound />;
    }
    return <ErrorState message={resource.message} retry={reload} />;
  }

  const run = resource.data;
  const summary = terminalSummary(run.status);
  return (
    <>
      <nav className="breadcrumbs" aria-label="Breadcrumb">
        <AppLink to="/runs">Runs</AppLink>
        <span aria-hidden="true">/</span>
        <span aria-current="page">#{run.id}</span>
      </nav>
      <PageHeader
        eyebrow={`Run #${String(run.id)}`}
        title={run.dag_name}
        description="Persisted execution, task, and retry-attempt history."
        action={<RefreshButton onClick={reload} />}
      />
      <section className="surface run-overview" aria-label="Run overview">
        <div className="run-overview__status">
          <StatusBadge value={run.status} />
          <StatusBadge value={run.severity} label={`${humanize(run.severity)} severity`} />
        </div>
        <dl className="detail-grid">
          <div>
            <dt>Trigger</dt>
            <dd>{humanize(run.trigger_reason)}</dd>
          </div>
          <div>
            <dt>Failure policy</dt>
            <dd>{humanize(run.on_failure)}</dd>
          </div>
          <div>
            <dt>Started</dt>
            <dd>
              <time dateTime={run.started_at}>{formatTimestamp(run.started_at)}</time>
            </dd>
          </div>
          <div>
            <dt>Finished</dt>
            <dd>
              {run.ended_at === null ? (
                "Still running"
              ) : (
                <time dateTime={run.ended_at}>{formatTimestamp(run.ended_at)}</time>
              )}
            </dd>
          </div>
        </dl>
        {summary === null ? null : <p className="safe-summary safe-summary--run">{summary}</p>}
      </section>
      <section className="task-history" aria-labelledby="task-history-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Execution graph</p>
            <h2 id="task-history-title">Task history</h2>
          </div>
          <span>{run.tasks.length} tasks</span>
        </div>
        {run.tasks.length === 0 ? (
          <p className="surface muted">No task history was persisted for this run.</p>
        ) : (
          <div className="task-list">
            {run.tasks.map((task) => (
              <TaskHistory key={task.name} task={task} />
            ))}
          </div>
        )}
      </section>
    </>
  );
}

export function RunDetailRoute({ rawRunId }: { rawRunId: string }) {
  if (!/^[1-9]\d*$/.test(rawRunId)) {
    return <RunNotFound malformed />;
  }
  const runId = Number(rawRunId);
  if (!Number.isSafeInteger(runId)) {
    return <RunNotFound malformed />;
  }
  return <ValidRunDetailPage runId={runId} />;
}
