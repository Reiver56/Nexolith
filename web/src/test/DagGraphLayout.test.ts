import type { DagGraph, TaskRunStatus } from "../api/types";
import { buildDagFlow, statusFor, statusLabel } from "../features/dags/graph/layout";
import { dagGraph } from "./fixtures";

function essentials(graph: DagGraph) {
  const model = buildDagFlow(graph);
  return {
    nodes: model.nodes.map(({ id, position, type }) => ({ id, position, type })),
    edges: model.edges.map(({ id, source, target }) => ({ id, source, target })),
  };
}

function taskAt(graph: DagGraph, index: number): DagGraph["tasks"][number] {
  const task = graph.tasks[index];
  if (task === undefined) {
    throw new Error(`Expected fixture task ${String(index)}.`);
  }
  return task;
}

test("lays out tasks and edges deterministically regardless of source order", () => {
  const shuffled: DagGraph = {
    ...dagGraph,
    tasks: [taskAt(dagGraph, 2), taskAt(dagGraph, 0), taskAt(dagGraph, 1)],
    trigger: { on_success_of: ["inventory sync", "warehouse-refresh"] },
  };
  expect(essentials(shuffled)).toEqual(essentials(dagGraph));
});

test("deduplicates dependency and cross-DAG edges while preserving direction", () => {
  const model = buildDagFlow({
    ...dagGraph,
    tasks: dagGraph.tasks.map((task) =>
      task.name === "publish" ? { ...task, depends_on: ["extract", "enrich", "extract"] } : task,
    ),
    trigger: { on_success_of: ["warehouse-refresh", "warehouse-refresh"] },
  });
  expect(model.edges.filter((edge) => edge.data?.relation === "dependency")).toHaveLength(3);
  expect(model.edges.filter((edge) => edge.data?.relation === "cross-dag")).toHaveLength(1);
  expect(model.edges).toContainEqual(
    expect.objectContaining({ source: "task:extract", target: "task:publish" }),
  );
});

test("keeps cross-DAG triggers at DAG boundaries, separate from task dependencies", () => {
  const model = buildDagFlow(dagGraph);
  const crossEdges = model.edges.filter((edge) => edge.data?.relation === "cross-dag");
  expect(crossEdges).toHaveLength(2);
  expect(crossEdges.every((edge) => edge.source.startsWith("dag-upstream:"))).toBe(true);
  expect(crossEdges.every((edge) => edge.target.startsWith("dag-current:"))).toBe(true);
  expect(model.upstreamDags).toEqual(["inventory sync", "warehouse-refresh"]);
});

test("maps persisted, missing-history, and unavailable statuses honestly", () => {
  expect(statusLabel(statusFor({ ...dagGraph, latest_run: null }, null))).toBe("No run history");
  expect(statusLabel(statusFor(dagGraph, null))).toBe("Status unavailable");
  expect(statusLabel(statusFor(dagGraph, "succeeded"))).toBe("Succeeded");
});

const taskStatuses: TaskRunStatus[] = [
  "pending",
  "running",
  "succeeded",
  "failed",
  "skipped",
  "blocked",
];

test.each(taskStatuses)("preserves the persisted %s status as text and node data", (status) => {
  const graph: DagGraph = {
    ...dagGraph,
    tasks: [{ name: "only", depends_on: [], status }],
  };
  const node = buildDagFlow(graph).nodes.find((candidate) => candidate.id === "task:only");
  expect(node?.data).toMatchObject({ status, statusLabel: statusLabel(status) });
  expect(node?.ariaLabel).toContain(statusLabel(status));
});

const invalidGraphs: { graph: DagGraph; expected: string }[] = [
  { graph: { ...dagGraph, tasks: [{ name: "a", depends_on: ["missing"], status: null }] }, expected: "invalid dependency" },
  { graph: { ...dagGraph, tasks: [{ name: "a", depends_on: ["a"], status: null }] }, expected: "invalid dependency" },
  { graph: { ...dagGraph, tasks: [{ name: "a", depends_on: ["b"], status: null }, { name: "b", depends_on: ["a"], status: null }] }, expected: "dependency cycle" },
  { graph: { ...dagGraph, tasks: [{ name: "a", depends_on: [], status: null }, { name: "a", depends_on: [], status: null }] }, expected: "duplicate task" },
];

test.each(invalidGraphs)("rejects unsafe or ambiguous DAG structures", ({ graph, expected }) => {
  expect(() => buildDagFlow(graph)).toThrow(expected);
});
