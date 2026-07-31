# Models

This package contains runtime domain models such as execution status and results. Models should
remain suitable for future persistence without adding persistence concerns to the runner.

Execution results record timing, status, row counts, and a safe optional error message.
