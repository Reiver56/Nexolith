# Configuration

This package defines Pydantic pipeline models and loads YAML with `${VARIABLE_NAME}` substitution.
Unknown fields and missing environment variables must produce concise errors without exposing
environment contents.

Configuration failures raise `ConfigurationError`; parser, validation, and file I/O causes are
chained internally while public messages omit raw input. Add a discriminated model when
introducing a new built-in component type.

Nexo Function destinations use `type: nexofunction.<name>`, a nested SQL `target`, and only
parameters declared by the resolved function. The loader builds the registry from built-ins and
`nexofunctions/*.py` beside the pipeline YAML, validates parameters, and binds the resolved
definition before execution. Discovery never depends on the process working directory or scans
outside that configuration-relative project directory.
