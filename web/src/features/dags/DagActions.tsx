import { useEffect, useRef, useState } from "react";

import { ApiClientError, registerDag, triggerDagRun } from "../../api/client";
import { announceBackendStateChanged } from "../../api/events";
import type { DagRegistration } from "../../api/types";
import { ActionFeedback } from "../../components/ActionFeedback";
import type { Feedback } from "../../components/ActionFeedback";
import { ConfirmationDialog } from "../../components/ConfirmationDialog";
import { navigate, runDetailPath } from "../../router";

function actionError(error: unknown, fallback: string): string {
  if (error instanceof ApiClientError) {
    return error.message;
  }
  return fallback;
}

function registrationMessage(result: DagRegistration): string {
  if (result.status === "created") {
    return `${result.dag_name} was registered.`;
  }
  if (result.status === "updated") {
    return `${result.dag_name} registration was updated.`;
  }
  return `${result.dag_name} registration is already current.`;
}

export function RegisterDagControl({ onRegistered }: { onRegistered: () => void }) {
  const [open, setOpen] = useState(false);
  const [sourcePath, setSourcePath] = useState("");
  const [force, setForce] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const controller = useRef<AbortController | null>(null);

  useEffect(() => () => controller.current?.abort(), []);

  function close(): void {
    if (busy) {
      return;
    }
    setOpen(false);
    setError(undefined);
  }

  async function confirm(): Promise<void> {
    const trimmedPath = sourcePath.trim();
    if (trimmedPath.length === 0) {
      setError("Enter the path to a DAG YAML file.");
      return;
    }
    setBusy(true);
    setError(undefined);
    controller.current = new AbortController();
    try {
      const result = await registerDag(
        { source_path: trimmedPath, force },
        controller.current.signal,
      );
      setOpen(false);
      setSourcePath("");
      setForce(false);
      setFeedback({
        tone: "success",
        message: registrationMessage(result),
      });
      onRegistered();
      announceBackendStateChanged();
    } catch (caught: unknown) {
      if (!(caught instanceof DOMException && caught.name === "AbortError")) {
        setError(actionError(caught, "Nexolith could not register this DAG."));
      }
    } finally {
      setBusy(false);
      controller.current = null;
    }
  }

  return (
    <>
      <div className="action-stack">
        <button
          className="button button--primary"
          type="button"
          onClick={() => {
            setFeedback(null);
            setOpen(true);
          }}
        >
          Register DAG
        </button>
        <ActionFeedback feedback={feedback} />
      </div>
      <ConfirmationDialog
        open={open}
        title="Register this DAG?"
        description="Nexolith will validate the selected YAML file and add or update its scheduling registration. No DAG task will run."
        confirmLabel="Register DAG"
        busy={busy}
        {...(error === undefined ? {} : { error })}
        onCancel={close}
        onConfirm={() => void confirm()}
      >
        <label className="field">
          <span>DAG YAML path</span>
          <input
            type="text"
            value={sourcePath}
            disabled={busy}
            autoComplete="off"
            placeholder="C:\\workflows\\orders.yaml"
            onChange={(event) => {
              setSourcePath(event.currentTarget.value);
            }}
          />
        </label>
        <label className="checkbox-field">
          <input
            type="checkbox"
            checked={force}
            disabled={busy}
            onChange={(event) => {
              setForce(event.currentTarget.checked);
            }}
          />
          <span>Replace changed registration metadata if this DAG is already registered.</span>
        </label>
      </ConfirmationDialog>
    </>
  );
}

export function TriggerDagControl({ dagName }: { dagName: string }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const controller = useRef<AbortController | null>(null);

  useEffect(() => () => controller.current?.abort(), []);

  async function confirm(): Promise<void> {
    setBusy(true);
    setError(undefined);
    controller.current = new AbortController();
    try {
      const result = await triggerDagRun(dagName, controller.current.signal);
      announceBackendStateChanged();
      navigate(runDetailPath(result.run_id));
    } catch (caught: unknown) {
      if (!(caught instanceof DOMException && caught.name === "AbortError")) {
        setError(actionError(caught, "Nexolith could not trigger this DAG."));
      }
    } finally {
      setBusy(false);
      controller.current = null;
    }
  }

  return (
    <>
      <button
        className="button button--secondary button--compact"
        type="button"
        onClick={() => {
          setError(undefined);
          setOpen(true);
        }}
      >
        Trigger run
      </button>
      <ConfirmationDialog
        open={open}
        title={`Trigger ${dagName}?`}
        description="Nexolith will execute this DAG now. Its tasks can perform external writes, and retrying after an uncertain response can create another run."
        confirmLabel="Trigger run"
        busy={busy}
        {...(error === undefined ? {} : { error })}
        onCancel={() => {
          if (!busy) {
            setOpen(false);
            setError(undefined);
          }
        }}
        onConfirm={() => void confirm()}
      />
    </>
  );
}
