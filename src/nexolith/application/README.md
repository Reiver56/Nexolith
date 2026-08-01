# Application layer

`PipelineApplication` owns the presentation-independent `validate_pipeline()` and
`run_pipeline()` use cases. It composes pipeline loading with a `PipelineRunner`; both dependencies
can be injected for tests or alternate adapters. The CLI calls these use cases and remains
responsible only for formatting results and mapping domain errors to exit codes.

## Lifecycle events

An optional `EventSink` receives immutable, typed events synchronously. With no sink, validation
and execution behave exactly as before. The contract is an initial foundation for future adapters,
not yet a compatibility-stable public API.

A successful run guarantees this order:

1. `PipelineLoadStarted`, `PipelineLoaded`;
2. `PipelineExecutionStarted`;
3. `ExtractionStarted`, `ExtractionCompleted`;
4. `TransformationsStarted`, `TransformationsCompleted`;
5. `WriteStarted`, `WriteCompleted`;
6. `PipelineExecutionCompleted`.

The transformation pair represents the complete ordered transformation sequence, including a
sequence containing zero transformations. Events describe only boundaries around work the current
runner actually performs; they do not claim percentages or intermediate progress.

A loading or execution failure emits `PipelineFailed` instead of the corresponding completion
event. Expected failures preserve the existing `ConfigurationError` or chained `ExecutionError`;
unexpected programming errors retain their original identity and use the `unexpected` category.
Failure events expose only the operation, observable phase, stable category, and optional pipeline
name. They never expose exception objects, configuration values, connection URLs, or paths.

Events produced inside the runner use the same shared contract and are forwarded directly to the
sink supplied to the application service; no CLI-specific translation is involved. Sinks are
synchronous observers and should return normally rather than raise exceptions.

```python
from pathlib import Path

from nexolith.application import ApplicationEvent, PipelineApplication


class Collector:
    def __init__(self) -> None:
        self.events: list[ApplicationEvent] = []

    def handle(self, event: ApplicationEvent) -> None:
        self.events.append(event)


collector = Collector()
application = PipelineApplication()
result = application.run_pipeline(Path("pipeline.yaml"), event_sink=collector)
```

Presentation, live rendering, progress bars, interactive shells, and slash commands do not belong
in this package.
