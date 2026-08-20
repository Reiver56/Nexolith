import nexoIcon from "../../../assets/nexo-monitor-icon.png";
import type { DagGraphNexoAction, DagGraphOperation } from "../../../api/types";

type CapabilityKind = DagGraphOperation["kind"] | DagGraphNexoAction["kind"];

export function OperationIcon({ kind }: { kind: CapabilityKind }) {
  if (kind === "nexo_function" || kind === "nexo_action") {
    return <img className="operation-icon operation-icon--nexo" src={nexoIcon} alt="" aria-hidden="true" />;
  }
  if (kind === "python") {
    return (
      <svg className="operation-icon" aria-hidden="true" viewBox="0 0 24 24">
        <path d="M12 2.75c-4.8 0-4.5 2.08-4.5 2.08v2.15h4.58v.65H5.68S2.6 7.28 2.6 12.1s2.69 4.65 2.69 4.65h1.6v-2.28s-.09-2.69 2.65-2.69h4.55s2.55.04 2.55-2.47V5.17S17.03 2.75 12 2.75Zm-2.52 1.44a.84.84 0 1 1 0 1.68.84.84 0 0 1 0-1.68Z" />
        <path d="M12 21.25c4.8 0 4.5-2.08 4.5-2.08v-2.15h-4.58v-.65h6.4s3.08.35 3.08-4.47-2.69-4.65-2.69-4.65h-1.6v2.28s.09 2.69-2.65 2.69H9.91s-2.55-.04-2.55 2.47v4.14S6.97 21.25 12 21.25Zm2.52-1.44a.84.84 0 1 1 0-1.68.84.84 0 0 1 0 1.68Z" />
      </svg>
    );
  }
  if (kind === "sql") {
    return (
      <svg className="operation-icon operation-icon--sql" aria-hidden="true" viewBox="0 0 24 24">
        <ellipse cx="12" cy="5" rx="7.5" ry="3" />
        <path d="M4.5 5v7c0 1.66 3.36 3 7.5 3s7.5-1.34 7.5-3V5M4.5 12v7c0 1.66 3.36 3 7.5 3s7.5-1.34 7.5-3v-7" />
      </svg>
    );
  }
  return (
    <svg className="operation-icon" aria-hidden="true" viewBox="0 0 24 24">
      <path d="M4 7.5h6l2 2h8v8.75A1.75 1.75 0 0 1 18.25 20H5.75A1.75 1.75 0 0 1 4 18.25V7.5Z" />
      <path d="M4 7.5V5.75C4 4.78 4.78 4 5.75 4h4l2 2H18c1.1 0 2 .9 2 2" />
    </svg>
  );
}
