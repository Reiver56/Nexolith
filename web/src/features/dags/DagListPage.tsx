import { useCallback } from "react";

import { listDags } from "../../api/client";
import type { DagSummary } from "../../api/types";
import { EmptyState, ErrorState, LoadingState, RefreshButton } from "../../components/AsyncStates";
import { PageHeader } from "../../components/PageHeader";
import { StatusBadge } from "../../components/StatusBadge";
import { useApiResource } from "../../hooks/useApiResource";
import { AppLink, dagGraphPath } from "../../router";
import { RegisterDagControl, TriggerDagControl } from "./DagActions";

function scheduleFor(dag: DagSummary): string {
  return dag.current_schedule ?? dag.registered_schedule ?? "Event-driven";
}

function triggerFor(dag: DagSummary): string {
  if (dag.trigger !== null) {
    return `After ${dag.trigger.on_success_of.join(", ")}`;
  }
  if (dag.current_schedule !== null || dag.registered_schedule !== null) {
    return "Interval schedule";
  }
  return "Manual registration only";
}

export function DagListPage() {
  const load = useCallback((signal: AbortSignal) => listDags(signal), []);
  const { resource, reload } = useApiResource(load, "Unable to reach the DAG registry.");

  return (
    <>
      <PageHeader
        eyebrow="Pipeline landscape"
        title="DAGs"
        description="Registered workflows, their scheduling posture, and operational importance."
        action={
          <div className="button-group">
            <RefreshButton onClick={reload} />
            <RegisterDagControl onRegistered={reload} />
          </div>
        }
      />
      {resource.status === "loading" ? <LoadingState label="Loading DAGs" /> : null}
      {resource.status === "error" ? (
        <ErrorState message={resource.message} retry={reload} />
      ) : null}
      {resource.status === "success" && resource.data.length === 0 ? (
        <EmptyState
          title="No registered DAGs"
          message="Register a DAG YAML file to add it to this scheduling registry."
        />
      ) : null}
      {resource.status === "success" && resource.data.length > 0 ? (
        <section className="surface" aria-label="Registered DAGs">
          <div className="surface__summary">
            <span>{resource.data.length} registered</span>
            <span>Sorted by name</span>
          </div>
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">DAG</th>
                  <th scope="col">State</th>
                  <th scope="col">Schedule</th>
                  <th scope="col">Priority</th>
                  <th scope="col">Severity</th>
                  <th scope="col">Trigger</th>
                  <th scope="col"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {[...resource.data]
                  .sort((left, right) => left.name.localeCompare(right.name))
                  .map((dag) => (
                    <tr key={dag.name} className={dag.enabled ? undefined : "is-disabled"}>
                      <th scope="row" data-label="DAG">
                        <span className="primary-value">{dag.name}</span>
                        {dag.source_status === "available" ? null : (
                          <span className="secondary-value">
                            Source {dag.source_status === "missing" ? "missing" : "invalid"}
                          </span>
                        )}
                      </th>
                      <td data-label="State">
                        <StatusBadge
                          value={dag.enabled ? "running-enabled" : "disabled"}
                          label={dag.enabled ? "Enabled" : "Disabled"}
                        />
                      </td>
                      <td data-label="Schedule">{scheduleFor(dag)}</td>
                      <td data-label="Priority">
                        {dag.current_priority === null ? (
                          <span className="muted">Unavailable</span>
                        ) : (
                          <StatusBadge value={dag.current_priority} />
                        )}
                      </td>
                      <td data-label="Severity">
                        {dag.current_severity === null ? (
                          <span className="muted">Unavailable</span>
                        ) : (
                          <StatusBadge value={dag.current_severity} />
                        )}
                      </td>
                      <td data-label="Trigger">{triggerFor(dag)}</td>
                      <td data-label="Actions">
                        <div className="table-actions">
                          {dag.source_status === "available" ? (
                            <>
                              <AppLink className="run-link" to={dagGraphPath(dag.name)}>
                                View graph <span aria-hidden="true">→</span>
                              </AppLink>
                              <TriggerDagControl dagName={dag.name} />
                            </>
                          ) : (
                            <span className="muted">Unavailable</span>
                          )}
                        </div>
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
