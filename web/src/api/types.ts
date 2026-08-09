import type { components, operations } from "./schema";

export type ApiInfo = components["schemas"]["ApiInfoResponse"];
export type ApiErrorCode = components["schemas"]["ApiErrorCode"];
export type ApiErrorResponse = components["schemas"]["ErrorResponse"];
export type DagSummary = components["schemas"]["DagSummaryResponse"];
export type DagList = components["schemas"]["DagListResponse"];
export type DagGraph = components["schemas"]["DagGraphResponse"];
export type DagGraphTask = components["schemas"]["DagGraphTaskResponse"];
export type DagGraphRun = components["schemas"]["DagGraphRunResponse"];
export type DagGraphHistoricalTask = components["schemas"]["DagGraphHistoricalTaskResponse"];
export type DagTaskSource = components["schemas"]["DagTaskSourceResponse"];
export type RunSummary = components["schemas"]["RunSummaryResponse"];
export type RunList = components["schemas"]["RunListResponse"];
export type RunDetail = components["schemas"]["RunDetailResponse"];
export type TaskRun = components["schemas"]["TaskRunResponse"];
export type TaskAttempt = components["schemas"]["TaskAttemptResponse"];
export type SchedulerStatus = components["schemas"]["SchedulerStatusResponse"];
export type TaskRunStatus = components["schemas"]["TaskRunStatus"];
export type RegisterDagRequest =
  operations["register_dag"]["requestBody"]["content"]["application/json"];
export type DagRegistration = components["schemas"]["DagRegistrationResponse"];
export type ConfirmedActionRequest =
  operations["trigger_dag_run"]["requestBody"]["content"]["application/json"];
export type DagRunAction = components["schemas"]["DagRunActionResponse"];
export type DagScheduleActionRequest =
  operations["pause_dag_schedule"]["requestBody"]["content"]["application/json"];
export type DagScheduleAction = components["schemas"]["DagScheduleActionResponse"];
export type SchedulerStart = components["schemas"]["SchedulerStartResponse"];
export type SchedulerStop = components["schemas"]["SchedulerStopResponse"];
