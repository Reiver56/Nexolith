import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  Panel,
  Position,
  ReactFlow,
  ReactFlowProvider,
  applyEdgeChanges,
  applyNodeChanges,
  useEdgesState,
  useNodesState,
  useReactFlow,
} from "@xyflow/react";
import type { EdgeChange, NodeChange, NodeProps, NodeTypes } from "@xyflow/react";
import { useEffect } from "react";

import type {
  DagFlowEdge,
  DagFlowModel,
  DagFlowNode,
  ExternalDagFlowNode,
  TaskFlowNode,
} from "./layout";

function TaskNode({ data, selected }: NodeProps<TaskFlowNode>) {
  return (
    <div className="task-node" data-selected={selected || undefined} data-status={data.status}>
      <Handle
        className="graph-handle"
        type="target"
        position={Position.Left}
        isConnectable={false}
      />
      <span className="task-node__kind">Task</span>
      <strong>{data.name}</strong>
      <span className="task-node__meta">
        <span className="task-node__status-mark" aria-hidden="true" />
        {data.statusLabel}
        <span aria-hidden="true">·</span>
        {data.dependencyCount === 1
          ? "1 dependency"
          : `${String(data.dependencyCount)} dependencies`}
      </span>
      <Handle
        className="graph-handle"
        type="source"
        position={Position.Right}
        isConnectable={false}
      />
    </div>
  );
}

function DagNode({ data, selected }: NodeProps<ExternalDagFlowNode>) {
  const current = data.relation === "current";
  return (
    <div className="dag-node" data-selected={selected || undefined} data-relation={data.relation}>
      <Handle
        className="graph-handle"
        type="target"
        position={Position.Left}
        isConnectable={false}
      />
      <span className="dag-node__kind">{current ? "Current DAG" : "Upstream DAG"}</span>
      <strong>{data.name}</strong>
      <span>{current ? "Trigger boundary" : "on_success_of"}</span>
      <Handle
        className="graph-handle"
        type="source"
        position={Position.Right}
        isConnectable={false}
      />
    </div>
  );
}

const nodeTypes = { task: TaskNode, dag: DagNode } satisfies NodeTypes;

function ControlledGraph({ model }: { model: DagFlowModel }) {
  const [nodes, setNodes] = useNodesState<DagFlowNode>(model.nodes);
  const [edges, setEdges] = useEdgesState<DagFlowEdge>(model.edges);
  const { fitView } = useReactFlow<DagFlowNode, DagFlowEdge>();

  useEffect(() => {
    setNodes((current) =>
      model.nodes.map((node) => ({
        ...node,
        selected: current.find((candidate) => candidate.id === node.id)?.selected ?? false,
      })),
    );
    setEdges((current) =>
      model.edges.map((edge) => ({
        ...edge,
        selected: current.find((candidate) => candidate.id === edge.id)?.selected ?? false,
      })),
    );
  }, [model, setEdges, setNodes]);

  const handleNodeChanges = (changes: NodeChange<DagFlowNode>[]): void => {
    const safeChanges = changes.filter(
      (change) => change.type === "select" || change.type === "dimensions",
    );
    setNodes((current) => applyNodeChanges(safeChanges, current));
  };

  const handleEdgeChanges = (changes: EdgeChange<DagFlowEdge>[]): void => {
    setEdges((current) =>
      applyEdgeChanges(
        changes.filter((change) => change.type === "select"),
        current,
      ),
    );
  };

  const resetLayout = (): void => {
    setNodes(model.nodes);
    setEdges(model.edges);
    window.requestAnimationFrame(() => {
      void fitView({ duration: 220, padding: 0.18 });
    });
  };

  return (
    <ReactFlow<DagFlowNode, DagFlowEdge>
      aria-label="Interactive DAG dependency graph"
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodesChange={handleNodeChanges}
      onEdgesChange={handleEdgeChanges}
      nodesDraggable={false}
      nodesConnectable={false}
      edgesReconnectable={false}
      elementsSelectable
      deleteKeyCode={null}
      minZoom={0.35}
      maxZoom={1.8}
      fitView
      fitViewOptions={{ padding: 0.18 }}
      preventScrolling={false}
      panOnDrag
      zoomOnPinch
      zoomOnDoubleClick
      zoomOnScroll={false}
      onlyRenderVisibleElements
    >
      <Background variant={BackgroundVariant.Dots} gap={22} size={1.15} />
      <Controls showInteractive={false} position="bottom-right" />
      <Panel position="top-right">
        <button className="graph-reset" type="button" onClick={resetLayout}>
          Reset layout
        </button>
      </Panel>
    </ReactFlow>
  );
}

export function DagGraphCanvas({ model }: { model: DagFlowModel }) {
  return (
    <div className="graph-canvas" data-testid="dag-graph-canvas">
      <ReactFlowProvider>
        <ControlledGraph model={model} />
      </ReactFlowProvider>
    </div>
  );
}
