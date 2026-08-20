# Nexolith package

This directory contains the installable package. The shared
[application layer](application/README.md), configuration, connectors, transformations, execution
state, CLI presentation, and domain exceptions remain separated by subpackage.

New pipeline capabilities should depend on the shared row types and domain interfaces rather than
CLI code. See the [project README](../../README.md) for installation and usage.

Named reusable destination operations live in [`nexofunctions`](nexofunctions/README.md). They are
separate from transformations and jobs: a destination function returns a write result, never rows.

`release_validation.py` is maintainer tooling used by the tagged-release workflow to enforce the
version, changelog, and distribution contract before publishing.
