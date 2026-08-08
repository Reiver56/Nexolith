import { humanize } from "../utils/format";

const POSITIVE = new Set(["succeeded", "available", "running-enabled"]);
const NEGATIVE = new Set(["failed", "critical", "invalid", "missing", "blocked"]);
const WARNING = new Set(["interrupted", "skipped", "high", "unknown"]);
const ACTIVE = new Set(["running", "normal", "medium"]);

export function StatusBadge({ label, value }: { label?: string; value: string }) {
  const tone = POSITIVE.has(value)
    ? "positive"
    : NEGATIVE.has(value)
      ? "negative"
      : WARNING.has(value)
        ? "warning"
        : ACTIVE.has(value)
          ? "active"
          : "neutral";

  return (
    <span className="status-badge" data-tone={tone}>
      <span className="status-badge__dot" aria-hidden="true" />
      {label ?? humanize(value)}
    </span>
  );
}
