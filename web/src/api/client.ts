import type {
  ApiErrorCode,
  ApiErrorResponse,
  ApiInfo,
  DagList,
  RunDetail,
  RunList,
  SchedulerStatus,
} from "./types";

const API_BASE = "/api/v1";
const SENSITIVE_VALUE =
  /(?:[a-z]:[\\/]|\/(?:home|users|var|tmp|etc)\/|(?:postgres(?:ql)?|mysql|sqlite|file):\/\/|traceback|secret|sentinel|password|token)/i;

export const READ_ONLY_ENDPOINTS = [
  "/api/v1",
  "/api/v1/dags",
  "/api/v1/dags/{dag_name}",
  "/api/v1/runs",
  "/api/v1/runs/{run_id}",
  "/api/v1/scheduler",
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

export function getApiInfo(signal?: AbortSignal): Promise<ApiInfo> {
  return getJson<ApiInfo>(API_BASE, signal);
}

export function listDags(signal?: AbortSignal): Promise<DagList> {
  return getJson<DagList>(`${API_BASE}/dags`, signal);
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
