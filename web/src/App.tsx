import { useEffect } from "react";

import { AppShell } from "./components/AppShell";
import { DagListPage } from "./features/dags/DagListPage";
import { DagGraphPage } from "./features/dags/graph/DagGraphPage";
import { RunDetailRoute } from "./features/runs/RunDetailPage";
import { RunListPage } from "./features/runs/RunListPage";
import { SchedulerPage } from "./features/scheduler/SchedulerPage";
import { AppLink, navigate, useRoute } from "./router";
import type { Route } from "./router";

function routeMotionKey(route: Route): string {
  if (route.page === "dag-graph") {
    return `${route.page}:${route.dagName}`;
  }
  if (route.page === "run") {
    return `${route.page}:${route.runId}`;
  }
  return route.page;
}

function UnknownRoute() {
  return (
    <section className="not-found">
      <p className="eyebrow">404</p>
      <h1 tabIndex={-1}>Page not found</h1>
      <p>The requested monitoring view does not exist.</p>
      <AppLink className="button button--primary" to="/dags">
        View DAGs
      </AppLink>
    </section>
  );
}

export function App() {
  const { pathname, route } = useRoute();

  useEffect(() => {
    if (pathname === "/") {
      navigate("/dags", { replace: true });
    }
  }, [pathname]);

  return (
    <AppShell pathname={pathname}>
      <div className="route-view" data-route={route.page} key={routeMotionKey(route)}>
        {route.page === "dags" ? <DagListPage /> : null}
        {route.page === "dag-graph" ? <DagGraphPage dagName={route.dagName} /> : null}
        {route.page === "runs" ? <RunListPage /> : null}
        {route.page === "run" ? <RunDetailRoute rawRunId={route.runId} /> : null}
        {route.page === "scheduler" ? <SchedulerPage /> : null}
        {route.page === "not-found" ? <UnknownRoute /> : null}
      </div>
    </AppShell>
  );
}
