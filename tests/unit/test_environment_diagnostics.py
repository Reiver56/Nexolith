from importlib import metadata

import pytest
from typer.testing import CliRunner

from nexolith.cli import app
from nexolith.cli import diagnostics as diagnostics_module
from nexolith.cli.diagnostics import (
    DiagnosticItem,
    EnvironmentDiagnostics,
    collect_environment_diagnostics,
    render_environment_diagnostics,
)

runner = CliRunner()


class EditableDistribution:
    def read_text(self, filename: str) -> str | None:
        if filename == "direct_url.json":
            return '{"dir_info": {"editable": true}, "url": "file:///private/path"}'
        return None


def test_render_environment_diagnostics_is_stable() -> None:
    report = EnvironmentDiagnostics(
        nexolith_version="1.2.3",
        python_version="3.12.4",
        python_implementation="CPython",
        operating_system="TestOS",
        operating_system_release="9",
        machine="test64",
        installation="editable package",
        dependencies=(DiagnosticItem("Core", "available (4.5.6)"),),
        features=(DiagnosticItem("Optional", "not installed"),),
    )

    assert render_environment_diagnostics(report) == (
        "Nexolith environment diagnostics\n"
        "================================\n"
        "Nexolith: 1.2.3\n"
        "Python: 3.12.4 (CPython)\n"
        "Platform: TestOS 9 (test64)\n"
        "Installation: editable package\n"
        "\n"
        "Core dependencies:\n"
        "  Core: available (4.5.6)\n"
        "\n"
        "Optional features:\n"
        "  Optional: not installed\n"
        "\n"
        "Privacy: environment variables, usernames, hostnames, and filesystem paths "
        "are not reported."
    )


def test_collect_environment_diagnostics_detects_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    versions = {
        "pydantic": "2.11.0",
        "PyYAML": "6.0.2",
        "SQLAlchemy": "2.0.41",
        "typer": "0.16.0",
        "psycopg": "3.2.9",
    }

    monkeypatch.setattr(metadata, "version", versions.__getitem__)
    monkeypatch.setattr(metadata, "distribution", lambda _name: EditableDistribution())
    monkeypatch.setattr(diagnostics_module.platform, "python_version", lambda: "3.12.4")
    monkeypatch.setattr(diagnostics_module.platform, "python_implementation", lambda: "CPython")
    monkeypatch.setattr(diagnostics_module.platform, "system", lambda: "TestOS")
    monkeypatch.setattr(diagnostics_module.platform, "release", lambda: "9")
    monkeypatch.setattr(diagnostics_module.platform, "machine", lambda: "test64")
    monkeypatch.setattr(diagnostics_module.sqlite3, "sqlite_version", "3.49.0")
    report = collect_environment_diagnostics()

    assert report.python_version == "3.12.4"
    assert report.python_implementation == "CPython"
    assert report.operating_system == "TestOS"
    assert report.operating_system_release == "9"
    assert report.machine == "test64"
    assert report.installation == "editable package"
    assert report.dependencies == (
        DiagnosticItem("Pydantic", "available (2.11.0)"),
        DiagnosticItem("PyYAML", "available (6.0.2)"),
        DiagnosticItem("SQLAlchemy", "available (2.0.41)"),
        DiagnosticItem("Typer", "available (0.16.0)"),
    )
    assert report.features[0] == DiagnosticItem("SQLite", "available (SQLite 3.49.0)")
    assert report.features[1] == DiagnosticItem("PostgreSQL", "available (3.2.9)")


def test_collect_environment_diagnostics_marks_missing_postgresql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_version = metadata.version

    def version(distribution_name: str) -> str:
        if distribution_name == "psycopg":
            raise metadata.PackageNotFoundError(distribution_name)
        return real_version(distribution_name)

    monkeypatch.setattr(metadata, "version", version)

    report = collect_environment_diagnostics()

    assert report.features[1] == DiagnosticItem("PostgreSQL", "not installed")


def test_diagnostics_command_is_secret_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    secrets = (
        "nxl31-database-password",
        "nxl31-local-username",
        "C:\\Users\\nxl31-private-home",
    )
    monkeypatch.setenv("DATABASE_URL", f"postgresql://user:{secrets[0]}@localhost/database")
    monkeypatch.setenv("USERNAME", secrets[1])
    monkeypatch.setenv("HOME", secrets[2])

    result = runner.invoke(app, ["diagnostics"])

    assert result.exit_code == 0
    assert result.stdout.startswith("Nexolith environment diagnostics\n")
    assert "Optional features:" in result.stdout
    assert "PostgreSQL:" in result.stdout
    assert "not reported" in result.stdout
    if any(secret in result.stdout for secret in secrets):
        pytest.fail("diagnostics output exposed a sentinel value", pytrace=False)
