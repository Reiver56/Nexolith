import sqlite3
from pathlib import Path

import pytest

from nexolith.process_identity import ProcessIdentity, ProcessIdentityUnavailable
from nexolith.state import DagRunStatus, StateStore, TaskRunStatus


def make_store(tmp_path: Path, name: str = "state.db") -> StateStore:
    return StateStore(tmp_path / name)


def test_schema_creation_on_a_fresh_database(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    assert not db_path.exists()

    store = make_store(tmp_path)
    try:
        conn = sqlite3.connect(str(db_path))
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert {
            "schema_version",
            "dags",
            "dag_runs",
            "task_runs",
            "task_attempts",
            "dag_trigger_reactions",
        } <= tables
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        assert version == 7
        columns = {row[1] for row in conn.execute("PRAGMA table_info(dag_runs)").fetchall()}
        assert "owner_create_time_ns" in columns
        conn.close()
    finally:
        store.close()

    assert db_path.exists()


def test_reopening_an_existing_database_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    StateStore(db_path).close()
    StateStore(db_path).close()  # must not raise, must not duplicate schema_version rows

    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("SELECT version FROM schema_version").fetchall()
    conn.close()
    assert rows == [(7,)]


def test_upgrading_an_existing_version_1_database_preserves_its_data(tmp_path: Path) -> None:
    """A database created before the 'skipped' status existed (schema_version
    1) must upgrade cleanly on next open, all the way through the current
    schema_version: existing task_runs rows survive both rebuild-under-a-
    new-name migrations, and both the 'skipped' and 'blocked' statuses
    become usable immediately after.
    """
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version (version) VALUES (1);
        CREATE TABLE dags (
            name TEXT PRIMARY KEY, source_path TEXT NOT NULL, schedule TEXT,
            enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE dag_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, dag_name TEXT NOT NULL REFERENCES dags(name),
            status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
            trigger_reason TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT, error TEXT
        );
        CREATE TABLE task_runs (
            dag_run_id INTEGER NOT NULL REFERENCES dag_runs(id), task_name TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'succeeded', 'failed')),
            started_at TEXT, ended_at TEXT, error TEXT,
            PRIMARY KEY (dag_run_id, task_name)
        );
        INSERT INTO dags VALUES ('etl', 'etl.yaml', NULL, 1, 't0', 't0');
        INSERT INTO dag_runs VALUES (1, 'etl', 'succeeded', 'manual', 't0', 't1', NULL);
        INSERT INTO task_runs VALUES (1, 'x', 'succeeded', 't0', 't1', NULL);
        """
    )
    conn.commit()
    conn.close()

    store = StateStore(db_path)
    try:
        preserved = store.list_task_runs(1)
        assert len(preserved) == 1
        assert preserved[0].task_name == "x"
        assert preserved[0].status is TaskRunStatus.SUCCEEDED

        run = store.get_dag_run(1)
        assert run is not None
        assert run.on_failure == "skip"  # backfilled default for a pre-existing row
        assert run.severity == "medium"  # backfilled default for a pre-existing row

        store.skip_task_run(1, "x")
        assert store.list_task_runs(1)[0].status is TaskRunStatus.SKIPPED

        store.block_task_run(1, "x")
        assert store.list_task_runs(1)[0].status is TaskRunStatus.BLOCKED
    finally:
        store.close()

    conn = sqlite3.connect(str(db_path))
    version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    conn.close()
    assert version == 7
    assert "dag_trigger_reactions" in tables


def test_register_and_read_back_a_dag(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.register_dag("nightly-etl", Path("workflow.yaml"), "0 2 * * *")

        dag = store.get_dag("nightly-etl")

        assert dag is not None
        assert dag.name == "nightly-etl"
        assert dag.source_path == "workflow.yaml"
        assert dag.schedule == "0 2 * * *"
        assert dag.enabled is True
        assert dag.created_at == dag.updated_at
    finally:
        store.close()


def test_registering_an_existing_dag_name_updates_it(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.register_dag("nightly-etl", Path("workflow.yaml"), "0 2 * * *")
        first = store.get_dag("nightly-etl")
        assert first is not None

        store.register_dag("nightly-etl", Path("workflow_v2.yaml"), "0 3 * * *", enabled=False)

        updated = store.get_dag("nightly-etl")
        assert updated is not None
        assert updated.source_path == "workflow_v2.yaml"
        assert updated.schedule == "0 3 * * *"
        assert updated.enabled is False
        assert len(store.list_dags()) == 1  # updated in place, not duplicated
    finally:
        store.close()


def test_set_dag_enabled(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.register_dag("d", Path("d.yaml"), None)
        store.set_dag_enabled("d", False)
        assert store.get_dag("d").enabled is False  # type: ignore[union-attr]
        store.set_dag_enabled("d", True)
        assert store.get_dag("d").enabled is True  # type: ignore[union-attr]
    finally:
        store.close()


def test_list_dags_returns_all_registered_dags_sorted_by_name(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.register_dag("b", Path("b.yaml"), None)
        store.register_dag("a", Path("a.yaml"), None)

        names = [dag.name for dag in store.list_dags()]

        assert names == ["a", "b"]
    finally:
        store.close()


def test_get_dag_returns_none_when_not_registered(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        assert store.get_dag("does-not-exist") is None
    finally:
        store.close()


def test_full_successful_run_lifecycle(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.register_dag("etl", Path("etl.yaml"), None)
        run_id = store.start_dag_run("etl", ["extract", "load"], trigger_reason="manual")

        run = store.get_dag_run(run_id)
        assert run is not None
        assert run.status is DagRunStatus.RUNNING
        assert run.ended_at is None
        assert run.owner_pid is not None
        assert run.owner_create_time_ns is not None

        tasks = store.list_task_runs(run_id)
        assert [task.task_name for task in tasks] == ["extract", "load"]
        assert all(task.status is TaskRunStatus.PENDING for task in tasks)

        store.start_task_run(run_id, "extract")
        store.complete_task_run(run_id, "extract", success=True)
        store.start_task_run(run_id, "load")
        store.complete_task_run(run_id, "load", success=True)
        store.complete_dag_run(run_id, success=True)

        finished_run = store.get_dag_run(run_id)
        assert finished_run is not None
        assert finished_run.status is DagRunStatus.SUCCEEDED
        assert finished_run.ended_at is not None

        finished_tasks = {task.task_name: task for task in store.list_task_runs(run_id)}
        assert finished_tasks["extract"].status is TaskRunStatus.SUCCEEDED
        assert finished_tasks["extract"].started_at is not None
        assert finished_tasks["extract"].ended_at is not None
        assert finished_tasks["load"].status is TaskRunStatus.SUCCEEDED
    finally:
        store.close()


def test_run_does_not_start_when_current_process_identity_is_unavailable(tmp_path: Path) -> None:
    def unavailable_identity() -> ProcessIdentity:
        raise ProcessIdentityUnavailable("simulated unavailable identity")

    store = StateStore(tmp_path / "state.db", process_identity=unavailable_identity)
    try:
        store.register_dag("etl", Path("etl.yaml"), None)

        with pytest.raises(ProcessIdentityUnavailable, match="simulated unavailable identity"):
            store.start_dag_run("etl", ["extract"], trigger_reason="manual")

        assert store.list_dag_runs("etl") == []
    finally:
        store.close()


def test_failed_run(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.register_dag("etl", Path("etl.yaml"), None)
        run_id = store.start_dag_run("etl", ["extract"], trigger_reason="manual")

        store.start_task_run(run_id, "extract")
        store.complete_task_run(run_id, "extract", success=False, error="connection refused")
        store.complete_dag_run(run_id, success=False, error="task 'extract' failed")

        run = store.get_dag_run(run_id)
        assert run is not None
        assert run.status is DagRunStatus.FAILED
        assert run.error == "task 'extract' failed"

        task = store.list_task_runs(run_id)[0]
        assert task.status is TaskRunStatus.FAILED
        assert task.error == "connection refused"
    finally:
        store.close()


def test_skip_task_run(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.register_dag("etl", Path("etl.yaml"), None)
        run_id = store.start_dag_run("etl", ["extract", "load"], trigger_reason="manual")

        store.skip_task_run(run_id, "load")

        task = next(t for t in store.list_task_runs(run_id) if t.task_name == "load")
        assert task.status is TaskRunStatus.SKIPPED
        assert task.started_at is None  # never actually started
        assert task.ended_at is not None
    finally:
        store.close()


def test_partially_completed_run_is_the_crash_recovery_relevant_case(tmp_path: Path) -> None:
    """A run that started, had one task succeed, then the process died before
    the remaining tasks or the run itself ever reached a terminal state --
    exactly what a scheduler must detect and reconstruct after an unclean
    shutdown. No 'crash' is simulated here (nothing to simulate -- the
    store just never gets told the run finished); the test is that this
    state is fully, accurately queryable from the store alone.
    """
    store = make_store(tmp_path)
    try:
        store.register_dag("etl", Path("etl.yaml"), None)
        run_id = store.start_dag_run(
            "etl", ["extract", "transform", "load"], trigger_reason="schedule"
        )
        store.start_task_run(run_id, "extract")
        store.complete_task_run(run_id, "extract", success=True)
        store.start_task_run(run_id, "transform")
        # ... process dies here: 'transform' never completes, 'load' never starts,
        # complete_dag_run() is never called.

        run = store.get_dag_run(run_id)
        assert run is not None
        assert run.status is DagRunStatus.RUNNING
        assert run.ended_at is None

        tasks = {task.task_name: task for task in store.list_task_runs(run_id)}
        assert tasks["extract"].status is TaskRunStatus.SUCCEEDED
        assert tasks["transform"].status is TaskRunStatus.RUNNING
        assert tasks["transform"].ended_at is None
        assert tasks["load"].status is TaskRunStatus.PENDING

        incomplete = store.list_incomplete_dag_runs()
        assert [incomplete_run.id for incomplete_run in incomplete] == [run_id]
    finally:
        store.close()


def test_list_incomplete_dag_runs_excludes_finished_runs(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.register_dag("etl", Path("etl.yaml"), None)
        finished_id = store.start_dag_run("etl", ["a"], trigger_reason="manual")
        store.complete_dag_run(finished_id, success=True)
        running_id = store.start_dag_run("etl", ["a"], trigger_reason="manual")

        incomplete_ids = [run.id for run in store.list_incomplete_dag_runs()]

        assert incomplete_ids == [running_id]
        assert finished_id not in incomplete_ids
    finally:
        store.close()


def test_latest_dag_run_with_multiple_runs_present(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.register_dag("etl", Path("etl.yaml"), None)
        first_id = store.start_dag_run("etl", ["a"], trigger_reason="manual")
        store.complete_dag_run(first_id, success=True)
        second_id = store.start_dag_run("etl", ["a"], trigger_reason="manual")
        store.complete_dag_run(second_id, success=False, error="boom")

        latest = store.latest_dag_run("etl")

        assert latest is not None
        assert latest.id == second_id
        assert latest.status is DagRunStatus.FAILED
    finally:
        store.close()


def test_list_dag_runs_returns_all_runs_most_recent_first(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.register_dag("etl", Path("etl.yaml"), None)
        first_id = store.start_dag_run("etl", ["a"], trigger_reason="manual")
        second_id = store.start_dag_run("etl", ["a"], trigger_reason="manual")

        run_ids = [run.id for run in store.list_dag_runs("etl")]

        assert run_ids == [second_id, first_id]
    finally:
        store.close()


def test_latest_dag_run_returns_none_for_a_dag_with_no_runs(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.register_dag("etl", Path("etl.yaml"), None)
        assert store.latest_dag_run("etl") is None
        assert store.list_dag_runs("etl") == []
    finally:
        store.close()


def test_get_dag_run_returns_none_for_a_nonexistent_run(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        assert store.get_dag_run(999) is None
        assert store.list_task_runs(999) == []
    finally:
        store.close()


def test_task_run_query_for_a_specific_run_only_returns_that_runs_tasks(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.register_dag("etl", Path("etl.yaml"), None)
        run_a = store.start_dag_run("etl", ["x"], trigger_reason="manual")
        run_b = store.start_dag_run("etl", ["x", "y"], trigger_reason="manual")

        assert [task.task_name for task in store.list_task_runs(run_a)] == ["x"]
        assert [task.task_name for task in store.list_task_runs(run_b)] == ["x", "y"]
    finally:
        store.close()


def test_journal_mode_is_wal(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        store.close()


def test_a_reader_is_not_blocked_by_an_uncommitted_writer(tmp_path: Path) -> None:
    """The concurrency case this schema is designed for: a writer (the
    future scheduler daemon) holding an open transaction must not block a
    concurrent reader (a future CLI command) in a separate connection --
    whether that's a second connection in the same process or a wholly
    separate one, SQLite's file-level WAL locking behaves identically
    either way, so a second `StateStore` on the same database file exercises
    the real mechanism under test. Under WAL, a reader sees the last
    *committed* state and proceeds immediately; under the default
    rollback-journal mode it would block or raise 'database is locked'.
    """
    db_path = tmp_path / "state.db"
    writer = StateStore(db_path)
    reader = StateStore(db_path)
    try:
        writer.register_dag("etl", Path("etl.yaml"), None)

        writer._conn.execute("BEGIN IMMEDIATE")
        writer._conn.execute("UPDATE dags SET schedule = ? WHERE name = ?", ("0 * * * *", "etl"))
        # Deliberately not committed yet -- a reader must still succeed, and
        # must still see the pre-write value (WAL readers see a consistent
        # snapshot, not a partially-applied uncommitted write).

        during_write = reader.get_dag("etl")

        writer._conn.commit()
        after_commit = reader.get_dag("etl")

        assert during_write is not None
        assert during_write.schedule is None
        assert after_commit is not None
        assert after_commit.schedule == "0 * * * *"
    finally:
        writer.close()
        reader.close()


def test_migration_4_applies_cleanly_on_a_schema_version_3_database(tmp_path: Path) -> None:
    """A database left at schema_version 3 (before dag_trigger_reactions
    existed, NXL-85) must upgrade cleanly: existing rows survive, and the
    new table becomes usable immediately after.
    """
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version (version) VALUES (3);
        CREATE TABLE dags (
            name TEXT PRIMARY KEY, source_path TEXT NOT NULL, schedule TEXT,
            enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE dag_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, dag_name TEXT NOT NULL REFERENCES dags(name),
            status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
            trigger_reason TEXT NOT NULL, on_failure TEXT NOT NULL DEFAULT 'skip',
            started_at TEXT NOT NULL, ended_at TEXT, error TEXT
        );
        CREATE TABLE task_runs (
            dag_run_id INTEGER NOT NULL REFERENCES dag_runs(id), task_name TEXT NOT NULL,
            status TEXT NOT NULL
                CHECK (status IN
                    ('pending', 'running', 'succeeded', 'failed', 'skipped', 'blocked')),
            started_at TEXT, ended_at TEXT, error TEXT,
            PRIMARY KEY (dag_run_id, task_name)
        );
        CREATE TABLE task_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, dag_run_id INTEGER NOT NULL,
            task_name TEXT NOT NULL, attempt_number INTEGER NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
            started_at TEXT NOT NULL, ended_at TEXT, error TEXT,
            FOREIGN KEY (dag_run_id, task_name) REFERENCES task_runs(dag_run_id, task_name)
        );
        INSERT INTO dags VALUES ('upstream', 'up.yaml', NULL, 1, 't0', 't0');
        INSERT INTO dags VALUES ('downstream', 'down.yaml', NULL, 1, 't0', 't0');
        INSERT INTO dag_runs
            VALUES (1, 'upstream', 'succeeded', 'manual', 'skip', 't0', 't1', NULL);
        INSERT INTO task_runs VALUES (1, 'extract', 'succeeded', 't0', 't1', NULL);
        INSERT INTO task_attempts
            VALUES (1, 1, 'extract', 1, 'succeeded', 't0', 't1', NULL);
        """
    )
    conn.commit()
    conn.close()

    store = StateStore(db_path)
    try:
        run = store.get_dag_run(1)
        assert run is not None
        assert run.status is DagRunStatus.SUCCEEDED  # pre-existing row preserved
        assert run.owner_pid is None
        assert run.owner_create_time_ns is None
        assert store.list_task_runs(1)[0].task_name == "extract"
        assert store.list_run_attempts(1)[0].task_name == "extract"

        assert store.get_last_reacted_upstream_run_id("downstream", "upstream") is None
        store.record_trigger_reaction("downstream", "upstream", 1)
        assert store.get_last_reacted_upstream_run_id("downstream", "upstream") == 1
    finally:
        store.close()

    conn = sqlite3.connect(str(db_path))
    version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.close()
    assert version == 7
    assert foreign_key_errors == []


def test_migration_7_preserves_version_6_run_task_and_attempt_history(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        PRAGMA foreign_keys=ON;
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version (version) VALUES (6);
        CREATE TABLE dags (
            name TEXT PRIMARY KEY, source_path TEXT NOT NULL, schedule TEXT,
            enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE dag_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dag_name TEXT NOT NULL REFERENCES dags(name),
            status TEXT NOT NULL
                CHECK (status IN ('running', 'succeeded', 'failed', 'interrupted')),
            trigger_reason TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT, error TEXT,
            on_failure TEXT NOT NULL DEFAULT 'skip', severity TEXT NOT NULL DEFAULT 'medium',
            owner_pid INTEGER
        );
        CREATE TABLE task_runs (
            dag_run_id INTEGER NOT NULL REFERENCES dag_runs(id), task_name TEXT NOT NULL,
            status TEXT NOT NULL
                CHECK (status IN
                    ('pending', 'running', 'succeeded', 'failed', 'skipped', 'blocked')),
            started_at TEXT, ended_at TEXT, error TEXT,
            PRIMARY KEY (dag_run_id, task_name)
        );
        CREATE TABLE task_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, dag_run_id INTEGER NOT NULL,
            task_name TEXT NOT NULL, attempt_number INTEGER NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
            started_at TEXT NOT NULL, ended_at TEXT, error TEXT,
            FOREIGN KEY (dag_run_id, task_name) REFERENCES task_runs(dag_run_id, task_name)
        );
        CREATE TABLE dag_trigger_reactions (
            downstream_dag_name TEXT NOT NULL, upstream_dag_name TEXT NOT NULL,
            last_reacted_run_id INTEGER NOT NULL,
            PRIMARY KEY (downstream_dag_name, upstream_dag_name)
        );
        INSERT INTO dags VALUES ('etl', 'etl.yaml', '1m', 1, 't0', 't0');
        INSERT INTO dag_runs VALUES
            (7, 'etl', 'running', 'manual', 't0', NULL, NULL, 'skip', 'high', 4242);
        INSERT INTO task_runs VALUES (7, 'extract', 'running', 't0', NULL, NULL);
        INSERT INTO task_attempts VALUES (9, 7, 'extract', 1, 'running', 't0', NULL, NULL);
        """
    )
    conn.commit()
    conn.close()

    store = StateStore(db_path)
    try:
        run = store.get_dag_run(7)
        assert run is not None
        assert run.owner_pid == 4242
        assert run.owner_create_time_ns is None
        assert store.list_task_runs(7)[0].status is TaskRunStatus.RUNNING
        assert store.list_run_attempts(7)[0].attempt_number == 1
    finally:
        store.close()

    conn = sqlite3.connect(str(db_path))
    version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.close()
    assert version == 7
    assert foreign_key_errors == []


def test_record_trigger_reaction_upserts_in_place(tmp_path: Path) -> None:
    """One row per (downstream, upstream) relationship, not one per
    reaction event -- recording a second reaction updates the same row
    rather than accumulating history the scheduler would otherwise have to
    aggregate on every tick.
    """
    store = make_store(tmp_path)
    try:
        assert store.get_last_reacted_upstream_run_id("downstream", "upstream") is None

        store.record_trigger_reaction("downstream", "upstream", 1)
        assert store.get_last_reacted_upstream_run_id("downstream", "upstream") == 1

        store.record_trigger_reaction("downstream", "upstream", 2)
        assert store.get_last_reacted_upstream_run_id("downstream", "upstream") == 2

        # A different downstream reacting to the same upstream is a wholly
        # separate relationship.
        assert store.get_last_reacted_upstream_run_id("other_downstream", "upstream") is None
    finally:
        store.close()
