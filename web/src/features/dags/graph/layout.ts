import { MarkerType, Position } from "@xyflow/react";
import type { Edge, Node } from "@xyflow/react";

import type { DagGraph, DagGraphNexoAction, DagGraphOperation, TaskRunStatus } from "../../../api/types";

export type GraphNodeStatus = TaskRunStatus | "no-history" | "unknown";

export type TaskNodeData = {
  kind: "task";
  name: string;
  status: GraphNodeStatus;
  statusLabel: string;
  dependencyCount: number;
  operations: DagGraphOperation[];
  actions: DagGraphNexoAction[];
} & Record<string, unknown>;

export type DagNodeData = {
  kind: "dag";
  name: string;
  relation: "current" | "upstream";
} & Record<string, unknown>;

export type TaskFlowNode = Node<TaskNodeData, "task">;
export type ExternalDagFlowNode = Node<DagNodeData, "dag">;
export type DagFlowNode = TaskFlowNode | ExternalDagFlowNode;
export type DagFlowEdge = Edge<{ relation: "dependency" | "cross-dag" }>;

export interface DagFlowModel {
  nodes: DagFlowNode[];
  edges: DagFlowEdge[];
  dependencySummary: {
    name: string;
    dependsOn: string[];
    statusLabel: string;
    operations: DagGraphOperation[];
    actions: DagGraphNexoAction[];
  }[];
  upstreamDags: string[];
}

const X_GAP = 300;
const Y_GAP = 150;
const CROSS_DAG_X_GAP = 320;

function compareText(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

function stableId(prefix: string, value: string): string {
  return `${prefix}:${encodeURIComponent(value)}`;
}

export function statusFor(graph: DagGraph, status: TaskRunStatus | null): GraphNodeStatus {
  if (status !== null) {
    return status;
  }
  return graph.latest_run === null ? "no-history" : "unknown";
}

export function statusLabel(status: GraphNodeStatus): string {
  if (status === "no-history") {
    return "No run history";
  }
  if (status === "unknown") {
    return "Status unavailable";
  }
  return status.charAt(0).toUpperCase() + status.slice(1);
}

function taskLayers(graph: DagGraph): Map<string, number> {
  const byName = new Map(graph.tasks.map((task) => [task.name, task]));
  if (byName.size !== graph.tasks.length) {
    throw new Error("DAG graph contains duplicate task identities.");
  }
  for (const task of graph.tasks) {
    for (const dependency of new Set(task.depends_on)) {
      if (!byName.has(dependency) || dependency === task.name) {
        throw new Error("DAG graph contains an invalid dependency.");
      }
    }
  }

  const layers = new Map<string, number>();
  const remaining = new Set(byName.keys());
  while (remaining.size > 0) {
    const eligible = [...remaining]
      .filter((name) =>
        [...new Set(byName.get(name)?.depends_on ?? [])].every((dependency) =>
          layers.has(dependency),
        ),
      )
      .sort(compareText);
    if (eligible.length === 0) {
      throw new Error("DAG graph contains a dependency cycle.");
    }
    for (const name of eligible) {
      const dependencies = [...new Set(byName.get(name)?.depends_on ?? [])];
      const layer = dependencies.reduce(
        (highest, dependency) => Math.max(highest, (layers.get(dependency) ?? -1) + 1),
        0,
      );
      layers.set(name, layer);
      remaining.delete(name);
    }
  }
  return layers;
}

export function buildDagFlow(graph: DagGraph): DagFlowModel {
  const layers = taskLayers(graph);
  const byLayer = new Map<number, string[]>();
  for (const [name, layer] of layers) {
    const names = byLayer.get(layer) ?? [];
    names.push(name);
    byLayer.set(layer, names);
  }
  for (const names of byLayer.values()) {
    names.sort(compareText);
  }

  const taskByName = new Map(graph.tasks.map((task) => [task.name, task]));
  const taskNodes: DagFlowNode[] = [];
  for (const [layer, names] of [...byLayer.entries()].sort(([left], [right]) => left - right)) {
    names.forEach((name, index) => {
      const task = taskByName.get(name);
      if (task === undefined) {
        throw new Error("DAG graph task identity changed during layout.");
      }
      const status = statusFor(graph, task.status);
      taskNodes.push({
        id: stableId("task", name),
        type: "task",
        position: {
          x: layer * X_GAP,
          y: (index - (names.length - 1) / 2) * Y_GAP,
        },
        data: {
          kind: "task",
          name,
          status,
          statusLabel: statusLabel(status),
          dependencyCount: new Set(task.depends_on).size,
          operations: task.operations,
          actions: task.actions,
        },
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        draggable: false,
        connectable: false,
        selectable: true,
        focusable: false,
        ariaLabel: `${name}, ${task.operations.map((operation) => operation.label).join(", ")}, ${String(task.actions.length)} actions, ${statusLabel(status)}, ${String(new Set(task.depends_on).size)} dependencies`,
        className: `graph-task graph-task--${status}`,
      });
    });
  }

  const dependencyEdges: DagFlowEdge[] = [];
  const dependencyIds = new Set<string>();
  for (const task of [...graph.tasks].sort((left, right) => compareText(left.name, right.name))) {
    for (const dependency of [...new Set(task.depends_on)].sort(compareText)) {
      const id = `dependency:${stableId("task", dependency)}->${stableId("task", task.name)}`;
      if (dependencyIds.has(id)) {
        continue;
      }
      dependencyIds.add(id);
      dependencyEdges.push({
        id,
        source: stableId("task", dependency),
        target: stableId("task", task.name),
        type: "smoothstep",
        markerEnd: { type: MarkerType.ArrowClosed },
        className: "graph-edge graph-edge--dependency",
        selectable: true,
        focusable: true,
        data: { relation: "dependency" },
        ariaLabel: `${dependency} is required by ${task.name}`,
      });
    }
  }

  const upstreamDags = [...new Set(graph.trigger?.on_success_of ?? [])].sort(compareText);
  const dagNodes: DagFlowNode[] = [];
  const crossDagEdges: DagFlowEdge[] = [];
  if (upstreamDags.length > 0) {
    const currentId = stableId("dag-current", graph.name);
    dagNodes.push({
      id: currentId,
      type: "dag",
      position: { x: -CROSS_DAG_X_GAP, y: 0 },
      data: { kind: "dag", name: graph.name, relation: "current" },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      draggable: false,
      connectable: false,
      selectable: true,
      focusable: true,
      ariaLabel: `${graph.name}, current DAG trigger boundary`,
      className: "graph-dag graph-dag--current",
    });
    upstreamDags.forEach((name, index) => {
      const upstreamId = stableId("dag-upstream", name);
      dagNodes.push({
        id: upstreamId,
        type: "dag",
        position: {
          x: -CROSS_DAG_X_GAP * 2,
          y: (index - (upstreamDags.length - 1) / 2) * 110,
        },
        data: { kind: "dag", name, relation: "upstream" },
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        draggable: false,
        connectable: false,
        selectable: true,
        focusable: true,
        ariaLabel: `${name}, upstream DAG; triggers ${graph.name} after success`,
        className: "graph-dag graph-dag--upstream",
      });
      crossDagEdges.push({
        id: `cross-dag:${upstreamId}->${currentId}`,
        source: upstreamId,
        target: currentId,
        type: "smoothstep",
        label: "on success",
        markerEnd: { type: MarkerType.ArrowClosed },
        className: "graph-edge graph-edge--cross-dag",
        selectable: true,
        focusable: true,
        data: { relation: "cross-dag" },
        ariaLabel: `${name} triggers ${graph.name} after successful completion`,
      });
    });
  }

  return {
    nodes: [...dagNodes, ...taskNodes],
    edges: [...crossDagEdges, ...dependencyEdges],
    dependencySummary: [...graph.tasks]
      .sort((left, right) => compareText(left.name, right.name))
      .map((task) => ({
        name: task.name,
        dependsOn: [...new Set(task.depends_on)].sort(compareText),
        statusLabel: statusLabel(statusFor(graph, task.status)),
        operations: task.operations,
        actions: task.actions,
      })),
    upstreamDags,
  };
}
