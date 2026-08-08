import type { DagGraphTask } from "../../../api/types";

export type TaskKind = DagGraphTask["kind"];

export function taskKindLabel(kind: TaskKind): string {
  return kind === "script" ? "Python script" : "Pipeline";
}
