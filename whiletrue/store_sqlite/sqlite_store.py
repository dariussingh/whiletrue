from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Literal

from whiletrue.core.events import WtEvent
from whiletrue.core.outcomes import ItemState
from whiletrue.core.store import ItemRecord, RunRecord, StepRecord
from whiletrue.store_sqlite.migrations import MIGRATIONS


def _dump(value: Any) -> str | None:
    return None if value is None else json.dumps(value)


def _load(text: str | None) -> Any:
    return None if text is None else json.loads(text)


class SqliteStore:
    """`StateStore` backed by SQLite (stdlib `sqlite3`)."""

    def __init__(self, path: str | Path) -> None:
        """`path` may be ":memory:". Parent directories are created for file databases."""
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: autocommit, so migrations manage their own transactions.
        self._db = sqlite3.connect(str(path), isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.executescript("PRAGMA journal_mode = WAL; PRAGMA foreign_keys = ON;")
        self._migrate()

    def _migrate(self) -> None:
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)"
        )
        current = self.schema_version()
        for index, sql in enumerate(MIGRATIONS):
            version = index + 1
            if version <= current:
                continue
            try:
                self._db.execute("BEGIN")
                for statement in _split_statements(sql):
                    self._db.execute(statement)
                self._db.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def schema_version(self) -> int:
        row = self._db.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
        return int(row["v"] or 0)

    def tables(self) -> list[str]:
        rows = self._db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        ).fetchall()
        return [r["name"] for r in rows]

    # ---- runs ----

    def create_run(
        self, *, id: str, pipeline_hash: str, started_at: int, lockfile: str | None = None
    ) -> None:
        self._db.execute(
            "INSERT INTO runs (id, pipeline_hash, lockfile, status, started_at) "
            "VALUES (?, ?, ?, 'running', ?)",
            (id, pipeline_hash, lockfile, started_at),
        )

    def finish_run(
        self, id: str, status: Literal["done", "failed", "parked", "cancelled"], ended_at: int
    ) -> None:
        self._db.execute(
            "UPDATE runs SET status = ?, ended_at = ? WHERE id = ?", (status, ended_at, id)
        )

    @staticmethod
    def _run(row: sqlite3.Row | None) -> RunRecord | None:
        if row is None:
            return None
        return RunRecord(
            id=row["id"],
            pipeline_hash=row["pipeline_hash"],
            lockfile=row["lockfile"],
            status=row["status"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
        )

    def get_run(self, id: str) -> RunRecord | None:
        return self._run(self._db.execute("SELECT * FROM runs WHERE id = ?", (id,)).fetchone())

    def latest_run(self) -> RunRecord | None:
        return self._run(
            self._db.execute(
                "SELECT * FROM runs ORDER BY started_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
        )

    # ---- items ----

    def upsert_item(self, item: ItemRecord) -> None:
        self._db.execute(
            """INSERT INTO items (run_id, id, source_key, state, payload, depends_on)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (run_id, id) DO UPDATE SET
                 source_key = excluded.source_key, state = excluded.state,
                 payload = excluded.payload, depends_on = excluded.depends_on""",
            (
                item.run_id,
                item.id,
                item.source_key,
                item.state,
                _dump(item.payload),
                json.dumps(item.depends_on),
            ),
        )

    def set_item_state(self, run_id: str, item_id: str, state: ItemState) -> None:
        cursor = self._db.execute(
            "UPDATE items SET state = ? WHERE run_id = ? AND id = ?", (state, run_id, item_id)
        )
        if cursor.rowcount == 0:
            raise KeyError(f'No item "{item_id}" in run "{run_id}"')

    def list_items(self, run_id: str) -> list[ItemRecord]:
        rows = self._db.execute(
            "SELECT * FROM items WHERE run_id = ? ORDER BY rowid", (run_id,)
        ).fetchall()
        return [
            ItemRecord(
                id=r["id"],
                run_id=r["run_id"],
                source_key=r["source_key"],
                state=r["state"],
                payload=_load(r["payload"]),
                depends_on=json.loads(r["depends_on"]),
            )
            for r in rows
        ]

    # ---- steps ----

    def start_step(
        self,
        *,
        run_id: str,
        item_id: str,
        node_path: str,
        attempt: int,
        input: Any,
        started_at: int,
    ) -> int:
        cursor = self._db.execute(
            """INSERT INTO steps (run_id, item_id, node_path, attempt, status, input, started_at)
               VALUES (?, ?, ?, ?, 'running', ?, ?)""",
            (run_id, item_id, node_path, attempt, _dump(input), started_at),
        )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def finish_step(
        self,
        step_id: int,
        *,
        status: Literal["completed", "errored"],
        ended_at: int,
        port: str | None = None,
        output: Any = None,
        cost_usd: float = 0,
        error: str | None = None,
    ) -> None:
        cursor = self._db.execute(
            """UPDATE steps SET status = ?, port = ?, output = ?, cost_usd = ?, error = ?,
                 ended_at = ? WHERE id = ?""",
            (status, port, _dump(output), cost_usd, error, ended_at, step_id),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"No step {step_id}")

    def list_steps(self, run_id: str, item_id: str | None = None) -> list[StepRecord]:
        if item_id is None:
            rows = self._db.execute(
                "SELECT * FROM steps WHERE run_id = ? ORDER BY id", (run_id,)
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM steps WHERE run_id = ? AND item_id = ? ORDER BY id",
                (run_id, item_id),
            ).fetchall()
        return [
            StepRecord(
                id=r["id"],
                run_id=r["run_id"],
                item_id=r["item_id"],
                node_path=r["node_path"],
                attempt=r["attempt"],
                status=r["status"],
                port=r["port"],
                input=_load(r["input"]),
                output=_load(r["output"]),
                cost_usd=r["cost_usd"],
                error=r["error"],
                started_at=r["started_at"],
                ended_at=r["ended_at"],
            )
            for r in rows
        ]

    # ---- events ----

    def append_event(self, event: WtEvent) -> None:
        self._db.execute(
            "INSERT INTO events (run_id, item_id, step_id, type, data, ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                event.run_id,
                event.item_id,
                event.step_id,
                event.type,
                json.dumps(event.data),
                event.ts,
            ),
        )

    def list_events(
        self, run_id: str, *, item_id: str | None = None, type: str | None = None
    ) -> list[WtEvent]:
        where = ["run_id = ?"]
        args: list[Any] = [run_id]
        if item_id is not None:
            where.append("item_id = ?")
            args.append(item_id)
        if type is not None:
            where.append("type = ?")
            args.append(type)
        rows = self._db.execute(
            f"SELECT * FROM events WHERE {' AND '.join(where)} ORDER BY id", args
        ).fetchall()
        return [
            WtEvent(
                type=r["type"],
                run_id=r["run_id"],
                item_id=r["item_id"],
                step_id=r["step_id"],
                ts=r["ts"],
                data=json.loads(r["data"]),
            )
            for r in rows
        ]

    def cache_get(self, key: str) -> Any | None:
        row = self._db.execute("SELECT value FROM cache WHERE key = ?", (key,)).fetchone()
        return None if row is None else json.loads(row["value"])

    def cache_put(self, key: str, value: Any, created_at: int) -> None:
        self._db.execute(
            "INSERT INTO cache (key, value, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, created_at = excluded.created_at",
            (key, json.dumps(value), created_at),
        )

    def close(self) -> None:
        self._db.close()


def _split_statements(sql: str) -> list[str]:
    """Splits a migration script into statements (our migrations contain no `;` in literals)."""
    return [s.strip() for s in sql.split(";") if s.strip()]
