import type { TaskKind } from "./taskKinds";

const LABELS: Record<TaskKind, string> = {
  pipeline: "Pipeline task",
  script: "Python script task",
};

export function TaskKindIcon({ kind }: { kind: TaskKind }) {
  const label = LABELS[kind];
  if (kind === "script") {
    return (
      <svg className="task-kind-icon" role="img" aria-label={label} viewBox="0 0 24 24">
        <path d="M8.5 4.5h5a3 3 0 0 1 3 3v2h-7a3 3 0 0 0-3 3v1h-2v-6a3 3 0 0 1 3-3Z" />
        <path d="M15.5 19.5h-5a3 3 0 0 1-3-3v-2h7a3 3 0 0 0 3-3v-1h2v6a3 3 0 0 1-3 3Z" />
        <circle cx="9" cy="7.5" r=".75" fill="currentColor" stroke="none" />
        <circle cx="15" cy="16.5" r=".75" fill="currentColor" stroke="none" />
      </svg>
    );
  }
  return (
    <svg className="task-kind-icon" role="img" aria-label={label} viewBox="0 0 24 24">
      <ellipse cx="12" cy="5.5" rx="7" ry="3" />
      <path d="M5 5.5v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6M5 11.5v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6" />
    </svg>
  );
}
