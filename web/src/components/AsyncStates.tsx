export function LoadingState({ label = "Loading data" }: { label?: string }) {
  return (
    <section
      className="state-panel state-panel--loading"
      data-state="loading"
      aria-busy="true"
      aria-label={label}
    >
      <span className="sr-only">{label}</span>
      <div className="skeleton skeleton--title" />
      <div className="skeleton" />
      <div className="skeleton skeleton--short" />
    </section>
  );
}

export function ErrorState({ message, retry }: { message: string; retry: () => void }) {
  return (
    <section className="state-panel" data-state="error" aria-live="assertive">
      <span className="state-panel__icon" aria-hidden="true">
        !
      </span>
      <div>
        <h2>Unable to load this view</h2>
        <p>{message}</p>
      </div>
      <button className="button button--secondary" type="button" onClick={retry}>
        Try again
      </button>
    </section>
  );
}

export function EmptyState({ title, message }: { title: string; message: string }) {
  return (
    <section className="state-panel state-panel--empty" data-state="empty">
      <span className="state-panel__icon" aria-hidden="true">
        ·
      </span>
      <div>
        <h2>{title}</h2>
        <p>{message}</p>
      </div>
    </section>
  );
}

export function RefreshButton({
  onClick,
  busy = false,
}: {
  onClick: () => void;
  busy?: boolean;
}) {
  return (
    <button
      className="button button--secondary"
      type="button"
      disabled={busy}
      aria-busy={busy}
      aria-label={busy ? "Refreshing" : "Refresh"}
      data-pending={busy || undefined}
      onClick={onClick}
    >
      <svg aria-hidden="true" viewBox="0 0 20 20" width="16" height="16">
        <path d="M15.4 6.2A6.5 6.5 0 1 0 16.3 12" />
        <path d="M15.4 2.8v3.7h-3.7" />
      </svg>
      Refresh
    </button>
  );
}
