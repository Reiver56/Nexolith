from collections.abc import Callable

from nexolith.config.models import (
    CsvDestinationConfig,
    CsvSourceConfig,
    DestinationConfig,
    SourceConfig,
    SqlDestinationConfig,
    SqlSourceConfig,
)
from nexolith.connectors.base import DestinationConnector, SourceConnector
from nexolith.connectors.csv import CsvDestination, CsvSource
from nexolith.connectors.sql import SqlDestination, SqlSource
from nexolith.exceptions import ConnectorError

SourceFactory = Callable[[SourceConfig], SourceConnector]
DestinationFactory = Callable[[DestinationConfig], DestinationConnector]


class ConnectorRegistry:
    def __init__(self) -> None:
        self._sources: dict[str, SourceFactory] = {}
        self._destinations: dict[str, DestinationFactory] = {}

    def register_source(self, name: str, factory: SourceFactory) -> None:
        self._sources[name] = factory

    def register_destination(self, name: str, factory: DestinationFactory) -> None:
        self._destinations[name] = factory

    def create_source(self, config: SourceConfig) -> SourceConnector:
        try:
            return self._sources[config.type](config)
        except KeyError as exc:
            raise ConnectorError(f"Unknown source connector: {config.type}") from exc

    def create_destination(self, config: DestinationConfig) -> DestinationConnector:
        try:
            return self._destinations[config.type](config)
        except KeyError as exc:
            raise ConnectorError(f"Unknown destination connector: {config.type}") from exc


def default_connector_registry() -> ConnectorRegistry:
    registry = ConnectorRegistry()

    def csv_source(config: SourceConfig) -> SourceConnector:
        assert isinstance(config, CsvSourceConfig)
        return CsvSource(config.path, config.encoding)

    def sql_source(config: SourceConfig) -> SourceConnector:
        assert isinstance(config, SqlSourceConfig)
        return SqlSource(config.connection_url, config.query, config.table, config.parameters)

    def csv_destination(config: DestinationConfig) -> DestinationConnector:
        assert isinstance(config, CsvDestinationConfig)
        return CsvDestination(config.path, config.encoding)

    def sql_destination(config: DestinationConfig) -> DestinationConnector:
        assert isinstance(config, SqlDestinationConfig)
        return SqlDestination(config.connection_url, config.table, config.mode)

    registry.register_source("csv", csv_source)
    registry.register_source("sqlite", sql_source)
    registry.register_source("postgresql", sql_source)
    registry.register_destination("csv", csv_destination)
    registry.register_destination("sqlite", sql_destination)
    registry.register_destination("postgresql", sql_destination)
    return registry
