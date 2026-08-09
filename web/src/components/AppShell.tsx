import { useCallback, useEffect } from "react";
import type { ReactNode } from "react";

import nexoMonitorIcon from "../assets/nexo-monitor-icon.png";
import { getApiInfo, getSchedulerStatus } from "../api/client";
import { BACKEND_STATE_CHANGED_EVENT } from "../api/events";
import { useApiResource } from "../hooks/useApiResource";
import { AppLink } from "../router";
import { StatusBadge } from "./StatusBadge";

interface SystemSnapshot {
  packageVersion: string;
  scheduler: "running" | "not-running" | "unknown";
}

async function loadSystemSnapshot(signal: AbortSignal): Promise<SystemSnapshot> {
  const info = await getApiInfo(signal);
  try {
    const scheduler = await getSchedulerStatus(signal);
    return {
      packageVersion: info.package_version,
      scheduler: scheduler.running ? "running" : "not-running",
    };
  } catch (error: unknown) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    return { packageVersion: info.package_version, scheduler: "unknown" };
  }
}

function ProductMark() {
  return (
    <img
      className="product-mark"
      src={nexoMonitorIcon}
      alt=""
      aria-hidden="true"
      width="32"
      height="32"
    />
  );
}

export function AppShell({ children, pathname }: { children: ReactNode; pathname: string }) {
  const loader = useCallback((signal: AbortSignal) => loadSystemSnapshot(signal), []);
  const { resource, reload } = useApiResource(loader, "Nexolith API is unreachable.");
  const dagsActive = pathname === "/" || pathname.startsWith("/dags");
  const runsActive = pathname.startsWith("/runs");
  const schedulerActive = pathname.startsWith("/scheduler");

  useEffect(() => {
    window.addEventListener(BACKEND_STATE_CHANGED_EVENT, reload);
    return () => {
      window.removeEventListener(BACKEND_STATE_CHANGED_EVENT, reload);
    };
  }, [reload]);

  return (
    <div className="app-frame">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <header className="app-header">
        <div className="app-header__inner">
          <AppLink className="product" to="/dags" aria-label="Nexolith monitoring home">
            <ProductMark />
            <span>
              <strong>Nexolith</strong>
              <small>Monitor</small>
            </span>
          </AppLink>
          <nav className="primary-nav" aria-label="Primary navigation">
            <AppLink to="/dags" aria-current={dagsActive ? "page" : undefined}>
              DAGs
            </AppLink>
            <AppLink to="/runs" aria-current={runsActive ? "page" : undefined}>
              Runs
            </AppLink>
            <AppLink
              to="/scheduler"
              aria-current={schedulerActive ? "page" : undefined}
            >
              Scheduler
            </AppLink>
          </nav>
          <div className="system-status" aria-live="polite">
            {resource.status === "loading" ? (
              <StatusBadge value="unknown" label="Checking API" />
            ) : resource.status === "error" ? (
              <StatusBadge value="failed" label="API unreachable" />
            ) : (
              <>
                <StatusBadge value="available" label="API online" />
                <StatusBadge
                  value={resource.data.scheduler === "running" ? "running-enabled" : "unknown"}
                  label={
                    resource.data.scheduler === "running"
                      ? "Scheduler running"
                      : resource.data.scheduler === "not-running"
                        ? "Scheduler stopped"
                        : "Scheduler unknown"
                  }
                />
                <span className="system-status__version">v{resource.data.packageVersion}</span>
              </>
            )}
            <button
              className="icon-button"
              type="button"
              disabled={resource.status === "loading"}
              aria-busy={resource.status === "loading"}
              data-pending={resource.status === "loading" || undefined}
              onClick={reload}
              aria-label="Refresh API and scheduler status"
              title="Refresh status"
            >
              <svg aria-hidden="true" viewBox="0 0 20 20">
                <path d="M15.4 6.2A6.5 6.5 0 1 0 16.3 12" />
                <path d="M15.4 2.8v3.7h-3.7" />
              </svg>
            </button>
          </div>
        </div>
      </header>
      <main id="main-content" className="main-content">
        {children}
      </main>
      <footer className="app-footer">
        <span>Monitoring and explicit actions</span>
        <span aria-hidden="true">·</span>
        <span>v0.3.4</span>
      </footer>
    </div>
  );
}
