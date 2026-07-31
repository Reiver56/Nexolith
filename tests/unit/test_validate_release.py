from pathlib import Path

import pytest

from nexolith.release_validation import ReleaseValidationError, validate_release


def write_release_files(root: Path, *, version: str = "1.2.3", status: str = "2026-07-31") -> None:
    (root / "src" / "nexolith").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "nexolith"\nversion = "{version}"\n', encoding="utf-8"
    )
    (root / "src" / "nexolith" / "__init__.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8"
    )
    (root / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## [{version}] - {status}\n", encoding="utf-8"
    )


def test_validate_release_accepts_matching_release(tmp_path: Path) -> None:
    write_release_files(tmp_path)
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / ".gitignore").touch()
    (dist_dir / "nexolith-1.2.3-py3-none-any.whl").touch()
    (dist_dir / "nexolith-1.2.3.tar.gz").touch()

    assert validate_release("v1.2.3", tmp_path, dist_dir) == "1.2.3"


@pytest.mark.parametrize("tag", ["1.2.3", "v1.2", "v1.2.3-rc.1", "v01.2.3"])
def test_validate_release_rejects_invalid_tags(tmp_path: Path, tag: str) -> None:
    write_release_files(tmp_path)

    with pytest.raises(ReleaseValidationError, match="exact form vX.Y.Z"):
        validate_release(tag, tmp_path)


def test_validate_release_rejects_version_mismatch(tmp_path: Path) -> None:
    write_release_files(tmp_path, version="1.2.4")

    with pytest.raises(ReleaseValidationError, match="does not match project.version"):
        validate_release("v1.2.3", tmp_path)


def test_validate_release_rejects_source_version_mismatch(tmp_path: Path) -> None:
    write_release_files(tmp_path)
    (tmp_path / "src" / "nexolith" / "__init__.py").write_text(
        '__version__ = "1.2.4"\n', encoding="utf-8"
    )

    with pytest.raises(ReleaseValidationError, match="does not match nexolith.__version__"):
        validate_release("v1.2.3", tmp_path)


def test_validate_release_rejects_unreleased_changelog(tmp_path: Path) -> None:
    write_release_files(tmp_path, status="Unreleased")

    with pytest.raises(ReleaseValidationError, match="must use an ISO release date"):
        validate_release("v1.2.3", tmp_path)


def test_validate_release_rejects_compact_release_date(tmp_path: Path) -> None:
    write_release_files(tmp_path, status="20260731")

    with pytest.raises(ReleaseValidationError, match="must use an ISO release date"):
        validate_release("v1.2.3", tmp_path)


def test_validate_release_rejects_nonexistent_release_date(tmp_path: Path) -> None:
    write_release_files(tmp_path, status="2026-02-30")

    with pytest.raises(ReleaseValidationError, match="must use an ISO release date"):
        validate_release("v1.2.3", tmp_path)


def test_validate_release_rejects_incomplete_distribution_set(tmp_path: Path) -> None:
    write_release_files(tmp_path)
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "nexolith-1.2.3-py3-none-any.whl").touch()

    with pytest.raises(ReleaseValidationError, match="dist must contain exactly"):
        validate_release("v1.2.3", tmp_path, dist_dir)


def test_validate_release_rejects_unexpected_distribution(tmp_path: Path) -> None:
    write_release_files(tmp_path)
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "nexolith-1.2.3-py3-none-any.whl").touch()
    (dist_dir / "nexolith-1.2.3.tar.gz").touch()
    (dist_dir / "unexpected.zip").touch()

    with pytest.raises(ReleaseValidationError, match="unexpected.zip"):
        validate_release("v1.2.3", tmp_path, dist_dir)


def test_validate_release_rejects_unexpected_directory(tmp_path: Path) -> None:
    write_release_files(tmp_path)
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "nexolith-1.2.3-py3-none-any.whl").touch()
    (dist_dir / "nexolith-1.2.3.tar.gz").touch()
    (dist_dir / "unexpected").mkdir()

    with pytest.raises(ReleaseValidationError, match="unexpected"):
        validate_release("v1.2.3", tmp_path, dist_dir)
