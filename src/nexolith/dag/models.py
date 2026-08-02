from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DagTaskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    pipeline: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)


class DagConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    tasks: list[DagTaskConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_task_graph_shape(self) -> "DagConfig":
        counts: dict[str, int] = {}
        for task in self.tasks:
            counts[task.name] = counts.get(task.name, 0) + 1
        duplicates = sorted(name for name, count in counts.items() if count > 1)
        if duplicates:
            raise ValueError(f"duplicate task name(s): {', '.join(duplicates)}")

        known_names = {task.name for task in self.tasks}
        for task in self.tasks:
            if task.name in task.depends_on:
                raise ValueError(f"task '{task.name}' cannot depend on itself")
            unknown = sorted(dep for dep in task.depends_on if dep not in known_names)
            if unknown:
                raise ValueError(
                    f"task '{task.name}' depends_on unknown task(s): {', '.join(unknown)}"
                )
        return self

    @classmethod
    def validate_document(cls, document: Any) -> "DagConfig":
        return cls.model_validate(document)
