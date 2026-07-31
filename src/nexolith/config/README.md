# Configuration

This package defines Pydantic pipeline models and loads YAML with `${VARIABLE_NAME}` substitution.
Unknown fields and missing environment variables must produce concise errors without exposing
environment contents.

Add a discriminated model when introducing a new built-in component type.
