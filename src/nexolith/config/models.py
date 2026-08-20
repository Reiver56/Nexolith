from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from nexolith.nexoactions.contracts import (
    ConfiguredActionParameterValue,
    NexoActionDefinition,
    NexoActionMatchMode,
    NexoActionOperator,
    action_name_from_identifier,
)
from nexolith.nexofunctions.contracts import (
    ConfiguredParameterValue,
    NexoFunctionDefinition,
    function_name_from_identifier,
)
from nexolith.types import Scalar


class ComponentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str


class CsvSourceConfig(ComponentConfig):
    type: Literal["csv"]
    path: str
    encoding: str = "utf-8"


class SqlSourceConfig(ComponentConfig):
    type: Literal["sqlite", "postgresql"]
    connection_url: str
    query: str | None = None
    # query_file (NXL-81): an alternative to inline `query`, resolved relative
    # to the pipeline YAML's own directory -- same convention as the DAG
    # format's `pipeline:` paths. Resolution and existence/readability
    # checks happen in `nexolith.config.loader.load_pipeline` (mirroring how
    # DAG pipeline references are resolved in the DAG validator, not the
    # model itself), which then populates `query` with the file's contents
    # so every downstream consumer keeps reading the same `query` field
    # regardless of which one the user wrote.
    query_file: str | None = None
    table: str | None = None
    # parameters (NXL-82): named values bound into `query`/`query_file` via
    # the real SQLAlchemy Core bound-parameter mechanism (`:name` syntax,
    # `connection.execute(text(query), parameters)`) -- never string
    # interpolation. Explicit declaration, not SQL-text inspection (see
    # nexolith.config.loader._resolve_parameters): every name this pipeline
    # ever binds must appear as a key here. A `None` value means "required,
    # not yet supplied" -- filled in later by a DAG task's own `parameters:`
    # override (nexolith.dag.models.DagTaskConfig.parameters), or it is a
    # load-time ConfigurationError, never a runtime driver error.
    parameters: dict[str, Scalar] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_query_or_table(self) -> "SqlSourceConfig":
        if self.query and self.query_file:
            raise ValueError("'query' and 'query_file' are mutually exclusive; specify only one")
        if not self.query and not self.query_file and not self.table:
            raise ValueError("either 'query', 'query_file', or 'table' is required")
        return self


class CsvDestinationConfig(ComponentConfig):
    type: Literal["csv"]
    path: str
    encoding: str = "utf-8"


class SqlDestinationConfig(ComponentConfig):
    type: Literal["sqlite", "postgresql"]
    connection_url: str
    table: str
    mode: Literal["append", "replace", "fail", "truncate"] = "fail"


class SqlFunctionTargetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["sqlite", "postgresql"]
    connection_url: str
    table: str = Field(min_length=1)


class NexoFunctionDestinationConfig(ComponentConfig):
    type: str
    target: SqlFunctionTargetConfig
    parameters: dict[str, ConfiguredParameterValue] = Field(default_factory=dict)
    _resolved_function: NexoFunctionDefinition | None = PrivateAttr(default=None)

    @field_validator("type")
    @classmethod
    def require_function_identifier(cls, value: str) -> str:
        try:
            function_name_from_identifier(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return value

    @property
    def resolved_function(self) -> NexoFunctionDefinition | None:
        return self._resolved_function

    def bind_function(self, definition: NexoFunctionDefinition) -> None:
        self._resolved_function = definition


class SelectConfig(ComponentConfig):
    type: Literal["select"]
    columns: list[str] = Field(min_length=1)


class RenameConfig(ComponentConfig):
    type: Literal["rename"]
    columns: dict[str, str] = Field(min_length=1)


class DropNullsConfig(ComponentConfig):
    type: Literal["drop_nulls"]
    columns: list[str] | None = None


class FilterConfig(ComponentConfig):
    type: Literal["filter"]
    column: str
    operator: Literal[
        "equals",
        "not_equals",
        "greater_than",
        "greater_than_or_equal",
        "less_than",
        "less_than_or_equal",
        "contains",
        "is_null",
        "is_not_null",
    ]
    value: Scalar = None

    @model_validator(mode="after")
    def require_value(self) -> "FilterConfig":
        if self.operator not in {"is_null", "is_not_null"} and self.value is None:
            raise ValueError(f"'value' is required for operator '{self.operator}'")
        return self


class PythonJobConfig(ComponentConfig):
    """python_job (NXL-83, ADR-7): a deliberate, scoped exception to the
    project's prior no-arbitrary-code-execution principle -- Model A only
    (in-process; receives/returns Nexolith's own in-flight `Rows`). `file`
    resolves relative to the pipeline YAML's own directory, same convention
    as `query_file`/DAG `pipeline:` references. `entrypoint` names a
    function in that file with the documented signature
    `def <entrypoint>(rows: Rows, context: JobContext) -> Rows`, default
    `"run"`. Trusted-local-code only, no sandboxing -- see
    nexolith.jobs.loader for exactly what "dynamic import" does and does
    not contain.
    """

    type: Literal["python_job"]
    file: str
    entrypoint: str = "run"
    parameters: dict[str, Scalar] = Field(default_factory=dict)


class NexoActionConditionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str = Field(min_length=1)
    operator: NexoActionOperator
    value: Scalar


class NexoActionIdempotencyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fields: list[str] = Field(min_length=1)

    @field_validator("fields")
    @classmethod
    def require_unique_non_empty_fields(cls, fields: list[str]) -> list[str]:
        if any(not field for field in fields):
            raise ValueError("idempotency fields cannot be empty")
        if len(fields) != len(set(fields)):
            raise ValueError("idempotency fields cannot contain duplicates")
        return fields


class NexoActionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    condition: NexoActionConditionConfig
    match: NexoActionMatchMode = NexoActionMatchMode.ANY
    idempotency: NexoActionIdempotencyConfig
    parameters: dict[str, ConfiguredActionParameterValue] = Field(default_factory=dict)
    _resolved_action: NexoActionDefinition | None = PrivateAttr(default=None)

    @field_validator("type")
    @classmethod
    def require_action_identifier(cls, value: str) -> str:
        try:
            action_name_from_identifier(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return value

    @property
    def resolved_action(self) -> NexoActionDefinition | None:
        return self._resolved_action

    def bind_action(self, definition: NexoActionDefinition) -> None:
        self._resolved_action = definition


SourceConfig = CsvSourceConfig | SqlSourceConfig
DestinationConfig = CsvDestinationConfig | SqlDestinationConfig | NexoFunctionDestinationConfig
TransformationConfig = (
    SelectConfig | RenameConfig | DropNullsConfig | FilterConfig | PythonJobConfig
)


class PipelineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    source: SourceConfig = Field(discriminator="type")
    transformations: list[TransformationConfig] = Field(default_factory=list)
    destination: DestinationConfig
    actions: list[NexoActionConfig] = Field(default_factory=list)

    @field_validator("destination", mode="before")
    @classmethod
    def select_destination_model(cls, value: Any) -> DestinationConfig:
        if isinstance(
            value, CsvDestinationConfig | SqlDestinationConfig | NexoFunctionDestinationConfig
        ):
            return value
        if not isinstance(value, dict):
            raise ValueError("destination must be a mapping")
        destination_type = value.get("type")
        if destination_type == "csv":
            return CsvDestinationConfig.model_validate(value)
        if destination_type in {"sqlite", "postgresql"}:
            return SqlDestinationConfig.model_validate(value)
        if isinstance(destination_type, str) and destination_type.startswith("nexofunction."):
            return NexoFunctionDestinationConfig.model_validate(value)
        raise ValueError(f"unknown destination type: {destination_type!r}")

    @model_validator(mode="after")
    def require_unique_actions(self) -> "PipelineConfig":
        identifiers = [action.type for action in self.actions]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Nexo Action identifiers must be unique within a pipeline")
        return self

    @classmethod
    def validate_document(cls, document: Any) -> "PipelineConfig":
        return cls.model_validate(document)
