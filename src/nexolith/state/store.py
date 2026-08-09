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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from nexolith.process_identity import (
    ProcessIdentity,
    ProcessIdentityLookupStatus,
    ProcessIdentityProvider,
    current_process_identity,
)
from nexolith.state.models import (
    DagRecord,
    DagRunRecord,
    DagRunStatus,
    TaskAttemptRecord,
    TaskAttemptStatus,
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
    (
        2,
        """
        -- Add 'skipped' to task_runs.status. SQLite has no ALTER TABLE for
        -- CHECK constraints, so this rebuilds the table under a temporary
        -- name and swaps it in. `DROP TABLE IF EXISTS task_runs_new` first
        -- makes this safe to re-run from any crash point: whether the
        -- previous attempt died before creating the copy, mid-copy, or
        -- after the rename already completed, retrying always converges on
        -- the same end state with the same data.
        DROP TABLE IF EXISTS task_runs_new;
        CREATE TABLE task_runs_new (
            dag_run_id INTEGER NOT NULL REFERENCES dag_runs(id),
            task_name TEXT NOT NULL,
            status TEXT NOT NULL
                CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'skipped')),
            started_at TEXT,
            ended_at TEXT,
            error TEXT,
            PRIMARY KEY (dag_run_id, task_name)
        );
        INSERT INTO task_runs_new (dag_run_id, task_name, status, started_at, ended_at, error)
            SELECT dag_run_id, task_name, status, started_at, ended_at, error FROM task_runs;
        DROP TABLE task_runs;
        ALTER TABLE task_runs_new RENAME TO task_runs;
        """,
    ),
    (
        3,
        """
        -- Retry/failure-propagation policy (NXL-79). Two schema changes:
        -- add 'blocked' to task_runs.status (same rebuild-and-swap
        -- technique as schema_version 2's 'skipped', for the same reason
        -- -- SQLite has no ALTER TABLE for CHECK constraints), and a new
        -- task_attempts table recording every real execution attempt.
        -- task_runs stays "the current/final state of this task in this
        -- run"; task_attempts holds the full history behind that state,
        -- one row per attempt, written the moment it starts.
        --
        -- dag_runs.on_failure (the policy actually in effect for that run)
        -- is added separately, in Python, before this script runs -- see
        -- _ensure_schema. Unlike a CHECK-constraint change, adding a
        -- plain column is a single ALTER TABLE with no rebuild needed, but
        -- SQLite has no "ADD COLUMN IF NOT EXISTS", so it needs its own
        -- idempotency check that a bare SQL script can't express.
        --
        -- task_attempts is dropped before task_runs is rebuilt, in that
        -- order: it has its own foreign key into task_runs, and SQLite
        -- refuses to drop a table another table still references (verified
        -- directly, not assumed). Dropping task_attempts first, before
        -- task_runs_new, makes retrying this whole script safe from any
        -- crash point, matching schema_version 2's own guarantee.
        DROP TABLE IF EXISTS task_attempts;
        DROP TABLE IF EXISTS task_runs_new;

        CREATE TABLE task_runs_new (
            dag_run_id INTEGER NOT NULL REFERENCES dag_runs(id),
            task_name TEXT NOT NULL,
            status TEXT NOT NULL
                CHECK (status IN
                    ('pending', 'running', 'succeeded', 'failed', 'skipped', 'blocked')),
            started_at TEXT,
            ended_at TEXT,
            error TEXT,
            PRIMARY KEY (dag_run_id, task_name)
        );
        INSERT INTO task_runs_new (dag_run_id, task_name, status, started_at, ended_at, error)
            SELECT dag_run_id, task_name, status, started_at, ended_at, error FROM task_runs;
        DROP TABLE task_runs;
        ALTER TABLE task_runs_new RENAME TO task_runs;

        CREATE TABLE task_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dag_run_id INTEGER NOT NULL,
            task_name TEXT NOT NULL,
            attempt_number INTEGER NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
            started_at TEXT NOT NULL,
            ended_at TEXT,
            error TEXT,
            FOREIGN KEY (dag_run_id, task_name) REFERENCES task_runs(dag_run_id, task_name)
        );
        CREATE INDEX IF NOT EXISTS idx_task_attempts_run_task
            ON task_attempts(dag_run_id, task_name);
        """,
    ),
    (
        4,
        """
        -- Cross-DAG triggers (NXL-85). The trigger *declaration* (which
        -- upstream DAGs, by name) lives in the downstream DAG's own YAML
        -- file, re-read fresh on every scheduler poll tick -- not
        -- duplicated into the store. What genuinely needs to be
        -- persisted, and can't be derived from anything else, is
        -- *reaction state*: for one (downstream, upstream) pair, the id
        -- of the most recent upstream dag_runs row this downstream has
        -- already triggered off of. Without it the scheduler could not
        -- tell "upstream has a new completed run I haven't reacted to"
        -- from "upstream's last completion already triggered me" --
        -- a timestamp comparison alone can't distinguish those (a
        -- successful run's started_at doesn't change after the fact, but
        -- neither does knowing IF this downstream already reacted to it).
        -- One row per relationship (not per reaction event): the latest
        -- reacted run id is all due-ness evaluation ever needs, so this
        -- upserts in place rather than growing an unbounded history table.
        CREATE TABLE IF NOT EXISTS dag_trigger_reactions (
            downstream_dag_name TEXT NOT NULL,
            upstream_dag_name TEXT NOT NULL,
            last_reacted_run_id INTEGER NOT NULL,
            PRIMARY KEY (downstream_dag_name, upstream_dag_name)
        );
        """,
    ),
    (
        5,
        """
        -- DAG severity classification (NXL-87). Severity is recorded with
        -- the run it belongs to -- the value declared in the DAG file at
        -- the moment this run started, not whatever the file says now --
        -- exactly the same reasoning schema_version 3 already established
        -- for dag_runs.on_failure. Same technique too: a plain ALTER TABLE
        -- ADD COLUMN, added in Python before this script runs (see
        -- _add_dag_runs_severity_column_if_missing / _ensure_schema),
        -- since SQLite has no "ADD COLUMN IF NOT EXISTS" and this
        -- migration needs no table rebuild (no CHECK constraint here
        -- either, matching on_failure's own precedent -- validity is
        -- enforced by DagConfig's Literal[...] type before a value ever
        -- reaches this column).
        """,
    ),
    (
        6,
        """
        -- Crash reconciliation (NXL-116). A run now records the process
        -- that owns it, allowing a fresh scheduler to distinguish a stale
        -- row from a genuinely active foreground `nexolith run` process.
        -- `interrupted` honestly records an execution whose outcome was
        -- never observed; it is terminal, but is not reported as failed.
        --
        -- SQLite cannot alter a CHECK constraint in place. Foreign-key
        -- enforcement is disabled only for this parent-table rebuild and
        -- restored before the migration completes; existing child rows
        -- continue to reference the replacement table named dag_runs.
        PRAGMA foreign_keys=OFF;
        DROP TABLE IF EXISTS dag_runs_new;
        CREATE TABLE dag_runs_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dag_name TEXT NOT NULL REFERENCES dags(name),
            status TEXT NOT NULL
                CHECK (status IN ('running', 'succeeded', 'failed', 'interrupted')),
            trigger_reason TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            error TEXT,
            on_failure TEXT NOT NULL DEFAULT 'skip',
            severity TEXT NOT NULL DEFAULT 'medium',
            owner_pid INTEGER
        );
        INSERT INTO dag_runs_new
            (id, dag_name, status, trigger_reason, started_at, ended_at, error,
             on_failure, severity, owner_pid)
            SELECT id, dag_name, status, trigger_reason, started_at, ended_at, error,
                   on_failure, severity, NULL
            FROM dag_runs;
        DROP TABLE dag_runs;
        ALTER TABLE dag_runs_new RENAME TO dag_runs;
        CREATE INDEX IF NOT EXISTS idx_dag_runs_dag_name ON dag_runs(dag_name);
        PRAGMA foreign_keys=ON;
        """,
    ),
    (
        7,
        """
        -- PID-reuse-safe run ownership (NXL-120). The nullable
        -- owner_create_time_ns column is added in Python before this script
        -- runs (see _add_dag_runs_owner_identity_column_if_missing), using
        -- the same idempotent ALTER TABLE pattern as schema versions 3 and
        -- 5. Existing version-6 rows deliberately remain NULL: inventing a
        -- creation time would risk confusing a reused PID with its original
        -- owner.
        """,
    ),
]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _add_dag_runs_on_failure_column_if_missing(conn: sqlite3.Connection) -> None:
    """SQLite's ALTER TABLE has no "ADD COLUMN IF NOT EXISTS" -- re-running
    it unconditionally raises "duplicate column name" on a second attempt
    (verified directly). A plain column addition doesn't need the
    rebuild-and-swap technique the CHECK-constraint changes use (that would
    itself require dropping dag_runs, which task_runs' own foreign key
    would then block -- also verified directly, not assumed), so this is
    just a Python-level existence check instead.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(dag_runs)").fetchall()}
    if "on_failure" not in columns:
        conn.execute("ALTER TABLE dag_runs ADD COLUMN on_failure TEXT NOT NULL DEFAULT 'skip'")


def _add_dag_runs_severity_column_if_missing(conn: sqlite3.Connection) -> None:
    """Same technique and same reason as `_add_dag_runs_on_failure_column_if_missing`
    (schema_version 3) -- see that function's docstring."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(dag_runs)").fetchall()}
    if "severity" not in columns:
        conn.execute("ALTER TABLE dag_runs ADD COLUMN severity TEXT NOT NULL DEFAULT 'medium'")


def _add_dag_runs_owner_identity_column_if_missing(conn: sqlite3.Connection) -> None:
    """Add NXL-120's creation-time half of the owner identity safely."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(dag_runs)").fetchall()}
    if "owner_create_time_ns" not in columns:
        conn.execute("ALTER TABLE dag_runs ADD COLUMN owner_create_time_ns INTEGER")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    existing = conn.execute("SELECT version FROM schema_version").fetchone()
    current_version = existing[0] if existing else 0
    has_row = existing is not None
    for version, script in _MIGRATIONS:
        if version <= current_version:
            continue
        if version == 3:
            _add_dag_runs_on_failure_column_if_missing(conn)
        elif version == 5:
            _add_dag_runs_severity_column_if_missing(conn)
        elif version == 7:
            _add_dag_runs_owner_identity_column_if_missing(conn)
        conn.executescript(script)
        with conn:
            if has_row:
                conn.execute("UPDATE schema_version SET version = ?", (version,))
            else:
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
                has_row = True
        current_version = version


@dataclass(frozen=True, slots=True)
class DagRunReconciliation:
    interrupted_run_ids: tuple[int, ...]
    unverifiable_runs: tuple[tuple[int, str], ...]


class StateStore:
    """A store per process, holding one open connection for its lifetime.
    Not thread-shared -- no thread introduced anywhere in this story, and
    the intended concurrency model is separate OS processes (scheduler
    daemon, CLI) each with their own `StateStore`/connection, not multiple
    threads sharing one.
    """

    def __init__(
        self,
        database_path: Path | None = None,
        *,
        process_identity: Callable[[], ProcessIdentity] | None = None,
    ) -> None:
        self.database_path = database_path or default_database_path()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.database_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        _ensure_schema(self._conn)
        self._process_identity = process_identity or current_process_identity

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

    def set_dag_enabled(self, name: str, enabled: bool) -> bool:
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE dags SET enabled = ?, updated_at = ? WHERE name = ?",
                (int(enabled), _now(), name),
            )
        return cursor.rowcount > 0

    def get_dag(self, name: str) -> DagRecord | None:
        row = self._conn.execute("SELECT * FROM dags WHERE name = ?", (name,)).fetchone()
        return _dag_record(row) if row is not None else None

    def list_dags(self) -> list[DagRecord]:
        rows = self._conn.execute("SELECT * FROM dags ORDER BY name").fetchall()
        return [_dag_record(row) for row in rows]

    # -- Run lifecycle ------------------------------------------------------

    def start_dag_run(
        self,
        dag_name: str,
        task_names: Sequence[str],
        *,
        trigger_reason: str,
        on_failure: str = "skip",
        severity: str = "medium",
        owner_identity: ProcessIdentity | None = None,
    ) -> int:
        """Record a DAG run starting, pre-creating every one of its tasks as
        'pending' in the same transaction. Recording the full expected task
        set up front (rather than inserting task rows lazily as each task
        starts) is what makes crash recovery possible from this store
        alone: after an unclean shutdown, a task still 'pending' or
        'running' is directly queryable without needing external knowledge
        of what the DAG was supposed to contain.

        `on_failure` ('skip' or 'block') is the policy actually in effect
        for this run, recorded here rather than only living in the DAG
        file, so a past run's history stays accurate even if the file's
        policy changes later. Defaults to 'skip' -- today's only behavior
        before this field existed -- so a caller that doesn't pass it gets
        exactly the pre-existing default. `severity` (NXL-87) is the same
        idea for how serious a failure of this run is to the business --
        snapshotted here, not read from the file later, for the same reason.
        """
        now = _now()
        identity = owner_identity or self._process_identity()
        with self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO dag_runs
                    (dag_name, status, trigger_reason, on_failure, severity, started_at,
                     owner_pid, owner_create_time_ns)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dag_name,
                    DagRunStatus.RUNNING.value,
                    trigger_reason,
                    on_failure,
                    severity,
                    now,
                    identity.pid,
                    identity.create_time_ns,
                ),
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

    def skip_task_run(self, dag_run_id: int, task_name: str) -> None:
        """A task that never ran because an upstream dependency it
        transitively depends on failed. `started_at` stays NULL -- it never
        started -- `ended_at` records when the skip was decided.
        """
        with self._conn:
            self._conn.execute(
                """
                UPDATE task_runs SET status = ?, ended_at = ?
                WHERE dag_run_id = ? AND task_name = ?
                """,
                (TaskRunStatus.SKIPPED.value, _now(), dag_run_id, task_name),
            )

    def block_task_run(self, dag_run_id: int, task_name: str) -> None:
        """A task that never ran because the DAG's on_failure policy is
        'block' and an earlier task in the run ultimately failed -- unlike
        `skip_task_run`, this doesn't imply any dependency relationship to
        the failure. `started_at` stays NULL, same as a skip.
        """
        with self._conn:
            self._conn.execute(
                """
                UPDATE task_runs SET status = ?, ended_at = ?
                WHERE dag_run_id = ? AND task_name = ?
                """,
                (TaskRunStatus.BLOCKED.value, _now(), dag_run_id, task_name),
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

    def interrupt_abandoned_dag_runs(
        self, lookup_process: ProcessIdentityProvider
    ) -> DagRunReconciliation:
        """Interrupt owners known dead or replaced; preserve unknown owners.

        A matching PID is insufficient because operating systems reuse
        PIDs. Runs are interrupted only when the process is absent or its
        creation time differs. Legacy rows and lookup failures remain
        running, failing closed against duplicate execution.
        """
        abandoned: list[DagRunRecord] = []
        unverifiable: list[tuple[int, str]] = []
        for run in self.list_incomplete_dag_runs():
            if run.owner_pid is None or run.owner_create_time_ns is None:
                unverifiable.append((run.id, "legacy_owner_identity"))
                continue

            stored_identity = ProcessIdentity(run.owner_pid, run.owner_create_time_ns)
            try:
                lookup = lookup_process(run.owner_pid)
            except Exception:
                unverifiable.append((run.id, ProcessIdentityLookupStatus.UNAVAILABLE.value))
                continue

            if lookup.status is ProcessIdentityLookupStatus.NOT_FOUND:
                abandoned.append(run)
            elif lookup.status is ProcessIdentityLookupStatus.FOUND:
                if lookup.identity != stored_identity:
                    abandoned.append(run)
            else:
                unverifiable.append((run.id, lookup.status.value))

        if not abandoned:
            return DagRunReconciliation((), tuple(unverifiable))
        ended_at = _now()
        with self._conn:
            self._conn.executemany(
                """
                UPDATE dag_runs SET status = ?, ended_at = ?
                WHERE id = ? AND status = ?
                """,
                [
                    (
                        DagRunStatus.INTERRUPTED.value,
                        ended_at,
                        run.id,
                        DagRunStatus.RUNNING.value,
                    )
                    for run in abandoned
                ],
            )
        return DagRunReconciliation(tuple(run.id for run in abandoned), tuple(unverifiable))

    # -- Retry attempts (schema_version 3) -----------------------------------
    #
    # task_runs stays "the current/final state of this task in this run";
    # these record the full history behind that state, one row per real
    # execution attempt, written the moment it starts -- the "no silent
    # retries" requirement lives here, not batched after the fact.

    def start_task_attempt(self, dag_run_id: int, task_name: str, attempt_number: int) -> int:
        now = _now()
        with self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO task_attempts
                    (dag_run_id, task_name, attempt_number, status, started_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (dag_run_id, task_name, attempt_number, TaskAttemptStatus.RUNNING.value, now),
            )
            attempt_id = cursor.lastrowid
            assert attempt_id is not None
        return attempt_id

    def complete_task_attempt(
        self, attempt_id: int, *, success: bool, error: str | None = None
    ) -> None:
        status = TaskAttemptStatus.SUCCEEDED if success else TaskAttemptStatus.FAILED
        with self._conn:
            self._conn.execute(
                "UPDATE task_attempts SET status = ?, ended_at = ?, error = ? WHERE id = ?",
                (status.value, _now(), error, attempt_id),
            )

    def list_task_attempts(self, dag_run_id: int, task_name: str) -> list[TaskAttemptRecord]:
        rows = self._conn.execute(
            """
            SELECT * FROM task_attempts WHERE dag_run_id = ? AND task_name = ?
            ORDER BY attempt_number
            """,
            (dag_run_id, task_name),
        ).fetchall()
        return [_task_attempt_record(row) for row in rows]

    def list_run_attempts(self, dag_run_id: int) -> list[TaskAttemptRecord]:
        """Every attempt across every task in one run -- what `runs show`
        needs to render retry history without a separate query per task.
        """
        rows = self._conn.execute(
            """
            SELECT * FROM task_attempts WHERE dag_run_id = ?
            ORDER BY task_name, attempt_number
            """,
            (dag_run_id,),
        ).fetchall()
        return [_task_attempt_record(row) for row in rows]

    # -- Cross-DAG trigger reactions (schema_version 4, NXL-85) --------------

    def get_last_reacted_upstream_run_id(
        self, downstream_dag_name: str, upstream_dag_name: str
    ) -> int | None:
        """None means this (downstream, upstream) relationship has never
        triggered a run -- either it's new, or upstream has never
        completed successfully yet.
        """
        row = self._conn.execute(
            """
            SELECT last_reacted_run_id FROM dag_trigger_reactions
            WHERE downstream_dag_name = ? AND upstream_dag_name = ?
            """,
            (downstream_dag_name, upstream_dag_name),
        ).fetchone()
        return row["last_reacted_run_id"] if row is not None else None

    def record_trigger_reaction(
        self, downstream_dag_name: str, upstream_dag_name: str, upstream_run_id: int
    ) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO dag_trigger_reactions
                    (downstream_dag_name, upstream_dag_name, last_reacted_run_id)
                VALUES (?, ?, ?)
                ON CONFLICT(downstream_dag_name, upstream_dag_name) DO UPDATE SET
                    last_reacted_run_id = excluded.last_reacted_run_id
                """,
                (downstream_dag_name, upstream_dag_name, upstream_run_id),
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

    def list_recent_dag_runs(self, limit: int = 20) -> list[DagRunRecord]:
        """Most recent runs across every DAG, not filtered to one -- what
        `nexolith runs list` (no `--dag` filter) needs. Added for that CLI
        story rather than story 1's original set of queries; a plain,
        read-only addition to this module, not a schema change.
        """
        rows = self._conn.execute(
            "SELECT * FROM dag_runs ORDER BY id DESC LIMIT ?", (limit,)
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
        on_failure=row["on_failure"],
        severity=row["severity"],
        owner_pid=row["owner_pid"],
        owner_create_time_ns=row["owner_create_time_ns"],
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


def _task_attempt_record(row: sqlite3.Row) -> TaskAttemptRecord:
    return TaskAttemptRecord(
        id=row["id"],
        dag_run_id=row["dag_run_id"],
        task_name=row["task_name"],
        attempt_number=row["attempt_number"],
        status=TaskAttemptStatus(row["status"]),
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        error=row["error"],
    )
