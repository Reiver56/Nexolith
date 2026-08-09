/**
 * Generated from Nexolith's FastAPI OpenAPI contract.
 * Run `npm run api:generate`; do not edit by hand.
 */

export interface paths {
    "/api/v1": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get API contract metadata
         * @description Returns stable API and package versions without host-specific data.
         */
        get: operations["get_api_info"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/dag-schedules/pause": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Pause a DAG schedule
         * @description Prevents future interval-triggered runs without affecting active, manual, API, or event-triggered execution.
         */
        post: operations["pause_dag_schedule"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/dag-schedules/resume": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Resume a DAG schedule
         * @description Allows future interval-triggered runs for a registered DAG.
         */
        post: operations["resume_dag_schedule"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/dags": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List registered DAGs
         * @description Lists registered DAGs with safe current declarative metadata.
         */
        get: operations["list_dags"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/dags/registrations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Register a DAG
         * @description Validates and registers a DAG without executing any task.
         */
        post: operations["register_dag"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/dags/{dag_name}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get a registered DAG
         * @description Reads the current registered DAG source and returns its safe structure.
         */
        get: operations["get_dag"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/dags/{dag_name}/graph": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get DAG dependency graph data
         * @description Returns the current safe DAG structure and latest persisted task statuses in one read-only response. Cross-DAG triggers remain DAG-level relationships.
         */
        get: operations["get_dag_graph"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/dags/{dag_name}/runs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Trigger a registered DAG run
         * @description Executes synchronously in a worker thread and returns the persisted terminal run. A retry after a lost response can create another run.
         */
        post: operations["trigger_dag_run"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/runs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List recent DAG runs
         * @description Returns a bounded newest-first view of persisted DAG runs.
         */
        get: operations["list_runs"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/runs/{run_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get DAG run history
         * @description Returns the persisted run, task, and retry-attempt history.
         */
        get: operations["get_run"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/scheduler": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get scheduler status
         * @description Checks PID and creation-time ownership without changing scheduler state.
         */
        get: operations["get_scheduler_status"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/scheduler/start": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Start the scheduler process
         * @description Starts and verifies a detached scheduler using the foreground CLI entrypoint.
         */
        post: operations["start_scheduler"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/scheduler/stop": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Stop the scheduler process
         * @description Terminates only the identity-verified pidfile owner and never unlinks as observer.
         */
        post: operations["stop_scheduler"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/task-details": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get registered task source details
         * @description Returns a bounded UTF-8 source definition for a task in an already registered DAG. Available only when the API server is bound to loopback; no filesystem path is accepted or returned.
         */
        get: operations["get_dag_task_source"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /**
         * ApiErrorCode
         * @enum {string}
         */
        ApiErrorCode: "dag_not_found" | "task_not_found" | "run_not_found" | "dag_source_missing" | "dag_configuration_invalid" | "task_source_unavailable" | "task_source_too_large" | "task_source_binary" | "task_source_invalid_encoding" | "source_access_not_allowed" | "origin_not_allowed" | "unsupported_media_type" | "method_not_allowed" | "host_not_allowed" | "resource_not_found" | "scheduler_already_running" | "scheduler_control_unavailable" | "request_validation_error" | "state_unavailable" | "scheduler_identity_unavailable" | "internal_error";
        /** ApiErrorDetail */
        ApiErrorDetail: {
            code: components["schemas"]["ApiErrorCode"];
            /** Message */
            message: string;
        };
        /** ApiInfoResponse */
        ApiInfoResponse: {
            /**
             * Actions Enabled
             * @constant
             */
            actions_enabled: true;
            /**
             * Api Version
             * @constant
             */
            api_version: "v1";
            /** Package Version */
            package_version: string;
            /** Read Only */
            read_only: boolean;
        };
        /** ConfirmedActionRequest */
        ConfirmedActionRequest: {
            /**
             * Confirm
             * @constant
             */
            confirm: true;
        };
        /** DagDetailResponse */
        DagDetailResponse: {
            /** Current Priority */
            current_priority: ("low" | "normal" | "high" | "critical") | null;
            /** Current Schedule */
            current_schedule: string | null;
            /** Current Severity */
            current_severity: ("low" | "medium" | "high" | "critical") | null;
            /** Declared Name */
            declared_name: string;
            /** Enabled */
            enabled: boolean;
            /** Name */
            name: string;
            /**
             * On Failure
             * @enum {string}
             */
            on_failure: "skip" | "block";
            /** Registered Schedule */
            registered_schedule: string | null;
            source_status: components["schemas"]["DagSourceStatus"];
            /** Tasks */
            tasks: components["schemas"]["DagTaskResponse"][];
            trigger: components["schemas"]["DagTriggerResponse"] | null;
        };
        /** DagGraphHistoricalTaskResponse */
        DagGraphHistoricalTaskResponse: {
            /** Name */
            name: string;
            status: components["schemas"]["TaskRunStatus"];
        };
        /** DagGraphResponse */
        DagGraphResponse: {
            /** Enabled */
            enabled: boolean;
            latest_run: components["schemas"]["DagGraphRunResponse"] | null;
            /** Name */
            name: string;
            /** Tasks */
            tasks: components["schemas"]["DagGraphTaskResponse"][];
            trigger: components["schemas"]["DagTriggerResponse"] | null;
            /** Unmapped Task History */
            unmapped_task_history: components["schemas"]["DagGraphHistoricalTaskResponse"][];
        };
        /** DagGraphRunResponse */
        DagGraphRunResponse: {
            /** Ended At */
            ended_at: string | null;
            /** Id */
            id: number;
            /**
             * Started At
             * Format: date-time
             */
            started_at: string;
            status: components["schemas"]["DagRunStatus"];
        };
        /** DagGraphTaskResponse */
        DagGraphTaskResponse: {
            /** Depends On */
            depends_on: string[];
            /**
             * Kind
             * @enum {string}
             */
            kind: "pipeline" | "script";
            /** Name */
            name: string;
            status: components["schemas"]["TaskRunStatus"] | null;
        };
        /** DagListResponse */
        DagListResponse: components["schemas"]["DagSummaryResponse"][];
        /** DagRegistrationResponse */
        DagRegistrationResponse: {
            /** Dag Name */
            dag_name: string;
            /** Enabled */
            enabled: boolean;
            /** Schedule */
            schedule: string | null;
            status: components["schemas"]["DagRegistrationStatus"];
        };
        /**
         * DagRegistrationStatus
         * @enum {string}
         */
        DagRegistrationStatus: "created" | "updated" | "unchanged";
        /** DagRunActionResponse */
        DagRunActionResponse: {
            /** Dag Name */
            dag_name: string;
            /** Run Id */
            run_id: number;
            status: components["schemas"]["DagRunStatus"];
        };
        /**
         * DagRunStatus
         * @enum {string}
         */
        DagRunStatus: "running" | "succeeded" | "failed" | "interrupted";
        /** DagScheduleActionRequest */
        DagScheduleActionRequest: {
            /**
             * Confirm
             * @constant
             */
            confirm: true;
            /** Dag Name */
            dag_name: string;
        };
        /** DagScheduleActionResponse */
        DagScheduleActionResponse: {
            /** Dag Name */
            dag_name: string;
            /** Enabled */
            enabled: boolean;
            /**
             * Schedule Status
             * @enum {string}
             */
            schedule_status: "scheduled" | "paused";
        };
        /**
         * DagSourceStatus
         * @enum {string}
         */
        DagSourceStatus: "available" | "missing" | "invalid";
        /** DagSummaryResponse */
        DagSummaryResponse: {
            /** Current Priority */
            current_priority: ("low" | "normal" | "high" | "critical") | null;
            /** Current Schedule */
            current_schedule: string | null;
            /** Current Severity */
            current_severity: ("low" | "medium" | "high" | "critical") | null;
            /** Enabled */
            enabled: boolean;
            /** Name */
            name: string;
            /** Registered Schedule */
            registered_schedule: string | null;
            source_status: components["schemas"]["DagSourceStatus"];
            trigger: components["schemas"]["DagTriggerResponse"] | null;
        };
        /** DagTaskResponse */
        DagTaskResponse: {
            /** Depends On */
            depends_on: string[];
            /**
             * Kind
             * @enum {string}
             */
            kind: "pipeline" | "script";
            /** Name */
            name: string;
            /** Retries */
            retries: number;
            /** Retry Backoff Multiplier */
            retry_backoff_multiplier: number;
            /** Retry Delay Seconds */
            retry_delay_seconds: number;
        };
        /** DagTaskSourceResponse */
        DagTaskSourceResponse: {
            /** Dag Name */
            dag_name: string;
            /** Depends On */
            depends_on: string[];
            /**
             * Kind
             * @enum {string}
             */
            kind: "pipeline" | "script";
            latest_status: components["schemas"]["TaskRunStatus"] | null;
            retry: components["schemas"]["TaskRetryResponse"];
            /** Source */
            source: string;
            /**
             * Source Language
             * @enum {string}
             */
            source_language: "yaml" | "python";
            /** Source Size Bytes */
            source_size_bytes: number;
            /** Task Name */
            task_name: string;
        };
        /** DagTriggerResponse */
        DagTriggerResponse: {
            /** On Success Of */
            on_success_of: string[];
        };
        /** ErrorResponse */
        ErrorResponse: {
            detail: components["schemas"]["ApiErrorDetail"];
        };
        /** RegisterDagRequest */
        RegisterDagRequest: {
            /**
             * Force
             * @default false
             */
            force: boolean;
            /** Source Path */
            source_path: string;
        };
        /** RunDetailResponse */
        RunDetailResponse: {
            /** Dag Name */
            dag_name: string;
            /** Ended At */
            ended_at: string | null;
            /** Error Summary */
            error_summary: string | null;
            /** Id */
            id: number;
            /**
             * On Failure
             * @enum {string}
             */
            on_failure: "skip" | "block";
            /**
             * Severity
             * @enum {string}
             */
            severity: "low" | "medium" | "high" | "critical";
            /**
             * Started At
             * Format: date-time
             */
            started_at: string;
            status: components["schemas"]["DagRunStatus"];
            /** Tasks */
            tasks: components["schemas"]["TaskRunResponse"][];
            /** Trigger Reason */
            trigger_reason: string;
        };
        /** RunListResponse */
        RunListResponse: components["schemas"]["RunSummaryResponse"][];
        /** RunSummaryResponse */
        RunSummaryResponse: {
            /** Dag Name */
            dag_name: string;
            /** Ended At */
            ended_at: string | null;
            /** Id */
            id: number;
            /**
             * Severity
             * @enum {string}
             */
            severity: "low" | "medium" | "high" | "critical";
            /**
             * Started At
             * Format: date-time
             */
            started_at: string;
            status: components["schemas"]["DagRunStatus"];
            /** Trigger Reason */
            trigger_reason: string;
        };
        /** SchedulerStartResponse */
        SchedulerStartResponse: {
            /** Pid */
            pid: number;
            /**
             * State
             * @constant
             */
            state: "started";
        };
        /** SchedulerStatusResponse */
        SchedulerStatusResponse: {
            /** Pid */
            pid: number | null;
            /** Running */
            running: boolean;
            /** Started At */
            started_at: string | null;
        };
        /** SchedulerStopResponse */
        SchedulerStopResponse: {
            /** Forced */
            forced: boolean;
            /** Pid */
            pid?: number | null;
            /**
             * State
             * @enum {string}
             */
            state: "stopped" | "not_running" | "uncertain";
        };
        /** TaskAttemptResponse */
        TaskAttemptResponse: {
            /** Attempt Number */
            attempt_number: number;
            /** Ended At */
            ended_at: string | null;
            /** Error Summary */
            error_summary: string | null;
            /**
             * Started At
             * Format: date-time
             */
            started_at: string;
            status: components["schemas"]["TaskAttemptStatus"];
        };
        /**
         * TaskAttemptStatus
         * @description A task_attempts row only ever exists once an attempt has actually
         *     started, so unlike TaskRunStatus there is no PENDING/SKIPPED/BLOCKED
         *     here -- an attempt is real execution or it doesn't exist yet.
         * @enum {string}
         */
        TaskAttemptStatus: "running" | "succeeded" | "failed";
        /** TaskRetryResponse */
        TaskRetryResponse: {
            /** Retries */
            retries: number;
            /** Retry Backoff Multiplier */
            retry_backoff_multiplier: number;
            /** Retry Delay Seconds */
            retry_delay_seconds: number;
        };
        /** TaskRunResponse */
        TaskRunResponse: {
            /** Attempts */
            attempts: components["schemas"]["TaskAttemptResponse"][];
            /** Ended At */
            ended_at: string | null;
            /** Error Summary */
            error_summary: string | null;
            /** Name */
            name: string;
            /** Started At */
            started_at: string | null;
            status: components["schemas"]["TaskRunStatus"];
        };
        /**
         * TaskRunStatus
         * @enum {string}
         */
        TaskRunStatus: "pending" | "running" | "succeeded" | "failed" | "skipped" | "blocked";
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    get_api_info: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiInfoResponse"];
                };
            };
            /** @description Host is not allowed. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Invalid request. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Internal service error. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description State or scheduler identity unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    pause_dag_schedule: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DagScheduleActionRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DagScheduleActionResponse"];
                };
            };
            /** @description Host is not allowed. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Browser origin is not allowed. */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description DAG not found. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Mutation method is not allowed. */
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description JSON request body required. */
            415: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Invalid request. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Internal service error. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description State or scheduler identity unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    resume_dag_schedule: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DagScheduleActionRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DagScheduleActionResponse"];
                };
            };
            /** @description Host is not allowed. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Browser origin is not allowed. */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description DAG not found. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Mutation method is not allowed. */
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description JSON request body required. */
            415: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Invalid request. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Internal service error. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description State or scheduler identity unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    list_dags: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DagListResponse"];
                };
            };
            /** @description Host is not allowed. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Invalid request. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Internal service error. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description State or scheduler identity unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    register_dag: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RegisterDagRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DagRegistrationResponse"];
                };
            };
            /** @description DAG registered. */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DagRegistrationResponse"];
                };
            };
            /** @description Host is not allowed. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Browser origin is not allowed. */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Mutation method is not allowed. */
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description DAG configuration invalid. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description JSON request body required. */
            415: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Invalid request. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Internal service error. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description State or scheduler identity unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    get_dag: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Registered DAG name. */
                dag_name: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DagDetailResponse"];
                };
            };
            /** @description Host is not allowed. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description DAG not found. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description DAG source unavailable or invalid. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Invalid request. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Internal service error. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description State or scheduler identity unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    get_dag_graph: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Registered DAG name. */
                dag_name: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DagGraphResponse"];
                };
            };
            /** @description Host is not allowed. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description DAG not found. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description DAG source unavailable or invalid. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Invalid request. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Internal service error. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description State or scheduler identity unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    trigger_dag_run: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Registered DAG name. */
                dag_name: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ConfirmedActionRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DagRunActionResponse"];
                };
            };
            /** @description Host is not allowed. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Browser origin is not allowed. */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description DAG not found. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Mutation method is not allowed. */
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description DAG source unavailable or invalid. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description JSON request body required. */
            415: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Invalid request. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Internal service error. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description State or scheduler identity unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    list_runs: {
        parameters: {
            query?: {
                /** @description Maximum number of recent runs to return. */
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunListResponse"];
                };
            };
            /** @description Host is not allowed. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Invalid request. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Internal service error. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description State or scheduler identity unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    get_run: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Persisted DAG run identifier. */
                run_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunDetailResponse"];
                };
            };
            /** @description Host is not allowed. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Run not found. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Invalid request. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Internal service error. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description State or scheduler identity unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    get_scheduler_status: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SchedulerStatusResponse"];
                };
            };
            /** @description Host is not allowed. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Invalid request. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Internal service error. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description State or scheduler identity unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    start_scheduler: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ConfirmedActionRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SchedulerStartResponse"];
                };
            };
            /** @description Host is not allowed. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Browser origin is not allowed. */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Mutation method is not allowed. */
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Scheduler already running. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description JSON request body required. */
            415: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Invalid request. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Internal service error. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description State or scheduler identity unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    stop_scheduler: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ConfirmedActionRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SchedulerStopResponse"];
                };
            };
            /** @description Termination remains uncertain. */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SchedulerStopResponse"];
                };
            };
            /** @description Host is not allowed. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Browser origin is not allowed. */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Mutation method is not allowed. */
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description JSON request body required. */
            415: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Invalid request. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Internal service error. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description State or scheduler identity unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    get_dag_task_source: {
        parameters: {
            query: {
                /** @description Registered DAG name. */
                dag_name: string;
                /** @description Task name in the registered DAG. */
                task_name: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DagTaskSourceResponse"];
                };
            };
            /** @description Host is not allowed. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Source access is not allowed. */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description DAG or task not found. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description DAG or task source unavailable. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Task source exceeds 256 KiB. */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Task source is not UTF-8 text. */
            415: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Invalid request. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Internal service error. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description State or scheduler identity unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
}
