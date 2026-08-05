"""End-to-end test of the bronze/silver/gold medallion example (NXL-84).

Copies the real `examples/medallion/` tree (not a fixture standing in for
it) into a tmp_path, runs the real DAG via `execute_dag`, and asserts on
concrete data correctness at every layer -- not just a successful exit
status. This is the test the example's own README points to.
"""

import csv
import shutil
from pathlib import Path

import pytest

from nexolith.dag import execute_dag
from nexolith.state import DagRunStatus, StateStore, TaskRunStatus


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_bronze_silver_gold_dag_runs_end_to_end_with_correct_data_at_every_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository_root = Path(__file__).parents[2]
    shutil.copytree(repository_root / "examples", tmp_path / "examples")
    monkeypatch.chdir(tmp_path)

    store = StateStore(tmp_path / "state.db")
    try:
        run_id = execute_dag(Path("examples/medallion/dag.yaml"), store)
        run = store.get_dag_run(run_id)
        assert run is not None
        assert run.status is DagRunStatus.SUCCEEDED

        tasks = {task.task_name: task for task in store.list_task_runs(run_id)}
        assert tasks["bronze"].status is TaskRunStatus.SUCCEEDED
        assert tasks["silver"].status is TaskRunStatus.SUCCEEDED
        assert tasks["gold"].status is TaskRunStatus.SUCCEEDED
    finally:
        store.close()

    # Bronze: raw ingestion, unchanged -- same row count and content as the source CSV.
    raw_rows = _read_csv(Path("examples/medallion/data/raw_orders.csv"))
    bronze_rows = _read_csv(Path("examples/medallion/bronze/orders.csv"))
    assert bronze_rows == raw_rows
    assert len(bronze_rows) == 10

    # Silver: the one exact duplicate (order_id 3) and the two invalid rows
    # (missing amount / missing customer_id) are gone; status is lowercased.
    silver_rows = _read_csv(Path("examples/medallion/silver/orders.csv"))
    assert len(silver_rows) == 7
    assert {row["order_id"] for row in silver_rows} == {"1", "2", "5", "6", "8", "9", "10"}
    assert all(row["status"] == row["status"].lower() for row in silver_rows)
    assert all(row["status"] in {"completed", "cancelled"} for row in silver_rows)

    # Gold: two independently-produced, business-ready aggregates from a
    # single real read of silver's output -- not derived from raw or bronze.
    by_customer = {
        row["customer_id"]: row["completed_revenue"]
        for row in _read_csv(Path("examples/medallion/gold/revenue_by_customer.csv"))
    }
    assert by_customer == {
        "101": "99.80",  # rows 1 and 10: 49.90 + 49.90
        "102": "152.10",  # rows 2 and 6: 120.00 + 32.10
        "103": "60.00",  # row 9
        "105": "200.00",  # row 8
    }
    assert "104" not in by_customer  # only completed orders count; 104's was cancelled

    by_status = {
        row["status"]: (row["order_count"], row["total_amount"])
        for row in _read_csv(Path("examples/medallion/gold/revenue_by_status.csv"))
    }
    assert by_status == {
        "completed": ("6", "511.90"),
        "cancelled": ("1", "75.50"),
    }
