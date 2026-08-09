const timestampFormatter = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

export function formatTimestamp(value: string | null): string {
  if (value === null) {
    return "Not finished";
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "Unknown time" : timestampFormatter.format(parsed);
}

export function humanize(value: string): string {
  return value
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export function formatDuration(
  startedAt: string,
  endedAt: string | null,
  now: () => number = Date.now,
): string {
  const start = new Date(startedAt).getTime();
  const end = endedAt === null ? now() : new Date(endedAt).getTime();
  if (!Number.isFinite(start) || !Number.isFinite(end)) {
    return "Unknown duration";
  }
  const seconds = Math.max(0, Math.floor((end - start) / 1_000));
  const hours = Math.floor(seconds / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);
  const remainingSeconds = seconds % 60;
  if (hours > 0) {
    return `${String(hours)}h ${String(minutes)}m ${String(remainingSeconds)}s`;
  }
  if (minutes > 0) {
    return `${String(minutes)}m ${String(remainingSeconds)}s`;
  }
  return `${String(remainingSeconds)}s`;
}
