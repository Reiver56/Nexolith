import csv
from pathlib import Path

import pytest

from nexolith.config import load_pipeline
from nexolith.config.models import PythonJobConfig
from nexolith.exceptions import ConfigurationError, ExecutionError
from nexolith.execution import DefaultPipelineRunner
from nexolith.models import ExecutionStatus


def write_pipeline(
    path: Path,
    *,
    source_csv: Path,
    dest_csv: Path,
    transformations: str,
    name: str = "job_pipeline",
) -> None:
    path.write_text(
        f"""
name: {name}
source:
  type: csv
  path: {source_csv.as_posix()}
transformations:
{transformations}
destination:
  type: csv
  path: {dest_csv.as_posix()}
""",
        encoding="utf-8",
    )


def test_python_job_performs_a_real_transformation_in_a_real_pipeline_run(tmp_path: Path) -> None:
    """Custom deduplication -- not natural to express with select/rename/
    drop_nulls/filter -- executed as a real transform step, real input and
    output verified (not just "doesn't crash")."""
    source = tmp_path / "input.csv"
    source.write_text(
        "id,name,amount\n1,Ada,10\n1,Ada,10\n2,Grace,20\n2,Grace,20\n", encoding="utf-8"
    )
    job = tmp_path / "dedup.py"
    job.write_text(
        "def run(rows, context):\n"
        "    seen = set()\n"
        "    result = []\n"
        "    for row in rows:\n"
        '        key = (row["id"], row["name"], row["amount"])\n'
        "        if key in seen:\n"
        "            continue\n"
        "        seen.add(key)\n"
        "        result.append(row)\n"
        "    return result\n",
        encoding="utf-8",
    )
    dest = tmp_path / "out.csv"
    pipeline = tmp_path / "pipeline.yaml"
    write_pipeline(
        pipeline,
        source_csv=source,
        dest_csv=dest,
        transformations="  - type: python_job\n    file: dedup.py",
    )

    config = load_pipeline(pipeline)
    result = DefaultPipelineRunner().run(config)

    assert result.status is ExecutionStatus.SUCCEEDED
    assert result.rows_read == 4
    assert result.rows_written == 2
    with dest.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["name"] for row in rows] == ["Ada", "Grace"]


def test_python_job_composes_with_a_filter_transform(tmp_path: Path) -> None:
    """A filter before and a python_job after, in the same pipeline: the
    job must only see the rows the filter already passed, and its own
    enrichment must survive to the final output."""
    source = tmp_path / "input.csv"
    source.write_text(
        "id,name,status\n1,ada,active\n2,grace,inactive\n3,marie,active\n", encoding="utf-8"
    )
    job = tmp_path / "enrich.py"
    job.write_text(
        "def run(rows, context):\n"
        "    for row in rows:\n"
        '        row["upper_name"] = row["name"].upper()\n'
        "    return rows\n",
        encoding="utf-8",
    )
    dest = tmp_path / "out.csv"
    pipeline = tmp_path / "pipeline.yaml"
    write_pipeline(
        pipeline,
        source_csv=source,
        dest_csv=dest,
        transformations=(
            "  - type: filter\n"
            "    column: status\n"
            "    operator: equals\n"
            "    value: active\n"
            "  - type: python_job\n"
            "    file: enrich.py"
        ),
    )

    config = load_pipeline(pipeline)
    result = DefaultPipelineRunner().run(config)

    assert result.status is ExecutionStatus.SUCCEEDED
    with dest.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert {row["name"] for row in rows} == {"ada", "marie"}
    assert {row["upper_name"] for row in rows} == {"ADA", "MARIE"}


def test_python_job_receives_declared_parameters(tmp_path: Path) -> None:
    source = tmp_path / "input.csv"
    source.write_text("id,amount\n1,5\n2,15\n", encoding="utf-8")
    job = tmp_path / "threshold.py"
    job.write_text(
        "def run(rows, context):\n"
        '    threshold = int(context.parameters["threshold"])\n'
        '    return [row for row in rows if int(row["amount"]) >= threshold]\n',
        encoding="utf-8",
    )
    dest = tmp_path / "out.csv"
    pipeline = tmp_path / "pipeline.yaml"
    write_pipeline(
        pipeline,
        source_csv=source,
        dest_csv=dest,
        transformations=(
            "  - type: python_job\n    file: threshold.py\n    parameters:\n      threshold: 10"
        ),
    )

    config = load_pipeline(pipeline)
    result = DefaultPipelineRunner().run(config)

    assert result.status is ExecutionStatus.SUCCEEDED
    assert result.rows_written == 1


def test_missing_entrypoint_caught_at_validation(tmp_path: Path) -> None:
    source = tmp_path / "input.csv"
    source.write_text("id\n1\n", encoding="utf-8")
    job = tmp_path / "job.py"
    job.write_text("def not_run(rows, context):\n    return rows\n", encoding="utf-8")
    dest = tmp_path / "out.csv"
    pipeline = tmp_path / "pipeline.yaml"
    write_pipeline(
        pipeline,
        source_csv=source,
        dest_csv=dest,
        transformations="  - type: python_job\n    file: job.py",
    )

    with pytest.raises(ConfigurationError, match="no function named 'run'"):
        load_pipeline(pipeline)


def test_misnamed_entrypoint_override_caught_at_validation(tmp_path: Path) -> None:
    source = tmp_path / "input.csv"
    source.write_text("id\n1\n", encoding="utf-8")
    job = tmp_path / "job.py"
    job.write_text("def run(rows, context):\n    return rows\n", encoding="utf-8")
    dest = tmp_path / "out.csv"
    pipeline = tmp_path / "pipeline.yaml"
    write_pipeline(
        pipeline,
        source_csv=source,
        dest_csv=dest,
        transformations="  - type: python_job\n    file: job.py\n    entrypoint: transform",
    )

    with pytest.raises(ConfigurationError, match="no function named 'transform'"):
        load_pipeline(pipeline)


def test_job_exception_fails_cleanly_and_redacts_the_original_message(tmp_path: Path) -> None:
    """A job's own exception message must never reach Nexolith's error
    output verbatim -- it could contain anything, including a credential or
    connection string the job's own code happened to embed. Only the
    exception's type name is safe to surface, matching the existing
    connector-error redaction convention."""
    source = tmp_path / "input.csv"
    source.write_text("id\n1\n", encoding="utf-8")
    job = tmp_path / "job.py"
    job.write_text(
        "def run(rows, context):\n"
        "    raise ValueError("
        '"failed connecting to postgresql://admin:supersecret@db.internal/prod")\n',
        encoding="utf-8",
    )
    dest = tmp_path / "out.csv"
    pipeline = tmp_path / "pipeline.yaml"
    write_pipeline(
        pipeline,
        source_csv=source,
        dest_csv=dest,
        transformations="  - type: python_job\n    file: job.py",
    )

    config = load_pipeline(pipeline)
    with pytest.raises(ExecutionError) as captured:
        DefaultPipelineRunner().run(config, raise_on_error=True)

    message = str(captured.value)
    assert "supersecret" not in message
    assert "postgresql://" not in message
    assert "ValueError" in message
    assert isinstance(captured.value.__cause__.__cause__, ValueError)


def test_missing_job_file_caught_at_validation(tmp_path: Path) -> None:
    source = tmp_path / "input.csv"
    source.write_text("id\n1\n", encoding="utf-8")
    dest = tmp_path / "out.csv"
    pipeline = tmp_path / "pipeline.yaml"
    write_pipeline(
        pipeline,
        source_csv=source,
        dest_csv=dest,
        transformations="  - type: python_job\n    file: does_not_exist.py",
    )

    with pytest.raises(ConfigurationError, match="Python job file not found"):
        load_pipeline(pipeline)


def test_job_file_path_resolves_relative_to_pipeline_directory_not_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir = tmp_path / "project"
    jobs_dir = project_dir / "jobs"
    jobs_dir.mkdir(parents=True)
    source = project_dir / "input.csv"
    source.write_text("id\n1\n", encoding="utf-8")
    (jobs_dir / "passthrough.py").write_text(
        "def run(rows, context):\n    return rows\n", encoding="utf-8"
    )
    dest = project_dir / "out.csv"
    pipeline_path = project_dir / "pipeline.yaml"
    write_pipeline(
        pipeline_path,
        source_csv=source,
        dest_csv=dest,
        transformations="  - type: python_job\n    file: jobs/passthrough.py",
    )

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    config = load_pipeline(pipeline_path)
    step = config.transformations[0]
    assert isinstance(step, PythonJobConfig)
    assert Path(step.file) == jobs_dir / "passthrough.py"
