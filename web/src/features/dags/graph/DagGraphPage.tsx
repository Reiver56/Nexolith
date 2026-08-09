import { useCallback, useMemo, useRef, useState } from "react";

import { getDagGraph } from "../../../api/client";
import type { DagGraph } from "../../../api/types";
import { EmptyState, ErrorState, LoadingState, RefreshButton } from "../../../components/AsyncStates";
import { PageHeader } from "../../../components/PageHeader";
import { StatusBadge } from "../../../components/StatusBadge";
import { usePollingResource } from "../../../hooks/usePollingResource";
import { AppLink } from "../../../router";
import { formatDuration, formatTimestamp } from "../../../utils/format";
import { ScheduleControl, TriggerDagControl } from "../DagActions";
import { DagGraphCanvas } from "./DagGraphCanvas";
import { TaskDetailsPanel } from "./TaskDetailsPanel";
import { TaskKindIcon } from "./TaskKindIcon";
import { taskKindLabel } from "./taskKinds";
import { buildDagFlow } from "./layout";
import type { DagFlowModel } from "./layout";

type BuiltGraph =
  | { status: "ready"; model: DagFlowModel }
  | { status: "invalid"; message: string };

function graphPollingInterval(graph: DagGraph): number {
  return graph.latest_run?.status === "running" ? 1_000 : 10_000;
}

function buildSafely(graph: DagGraph): BuiltGraph {
  try {
    return { status: "ready", model: buildDagFlow(graph) };
  } catch {
    return {
      status: "invalid",
      message: "The saved DAG structure could not be arranged safely. Refresh after validating the DAG definition.",
    };
  }
}

function DagNotFound({ dagName }: { dagName: string }) {
  return (
    <section className="not-found" aria-live="polite">
      <p className="eyebrow">DAG unavailable</p>
      <h1 tabIndex={-1}>DAG not found</h1>
      <p>
        The DAG named <strong>{dagName}</strong> is not registered or its current definition is
        unavailable.
      </p>
      <AppLink className="button button--primary" to="/dags">
        Return to DAGs
      </AppLink>
    </section>
  );
}

function RunContext({ graph }: { graph: DagGraph }) {
  const run = graph.latest_run;
  const completedTasks = graph.tasks.filter(
    (task) => task.status !== null && !["pending", "running"].includes(task.status),
  ).length;
  const runningTasks =
    run?.status === "running"
      ? graph.tasks.filter((task) => task.status === "running").map((task) => task.name)
      : [];
  return (
    <section className="graph-run-context" aria-label="Live DAG status">
      <div>
        <span className="graph-context__label">Schedule</span>
        {graph.schedule === null ? (
          <strong>Event-driven</strong>
        ) : (
          <StatusBadge
            value={graph.enabled ? "running-enabled" : "disabled"}
            label={graph.enabled ? "Scheduled" : "Paused"}
          />
        )}
      </div>
      {run === null ? (
        <div>
          <span className="graph-context__label">Task status</span>
          <strong>No persisted runs</strong>
        </div>
      ) : (
        <>
          <div>
            <span className="graph-context__label">Latest persisted run</span>
            <AppLink className="run-link" to={`/runs/${String(run.id)}`}>
              Run #{run.id}
            </AppLink>
          </div>
          <div>
            <span className="graph-context__label">Run status</span>
            <StatusBadge value={run.status} />
          </div>
          <div>
            <span className="graph-context__label">Started</span>
            <time dateTime={run.started_at}>{formatTimestamp(run.started_at)}</time>
          </div>
          <div>
            <span className="graph-context__label">Duration</span>
            <strong>{formatDuration(run.started_at, run.ended_at)}</strong>
          </div>
          <div>
            <span className="graph-context__label">Task progress</span>
            <strong>{completedTasks} of {graph.tasks.length} completed</strong>
          </div>
          <div>
            <span className="graph-context__label">Running task</span>
            <strong>{runningTasks.length === 0 ? "None" : runningTasks.join(", ")}</strong>
          </div>
        </>
      )}
    </section>
  );
}

function GraphLegend() {
  return (
    <section className="graph-legend" aria-labelledby="graph-legend-title">
      <h2 id="graph-legend-title">Graph legend</h2>
      <ul>
        <li><span className="legend-line" aria-hidden="true" />Task dependency</li>
        <li><span className="legend-line legend-line--cross" aria-hidden="true" />Cross-DAG trigger</li>
        <li><span className="legend-status legend-status--running" aria-hidden="true" />Running</li>
        <li><span className="legend-status legend-status--succeeded" aria-hidden="true" />Succeeded</li>
        <li><span className="legend-status legend-status--failed" aria-hidden="true" />Failed or blocked</li>
        <li><span className="legend-status legend-status--neutral" aria-hidden="true" />Pending, skipped, or no history</li>
      </ul>
    </section>
  );
}

function AccessibleSummary({
  graph,
  model,
  selectedTaskName,
  onTaskSelect,
}: {
  graph: DagGraph;
  model: DagFlowModel;
  selectedTaskName: string | null;
  onTaskSelect: (taskName: string, trigger: HTMLButtonElement) => void;
}) {
  return (
    <section className="graph-summary" aria-labelledby="graph-summary-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Accessible alternative</p>
          <h2 id="graph-summary-title">Dependency summary</h2>
        </div>
        <span>{graph.tasks.length} tasks</span>
      </div>
      {model.upstreamDags.length > 0 ? (
        <p className="graph-summary__trigger">
          <strong>Cross-DAG trigger:</strong> this DAG runs after successful completion of {model.upstreamDags.join(", ")}.
        </p>
      ) : (
        <p className="graph-summary__trigger">No cross-DAG trigger is configured.</p>
      )}
      <ul className="graph-summary__tasks">
        {model.dependencySummary.map((task) => (
          <li key={task.name}>
            <div>
              <button
                className="graph-summary__task-button"
                type="button"
                data-task-name={task.name}
                onClick={(event) => {
                  onTaskSelect(task.name, event.currentTarget);
                }}
                aria-pressed={selectedTaskName === task.name}
                aria-label={`Open details for ${task.name}, ${taskKindLabel(task.kind)}, ${task.statusLabel}`}
              >
                <TaskKindIcon kind={task.kind} />
                <strong>{task.name}</strong>
                <span>{taskKindLabel(task.kind)}</span>
              </button>
              <span>{task.statusLabel}</span>
            </div>
            <p>{task.dependsOn.length === 0 ? "No dependencies" : `Depends on ${task.dependsOn.join(", ")}`}</p>
          </li>
        ))}
      </ul>
      {graph.unmapped_task_history.length > 0 ? (
        <aside className="graph-history-note" aria-label="Historical tasks outside current definition">
          <strong>Not in the current definition</strong>
          <p>
            The latest run also recorded {graph.unmapped_task_history.map((task) => `${task.name} (${task.status})`).join(", ")}.
            These historical tasks are not drawn as current nodes.
          </p>
        </aside>
      ) : null}
    </section>
  );
}

function LoadedGraph({
  graph,
  graphRevision,
  selectedTaskName,
  onTaskSelect,
  onTaskClose,
  refresh,
  refreshing,
  refreshError,
}: {
  graph: DagGraph;
  graphRevision: number;
  selectedTaskName: string | null;
  onTaskSelect: (taskName: string, trigger: HTMLButtonElement) => void;
  onTaskClose: () => void;
  refresh: () => void;
  refreshing: boolean;
  refreshError?: string;
}) {
  const built = useMemo(() => buildSafely(graph), [graph]);
  return (
    <>
      <nav className="breadcrumbs" aria-label="Breadcrumb">
        <AppLink to="/dags">DAGs</AppLink>
        <span aria-hidden="true">/</span>
        <span aria-current="page">Graph</span>
      </nav>
      <PageHeader
        eyebrow="Dependency graph"
        title={graph.name}
        description="Current task structure with statuses from the latest persisted run. Pan, zoom, or use the text summary below."
        action={
          <div className="button-group">
            <RefreshButton onClick={refresh} />
            {graph.schedule === null ? null : (
              <ScheduleControl dagName={graph.name} enabled={graph.enabled} onChanged={refresh} />
            )}
            <TriggerDagControl dagName={graph.name} />
          </div>
        }
      />
      <p className="graph-refresh-state" aria-live="polite">
        {refreshing
          ? "Refreshing live DAG data…"
          : refreshError ??
            (graph.latest_run?.status === "running"
              ? "Live data refreshes every second while this run is active."
              : "Live data refreshes every 10 seconds while this DAG is idle.")}
      </p>
      <RunContext graph={graph} />
      {graph.tasks.length === 0 ? (
        <EmptyState title="No tasks to map" message="This DAG definition does not contain any tasks." />
      ) : built.status === "invalid" ? (
        <ErrorState message={built.message} retry={refresh} />
      ) : (
        <>
          <GraphLegend />
          <div className="graph-workspace" data-panel-open={selectedTaskName !== null || undefined}>
            <DagGraphCanvas
              model={built.model}
              selectedTaskName={selectedTaskName}
              onTaskSelect={onTaskSelect}
            />
            {selectedTaskName === null ? null : (
              <TaskDetailsPanel
                key={selectedTaskName}
                dagName={graph.name}
                taskName={selectedTaskName}
                refreshToken={graphRevision}
                onClose={onTaskClose}
              />
            )}
          </div>
          <AccessibleSummary
            graph={graph}
            model={built.model}
            selectedTaskName={selectedTaskName}
            onTaskSelect={onTaskSelect}
          />
        </>
      )}
    </>
  );
}

export function DagGraphPage({ dagName }: { dagName: string }) {
  const [selectedTaskName, setSelectedTaskName] = useState<string | null>(null);
  const restoreFocusRef = useRef<HTMLButtonElement | null>(null);
  const selectTask = useCallback((taskName: string, trigger: HTMLButtonElement) => {
    restoreFocusRef.current = trigger;
    setSelectedTaskName(taskName);
  }, []);
  const closeTask = useCallback(() => {
    const selectedName = selectedTaskName;
    setSelectedTaskName(null);
    const trigger = restoreFocusRef.current;
    if (trigger?.isConnected) {
      trigger.focus();
      return;
    }
    const fallback = [...document.querySelectorAll<HTMLButtonElement>("[data-task-name]")].find(
      (candidate) => candidate.dataset.taskName === selectedName,
    );
    fallback?.focus();
  }, [selectedTaskName]);
  const load = useCallback(
    async (signal: AbortSignal) => {
      const graph = await getDagGraph(dagName, signal);
      setSelectedTaskName((current) =>
        current === null || graph.tasks.some((task) => task.name === current) ? current : null,
      );
      return graph;
    },
    [dagName],
  );
  const { resource, refresh } = usePollingResource(
    load,
    "Unable to reach this DAG graph.",
    graphPollingInterval,
  );

  if (resource.status === "loading") {
    return <LoadingState label={`Loading graph for ${dagName}`} />;
  }
  if (resource.status === "error") {
    if (resource.code === "dag_not_found") {
      return <DagNotFound dagName={dagName} />;
    }
    return <ErrorState message={resource.message} retry={refresh} />;
  }
  return (
    <LoadedGraph
      graph={resource.data}
      graphRevision={resource.revision}
      selectedTaskName={selectedTaskName}
      onTaskSelect={selectTask}
      onTaskClose={closeTask}
      refresh={refresh}
      refreshing={resource.refreshing}
      {...(resource.refreshError === undefined ? {} : { refreshError: resource.refreshError })}
    />
  );
}
