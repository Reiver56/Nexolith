"""Collect and render shareable, secret-safe environment diagnostics."""

import json
import platform
import sqlite3
from dataclasses import dataclass
from importlib import metadata

from nexolith import __version__


@dataclass(frozen=True)
class DiagnosticItem:
    """A named diagnostic value rendered in a stable order."""

    name: str
    value: str


@dataclass(frozen=True)
class EnvironmentDiagnostics:
    """Environment details that are safe to include in a public issue."""

    nexolith_version: str
    python_version: str
    python_implementation: str
    operating_system: str
    operating_system_release: str
    machine: str
    installation: str
    dependencies: tuple[DiagnosticItem, ...]
    features: tuple[DiagnosticItem, ...]


_CORE_DISTRIBUTIONS = (
    ("Pydantic", "pydantic"),
    ("PyYAML", "PyYAML"),
    ("SQLAlchemy", "SQLAlchemy"),
    ("Typer", "typer"),
)


def _known(value: str) -> str:
    return value or "unknown"


def _distribution_status(distribution_name: str) -> str:
    try:
        installed_version = metadata.version(distribution_name)
    except metadata.PackageNotFoundError:
        return "not installed"
    return f"available ({installed_version})"


def _installation_status() -> str:
    try:
        distribution = metadata.distribution("nexolith")
    except metadata.PackageNotFoundError:
        return "package metadata unavailable"
    direct_url = distribution.read_text("direct_url.json")
    if direct_url is None:
        return "installed package"
    try:
        direct_url_data = json.loads(direct_url)
    except (json.JSONDecodeError, TypeError):
        return "installed package"
    if not isinstance(direct_url_data, dict):
        return "installed package"
    directory_info = direct_url_data.get("dir_info")
    if isinstance(directory_info, dict) and directory_info.get("editable") is True:
        return "editable package"
    return "installed package"


def collect_environment_diagnostics() -> EnvironmentDiagnostics:
    """Collect bounded diagnostics without reading environment values or reporting local paths."""

    dependencies = tuple(
        DiagnosticItem(label, _distribution_status(distribution_name))
        for label, distribution_name in _CORE_DISTRIBUTIONS
    )
    features = (
        DiagnosticItem("SQLite", f"available (SQLite {sqlite3.sqlite_version})"),
        DiagnosticItem("PostgreSQL", _distribution_status("psycopg")),
    )
    return EnvironmentDiagnostics(
        nexolith_version=__version__,
        python_version=_known(platform.python_version()),
        python_implementation=_known(platform.python_implementation()),
        operating_system=_known(platform.system()),
        operating_system_release=_known(platform.release()),
        machine=_known(platform.machine()),
        installation=_installation_status(),
        dependencies=dependencies,
        features=features,
    )


def render_environment_diagnostics(report: EnvironmentDiagnostics) -> str:
    """Render diagnostics as deterministic plain text suitable for an issue report."""

    lines = [
        "Nexolith environment diagnostics",
        "================================",
        f"Nexolith: {report.nexolith_version}",
        f"Python: {report.python_version} ({report.python_implementation})",
        (
            "Platform: "
            f"{report.operating_system} {report.operating_system_release} ({report.machine})"
        ),
        f"Installation: {report.installation}",
        "",
        "Core dependencies:",
    ]
    lines.extend(f"  {item.name}: {item.value}" for item in report.dependencies)
    lines.extend(("", "Optional features:"))
    lines.extend(f"  {item.name}: {item.value}" for item in report.features)
    lines.extend(
        (
            "",
            "Privacy: environment variables, usernames, hostnames, and filesystem paths "
            "are not reported.",
        )
    )
    return "\n".join(lines)
