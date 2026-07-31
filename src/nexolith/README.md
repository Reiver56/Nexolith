# Nexolith package

This directory contains the application package. Configuration, connectors, transformations,
execution state, CLI presentation, and domain exceptions remain separated by subpackage.

New pipeline capabilities should depend on the shared row types and domain interfaces rather than
CLI code. See the [project README](../../README.md) for installation and usage.
