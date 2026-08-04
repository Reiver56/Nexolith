import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from nexolith.config.models import PipelineConfig, SqlSourceConfig
from nexolith.exceptions import ConfigurationError
from nexolith.types import Scalar

ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _resolve_string(value: str) -> str:
    missing: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        resolved = os.getenv(name)
        if resolved is None:
            missing.add(name)
            return match.group(0)
        return resolved

    result = ENV_PATTERN.sub(replace, value)
    if missing:
        names = ", ".join(sorted(missing))
        raise ConfigurationError(f"Missing environment variable(s): {names}")
    return result


def _resolve_environment(value: Any) -> Any:
    if isinstance(value, str):
        return _resolve_string(value)
    if isinstance(value, list):
        return [_resolve_environment(item) for item in value]
    if isinstance(value, dict):
        return {key: _resolve_environment(item) for key, item in value.items()}
    return value


def load_pipeline(
    path: Path, parameter_overrides: Mapping[str, Scalar] | None = None
) -> PipelineConfig:
    if not path.is_file():
        raise ConfigurationError(f"Pipeline file not found: {path}. Check the path and try again.")
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(
            f"Could not read pipeline file: {path}. Check file permissions."
        ) from exc
    try:
        document = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise ConfigurationError(f"Invalid YAML in {path}{location}.") from exc
    if not isinstance(document, dict):
        raise ConfigurationError("Pipeline YAML must contain a mapping at its root")
    try:
        config = PipelineConfig.validate_document(_resolve_environment(document))
    except ValidationError as exc:
        errors = []
        for error in exc.errors(include_url=False):
            location = ".".join(str(part) for part in error["loc"])
            errors.append(f"{location}: {error['msg']}")
        raise ConfigurationError("Invalid pipeline configuration:\n" + "\n".join(errors)) from exc
    _resolve_query_file(config, path.parent)
    _resolve_parameters(config, parameter_overrides)
    return config


def _resolve_parameters(config: PipelineConfig, overrides: Mapping[str, Scalar] | None) -> None:
    """Merge NXL-82 parameter overrides (e.g. a DAG task's own `parameters:`
    block) on top of the pipeline's own static `parameters:` declaration,
    then confirm every declared name has a real value before the query is
    allowed to run. A declared parameter with no static value (`null` in
    YAML) exists specifically to be filled this way; if it still isn't after
    merging, that is the "missing required parameter" case the acceptance
    criteria calls out -- caught here, at load time, for both
    `nexolith validate` and `nexolith run`, rather than surfacing as an
    opaque driver error once the query executes. Declared names not
    referenced by the actual query text are not detected here -- see the
    field-level docstring on `SqlSourceConfig.parameters` for why (explicit
    declaration was chosen over parsing the SQL).
    """
    source = config.source
    overrides = overrides or {}
    if not isinstance(source, SqlSourceConfig):
        if overrides:
            raise ConfigurationError(
                "Parameter overrides were supplied, but this pipeline's source does not "
                "declare a SQL query."
            )
        return
    unknown = sorted(set(overrides) - set(source.parameters))
    if unknown:
        raise ConfigurationError(
            "Unknown parameter override(s), not declared in this pipeline's 'parameters': "
            + ", ".join(unknown)
        )
    merged = {**source.parameters, **overrides}
    missing = sorted(name for name, value in merged.items() if value is None)
    if missing:
        raise ConfigurationError("Missing required parameter(s): " + ", ".join(missing))
    source.parameters = merged


def _resolve_query_file(config: PipelineConfig, base_dir: Path) -> None:
    """Resolve `query_file` (NXL-81) relative to the pipeline YAML's own
    directory -- same convention as `pipeline:` paths in the DAG format --
    and load its contents into `query` so every downstream consumer of a
    SqlSourceConfig keeps reading the same field regardless of which one the
    user wrote. Runs for every `load_pipeline` call, so both `validate` and
    `run` catch a missing/unreadable query file at load time.
    """
    source = config.source
    if not isinstance(source, SqlSourceConfig) or not source.query_file:
        return
    query_path = Path(source.query_file)
    if not query_path.is_absolute():
        query_path = base_dir / query_path
    if not query_path.is_file():
        raise ConfigurationError(
            f"Query file not found: {query_path}. Check the path and try again."
        )
    try:
        source.query = query_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(
            f"Could not read query file: {query_path}. Check file permissions."
        ) from exc
