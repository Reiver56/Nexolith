import { useCallback } from "react";

import { listRuns } from "../../api/client";
import { EmptyState, ErrorState, LoadingState, RefreshButton } from "../../components/AsyncStates";
import { PageHeader } from "../../components/PageHeader";
import { StatusBadge } from "../../components/StatusBadge";
import { useApiResource } from "../../hooks/useApiResource";
import { AppLink } from "../../router";
import { formatTimestamp, humanize } from "../../utils/format";

const RUN_LIMIT = 50;

export function RunListPage() {
  const load = useCallback((signal: AbortSignal) => listRuns(RUN_LIMIT, signal), []);
  const { resource, reload } = useApiResource(load, "Unable to reach the run history.");

  return (
    <>
      <PageHeader
        eyebrow="Execution history"
        title="Runs"
        description={`The ${String(RUN_LIMIT)} most recent persisted DAG executions, newest first.`}
        action={<RefreshButton onClick={reload} busy={resource.status === "loading"} />}
      />
      {resource.status === "loading" ? <LoadingState label="Loading runs" /> : null}
      {resource.status === "error" ? (
        <ErrorState message={resource.message} retry={reload} />
      ) : null}
      {resource.status === "success" && resource.data.length === 0 ? (
        <EmptyState
          title="No run history yet"
          message="Completed and in-progress DAG runs will appear here."
        />
      ) : null}
      {resource.status === "success" && resource.data.length > 0 ? (
        <section className="surface" aria-label="Recent DAG runs">
          <div className="surface__summary">
            <span>{resource.data.length} shown</span>
            <span>Limit {RUN_LIMIT}</span>
          </div>
          <div className="table-scroll">
            <table className="data-table data-table--runs">
              <thead>
                <tr>
                  <th scope="col">Run</th>
                  <th scope="col">DAG</th>
                  <th scope="col">Status</th>
                  <th scope="col">Trigger</th>
                  <th scope="col">Severity</th>
                  <th scope="col">Started</th>
                  <th scope="col">Finished</th>
                </tr>
              </thead>
              <tbody>
                {resource.data.map((run) => (
                  <tr key={run.id}>
                    <th scope="row" data-label="Run">
                      <AppLink className="run-link" to={`/runs/${String(run.id)}`}>
                        #{run.id}
                        <span aria-hidden="true">→</span>
                      </AppLink>
                    </th>
                    <td data-label="DAG">
                      <span className="primary-value">{run.dag_name}</span>
                    </td>
                    <td data-label="Status">
                      <StatusBadge value={run.status} />
                    </td>
                    <td data-label="Trigger">{humanize(run.trigger_reason)}</td>
                    <td data-label="Severity">
                      <StatusBadge value={run.severity} />
                    </td>
                    <td data-label="Started">
                      <time dateTime={run.started_at}>{formatTimestamp(run.started_at)}</time>
                    </td>
                    <td data-label="Finished">
                      {run.ended_at === null ? (
                        <span className="live-value">Running now</span>
                      ) : (
                        <time dateTime={run.ended_at}>{formatTimestamp(run.ended_at)}</time>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
    </>
  );
}
