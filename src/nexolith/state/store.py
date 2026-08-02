"""SQLite-backed persistence for registered DAGs, their schedules, and run
history. Storage layer + read/write contract only -- no scheduler loop, no
executor. A future scheduler story is the intended sole writer (a single
long-running process); CLI commands (a later story too) are expected to
read concurrently from separate processes. `PRAGMA journal_mode=WAL` is set
specifically for that: WAL lets readers proceed without blocking on -- or
being blocked by -- the one writer, which plain rollback-journal mode does
not.

Uses the standard library's `sqlite3` directly, not SQLAlchemy (already a
dependency, but only for the pluggable user-pipeline SQL connector, a
different concern: that abstracts over multiple *user* database engines,
this is Nexolith's own fixed, SQLite-only internal state).
"""

import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from nexolith.state.models import (
    DagRecord,
    DagRunRecord,
    DagRunStatus,
    TaskRunRecord,
    TaskRunStatus,
)
from nexolith.state.paths import default_database_path

# Each entry is (version, script). Applied in order, skipping any version
# already recorded in schema_version -- enough for a store whose shape will
# grow across the rest of this initiative, without a migration framework.
# `IF NOT EXISTS` on every statement makes re-applying an already-applied
# script harmless, which matters because schema creation and the
# schema_version bookkeeping commit separately (see _ensure_schema).
_MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS dags (
            name TEXT PRIMARY KEY,
            source_path TEXT NOT NULL,
            schedule TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS dag_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dag_name TEXT NOT NULL REFERENCES dags(name),
            status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
            trigger_reason TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_dag_runs_dag_name ON dag_runs(dag_name);

        CREATE TABLE IF NOT EXISTS task_runs (
            dag_run_id INTEGER NOT NULL REFERENCES dag_runs(id),
            task_name TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'succeeded', 'failed')),
            started_at TEXT,
            ended_at TEXT,
            error TEXT,
            PRIMARY KEY (dag_run_id, task_name)
        );
        """,
    ),
]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    existing = conn.execute("SELECT version FROM schema_version").fetchone()
    current_version = existing[0] if existing else 0
    has_row = existing is not None
    for version, script in _MIGRATIONS:
        if version <= current_version:
            continue
        conn.executescript(script)
        with conn:
            if has_row:
                conn.execute("UPDATE schema_version SET version = ?", (version,))
            else:
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
                has_row = True
        current_version = version


class StateStore:
    """A store per process, holding one open connection for its lifetime.
    Not thread-shared -- no thread introduced anywhere in this story, and
    the intended concurrency model is separate OS processes (scheduler
    daemon, CLI) each with their own `StateStore`/connection, not multiple
    threads sharing one.
    """

    def __init__(self, database_path: Path | None = None) -> None:
        self.database_path = database_path or default_database_path()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.database_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        _ensure_schema(self._conn)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "StateStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- DAG registration -------------------------------------------------

    def register_dag(
        self,
        name: str,
        source_path: Path,
        schedule: str | None,
        *,
        enabled: bool = True,
    ) -> None:
        """Insert a new DAG registration, or update an existing one with
        the same name (source path, schedule, and enabled flag included).
        """
        now = _now()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO dags (name, source_path, schedule, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    source_path = excluded.source_path,
                    schedule = excluded.schedule,
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (name, str(source_path), schedule, int(enabled), now, now),
            )

    def set_dag_enabled(self, name: str, enabled: bool) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE dags SET enabled = ?, updated_at = ? WHERE name = ?",
                (int(enabled), _now(), name),
            )

    def get_dag(self, name: str) -> DagRecord | None:
        row = self._conn.execute("SELECT * FROM dags WHERE name = ?", (name,)).fetchone()
        return _dag_record(row) if row is not None else None

    def list_dags(self) -> list[DagRecord]:
        rows = self._conn.execute("SELECT * FROM dags ORDER BY name").fetchall()
        return [_dag_record(row) for row in rows]

    # -- Run lifecycle ------------------------------------------------------

    def start_dag_run(
        self, dag_name: str, task_names: Sequence[str], *, trigger_reason: str
    ) -> int:
        """Record a DAG run starting, pre-creating every one of its tasks as
        'pending' in the same transaction. Recording the full expected task
        set up front (rather than inserting task rows lazily as each task
        starts) is what makes crash recovery possible from this store
        alone: after an unclean shutdown, a task still 'pending' or
        'running' is directly queryable without needing external knowledge
        of what the DAG was supposed to contain.
        """
        now = _now()
        with self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO dag_runs (dag_name, status, trigger_reason, started_at)
                VALUES (?, ?, ?, ?)
                """,
                (dag_name, DagRunStatus.RUNNING.value, trigger_reason, now),
            )
            dag_run_id = cursor.lastrowid
            assert dag_run_id is not None
            self._conn.executemany(
                """
                INSERT INTO task_runs (dag_run_id, task_name, status)
                VALUES (?, ?, ?)
                """,
                [(dag_run_id, task_name, TaskRunStatus.PENDING.value) for task_name in task_names],
            )
        return dag_run_id

    def start_task_run(self, dag_run_id: int, task_name: str) -> None:
        with self._conn:
            self._conn.execute(
                """
                UPDATE task_runs SET status = ?, started_at = ?
                WHERE dag_run_id = ? AND task_name = ?
                """,
                (TaskRunStatus.RUNNING.value, _now(), dag_run_id, task_name),
            )

    def complete_task_run(
        self, dag_run_id: int, task_name: str, *, success: bool, error: str | None = None
    ) -> None:
        status = TaskRunStatus.SUCCEEDED if success else TaskRunStatus.FAILED
        with self._conn:
            self._conn.execute(
                """
                UPDATE task_runs SET status = ?, ended_at = ?, error = ?
                WHERE dag_run_id = ? AND task_name = ?
                """,
                (status.value, _now(), error, dag_run_id, task_name),
            )

    def complete_dag_run(self, dag_run_id: int, *, success: bool, error: str | None = None) -> None:
        status = DagRunStatus.SUCCEEDED if success else DagRunStatus.FAILED
        with self._conn:
            self._conn.execute(
                "UPDATE dag_runs SET status = ?, ended_at = ?, error = ? WHERE id = ?",
                (status.value, _now(), error, dag_run_id),
            )

    # -- Queries ------------------------------------------------------------

    def get_dag_run(self, dag_run_id: int) -> DagRunRecord | None:
        row = self._conn.execute("SELECT * FROM dag_runs WHERE id = ?", (dag_run_id,)).fetchone()
        return _dag_run_record(row) if row is not None else None

    def list_dag_runs(self, dag_name: str) -> list[DagRunRecord]:
        rows = self._conn.execute(
            "SELECT * FROM dag_runs WHERE dag_name = ? ORDER BY id DESC", (dag_name,)
        ).fetchall()
        return [_dag_run_record(row) for row in rows]

    def latest_dag_run(self, dag_name: str) -> DagRunRecord | None:
        row = self._conn.execute(
            "SELECT * FROM dag_runs WHERE dag_name = ? ORDER BY id DESC LIMIT 1", (dag_name,)
        ).fetchone()
        return _dag_run_record(row) if row is not None else None

    def list_task_runs(self, dag_run_id: int) -> list[TaskRunRecord]:
        rows = self._conn.execute(
            "SELECT * FROM task_runs WHERE dag_run_id = ? ORDER BY task_name", (dag_run_id,)
        ).fetchall()
        return [_task_run_record(row) for row in rows]

    def list_incomplete_dag_runs(self) -> list[DagRunRecord]:
        """Every DAG run still recorded as 'running' -- i.e. never reached
        'succeeded' or 'failed'. After a clean shutdown this is empty; after
        a crash, it's exactly the set a future scheduler needs to inspect
        and decide how to recover. Built here so that recovery logic (not
        part of this story) has a store-level query to call.
        """
        rows = self._conn.execute(
            "SELECT * FROM dag_runs WHERE status = ? ORDER BY id", (DagRunStatus.RUNNING.value,)
        ).fetchall()
        return [_dag_run_record(row) for row in rows]


def _dag_record(row: sqlite3.Row) -> DagRecord:
    return DagRecord(
        name=row["name"],
        source_path=row["source_path"],
        schedule=row["schedule"],
        enabled=bool(row["enabled"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _dag_run_record(row: sqlite3.Row) -> DagRunRecord:
    return DagRunRecord(
        id=row["id"],
        dag_name=row["dag_name"],
        status=DagRunStatus(row["status"]),
        trigger_reason=row["trigger_reason"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        error=row["error"],
    )


def _task_run_record(row: sqlite3.Row) -> TaskRunRecord:
    return TaskRunRecord(
        dag_run_id=row["dag_run_id"],
        task_name=row["task_name"],
        status=TaskRunStatus(row["status"]),
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        error=row["error"],
    )
