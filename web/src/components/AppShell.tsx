import { useCallback } from "react";
import type { ReactNode } from "react";

import { getApiInfo, getSchedulerStatus } from "../api/client";
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
    <svg className="product-mark" aria-hidden="true" viewBox="0 0 32 32">
      <path d="M16 3.5 27 9.8v12.4L16 28.5 5 22.2V9.8L16 3.5Z" />
      <path d="m5.5 10 10.5 6 10.5-6M16 16v12" />
    </svg>
  );
}

export function AppShell({ children, pathname }: { children: ReactNode; pathname: string }) {
  const loader = useCallback((signal: AbortSignal) => loadSystemSnapshot(signal), []);
  const { resource, reload } = useApiResource(loader, "Nexolith API is unreachable.");
  const dagsActive = pathname === "/" || pathname.startsWith("/dags");
  const runsActive = pathname.startsWith("/runs");

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
        <span>Read-only monitoring</span>
        <span aria-hidden="true">·</span>
        <span>NXL-114 · v0.3.4</span>
      </footer>
    </div>
  );
}
