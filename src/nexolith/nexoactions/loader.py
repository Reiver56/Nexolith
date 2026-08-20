"""Trusted project-local Nexo Action discovery."""

import importlib.util
import uuid
from pathlib import Path

from nexolith.exceptions import ConfigurationError
from nexolith.nexoactions.contracts import NexoActionDefinition
from nexolith.nexoactions.registry import NexoActionRegistry, builtin_nexo_action_registry

LOCAL_DIRECTORY_NAME = "nexoactions"
LOCAL_EXPORT_NAME = "NEXO_ACTION"


def load_nexo_action_registry(project_root: Path) -> NexoActionRegistry:
    registry = builtin_nexo_action_registry()
    for definition in discover_local_nexo_actions(project_root):
        registry.register(definition)
    return registry


def discover_local_nexo_actions(project_root: Path) -> tuple[NexoActionDefinition, ...]:
    root = project_root.resolve()
    directory = root / LOCAL_DIRECTORY_NAME
    if not directory.exists():
        return ()
    if directory.is_symlink() or not directory.is_dir():
        raise ConfigurationError(
            f"Local Nexo Action location '{LOCAL_DIRECTORY_NAME}' must be a real directory."
        )

    discovered: list[tuple[NexoActionDefinition, str]] = []
    for path in sorted(directory.glob("*.py"), key=lambda item: item.name):
        if path.name == "__init__.py" or path.name.startswith("_"):
            continue
        if path.is_symlink() or path.resolve().parent != directory.resolve():
            raise ConfigurationError(
                f"Local Nexo Action file '{path.name}' must stay inside '{LOCAL_DIRECTORY_NAME}'."
            )
        discovered.append((_load_definition(path), path.name))

    by_name: dict[str, str] = {}
    for definition, filename in discovered:
        previous = by_name.get(definition.name)
        if previous is not None:
            raise ConfigurationError(
                f"Duplicate local Nexo Action '{definition.name}' in '{previous}' and '{filename}'."
            )
        by_name[definition.name] = filename
    return tuple(definition for definition, _ in discovered)


def _load_definition(path: Path) -> NexoActionDefinition:
    module_name = f"nexolith_local_action_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ConfigurationError(f"Could not load local Nexo Action file '{path.name}'.")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise ConfigurationError(
            f"Local Nexo Action file '{path.name}' raised {type(exc).__name__} while importing."
        ) from exc
    definition = getattr(module, LOCAL_EXPORT_NAME, None)
    if not isinstance(definition, NexoActionDefinition):
        raise ConfigurationError(
            f"Local Nexo Action file '{path.name}' must export one {LOCAL_EXPORT_NAME} definition."
        )
    return definition
