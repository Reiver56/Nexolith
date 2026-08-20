# Declarative Nexo Action

This service-free example reads a fixed sensor CSV, writes the normal pipeline output, then invokes
a trusted local action only for rows whose `status` is `alert`.

From the repository root:

```powershell
uv run nexolith validate examples/nexo-actions/pipeline.yaml
uv run nexolith run examples/nexo-actions/pipeline.yaml
```

The run creates:

- `processed.csv`, the ordinary destination containing all three rows;
- `action-records.jsonl`, one bounded synthetic record containing the action identifier, matched
  count, and opaque idempotency key. It contains no sensor payload.

Run the pipeline again: the handler receives the same key and deliberately avoids appending a
duplicate record. This deduplication belongs to the handler, not Nexolith.

The relevant YAML is:

```yaml
actions:
  - type: nexoaction.record_sensor_alert
    condition:
      field: status
      operator: equals
      value: alert
    match: any
    idempotency:
      fields: [sensor_id, observed_at]
    parameters:
      output_path: action-records.jsonl
```

Nexolith discovers `nexoactions/record_sensor_alert.py` relative to `pipeline.yaml`, validates its
declared parameter, evaluates the complete input batch, and passes only the two matched rows to the
handler after `processed.csv` has been written.

Delivery is at-least-once with a stable key. If the action fails, the pipeline fails even though
the destination may already have committed. A DAG retry repeats the pipeline write and may invoke
the action again with the same key. Handlers performing real side effects must therefore dedupe by
`context.idempotency_key`.

The local module is trusted Python imported without a sandbox. Do not use untrusted action code.
This example is batch processing, not real-time streaming. An interval-scheduled DAG can poll
slow-changing inputs for operationally near-real-time behavior; true push/event-stream delivery is
a separate feature.
