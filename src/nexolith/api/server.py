"""Optional-dependency boundary for running the monitoring API."""

import importlib
from collections.abc import Callable
from typing import Protocol, cast


class ApiDependenciesUnavailable(RuntimeError):
    pass


class ApiServerStartupError(RuntimeError):
    pass


class ServerRunner(Protocol):
    def __call__(self, application: object, *, host: str, port: int) -> None: ...


def run_api_server(
    host: str,
    port: int,
    *,
    runner: ServerRunner | None = None,
    app_loader: Callable[[], object] | None = None,
) -> None:
    """Load optional API dependencies only when the server is requested."""
    try:
        if runner is None:
            uvicorn = importlib.import_module("uvicorn")
            selected_runner = cast(ServerRunner, uvicorn.run)
        else:
            selected_runner = runner
        if app_loader is not None:
            application = app_loader()
        else:
            app_module = importlib.import_module("nexolith.api.app")
            create_app = cast(Callable[[], object], app_module.create_app)
            application = create_app()
    except ModuleNotFoundError as exc:
        if exc.name in {"fastapi", "uvicorn"}:
            raise ApiDependenciesUnavailable(
                "API dependencies are not installed. Install Nexolith with the 'api' extra."
            ) from exc
        raise

    try:
        selected_runner(application, host=host, port=port)
    except OSError as exc:
        raise ApiServerStartupError(
            f"API server could not bind to {host}:{port}. The address may already be in use."
        ) from exc
    except SystemExit as exc:
        if exc.code in {None, 0}:
            return
        raise ApiServerStartupError(
            f"API server could not start on {host}:{port}. The address may already be in use."
        ) from exc
