from collections.abc import Callable

from nexolith.config.models import (
    DropNullsConfig,
    FilterConfig,
    PythonJobConfig,
    RenameConfig,
    SelectConfig,
    TransformationConfig,
)
from nexolith.exceptions import TransformationError
from nexolith.transformations.base import Transformation
from nexolith.transformations.builtin import DropNulls, Filter, Rename, Select
from nexolith.transformations.python_job import PythonJob

TransformationFactory = Callable[[TransformationConfig], Transformation]


class TransformationRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, TransformationFactory] = {}

    def register(self, name: str, factory: TransformationFactory) -> None:
        self._factories[name] = factory

    def create(self, config: TransformationConfig) -> Transformation:
        try:
            return self._factories[config.type](config)
        except KeyError as exc:
            raise TransformationError(f"Unknown transformation: {config.type}") from exc


def default_transformation_registry() -> TransformationRegistry:
    registry = TransformationRegistry()

    def select(config: TransformationConfig) -> Transformation:
        assert isinstance(config, SelectConfig)
        return Select(config.columns)

    def rename(config: TransformationConfig) -> Transformation:
        assert isinstance(config, RenameConfig)
        return Rename(config.columns)

    def drop_nulls(config: TransformationConfig) -> Transformation:
        assert isinstance(config, DropNullsConfig)
        return DropNulls(config.columns)

    def filter_rows(config: TransformationConfig) -> Transformation:
        assert isinstance(config, FilterConfig)
        return Filter(config.column, config.operator, config.value)

    def python_job(config: TransformationConfig) -> Transformation:
        assert isinstance(config, PythonJobConfig)
        return PythonJob(config.file, config.entrypoint, config.parameters)

    registry.register("select", select)
    registry.register("rename", rename)
    registry.register("drop_nulls", drop_nulls)
    registry.register("filter", filter_rows)
    registry.register("python_job", python_job)
    return registry
