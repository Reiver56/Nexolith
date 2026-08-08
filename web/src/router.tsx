/* eslint-disable react-refresh/only-export-components -- routing is one tiny cohesive module. */
import { useSyncExternalStore } from "react";
import type { AnchorHTMLAttributes, MouseEvent, ReactNode } from "react";

const NAVIGATION_EVENT = "nexolith:navigate";

export type Route =
  | { page: "dags" }
  | { page: "dag-graph"; dagName: string }
  | { page: "runs" }
  | { page: "run"; runId: string }
  | { page: "not-found" };

function subscribe(listener: () => void): () => void {
  window.addEventListener("popstate", listener);
  window.addEventListener(NAVIGATION_EVENT, listener);
  return () => {
    window.removeEventListener("popstate", listener);
    window.removeEventListener(NAVIGATION_EVENT, listener);
  };
}

function snapshot(): string {
  return window.location.pathname;
}

export function navigate(path: string, options: { replace?: boolean } = {}): void {
  if (options.replace) {
    window.history.replaceState(null, "", path);
  } else {
    window.history.pushState(null, "", path);
  }
  window.dispatchEvent(new Event(NAVIGATION_EVENT));
}

export function parseRoute(pathname: string): Route {
  const normalized = pathname !== "/" && pathname.endsWith("/") ? pathname.slice(0, -1) : pathname;
  if (normalized === "/" || normalized === "/dags") {
    return { page: "dags" };
  }
  if (normalized === "/runs") {
    return { page: "runs" };
  }
  const graphMatch = /^\/dags\/([^/]+)\/graph$/.exec(normalized);
  if (graphMatch?.[1] !== undefined) {
    try {
      return { page: "dag-graph", dagName: decodeURIComponent(graphMatch[1]) };
    } catch {
      return { page: "not-found" };
    }
  }
  const runMatch = /^\/runs\/([^/]+)$/.exec(normalized);
  if (runMatch?.[1] !== undefined) {
    try {
      return { page: "run", runId: decodeURIComponent(runMatch[1]) };
    } catch {
      return { page: "not-found" };
    }
  }
  return { page: "not-found" };
}

export function dagGraphPath(dagName: string): string {
  return `/dags/${encodeURIComponent(dagName)}/graph`;
}

export function useRoute(): { pathname: string; route: Route } {
  const pathname = useSyncExternalStore(subscribe, snapshot, () => "/dags");
  return { pathname, route: parseRoute(pathname) };
}

type AppLinkProps = AnchorHTMLAttributes<HTMLAnchorElement> & {
  children: ReactNode;
  to: string;
};

export function AppLink({ children, onClick, to, ...props }: AppLinkProps) {
  function handleClick(event: MouseEvent<HTMLAnchorElement>): void {
    onClick?.(event);
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey
    ) {
      return;
    }
    event.preventDefault();
    navigate(to);
  }

  return (
    <a {...props} href={to} onClick={handleClick}>
      {children}
    </a>
  );
}
