import type {
  ApiErrorCode,
  ApiErrorResponse,
  ApiInfo,
  ConfirmedActionRequest,
  DagGraph,
  DagList,
  DagRegistration,
  DagRunAction,
  DagScheduleAction,
  DagScheduleActionRequest,
  DagTaskSource,
  RegisterDagRequest,
  RunDetail,
  RunList,
  SchedulerStart,
  SchedulerStop,
  SchedulerStatus,
} from "./types";

const API_BASE = "/api/v1";
const SENSITIVE_VALUE =
  /(?:[a-z]:[\\/]|\/(?:home|users|var|tmp|etc)\/|(?:postgres(?:ql)?|mysql|sqlite|file):\/\/|traceback|secret|sentinel|password|token)/i;

export const API_ENDPOINTS = [
  "/api/v1",
  "/api/v1/dags",
  "/api/v1/dags/{dag_name}",
  "/api/v1/dags/{dag_name}/graph",
  "/api/v1/task-details",
  "/api/v1/runs",
  "/api/v1/runs/{run_id}",
  "/api/v1/scheduler",
  "/api/v1/dags/registrations",
  "/api/v1/dags/{dag_name}/runs",
  "/api/v1/dag-schedules/pause",
  "/api/v1/dag-schedules/resume",
  "/api/v1/scheduler/start",
  "/api/v1/scheduler/stop",
] as const;

export class ApiClientError extends Error {
  readonly status: number;
  readonly code: ApiErrorCode | undefined;

  constructor(message: string, status: number, code?: ApiErrorCode) {
    super(message);
    this.name = "ApiClientError";
    this.status = status;
    this.code = code;
  }
}

function isApiErrorResponse(value: unknown): value is ApiErrorResponse {
  if (typeof value !== "object" || value === null || !("detail" in value)) {
    return false;
  }
  const detail = value.detail;
  return (
    typeof detail === "object" &&
    detail !== null &&
    "code" in detail &&
    typeof detail.code === "string" &&
    "message" in detail &&
    typeof detail.message === "string"
  );
}

function safeBackendMessage(message: string): string {
  if (SENSITIVE_VALUE.test(message) || message.length > 240) {
    return "Nexolith returned a protected error response.";
  }
  return message;
}

async function getJson<ResponseBody>(path: string, signal?: AbortSignal): Promise<ResponseBody> {
  const response = await fetch(path, {
    method: "GET",
    headers: { Accept: "application/json" },
    credentials: "same-origin",
    signal,
  });

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ApiClientError("Nexolith returned an unreadable response.", response.status);
  }

  if (!response.ok) {
    if (isApiErrorResponse(payload)) {
      throw new ApiClientError(
        safeBackendMessage(payload.detail.message),
        response.status,
        payload.detail.code,
      );
    }
    throw new ApiClientError("Nexolith could not complete the request.", response.status);
  }

  return payload as ResponseBody;
}

async function postJson<ResponseBody>(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): Promise<ResponseBody> {
  const response = await fetch(path, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    credentials: "same-origin",
    body: JSON.stringify(body),
    signal,
  });

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ApiClientError("Nexolith returned an unreadable response.", response.status);
  }

  if (!response.ok) {
    if (isApiErrorResponse(payload)) {
      throw new ApiClientError(
        safeBackendMessage(payload.detail.message),
        response.status,
        payload.detail.code,
      );
    }
    throw new ApiClientError("Nexolith could not complete the request.", response.status);
  }

  return payload as ResponseBody;
}

export function getApiInfo(signal?: AbortSignal): Promise<ApiInfo> {
  return getJson<ApiInfo>(API_BASE, signal);
}

export function listDags(signal?: AbortSignal): Promise<DagList> {
  return getJson<DagList>(`${API_BASE}/dags`, signal);
}

const TASK_STATUSES = new Set([
  "pending",
  "running",
  "succeeded",
  "failed",
  "skipped",
  "blocked",
]);
const RUN_STATUSES = new Set(["running", "succeeded", "failed", "interrupted"]);

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isDagGraph(value: unknown): value is DagGraph {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const graph = value as Record<string, unknown>;
  if (
    typeof graph.name !== "string" ||
    typeof graph.enabled !== "boolean" ||
    (graph.schedule !== null && typeof graph.schedule !== "string") ||
    !Array.isArray(graph.tasks)
  ) {
    return false;
  }
  if (
    graph.trigger !== null &&
    (typeof graph.trigger !== "object" ||
      !isStringArray((graph.trigger as Record<string, unknown>).on_success_of))
  ) {
    return false;
  }
  const validTasks = graph.tasks.every((task) => {
    if (typeof task !== "object" || task === null) {
      return false;
    }
    const candidate = task as Record<string, unknown>;
    return (
      typeof candidate.name === "string" &&
      (candidate.kind === "pipeline" || candidate.kind === "script") &&
      isStringArray(candidate.depends_on) &&
      (candidate.status === null ||
        (typeof candidate.status === "string" && TASK_STATUSES.has(candidate.status)))
    );
  });
  if (!validTasks || !Array.isArray(graph.unmapped_task_history)) {
    return false;
  }
  const validHistory = graph.unmapped_task_history.every((task) => {
    if (typeof task !== "object" || task === null) {
      return false;
    }
    const candidate = task as Record<string, unknown>;
    return (
      typeof candidate.name === "string" &&
      typeof candidate.status === "string" &&
      TASK_STATUSES.has(candidate.status)
    );
  });
  if (!validHistory || graph.latest_run === undefined) {
    return false;
  }
  if (graph.latest_run === null) {
    return true;
  }
  if (typeof graph.latest_run !== "object") {
    return false;
  }
  const run = graph.latest_run as Record<string, unknown>;
  return (
    typeof run.id === "number" &&
    Number.isInteger(run.id) &&
    run.id > 0 &&
    typeof run.status === "string" &&
    RUN_STATUSES.has(run.status) &&
    typeof run.started_at === "string" &&
    (run.ended_at === null || typeof run.ended_at === "string")
  );
}

export async function getDagGraph(
  dagName: string,
  signal?: AbortSignal,
): Promise<DagGraph> {
  const payload = await getJson<unknown>(
    `${API_BASE}/dags/${encodeURIComponent(dagName)}/graph`,
    signal,
  );
  if (!isDagGraph(payload)) {
    throw new ApiClientError("Nexolith returned malformed graph data.", 502);
  }
  return payload;
}

export function getDagTaskSource(
  dagName: string,
  taskName: string,
  signal?: AbortSignal,
): Promise<DagTaskSource> {
  const parameters = new URLSearchParams({ dag_name: dagName, task_name: taskName });
  return getJson<DagTaskSource>(`${API_BASE}/task-details?${parameters.toString()}`, signal);
}

export function listRuns(limit = 50, signal?: AbortSignal): Promise<RunList> {
  const boundedLimit = Math.max(1, Math.min(100, Math.trunc(limit)));
  return getJson<RunList>(`${API_BASE}/runs?limit=${String(boundedLimit)}`, signal);
}

export function getRun(runId: number, signal?: AbortSignal): Promise<RunDetail> {
  return getJson<RunDetail>(`${API_BASE}/runs/${String(runId)}`, signal);
}

export function getSchedulerStatus(signal?: AbortSignal): Promise<SchedulerStatus> {
  return getJson<SchedulerStatus>(`${API_BASE}/scheduler`, signal);
}

export function registerDag(
  request: RegisterDagRequest,
  signal?: AbortSignal,
): Promise<DagRegistration> {
  return postJson<DagRegistration>(
    `${API_BASE}/dags/registrations`,
    request,
    signal,
  );
}

const CONFIRMED_ACTION: ConfirmedActionRequest = { confirm: true };

export function triggerDagRun(
  dagName: string,
  signal?: AbortSignal,
): Promise<DagRunAction> {
  return postJson<DagRunAction>(
    `${API_BASE}/dags/${encodeURIComponent(dagName)}/runs`,
    CONFIRMED_ACTION,
    signal,
  );
}

export function pauseDagSchedule(
  dagName: string,
  signal?: AbortSignal,
): Promise<DagScheduleAction> {
  const request: DagScheduleActionRequest = { confirm: true, dag_name: dagName };
  return postJson<DagScheduleAction>(`${API_BASE}/dag-schedules/pause`, request, signal);
}

export function resumeDagSchedule(
  dagName: string,
  signal?: AbortSignal,
): Promise<DagScheduleAction> {
  const request: DagScheduleActionRequest = { confirm: true, dag_name: dagName };
  return postJson<DagScheduleAction>(`${API_BASE}/dag-schedules/resume`, request, signal);
}

export function startScheduler(signal?: AbortSignal): Promise<SchedulerStart> {
  return postJson<SchedulerStart>(
    `${API_BASE}/scheduler/start`,
    CONFIRMED_ACTION,
    signal,
  );
}

export function stopScheduler(signal?: AbortSignal): Promise<SchedulerStop> {
  return postJson<SchedulerStop>(
    `${API_BASE}/scheduler/stop`,
    CONFIRMED_ACTION,
    signal,
  );
}
