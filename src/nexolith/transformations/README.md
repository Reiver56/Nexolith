# Transformations

Transformations operate on in-memory `Rows` and are created through the transformation registry.
Built-ins must be deterministic and must never evaluate configuration as Python code.

Invalid columns, operations, or registered types raise `TransformationError` with an actionable,
data-safe message. Add focused unit tests for every new transformation and each supported operator.
