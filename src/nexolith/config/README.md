# Configuration

This package defines Pydantic pipeline models and loads YAML with `${VARIABLE_NAME}` substitution.
Unknown fields and missing environment variables must produce concise errors without exposing
environment contents.

Configuration failures raise `ConfigurationError`; parser, validation, and file I/O causes are
chained internally while public messages omit raw input. Add a discriminated model when
introducing a new built-in component type.
