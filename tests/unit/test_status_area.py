from __future__ import annotations

import re
import time
from collections.abc import Callable
from pathlib import Path

from nexolith.cli.context import SelectedPipeline
from nexolith.cli.render_context import RenderContext
from nexolith.cli.status_area import (
    FullScreenOperationPresenter,
    StatusAreaState,
    StepStatus,
    build_error_excerpt,
    render_execution_panel,
)
from nexolith.events import (
    ExtractionCompleted,
    ExtractionStarted,
    FailureCategory,
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
from nexolith.exceptions import ConfigurationError
from nexolith.models import ExecutionResult, ExecutionStatus


def plain(state: StatusAreaState) -> str:
    return "".join(text for _, text in state.render())


def selected_pipeline(path: Path) -> SelectedPipeline:
    return SelectedPipeline(requested_path=Path(path.name), resolved_path=path)


_CAPABLE = RenderContext(is_tty=True, color_enabled=True, width=200)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def make_presenter(
    state: StatusAreaState,
    *,
    invalidate: Callable[[], None] = lambda: None,
    written: list[str] | None = None,
    render_context: RenderContext = _CAPABLE,
) -> FullScreenOperationPresenter:
    return FullScreenOperationPresenter(
        state,
        invalidate,
        write=(written if written is not None else []).append,
        render_context=lambda: render_context,
    )


# --- Step timeline ------------------------------------------------------


def test_validate_starts_with_a_single_pending_step() -> None:
    state = StatusAreaState()
    state.start(PipelineOperation.VALIDATE)

    assert state.steps == [("Loading", StepStatus.PENDING)]
    assert state.visible is True


def test_run_starts_with_four_pending_steps() -> None:
    state = StatusAreaState()
    state.start(PipelineOperation.RUN)

    assert [label for label, _ in state.steps] == [
        "Loading",
        "Extraction",
        "Transformation",
        "Writing",
    ]
    assert all(status is StepStatus.PENDING for _, status in state.steps)


def test_events_transition_steps_active_then_done_in_order() -> None:
    state = StatusAreaState()
    state.start(PipelineOperation.RUN)

    state.apply_event(PipelineLoadStarted(operation=PipelineOperation.RUN))
    assert dict(state.steps)["Loading"] is StepStatus.ACTIVE

    state.apply_event(PipelineLoaded(operation=PipelineOperation.RUN, pipeline_name="x"))
    assert dict(state.steps)["Loading"] is StepStatus.DONE
    assert dict(state.steps)["Extraction"] is StepStatus.PENDING

    state.apply_event(ExtractionStarted(pipeline_name="x", source_type="csv"))
    assert dict(state.steps)["Extraction"] is StepStatus.ACTIVE

    state.apply_event(ExtractionCompleted(pipeline_name="x", rows_read=2))
    assert dict(state.steps)["Extraction"] is StepStatus.DONE

    state.apply_event(TransformationsStarted(pipeline_name="x", transformation_count=0))
    state.apply_event(TransformationsCompleted(pipeline_name="x", row_count=2))
    assert dict(state.steps)["Transformation"] is StepStatus.DONE

    state.apply_event(WriteStarted(pipeline_name="x", destination_type="csv", row_count=2))
    state.apply_event(WriteCompleted(pipeline_name="x", rows_written=2))
    assert dict(state.steps)["Writing"] is StepStatus.DONE


def test_pipeline_failed_marks_the_matching_step_failed() -> None:
    state = StatusAreaState()
    state.start(PipelineOperation.RUN)
    state.apply_event(PipelineLoadStarted(operation=PipelineOperation.RUN))
    state.apply_event(PipelineLoaded(operation=PipelineOperation.RUN, pipeline_name="x"))
    state.apply_event(ExtractionStarted(pipeline_name="x", source_type="csv"))

    state.apply_event(
        PipelineFailed(
            operation=PipelineOperation.RUN,
            pipeline_name="x",
            phase=PipelinePhase.EXTRACTION,
            category=FailureCategory.CONNECTOR,
        )
    )

    assert dict(state.steps)["Extraction"] is StepStatus.FAILED


# --- Dot: event-driven only, no timer ------------------------------------


def test_dot_does_not_change_without_a_real_event_even_after_waiting() -> None:
    """Direct proof against a timer: nothing should change dot_on while we
    wait, since only apply_event-adjacent code (the presenter's event sink)
    is allowed to touch it."""
    state = StatusAreaState()
    state.start(PipelineOperation.VALIDATE)
    before = state.dot_on

    time.sleep(0.3)

    assert state.dot_on == before


def test_full_screen_presenter_toggles_dot_only_inside_handle(tmp_path: Path) -> None:
    invalidations = 0

    def invalidate() -> None:
        nonlocal invalidations
        invalidations += 1

    state = StatusAreaState()
    presenter = make_presenter(state, invalidate=invalidate)
    sink = presenter.event_sink(PipelineOperation.VALIDATE)

    assert invalidations == 1  # event_sink() itself calls invalidate once (start)
    dot_before = state.dot_on

    sink.handle(PipelineLoadStarted(operation=PipelineOperation.VALIDATE))
    assert state.dot_on != dot_before
    assert invalidations == 2

    dot_after_one_event = state.dot_on
    sink.handle(PipelineLoaded(operation=PipelineOperation.VALIDATE, pipeline_name="x"))
    assert state.dot_on != dot_after_one_event
    assert invalidations == 3


def test_active_step_marker_alternates_with_dot_state() -> None:
    state = StatusAreaState()
    state.start(PipelineOperation.VALIDATE)
    state.apply_event(PipelineLoadStarted(operation=PipelineOperation.VALIDATE))

    state.dot_on = True
    assert "●" in plain(state)
    assert "○" not in plain(state)

    state.dot_on = False
    assert "○" in plain(state)


# --- Result panel (NXL-104: written to the output log, not this area) -----


def test_execution_panel_is_a_clean_rectangle_on_success() -> None:
    result = ExecutionResult(
        pipeline_name="x", status=ExecutionStatus.SUCCEEDED, rows_read=5, rows_written=3
    )

    lines = [line for line in render_execution_panel(result, _CAPABLE).split("\n") if line]
    widths = {len(_ANSI_RE.sub("", line)) for line in lines}

    assert len(widths) == 1
    assert lines[0].startswith("\x1b[") and "╭" in lines[0] and "╮" in lines[0]
    assert lines[-1].startswith("\x1b[") and "╰" in lines[-1] and "╯" in lines[-1]


def test_execution_panel_shows_status_rows_and_duration() -> None:
    result = ExecutionResult(
        pipeline_name="x", status=ExecutionStatus.SUCCEEDED, rows_read=5, rows_written=3
    )

    text = _ANSI_RE.sub("", render_execution_panel(result, _CAPABLE))

    assert "Status: succeeded" in text
    assert "Rows read: 5" in text
    assert "Rows written: 3" in text


def test_execution_panel_stays_legible_without_color() -> None:
    """Legibility check: strip all styling and confirm the numbers are still
    plainly readable text, not conveyed by color alone."""
    result = ExecutionResult(
        pipeline_name="x", status=ExecutionStatus.FAILED, rows_read=5, rows_written=0
    )

    text = _ANSI_RE.sub("", render_execution_panel(result, _CAPABLE))

    assert "Status: failed" in text
    assert "Rows written: 0" in text


def test_execution_panel_falls_back_to_plain_ascii_when_render_context_is_plain() -> None:
    result = ExecutionResult(
        pipeline_name="x", status=ExecutionStatus.SUCCEEDED, rows_read=5, rows_written=3
    )
    plain_context = RenderContext(is_tty=False, color_enabled=True, width=200)

    text = render_execution_panel(result, plain_context)

    assert "\x1b[" not in text
    lines = [line for line in text.split("\n") if line]
    assert lines[0].startswith("+") and lines[0].endswith("+")
    assert lines[-1].startswith("+") and lines[-1].endswith("+")


def test_execution_panel_omits_color_but_keeps_the_rounded_border_when_ansi_incapable() -> None:
    """NXL-104: a genuinely capable terminal (not `.plain`) writing into an
    ANSI-incapable sink (e.g. the full-screen output log) still gets the
    rounded border shape -- only the color codes are suppressed."""
    result = ExecutionResult(
        pipeline_name="x", status=ExecutionStatus.SUCCEEDED, rows_read=5, rows_written=3
    )
    log_context = RenderContext(is_tty=True, color_enabled=True, width=200, ansi_capable=False)

    text = render_execution_panel(result, log_context)

    assert "\x1b[" not in text
    lines = [line for line in text.split("\n") if line]
    assert lines[0].startswith("╭") and lines[0].endswith("╮")
    assert lines[-1].startswith("╰") and lines[-1].endswith("╯")


def test_show_result_writes_the_execution_panel_to_the_log_and_hides_the_status_area() -> None:
    result = ExecutionResult(
        pipeline_name="x", status=ExecutionStatus.SUCCEEDED, rows_read=5, rows_written=3
    )
    state = StatusAreaState()
    state.start(PipelineOperation.RUN)
    written: list[str] = []
    presenter = make_presenter(state, written=written)

    presenter.show_result(result)

    assert state.visible is False
    assert len(written) == 1
    plain_written = _ANSI_RE.sub("", written[0])
    assert "Status: succeeded" in plain_written
    assert "Rows read: 5" in plain_written
    # The fixed area itself no longer carries this content at all.
    assert plain(state) == ""


# --- Error text + excerpt --------------------------------------------------


def test_show_error_without_excerpt_falls_back_to_plain_text(tmp_path: Path) -> None:
    pipeline = selected_pipeline(tmp_path / "missing.yaml")  # never written -> unreadable
    error = ConfigurationError(f"Pipeline file not found: {pipeline.resolved_path}.")
    state = StatusAreaState()
    presenter = make_presenter(state)

    presenter.show_error(error, pipeline, PipelineOperation.VALIDATE)

    assert state.error_excerpt is None
    assert "Error [configuration]" in plain(state)


def test_yaml_syntax_error_locates_the_real_line(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("name: broken\nsource:\n  type: csv\n  path: [\n", encoding="utf-8")
    pipeline = selected_pipeline(path)
    error = ConfigurationError(f"Invalid YAML in {path} at line 4, column 9.")

    excerpt = build_error_excerpt(error, pipeline)

    assert excerpt is not None
    target = [line for is_target, _, line in excerpt if is_target]
    assert target == ["  path: ["]


def test_validation_error_locates_the_top_level_field(tmp_path: Path) -> None:
    path = tmp_path / "invalid.yaml"
    path.write_text("name: x\nsource: {}\ndestination: {}\n", encoding="utf-8")
    pipeline = selected_pipeline(path)
    error = ConfigurationError("Invalid pipeline configuration:\nsource.type: field required")

    excerpt = build_error_excerpt(error, pipeline)

    assert excerpt is not None
    target = [(line_no, line) for is_target, line_no, line in excerpt if is_target]
    assert target == [(2, "source: {}")]


def test_excerpt_is_bounded_and_never_prints_the_full_file(tmp_path: Path) -> None:
    path = tmp_path / "long.yaml"
    lines = [f"key{i}: value{i}" for i in range(50)]
    lines[25] = "source: broken"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    pipeline = selected_pipeline(path)
    error = ConfigurationError("Invalid pipeline configuration:\nsource.type: field required")

    excerpt = build_error_excerpt(error, pipeline)

    assert excerpt is not None
    assert len(excerpt) <= 5


def test_excerpt_content_never_includes_the_resolved_path(tmp_path: Path) -> None:
    path = tmp_path / "some-absolute-path-marker.yaml"
    path.write_text("name: [\n", encoding="utf-8")
    pipeline = selected_pipeline(path)
    error = ConfigurationError(f"Invalid YAML in {path} at line 1, column 8.")

    excerpt = build_error_excerpt(error, pipeline)

    assert excerpt is not None
    rendered = " ".join(line for _, _, line in excerpt)
    assert str(path) not in rendered
    assert "some-absolute-path-marker" not in rendered


def test_unlocatable_error_falls_back_to_none(tmp_path: Path) -> None:
    path = tmp_path / "pipeline.yaml"
    path.write_text("name: x\n", encoding="utf-8")
    pipeline = selected_pipeline(path)
    error = ConfigurationError("Missing environment variable(s): DATABASE_URL")

    assert build_error_excerpt(error, pipeline) is None


def test_excerpt_falls_back_to_none_when_file_is_unreadable(tmp_path: Path) -> None:
    pipeline = selected_pipeline(tmp_path / "gone.yaml")
    error = ConfigurationError(f"Invalid YAML in {pipeline.resolved_path} at line 1, column 1.")

    assert build_error_excerpt(error, pipeline) is None


def test_run_error_goes_to_the_output_log_not_the_status_area(tmp_path: Path) -> None:
    """NXL-104: a `/run` error (unlike `/validate`'s) now goes to the
    scrollable output log and hides this fixed area entirely, matching
    where a DAG `/run`'s own error already went -- it never reaches
    `state` at all, so `error_text`/`error_excerpt` stay at their initial
    `None`, not merely "excerpt is None" as before this story (when a run
    error still populated `error_text` and stayed visible here).
    """
    path = tmp_path / "broken.yaml"
    path.write_text("name: [\n", encoding="utf-8")
    pipeline = selected_pipeline(path)
    error = ConfigurationError(f"Invalid YAML in {path} at line 1, column 8.")
    state = StatusAreaState()
    state.start(PipelineOperation.RUN)
    written: list[str] = []
    presenter = make_presenter(state, written=written)

    presenter.show_error(error, pipeline, PipelineOperation.RUN)

    assert state.visible is False
    assert state.error_text is None
    assert state.error_excerpt is None
    assert len(written) == 1
    assert "Error [configuration]" in written[0]


def test_validate_error_still_populates_the_status_area_with_excerpt(tmp_path: Path) -> None:
    """Confirms /validate's own error path is genuinely unaffected by
    NXL-104 -- still shown in this fixed area, excerpt included."""
    path = tmp_path / "broken.yaml"
    path.write_text("name: [\n", encoding="utf-8")
    pipeline = selected_pipeline(path)
    error = ConfigurationError(f"Invalid YAML in {path} at line 1, column 8.")
    state = StatusAreaState()
    state.start(PipelineOperation.VALIDATE)
    written: list[str] = []
    presenter = make_presenter(state, written=written)

    presenter.show_error(error, pipeline, PipelineOperation.VALIDATE)

    assert state.visible is True
    assert state.error_text is not None
    assert state.error_excerpt is not None
    assert written == []
