"""Source-checkout development launcher for the API and React monitor."""

from __future__ import annotations

import contextlib
import importlib.util
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import psutil


class DevelopmentLauncherError(RuntimeError):
    """An expected, safely renderable local-development failure."""


@dataclass(frozen=True, slots=True)
class DevelopmentOptions:
    api_host: str = "127.0.0.1"
    api_port: int = 8765
    web_host: str = "127.0.0.1"
    web_port: int = 5173
    open_browser: bool = False


@dataclass(frozen=True, slots=True)
class SourceCheckout:
    root: Path
    web: Path
    node_engine: str
    npm_engine: str


class ChildProcess(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self) -> None: ...


ProcessFactory = Callable[[Sequence[str], Path, Mapping[str, str] | None], ChildProcess]
CommandVersionReader = Callable[[Sequence[str], Path], str]
UrlProbe = Callable[[str], bool]
BrowserOpener = Callable[[str], bool]
Reporter = Callable[[str, bool], None]
ProcessTerminator = Callable[[ChildProcess, bool], None]
BindAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
LocalhostResolver = Callable[[], frozenset[BindAddress]]

_VERSION_PATTERN = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+].*)?$")
_COMPARATOR_PATTERN = re.compile(r"^(>=|<=|>|<|=)?(\d+(?:\.\d+){0,2})$")
_READINESS_TIMEOUT_SECONDS = 30.0
_POLL_INTERVAL_SECONDS = 0.1
_SHUTDOWN_TIMEOUT_SECONDS = 5.0


def find_source_checkout(module_file: Path | None = None) -> SourceCheckout:
    """Find this package's checkout without consulting the caller's working directory."""
    start = (module_file or Path(__file__)).resolve()
    for candidate in start.parents:
        pyproject = candidate / "pyproject.toml"
        package_json = candidate / "web" / "package.json"
        package_source = candidate / "src" / "nexolith"
        if not (pyproject.is_file() and package_json.is_file() and package_source.is_dir()):
            continue
        try:
            start.relative_to(package_source)
        except ValueError:
            continue
        try:
            project = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            package = json.loads(package_json.read_text(encoding="utf-8"))
            engines = package["engines"]
            node_engine = engines["node"]
            npm_engine = engines["npm"]
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            tomllib.TOMLDecodeError,
            KeyError,
            TypeError,
        ) as exc:
            raise DevelopmentLauncherError(
                "The source checkout has invalid development metadata."
            ) from exc
        project_metadata = project.get("project")
        if not isinstance(project_metadata, dict) or project_metadata.get("name") != "nexolith":
            continue
        if not isinstance(node_engine, str) or not isinstance(npm_engine, str):
            raise DevelopmentLauncherError(
                "The source checkout has invalid Node.js engine requirements."
            )
        return SourceCheckout(candidate, candidate / "web", node_engine, npm_engine)
    raise DevelopmentLauncherError(
        "nexolith dev requires a Nexolith source checkout; the future nexolith ui command will "
        "provide the packaged user-facing launcher."
    )


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _parse_version(value: str) -> tuple[int, int, int]:
    match = _VERSION_PATTERN.fullmatch(value.strip())
    if match is None:
        raise DevelopmentLauncherError("A required tool returned an invalid version.")
    major, minor, patch = match.groups()
    return int(major), int(minor or 0), int(patch or 0)


def version_satisfies(version: str, requirement: str) -> bool:
    """Evaluate the simple comparator ranges committed in ``web/package.json``."""
    parsed = _parse_version(version)
    tokens = requirement.split()
    if not tokens:
        raise DevelopmentLauncherError("The frontend engine requirement is empty.")
    for token in tokens:
        match = _COMPARATOR_PATTERN.fullmatch(token)
        if match is None:
            raise DevelopmentLauncherError("The frontend engine requirement is unsupported.")
        operator = match.group(1) or "="
        expected = _parse_version(match.group(2))
        comparisons = {
            "=": parsed == expected,
            ">": parsed > expected,
            ">=": parsed >= expected,
            "<": parsed < expected,
            "<=": parsed <= expected,
        }
        if not comparisons[operator]:
            return False
    return True


def _default_version_reader(command: Sequence[str], cwd: Path) -> str:
    try:
        result = subprocess.run(
            list(command),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DevelopmentLauncherError(
            "A required development tool could not be executed."
        ) from exc
    if result.returncode != 0:
        raise DevelopmentLauncherError("A required development tool could not report its version.")
    return result.stdout.strip()


def _safe_executable_finder(name: str) -> str | None:
    """Search absolute PATH entries only; never execute a same-name file from the caller's cwd."""
    for entry in os.environ.get("PATH", os.defpath).split(os.pathsep):
        directory = Path(entry)
        if not directory.is_absolute():
            continue
        found = shutil.which(name, path=str(directory))
        if found is None:
            continue
        candidate = Path(found).resolve()
        if candidate.is_file():
            return str(candidate)
    return None


def _canonical_ip_address(host: str) -> BindAddress:
    address = ipaddress.ip_address(host)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _validate_host(host: str, label: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        address = _canonical_ip_address(host)
    except ValueError as exc:
        raise DevelopmentLauncherError(f"{label} host must be an IP address or localhost.") from exc
    if address.is_unspecified or address.is_multicast:
        raise DevelopmentLauncherError(f"{label} host must be a specific unicast address.")
    return address.is_loopback


def _resolve_localhost_addresses() -> frozenset[BindAddress]:
    try:
        records = socket.getaddrinfo("localhost", 0, type=socket.SOCK_STREAM)
        addresses = frozenset(_canonical_ip_address(str(record[4][0])) for record in records)
    except (OSError, ValueError) as exc:
        raise DevelopmentLauncherError(
            "localhost bind addresses could not be resolved safely."
        ) from exc
    if not addresses or any(not address.is_loopback for address in addresses):
        raise DevelopmentLauncherError(
            "localhost did not resolve exclusively to loopback addresses."
        )
    return addresses


def _effective_bind_addresses(
    host: str, localhost_resolver: LocalhostResolver
) -> frozenset[BindAddress]:
    if host.casefold() != "localhost":
        return frozenset((_canonical_ip_address(host),))
    try:
        addresses = frozenset(
            _canonical_ip_address(str(address)) for address in localhost_resolver()
        )
    except (OSError, ValueError) as exc:
        raise DevelopmentLauncherError(
            "localhost bind addresses could not be resolved safely."
        ) from exc
    if not addresses or any(not address.is_loopback for address in addresses):
        raise DevelopmentLauncherError(
            "localhost did not resolve exclusively to loopback addresses."
        )
    return addresses


def _bind_targets_overlap(
    first_host: str,
    first_port: int,
    second_host: str,
    second_port: int,
    *,
    localhost_resolver: LocalhostResolver = _resolve_localhost_addresses,
) -> bool:
    """Return whether two validated targets can claim the same effective socket address."""
    if first_port != second_port:
        return False
    if first_host.casefold() == second_host.casefold():
        return True
    first_addresses = _effective_bind_addresses(first_host, localhost_resolver)
    second_addresses = _effective_bind_addresses(second_host, localhost_resolver)
    return not first_addresses.isdisjoint(second_addresses)


def _port_is_available(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return False
    except ConnectionRefusedError:
        return True
    except TimeoutError:
        # A local listener can deliberately avoid accepting connections. Let the
        # servers' strict bind remain authoritative for this uncommon case.
        return True
    except OSError:
        return True


def _default_process_factory(
    command: Sequence[str], cwd: Path, environment: Mapping[str, str] | None
) -> ChildProcess:
    creationflags = 0
    start_new_session = False
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        start_new_session = True
    return subprocess.Popen(
        list(command),
        cwd=cwd,
        env=None if environment is None else dict(environment),
        creationflags=creationflags,
        start_new_session=start_new_session,
    )


def _default_probe(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=0.5) as response:
            return 200 <= int(response.status) < 300
    except (OSError, urllib.error.URLError, ValueError):
        return False


def _report(message: str, error: bool) -> None:
    stream = sys.stderr if error else sys.stdout
    print(message, file=stream, flush=True)


def _url(host: str, port: int, path: str = "") -> str:
    display_host = f"[{host}]" if ":" in host else host
    return f"http://{display_host}:{port}{path}"


def _npm_install_command(platform: str) -> str:
    executable = "npm.cmd" if platform == "win32" else "npm"
    separator = "\\" if platform == "win32" else "/"
    return f'{executable} --prefix "<checkout>{separator}web" ci'


def validate_development_environment(
    checkout: SourceCheckout,
    *,
    platform: str = sys.platform,
    executable_finder: Callable[[str], str | None] = _safe_executable_finder,
    version_reader: CommandVersionReader = _default_version_reader,
    module_available: Callable[[str], bool] = _module_available,
) -> tuple[str, str]:
    """Return verified Node.js and npm executables, without installing anything."""
    if not (module_available("fastapi") and module_available("uvicorn")):
        raise DevelopmentLauncherError(
            'API dependencies are missing. Run: uv sync --project "<checkout>" --extra api'
        )

    node = executable_finder("node")
    npm_name = "npm.cmd" if platform == "win32" else "npm"
    npm = executable_finder(npm_name)
    if node is None or npm is None:
        raise DevelopmentLauncherError(
            f"Node.js and {npm_name} are required; install versions matching web/package.json."
        )

    node_version = version_reader((node, "--version"), checkout.root)
    npm_version = version_reader((npm, "--version"), checkout.root)
    if not version_satisfies(node_version, checkout.node_engine):
        raise DevelopmentLauncherError(
            f"Node.js {node_version} does not satisfy web/package.json ({checkout.node_engine})."
        )
    if not version_satisfies(npm_version, checkout.npm_engine):
        raise DevelopmentLauncherError(
            f"npm {npm_version} does not satisfy web/package.json ({checkout.npm_engine})."
        )

    vite = checkout.web / "node_modules" / ".bin" / ("vite.cmd" if platform == "win32" else "vite")
    if not vite.is_file():
        raise DevelopmentLauncherError(
            f"Frontend dependencies are missing. Run: {_npm_install_command(platform)}"
        )
    return node, npm


def _terminate_owned_process(process: ChildProcess, include_descendants: bool = True) -> None:
    if process.poll() is not None:
        return
    try:
        root = psutil.Process(process.pid)
        children = root.children(recursive=True) if include_descendants else []
    except (psutil.Error, OSError):
        children = []
        root = None
    if root is None:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            process.terminate()
    targets = [*reversed(children), *([] if root is None else [root])]
    for target in targets:
        with contextlib.suppress(psutil.Error, OSError):
            target.terminate()
    if targets:
        _gone, alive = psutil.wait_procs(targets, timeout=_SHUTDOWN_TIMEOUT_SECONDS)
        for target in alive:
            with contextlib.suppress(psutil.Error, OSError):
                target.kill()
        psutil.wait_procs(alive, timeout=_SHUTDOWN_TIMEOUT_SECONDS)
    with contextlib.suppress(OSError, subprocess.SubprocessError, TimeoutError):
        process.wait(timeout=_SHUTDOWN_TIMEOUT_SECONDS)


def _wait_until_ready(
    processes: Sequence[tuple[str, ChildProcess]],
    targets: Sequence[tuple[str, str]],
    *,
    probe: UrlProbe,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    ready = [False] * len(targets)
    deadline = monotonic() + _READINESS_TIMEOUT_SECONDS
    while monotonic() < deadline:
        for name, process in processes:
            return_code = process.poll()
            if return_code is not None:
                raise DevelopmentLauncherError(
                    f"{name} exited during startup (exit code {return_code})."
                )
        for index, (_name, url) in enumerate(targets):
            if not ready[index]:
                ready[index] = probe(url)
        if all(ready):
            return
        sleep(_POLL_INTERVAL_SECONDS)
    pending = [name for (name, _url), is_ready in zip(targets, ready, strict=True) if not is_ready]
    subject = " and ".join(pending)
    raise DevelopmentLauncherError(f"{subject} did not become ready within 30 seconds.")


def _supervise(
    processes: Sequence[tuple[str, ChildProcess]], sleep: Callable[[float], None]
) -> None:
    while True:
        for name, process in processes:
            return_code = process.poll()
            if return_code is not None:
                raise DevelopmentLauncherError(
                    f"{name} exited unexpectedly (exit code {return_code})."
                )
        sleep(_POLL_INTERVAL_SECONDS)


def run_development_servers(
    options: DevelopmentOptions,
    *,
    module_file: Path | None = None,
    platform: str = sys.platform,
    executable_finder: Callable[[str], str | None] = _safe_executable_finder,
    version_reader: CommandVersionReader = _default_version_reader,
    process_factory: ProcessFactory = _default_process_factory,
    port_available: Callable[[str, int], bool] = _port_is_available,
    probe: UrlProbe = _default_probe,
    browser_opener: BrowserOpener = webbrowser.open,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    reporter: Reporter = _report,
    process_terminator: ProcessTerminator = _terminate_owned_process,
    localhost_resolver: LocalhostResolver = _resolve_localhost_addresses,
) -> int:
    """Start, verify, and supervise the two source-checkout development servers."""
    checkout = find_source_checkout(module_file)
    api_is_loopback = _validate_host(options.api_host, "API")
    web_is_loopback = _validate_host(options.web_host, "Web")
    if not (api_is_loopback and web_is_loopback):
        reporter(
            "Warning: a non-loopback development bind exposes an unauthenticated local service.",
            True,
        )
    if _bind_targets_overlap(
        options.api_host,
        options.api_port,
        options.web_host,
        options.web_port,
        localhost_resolver=localhost_resolver,
    ):
        raise DevelopmentLauncherError("API and web servers cannot use the same address and port.")
    for label, host, port in (
        ("API", options.api_host, options.api_port),
        ("Web", options.web_host, options.web_port),
    ):
        if not 1 <= port <= 65535:
            raise DevelopmentLauncherError(f"{label} port must be between 1 and 65535.")
        if not port_available(host, port):
            raise DevelopmentLauncherError(
                f"{label} port {port} on {host} is already in use or unavailable."
            )

    _node, npm = validate_development_environment(
        checkout,
        platform=platform,
        executable_finder=executable_finder,
        version_reader=version_reader,
    )
    api_url = _url(options.api_host, options.api_port)
    web_url = _url(options.web_host, options.web_port)
    api_command = (
        sys.executable,
        "-m",
        "nexolith",
        "api",
        "start",
        "--host",
        options.api_host,
        "--port",
        str(options.api_port),
    )
    web_command = (
        npm,
        "run",
        "dev",
        "--",
        "--host",
        options.web_host,
        "--port",
        str(options.web_port),
        "--strictPort",
    )
    web_environment = dict(os.environ)
    web_environment["NEXOLITH_API_URL"] = api_url
    processes: list[tuple[str, ChildProcess]] = []
    try:
        try:
            processes.append(("API server", process_factory(api_command, checkout.root, None)))
            processes.append(
                ("Web server", process_factory(web_command, checkout.web, web_environment))
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DevelopmentLauncherError("A development server process could not start.") from exc

        _wait_until_ready(
            processes,
            (
                ("API server", _url(options.api_host, options.api_port, "/api/v1")),
                ("Web server", web_url),
            ),
            probe=probe,
            monotonic=monotonic,
            sleep=sleep,
        )
        reporter(f"API ready: {api_url}", False)
        reporter(f"Monitor ready: {web_url}", False)
        reporter("Press Ctrl+C to stop both development servers.", False)
        if options.open_browser:
            try:
                opened = browser_opener(web_url)
            except (OSError, webbrowser.Error) as exc:
                raise DevelopmentLauncherError("The browser could not be opened.") from exc
            if not opened:
                raise DevelopmentLauncherError("The browser could not be opened.")
        _supervise(processes, sleep)
    except KeyboardInterrupt:
        reporter("Stopping development servers...", False)
        return 130
    finally:
        for name, process in reversed(processes):
            # Vite/npm workers belong to this launcher. API descendants may include the
            # deliberately detached scheduler started through an explicit API action.
            process_terminator(process, name == "Web server")
    return 0
