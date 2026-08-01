# Execution

The default runner coordinates source creation, ordered transformations, destination writes, and
execution metadata. It logs lifecycle events without logging component configuration.

The runner emits the extraction, aggregate transformation, writing, completion, and failure
boundaries defined by the shared application event contract. It does not estimate progress or
depend on a presentation framework. See the [application documentation](../application/README.md)
for ordering and payload guarantees.

Expected domain failures produce a failed result for API compatibility. Callers such as the CLI can
set `raise_on_error=True` to receive an `ExecutionError` chained to the specific domain cause.
Unexpected exceptions propagate unchanged so programming errors remain debuggable.

Keep orchestration independent from CLI formatting and connector-specific behavior. Logs may name
the exception type but must not include configuration values or credentials.
