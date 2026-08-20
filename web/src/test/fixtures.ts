import type {
  ApiInfo,
  DagGraph,
  DagList,
  RunDetail,
  RunList,
  SchedulerStatus,
} from "../api/types";

export const apiInfo: ApiInfo = {
  actions_enabled: true,
  api_version: "v1",
  package_version: "0.3.0",
  read_only: false,
};

export const schedulerRunning: SchedulerStatus = {
  running: true,
  pid: 4242,
  started_at: "2026-08-08T08:00:00Z",
};

export const schedulerStopped: SchedulerStatus = {
  running: false,
  pid: null,
  started_at: null,
};

export const dags: DagList = [
  {
    name: "warehouse-refresh",
    enabled: false,
    registered_schedule: "30m",
    current_schedule: "30m",
    current_priority: "low",
    current_severity: "medium",
    trigger: null,
    source_status: "available",
  },
  {
    name: "billing-close",
    enabled: true,
    registered_schedule: "5m",
    current_schedule: "5m",
    current_priority: "critical",
    current_severity: "high",
    trigger: { on_success_of: ["warehouse-refresh"] },
    source_status: "available",
  },
];

export const dagGraph: DagGraph = {
  name: "billing-close",
  enabled: true,
  schedule: "5m",
  trigger: { on_success_of: ["warehouse-refresh", "inventory sync"] },
  tasks: [
    {
      name: "extract",
      kind: "pipeline",
      depends_on: [],
      status: "succeeded",
      operations: [
        { kind: "sql", phase: "source", label: "SQL source", backend: "postgresql" },
        { kind: "pipeline", phase: "destination", label: "Pipeline destination" },
      ],
      actions: [],
    },
    {
      name: "enrich",
      kind: "script",
      depends_on: ["extract"],
      status: "running",
      operations: [
        {
          kind: "python",
          phase: "task",
          label: "Python script",
          preview: "def run(context): ...",
          preview_language: "python",
        },
      ],
      actions: [],
    },
    {
      name: "publish",
      kind: "pipeline",
      depends_on: ["extract", "enrich"],
      status: "blocked",
      operations: [
        { kind: "pipeline", phase: "source", label: "Pipeline source" },
        {
          kind: "python",
          phase: "transformation",
          label: "Python transformation",
          preview: "def normalize(rows, context): ...",
          preview_language: "python",
        },
        {
          kind: "nexo_function",
          phase: "destination",
          label: "Nexo Function",
          identifier: "nexofunction.upsert_rows",
          backend: "sqlite",
        },
      ],
      actions: [
        {
          kind: "nexo_action",
          label: "Nexo Action",
          identifier: "nexoaction.notify_owner",
          match: "all",
          condition_field: "customer_id",
          condition_operator: "greater_than",
          idempotency_fields: ["order_id"],
          preflight: "before_destination_write",
          invocation: "after_write_completed",
          delivery: "at_least_once",
        },
      ],
    },
  ],
  latest_run: {
    id: 12,
    status: "interrupted",
    started_at: "2026-08-08T10:00:00Z",
    ended_at: "2026-08-08T10:01:00Z",
  },
  unmapped_task_history: [{ name: "retired-task", status: "skipped" }],
};

export const runs: RunList = [
  {
    id: 12,
    dag_name: "billing-close",
    status: "interrupted",
    trigger_reason: "scheduler",
    severity: "high",
    started_at: "2026-08-08T10:00:00Z",
    ended_at: "2026-08-08T10:01:00Z",
  },
  {
    id: 11,
    dag_name: "warehouse-refresh",
    status: "failed",
    trigger_reason: "cross_dag",
    severity: "critical",
    started_at: "2026-08-08T09:00:00Z",
    ended_at: "2026-08-08T09:03:00Z",
  },
];

export const runDetail: RunDetail = {
  id: 12,
  dag_name: "billing-close",
  status: "interrupted",
  trigger_reason: "scheduler",
  severity: "high",
  started_at: "2026-08-08T10:00:00Z",
  ended_at: "2026-08-08T10:01:00Z",
  on_failure: "block",
  error_summary: "NXL_UI_SECRET_SENTINEL C:\\Users\\operator\\private.txt",
  tasks: [
    {
      name: "extract",
      status: "failed",
      started_at: "2026-08-08T10:00:00Z",
      ended_at: "2026-08-08T10:00:20Z",
      error_summary: "postgresql://user:password@internal/db",
      attempts: [
        {
          attempt_number: 1,
          status: "failed",
          started_at: "2026-08-08T10:00:00Z",
          ended_at: "2026-08-08T10:00:10Z",
          error_summary: "/home/operator/.secrets",
        },
        {
          attempt_number: 2,
          status: "failed",
          started_at: "2026-08-08T10:00:12Z",
          ended_at: "2026-08-08T10:00:20Z",
          error_summary: "NXL_UI_SECRET_SENTINEL",
        },
      ],
    },
    {
      name: "enrich",
      status: "skipped",
      started_at: null,
      ended_at: null,
      error_summary: null,
      attempts: [],
    },
    {
      name: "publish",
      status: "blocked",
      started_at: null,
      ended_at: null,
      error_summary: null,
      attempts: [],
    },
  ],
};
