"""Recursive DAG-file discovery for `/open` with no argument (NXL-107).

Pure filesystem + detection logic, no I/O beyond reading candidate files
(via `detect_document_kind()`) -- presentation lives in `interactive.py`,
same separation every other renderer in this package already follows.
"""

import os
from pathlib import Path

from nexolith.cli.document_kind import DocumentKind, detect_document_kind

# Directory names never worth descending into for DAG discovery: version
# control internals, this project's own real `.gitignore` tooling/venv/cache
# entries (`.venv`, `.uv-cache`, `.uv-python`, `__pycache__`, `.pytest_cache`,
# `.mypy_cache`, `.ruff_cache`), editor state, and build output -- all
# confirmed against this repository's actual `.gitignore`, not guessed.
# `node_modules` is included defensively (no JS tooling exists in this
# project today, but it is the universally-recognized equivalent the story
# itself names, and it can never contain a DAG file regardless).
_SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        ".venv",
        ".uv-cache",
        ".uv-python",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".idea",
        ".vscode",
        "dist",
        "build",
        "node_modules",
        "htmlcov",
    }
)


def _skip_dir(name: str) -> bool:
    return name in _SKIP_DIR_NAMES or name.endswith(".egg-info")


def discover_dag_files(root: Path) -> list[Path]:
    """Recursively find every real DAG file under `root`: every `.yaml`/
    `.yml` file confirmed structurally (`detect_document_kind()` -- the same
    detector `/open <path>`, `validate`, and `run` already use) to be a DAG,
    not a pipeline or unrelated YAML file. Skips `_SKIP_DIR_NAMES`
    directories entirely -- pruned via `os.walk()`'s own in-place `dirnames`
    mutation, so a skipped directory is never even entered, not walked and
    then discarded. Sorted for a stable, deterministic pick-list order
    (repeat discoveries in the same tree always number identically).
    """
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if not _skip_dir(name)]
        for filename in filenames:
            if not filename.endswith((".yaml", ".yml")):
                continue
            candidate = Path(dirpath) / filename
            if detect_document_kind(candidate) is DocumentKind.DAG:
                found.append(candidate)
    return sorted(found)
