import importlib
import os
import socket
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

import psutil
import pytest
from typer.testing import CliRunner

from nexolith.cli.development import (
    DevelopmentLauncherError,
    DevelopmentOptions,
    SourceCheckout,
    _default_probe,
    _port_is_available,
    _terminate_owned_process,
    find_source_checkout,
    run_development_servers,
    validate_development_environment,
    version_satisfies,
)

cli_app = importlib.import_module("nexolith.cli.app")


class FakeProcess:
    def __init__(self, pid: int, polls: list[int | None] | None = None) -> None:
        self.pid = pid
        self._polls = polls or [None]
        self.wait_calls: list[float | None] = []

    def poll(self) -> int | None:
        if len(self._polls) > 1:
            return self._polls.pop(0)
        return self._polls[0]

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls.append(timeout)
        return 0

    def terminate(self) -> None:
        self._polls = [1]


def make_checkout(tmp_path: Path, *, dependencies: bool = True) -> tuple[Path, SourceCheckout]:
    root = tmp_path / "checkout"
    module = root / "src" / "nexolith" / "cli" / "development.py"
    module.parent.mkdir(parents=True)
    module.write_text("", encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nname = "nexolith"\n', encoding="utf-8")
    web = root / "web"
    web.mkdir()
    (web / "package.json").write_text(
        '{"engines":{"node":">=24.11.0 <25","npm":">=11 <12"}}', encoding="utf-8"
    )
    if dependencies:
        binary = web / "node_modules" / ".bin"
        binary.mkdir(parents=True)
        (binary / ("vite.cmd" if os.name == "nt" else "vite")).write_text("", encoding="utf-8")
    return module, SourceCheckout(root, web, ">=24.11.0 <25", ">=11 <12")


@pytest.mark.parametrize(
    ("version", "requirement", "expected"),
    [
        ("v24.11.0", ">=24.11.0 <25", True),
        ("24.10.9", ">=24.11.0 <25", False),
        ("25.0.0", ">=24.11.0 <25", False),
        ("11.9.1", ">=11 <12", True),
        ("12.0.0", ">=11 <12", False),
    ],
)
def test_version_ranges(version: str, requirement: str, expected: bool) -> None:
    assert version_satisfies(version, requirement) is expected


def test_version_range_rejects_unexpected_syntax() -> None:
    with pytest.raises(DevelopmentLauncherError, match="unsupported"):
        version_satisfies("24.11.0", "^24")


def test_source_checkout_discovery_does_not_depend_on_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, expected = make_checkout(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert find_source_checkout(module) == expected


def test_source_checkout_discovery_rejects_installed_only_layout(tmp_path: Path) -> None:
    module = tmp_path / "site-packages" / "nexolith" / "cli" / "development.py"
    module.parent.mkdir(parents=True)
    module.write_text("", encoding="utf-8")

    with pytest.raises(DevelopmentLauncherError, match="source checkout"):
        find_source_checkout(module)


def test_source_checkout_discovery_reports_missing_frontend(tmp_path: Path) -> None:
    root = tmp_path / "checkout"
    module = root / "src" / "nexolith" / "cli" / "development.py"
    module.parent.mkdir(parents=True)
    module.write_text("", encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nname = "nexolith"\n', encoding="utf-8")

    with pytest.raises(DevelopmentLauncherError, match="future nexolith ui"):
        find_source_checkout(module)


def test_environment_validation_uses_windows_npm_and_declared_versions(tmp_path: Path) -> None:
    _module, checkout = make_checkout(tmp_path)
    requested: list[str] = []

    def find(name: str) -> str:
        requested.append(name)
        return f"tool/{name}"

    def version(command: Sequence[str], cwd: Path) -> str:
        assert cwd == checkout.root
        return "v24.11.0" if command[0].endswith("node") else "11.0.0"

    assert validate_development_environment(
        checkout,
        platform="win32",
        executable_finder=find,
        version_reader=version,
        module_available=lambda _name: True,
    ) == ("tool/node", "tool/npm.cmd")
    assert requested == ["node", "npm.cmd"]


def test_environment_validation_uses_posix_npm(tmp_path: Path) -> None:
    _module, checkout = make_checkout(tmp_path)
    (checkout.web / "node_modules" / ".bin" / "vite").write_text("", encoding="utf-8")
    requested: list[str] = []

    result = validate_development_environment(
        checkout,
        platform="linux",
        executable_finder=lambda name: requested.append(name) or f"/tools/{name}",
        version_reader=lambda command, _cwd: "24.11.0" if command[0].endswith("node") else "11.0.0",
        module_available=lambda _name: True,
    )

    assert result == ("/tools/node", "/tools/npm")
    assert requested == ["node", "npm"]


def test_environment_validation_reports_missing_api_extra(tmp_path: Path) -> None:
    _module, checkout = make_checkout(tmp_path)

    with pytest.raises(DevelopmentLauncherError, match="uv sync --project"):
        validate_development_environment(checkout, module_available=lambda _name: False)


@pytest.mark.parametrize("missing", ["node", "npm"])
def test_environment_validation_reports_missing_node_or_npm(tmp_path: Path, missing: str) -> None:
    _module, checkout = make_checkout(tmp_path)

    def find(name: str) -> str | None:
        if name.startswith(missing):
            return None
        return name

    with pytest.raises(DevelopmentLauncherError, match="required"):
        validate_development_environment(
            checkout,
            platform="linux",
            executable_finder=find,
            module_available=lambda _name: True,
        )


def test_environment_validation_reports_missing_frontend_dependencies(tmp_path: Path) -> None:
    _module, checkout = make_checkout(tmp_path, dependencies=False)

    with pytest.raises(DevelopmentLauncherError, match=r'npm --prefix "<checkout>/web" ci'):
        validate_development_environment(
            checkout,
            platform="linux",
            executable_finder=lambda name: f"/tools/{name}",
            version_reader=lambda command, _cwd: (
                "24.11.0" if command[0].endswith("node") else "11.0.0"
            ),
            module_available=lambda _name: True,
        )


def test_environment_validation_rejects_wrong_node_version(tmp_path: Path) -> None:
    _module, checkout = make_checkout(tmp_path)

    with pytest.raises(DevelopmentLauncherError, match="does not satisfy"):
        validate_development_environment(
            checkout,
            platform=os.sys.platform,
            executable_finder=lambda name: name,
            version_reader=lambda command, _cwd: (
                "23.0.0" if command[0].endswith("node") else "11.0.0"
            ),
            module_available=lambda _name: True,
        )


def test_environment_validation_rejects_wrong_npm_version(tmp_path: Path) -> None:
    _module, checkout = make_checkout(tmp_path)

    with pytest.raises(DevelopmentLauncherError, match="npm 12.0.0 does not satisfy"):
        validate_development_environment(
            checkout,
            platform=os.sys.platform,
            executable_finder=lambda name: name,
            version_reader=lambda command, _cwd: (
                "24.11.0" if command[0].endswith("node") else "12.0.0"
            ),
            module_available=lambda _name: True,
        )


def test_launcher_starts_from_discovered_directories_and_stops_on_interrupt(
    tmp_path: Path,
) -> None:
    module, checkout = make_checkout(tmp_path)
    processes = [FakeProcess(1001), FakeProcess(1002)]
    launches: list[tuple[tuple[str, ...], Path, Mapping[str, str] | None]] = []
    stopped: list[tuple[int, bool]] = []
    reports: list[tuple[str, bool]] = []
    browser_urls: list[str] = []
    original_directory = Path.cwd()

    def launch(
        command: Sequence[str], cwd: Path, environment: Mapping[str, str] | None
    ) -> FakeProcess:
        launches.append((tuple(command), cwd, environment))
        return processes[len(launches) - 1]

    def interrupt(_seconds: float) -> None:
        raise KeyboardInterrupt

    result = run_development_servers(
        DevelopmentOptions(open_browser=True),
        module_file=module,
        platform=os.sys.platform,
        executable_finder=lambda name: name,
        version_reader=lambda command, _cwd: "24.11.0" if command[0].endswith("node") else "11.0.0",
        process_factory=launch,
        port_available=lambda _host, _port: True,
        probe=lambda _url: True,
        browser_opener=lambda url: browser_urls.append(url) or True,
        sleep=interrupt,
        reporter=lambda message, error: reports.append((message, error)),
        process_terminator=lambda process, descendants: stopped.append((process.pid, descendants)),
    )

    assert result == 130
    assert launches[0][1] == checkout.root
    assert launches[1][1] == checkout.web
    assert launches[0][0][1:4] == ("-m", "nexolith", "api")
    assert launches[1][0][1:3] == ("run", "dev")
    assert launches[1][2] is not None
    assert launches[1][2]["NEXOLITH_API_URL"] == "http://127.0.0.1:8765"
    assert browser_urls == ["http://127.0.0.1:5173"]
    assert stopped == [(1002, True), (1001, False)]
    assert ("API ready: http://127.0.0.1:8765", False) in reports
    assert Path.cwd() == original_directory


def test_launcher_default_does_not_open_browser(tmp_path: Path) -> None:
    module, _checkout = make_checkout(tmp_path)
    opened = False
    stopped: list[int] = []
    processes = iter((FakeProcess(1001, [None, 7]), FakeProcess(1002)))

    def open_browser(_url: str) -> bool:
        nonlocal opened
        opened = True
        return True

    with pytest.raises(DevelopmentLauncherError, match="exited unexpectedly"):
        run_development_servers(
            DevelopmentOptions(),
            module_file=module,
            platform=os.sys.platform,
            executable_finder=lambda name: name,
            version_reader=lambda command, _cwd: (
                "24.11.0" if command[0].endswith("node") else "11.0.0"
            ),
            process_factory=lambda _command, _cwd, _environment: next(processes),
            port_available=lambda _host, _port: True,
            probe=lambda _url: True,
            browser_opener=open_browser,
            sleep=lambda _seconds: None,
            reporter=lambda _message, _error: None,
            process_terminator=lambda process, _descendants: stopped.append(process.pid),
        )
    assert opened is False
    assert stopped == [1002, 1001]


def test_launcher_browser_failure_stops_both_processes(tmp_path: Path) -> None:
    module, _checkout = make_checkout(tmp_path)
    stopped: list[int] = []
    pids = iter((1001, 1002))

    with pytest.raises(DevelopmentLauncherError, match="browser could not be opened"):
        run_development_servers(
            DevelopmentOptions(open_browser=True),
            module_file=module,
            platform=os.sys.platform,
            executable_finder=lambda name: name,
            version_reader=lambda command, _cwd: (
                "24.11.0" if command[0].endswith("node") else "11.0.0"
            ),
            process_factory=lambda _command, _cwd, _environment: FakeProcess(next(pids)),
            port_available=lambda _host, _port: True,
            probe=lambda _url: True,
            browser_opener=lambda _url: False,
            reporter=lambda _message, _error: None,
            process_terminator=lambda process, _descendants: stopped.append(process.pid),
        )
    assert stopped == [1002, 1001]


def test_launcher_readiness_timeout_is_bounded_and_cleans_up(tmp_path: Path) -> None:
    module, _checkout = make_checkout(tmp_path)
    stopped: list[int] = []
    pids = iter((1001, 1002))
    now = 0.0

    def monotonic() -> float:
        nonlocal now
        now += 10.0
        return now

    with pytest.raises(DevelopmentLauncherError, match="within 30 seconds"):
        run_development_servers(
            DevelopmentOptions(),
            module_file=module,
            platform=os.sys.platform,
            executable_finder=lambda name: name,
            version_reader=lambda command, _cwd: (
                "24.11.0" if command[0].endswith("node") else "11.0.0"
            ),
            process_factory=lambda _command, _cwd, _environment: FakeProcess(next(pids)),
            port_available=lambda _host, _port: True,
            probe=lambda _url: False,
            monotonic=monotonic,
            sleep=lambda _seconds: None,
            process_terminator=lambda process, _descendants: stopped.append(process.pid),
        )
    assert stopped == [1002, 1001]


@pytest.mark.parametrize(("failed_index", "service"), [(0, "API server"), (1, "Web server")])
def test_launcher_reports_api_or_vite_startup_exit(
    tmp_path: Path, failed_index: int, service: str
) -> None:
    module, _checkout = make_checkout(tmp_path)
    processes = [FakeProcess(1001), FakeProcess(1002)]
    processes[failed_index] = FakeProcess(1001 + failed_index, [9])
    launches = iter(processes)
    stopped: list[int] = []

    with pytest.raises(DevelopmentLauncherError, match=f"{service} exited during startup"):
        run_development_servers(
            DevelopmentOptions(),
            module_file=module,
            platform=os.sys.platform,
            executable_finder=lambda name: name,
            version_reader=lambda command, _cwd: (
                "24.11.0" if command[0].endswith("node") else "11.0.0"
            ),
            process_factory=lambda _command, _cwd, _environment: next(launches),
            port_available=lambda _host, _port: True,
            probe=lambda _url: False,
            process_terminator=lambda process, _descendants: stopped.append(process.pid),
        )
    assert stopped == [1002, 1001]


def test_launcher_cleans_partial_startup(tmp_path: Path) -> None:
    module, _checkout = make_checkout(tmp_path)
    first = FakeProcess(1001)
    calls = 0
    stopped: list[int] = []

    def launch(
        _command: Sequence[str], _cwd: Path, _environment: Mapping[str, str] | None
    ) -> FakeProcess:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("sentinel-private-detail")
        return first

    with pytest.raises(DevelopmentLauncherError, match="could not start") as caught:
        run_development_servers(
            DevelopmentOptions(),
            module_file=module,
            platform=os.sys.platform,
            executable_finder=lambda name: name,
            version_reader=lambda command, _cwd: (
                "24.11.0" if command[0].endswith("node") else "11.0.0"
            ),
            process_factory=launch,
            port_available=lambda _host, _port: True,
            process_terminator=lambda process, _descendants: stopped.append(process.pid),
        )
    assert "sentinel-private-detail" not in str(caught.value)
    assert stopped == [1001]


def test_launcher_refuses_occupied_port_before_starting(tmp_path: Path) -> None:
    module, _checkout = make_checkout(tmp_path)
    launched = False

    def launch(
        _command: Sequence[str], _cwd: Path, _environment: Mapping[str, str] | None
    ) -> FakeProcess:
        nonlocal launched
        launched = True
        return FakeProcess(1001)

    with pytest.raises(DevelopmentLauncherError, match="already in use"):
        run_development_servers(
            DevelopmentOptions(),
            module_file=module,
            process_factory=launch,
            port_available=lambda _host, port: port != 8765,
        )
    assert launched is False


def test_launcher_refuses_occupied_web_port_before_starting(tmp_path: Path) -> None:
    module, _checkout = make_checkout(tmp_path)
    with pytest.raises(DevelopmentLauncherError, match="Web port 5173"):
        run_development_servers(
            DevelopmentOptions(),
            module_file=module,
            port_available=lambda _host, port: port != 5173,
        )


def test_real_port_check_detects_listener_and_allows_reuse_after_close() -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = int(listener.getsockname()[1])
        assert not _port_is_available("127.0.0.1", port)

    assert _port_is_available("127.0.0.1", port)


def test_launcher_rejects_non_loopback_and_duplicate_ports(tmp_path: Path) -> None:
    module, _checkout = make_checkout(tmp_path)
    with pytest.raises(DevelopmentLauncherError, match="specific unicast"):
        run_development_servers(DevelopmentOptions(api_host="0.0.0.0"), module_file=module)
    with pytest.raises(DevelopmentLauncherError, match="same address and port"):
        run_development_servers(
            DevelopmentOptions(api_port=8765, web_port=8765), module_file=module
        )


def test_explicit_non_loopback_host_warns_and_is_used(tmp_path: Path) -> None:
    module, _checkout = make_checkout(tmp_path)
    reports: list[tuple[str, bool]] = []
    commands: list[tuple[str, ...]] = []
    pids = iter((1001, 1002))

    result = run_development_servers(
        DevelopmentOptions(api_host="192.0.2.10"),
        module_file=module,
        platform=os.sys.platform,
        executable_finder=lambda name: name,
        version_reader=lambda command, _cwd: "24.11.0" if command[0].endswith("node") else "11.0.0",
        process_factory=lambda command, _cwd, _environment: (
            commands.append(tuple(command)) or FakeProcess(next(pids))
        ),
        port_available=lambda _host, _port: True,
        probe=lambda _url: True,
        sleep=lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt),
        reporter=lambda message, error: reports.append((message, error)),
        process_terminator=lambda _process, _descendants: None,
    )

    assert result == 130
    assert "192.0.2.10" in commands[0]
    assert any(error and "non-loopback" in message for message, error in reports)


def test_cli_dev_expected_failure_has_no_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli_app,
        "run_development_servers",
        lambda _options: (_ for _ in ()).throw(DevelopmentLauncherError("safe message")),
    )

    result = CliRunner().invoke(cli_app.app, ["dev"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Development startup failed: safe message" in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_dev_exposes_documented_options() -> None:
    result = CliRunner().invoke(cli_app.app, ["dev", "--help"])

    assert result.exit_code == 0
    for option in ("--api-host", "--api-port", "--web-host", "--web-port", "--open"):
        assert option in result.stdout


def test_real_owned_process_tree_is_terminated(tmp_path: Path) -> None:
    child_pid_file = tmp_path / "child.pid"
    child_code = "import time; time.sleep(30)"
    parent_code = (
        "import pathlib, subprocess, sys, time; "
        f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        f"pathlib.Path({str(child_pid_file)!r}).write_text(str(child.pid)); "
        "time.sleep(30)"
    )
    parent = subprocess.Popen([sys.executable, "-c", parent_code])
    child_pid: int | None = None
    child_process: psutil.Process | None = None
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not child_pid_file.exists():
            time.sleep(0.02)
        assert child_pid_file.exists()
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))
        child_process = psutil.Process(child_pid)

        _terminate_owned_process(parent)

        assert parent.poll() is not None
        deadline = time.monotonic() + 5
        while child_process.is_running() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not child_process.is_running()
    finally:
        if parent.poll() is None:
            parent.kill()
        if child_process is not None and child_process.is_running():
            child_process.kill()


def test_real_dual_process_startup_readiness_and_cleanup(tmp_path: Path) -> None:
    module, _checkout = make_checkout(tmp_path)
    api_port = _unused_tcp_port()
    web_port = _unused_tcp_port()
    while web_port == api_port:
        web_port = _unused_tcp_port()
    processes: list[subprocess.Popen[bytes]] = []
    ready_urls: set[str] = set()

    server_code = (
        "import http.server,sys; "
        "handler=type('Handler',(http.server.BaseHTTPRequestHandler,),{"
        "'do_GET':lambda self:(self.send_response(200),self.end_headers(),"
        "self.wfile.write(b'ok')),'log_message':lambda *args:None}); "
        "http.server.ThreadingHTTPServer(('127.0.0.1',int(sys.argv[1])),handler).serve_forever()"
    )

    def launch(
        _command: Sequence[str], _cwd: Path, _environment: Mapping[str, str] | None
    ) -> subprocess.Popen[bytes]:
        port = api_port if not processes else web_port
        process = subprocess.Popen(
            [sys.executable, "-c", server_code, str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        processes.append(process)
        return process

    def probe(url: str) -> bool:
        result = _default_probe(url)
        if result:
            ready_urls.add(url)
        return result

    def bounded_sleep(seconds: float) -> None:
        if len(ready_urls) == 2:
            raise KeyboardInterrupt
        time.sleep(seconds)

    result = run_development_servers(
        DevelopmentOptions(api_port=api_port, web_port=web_port),
        module_file=module,
        platform=os.sys.platform,
        executable_finder=lambda name: name,
        version_reader=lambda command, _cwd: "24.11.0" if command[0].endswith("node") else "11.0.0",
        process_factory=launch,
        probe=probe,
        sleep=bounded_sleep,
        reporter=lambda _message, _error: None,
    )

    assert result == 130
    assert len(ready_urls) == 2
    assert all(process.poll() is not None for process in processes)


def _unused_tcp_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])
