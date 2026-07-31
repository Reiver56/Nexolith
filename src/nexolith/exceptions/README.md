# Domain exceptions

All expected failures derive from `NexolithError` and use one of four stable categories:
`ConfigurationError`, `ConnectorError`, `TransformationError`, or `ExecutionError`. Messages may
be shown by the CLI, so they must be actionable and safe for terminal output.

Wrap low-level exceptions with `raise ... from ...` so debugging retains the original cause. Do not
include secrets, tokens, usernames, passwords, authenticated URLs, or raw library diagnostics in
the public message. Unexpected programming errors must not be converted to domain errors.
