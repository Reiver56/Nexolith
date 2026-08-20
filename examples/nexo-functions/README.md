# Nexo Functions example

This service-free example demonstrates project-local discovery. Run it from the repository root:

```bash
uv run nexolith validate examples/nexo-functions/pipeline.yaml
uv run nexolith run examples/nexo-functions/pipeline.yaml
```

`pipeline.yaml` resolves `nexofunction.append_write` from
`nexofunctions/append_write.py`, beside the pipeline file. The function declares no parameters,
receives the in-flight rows, writes through the stable destination capability, and returns a
`NexoFunctionResult`. The generated `nexo-functions.db` is ignored by Git.

Local modules are trusted Python code. Nexolith imports them during validation and provides no
sandbox. Never place untrusted code in `nexofunctions/`.
