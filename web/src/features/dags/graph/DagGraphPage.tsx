import { useCallback, useMemo } from "react";

import { getDagGraph } from "../../../api/client";
import type { DagGraph } from "../../../api/types";
import { EmptyState, ErrorState, LoadingState, RefreshButton } from "../../../components/AsyncStates";
import { PageHeader } from "../../../components/PageHeader";
import { StatusBadge } from "../../../components/StatusBadge";
import { usePollingResource } from "../../../hooks/usePollingResource";
import { AppLink } from "../../../router";
import { formatTimestamp } from "../../../utils/format";
import { DagGraphCanvas } from "./DagGraphCanvas";
import { buildDagFlow } from "./layout";
import type { DagFlowModel } from "./layout";

type BuiltGraph =
  | { status: "ready"; model: DagFlowModel }
  | { status: "invalid"; message: string };

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
  return (
    <section className="graph-run-context" aria-label="Graph status context">
      <div>
        <span className="graph-context__label">Structure</span>
        <strong>Current DAG definition</strong>
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

function AccessibleSummary({ graph, model }: { graph: DagGraph; model: DagFlowModel }) {
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
            <div><strong>{task.name}</strong><span>{task.statusLabel}</span></div>
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

function LoadedGraph({ graph, refresh, refreshing, refreshError }: {
  graph: DagGraph;
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
        action={<RefreshButton onClick={refresh} />}
      />
      <p className="graph-refresh-state" aria-live="polite">
        {refreshing ? "Refreshing graph data…" : refreshError ?? "Graph data refreshes every 5 seconds while this tab is visible."}
      </p>
      <RunContext graph={graph} />
      {graph.tasks.length === 0 ? (
        <EmptyState title="No tasks to map" message="This DAG definition does not contain any tasks." />
      ) : built.status === "invalid" ? (
        <ErrorState message={built.message} retry={refresh} />
      ) : (
        <>
          <GraphLegend />
          <DagGraphCanvas model={built.model} />
          <AccessibleSummary graph={graph} model={built.model} />
        </>
      )}
    </>
  );
}

export function DagGraphPage({ dagName }: { dagName: string }) {
  const load = useCallback((signal: AbortSignal) => getDagGraph(dagName, signal), [dagName]);
  const { resource, refresh } = usePollingResource(load, "Unable to reach this DAG graph.");

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
      refresh={refresh}
      refreshing={resource.refreshing}
      {...(resource.refreshError === undefined ? {} : { refreshError: resource.refreshError })}
    />
  );
}
