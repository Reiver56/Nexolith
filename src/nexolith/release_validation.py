"""Fail-closed validation for a tagged Nexolith release."""

from __future__ import annotations

import argparse
import re
import tomllib
from datetime import date
from pathlib import Path

TAG_PATTERN = re.compile(r"v(?P<version>(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))")
SOURCE_VERSION_PATTERN = re.compile(r'^__version__ = "(?P<version>[^"]+)"$', re.MULTILINE)
RELEASE_DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", re.ASCII)


class ReleaseValidationError(ValueError):
    """Raised when release inputs do not satisfy the publishing contract."""


def _tag_version(tag: str) -> str:
    match = TAG_PATTERN.fullmatch(tag)
    if match is None:
        raise ReleaseValidationError("release tag must use the exact form vX.Y.Z")
    return match.group("version")


def _project_version(root: Path) -> str:
    with (root / "pyproject.toml").open("rb") as pyproject_file:
        pyproject = tomllib.load(pyproject_file)
    version = pyproject.get("project", {}).get("version")
    if not isinstance(version, str):
        raise ReleaseValidationError("pyproject.toml must declare project.version")
    return version


def _source_version(root: Path) -> str:
    source = (root / "src" / "nexolith" / "__init__.py").read_text(encoding="utf-8")
    match = SOURCE_VERSION_PATTERN.search(source)
    if match is None:
        raise ReleaseValidationError("src/nexolith/__init__.py must declare __version__")
    return match.group("version")


def _validate_changelog(root: Path, version: str) -> None:
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    heading_pattern = re.compile(
        rf"^## \[{re.escape(version)}\] - (?P<release_date>[^\r\n]+)$", re.MULTILINE
    )
    headings = list(heading_pattern.finditer(changelog))
    if len(headings) != 1:
        raise ReleaseValidationError(
            f"CHANGELOG.md must contain exactly one section for [{version}]"
        )

    release_date = headings[0].group("release_date")
    if RELEASE_DATE_PATTERN.fullmatch(release_date) is None:
        raise ReleaseValidationError(
            f"CHANGELOG.md section [{version}] must use an ISO release date in YYYY-MM-DD format, "
            f"not {release_date!r}"
        )
    try:
        date.fromisoformat(release_date)
    except ValueError as error:
        raise ReleaseValidationError(
            f"CHANGELOG.md section [{version}] must use an ISO release date in YYYY-MM-DD format, "
            f"not {release_date!r}"
        ) from error


def _validate_distributions(dist_dir: Path, version: str) -> None:
    expected_names = {
        f"nexolith-{version}-py3-none-any.whl",
        f"nexolith-{version}.tar.gz",
    }
    actual_names = {path.name for path in dist_dir.iterdir() if not path.name.startswith(".")}
    if actual_names != expected_names:
        expected = ", ".join(sorted(expected_names))
        actual = ", ".join(sorted(actual_names)) or "none"
        raise ReleaseValidationError(f"dist must contain exactly {expected}; found {actual}")


def validate_release(tag: str, root: Path, dist_dir: Path | None = None) -> str:
    """Validate tag, package versions, changelog, and optional built artifacts."""
    version = _tag_version(tag)
    project_version = _project_version(root)
    source_version = _source_version(root)
    if project_version != version:
        raise ReleaseValidationError(
            f"tag version {version} does not match project.version {project_version}"
        )
    if source_version != version:
        raise ReleaseValidationError(
            f"tag version {version} does not match nexolith.__version__ {source_version}"
        )

    _validate_changelog(root, version)
    if dist_dir is not None:
        _validate_distributions(dist_dir, version)
    return version


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="release tag in the form vX.Y.Z")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    parser.add_argument("--dist-dir", type=Path, help="built distribution directory")
    arguments = parser.parse_args()

    try:
        version = validate_release(arguments.tag, arguments.root, arguments.dist_dir)
    except (OSError, ReleaseValidationError, tomllib.TOMLDecodeError) as error:
        parser.exit(1, f"release validation failed: {error}\n")
    print(f"release validation passed for {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
