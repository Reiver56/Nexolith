from nexolith.cli.event_renderer import InteractiveEventRenderer, render_event
from nexolith.events import (
    ExtractionCompleted,
    ExtractionStarted,
    FailureCategory,
    PipelineExecutionCompleted,
    PipelineExecutionStarted,
    PipelineFailed,
    PipelineLoaded,
    PipelineLoadStarted,
    PipelineOperation,
    PipelinePhase,
    TransformationsCompleted,
    TransformationsStarted,
    WriteCompleted,
    WriteStarted,
)


def public_events() -> list[object]:
    return [
        PipelineLoadStarted(PipelineOperation.RUN),
        PipelineLoaded(PipelineOperation.RUN, "pipeline"),
        PipelineExecutionStarted("pipeline"),
        ExtractionStarted("pipeline", "sensitive-source-type"),
        ExtractionCompleted("pipeline", 3),
        TransformationsStarted("pipeline", 2),
        TransformationsCompleted("pipeline", 2),
        WriteStarted("pipeline", "sensitive-destination-type", 2),
        WriteCompleted("pipeline", 2),
        PipelineExecutionCompleted("pipeline", 3, 2),
        PipelineFailed(
            PipelineOperation.RUN,
            "pipeline",
            PipelinePhase.WRITING,
            FailureCategory.CONNECTOR,
        ),
    ]


def test_renderer_maps_every_public_event_without_sensitive_fields() -> None:
    rendered = [render_event(event) for event in public_events()]
    text = "\n".join(line for line in rendered if line is not None)

    assert all(line is not None for line in rendered)
    assert "Loading pipeline..." in text
    assert "Starting execution..." in text
    assert "Extracting..." in text
    assert "Transforming..." in text
    assert "Writing..." in text
    assert "Pipeline completed." in text
    assert "sensitive-source-type" not in text
    assert "sensitive-destination-type" not in text
    assert "%" not in text
    assert "\x1b" not in text


def test_validation_loaded_event_has_validation_outcome() -> None:
    event = PipelineLoaded(PipelineOperation.VALIDATE, "pipeline")

    assert render_event(event) == "Pipeline valid."


def test_renderer_writes_each_event_incrementally() -> None:
    output: list[str] = []
    renderer = InteractiveEventRenderer(output.append)

    renderer.handle(PipelineLoadStarted(PipelineOperation.RUN))
    renderer.handle(PipelineExecutionStarted("pipeline"))

    assert output == ["Loading pipeline...", "Starting execution..."]


def test_renderer_ignores_future_events_and_writer_errors() -> None:
    class FutureEvent:
        pass

    calls = 0

    def broken_writer(_: str) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("controlled renderer failure")

    renderer = InteractiveEventRenderer(broken_writer)

    renderer.handle(PipelineLoadStarted(PipelineOperation.RUN))
    assert render_event(FutureEvent()) is None
    assert calls == 1
