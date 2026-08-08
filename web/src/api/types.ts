import type { components } from "./schema";

export type ApiInfo = components["schemas"]["ApiInfoResponse"];
export type ApiErrorCode = components["schemas"]["ApiErrorCode"];
export type ApiErrorResponse = components["schemas"]["ErrorResponse"];
export type DagSummary = components["schemas"]["DagSummaryResponse"];
export type DagList = components["schemas"]["DagListResponse"];
export type RunSummary = components["schemas"]["RunSummaryResponse"];
export type RunList = components["schemas"]["RunListResponse"];
export type RunDetail = components["schemas"]["RunDetailResponse"];
export type TaskRun = components["schemas"]["TaskRunResponse"];
export type TaskAttempt = components["schemas"]["TaskAttemptResponse"];
export type SchedulerStatus = components["schemas"]["SchedulerStatusResponse"];
