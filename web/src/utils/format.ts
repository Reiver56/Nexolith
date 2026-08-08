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
