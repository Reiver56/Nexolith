# Source layout

Installable Python code lives under `src/nexolith` so imports during development resolve through
the installed package rather than the repository root.

Use `uv sync --extra dev` to create the editable development environment. Package-specific
architecture is documented in [`nexolith/README.md`](nexolith/README.md).
