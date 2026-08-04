from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    mode: Literal["append", "replace", "fail"] = "fail"


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


SourceConfig = CsvSourceConfig | SqlSourceConfig
DestinationConfig = CsvDestinationConfig | SqlDestinationConfig
TransformationConfig = SelectConfig | RenameConfig | DropNullsConfig | FilterConfig


class PipelineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    source: SourceConfig = Field(discriminator="type")
    transformations: list[TransformationConfig] = Field(default_factory=list)
    destination: DestinationConfig = Field(discriminator="type")

    @classmethod
    def validate_document(cls, document: Any) -> "PipelineConfig":
        return cls.model_validate(document)
