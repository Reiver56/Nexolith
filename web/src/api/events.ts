export const BACKEND_STATE_CHANGED_EVENT = "nexolith:backend-state-changed";

export function announceBackendStateChanged(): void {
  window.dispatchEvent(new Event(BACKEND_STATE_CHANGED_EVENT));
}
