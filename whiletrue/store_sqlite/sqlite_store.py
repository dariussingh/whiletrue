from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Literal

from whiletrue.core.events import WtEvent
from whiletrue.core.history import (
    CommentRecord,
    Decision,
    EffectRecord,
    LearningRecord,
    LearningStatus,
    ParkRecord,
    SteerRecord,
)
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
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "created_at = excluded.created_at",
            (key, json.dumps(value), created_at),
        )

    # ---- human input and side effects ----

    @staticmethod
    def _park_row(r: sqlite3.Row) -> ParkRecord:
        return ParkRecord(
            id=r["id"],
            run_id=r["run_id"],
            item_id=r["item_id"],
            node_path=r["node_path"],
            what=r["what"],
            wake=_load(r["wake"]) or {},
            reason=r["reason"] or "",
            created_at=r["created_at"],
            decision=r["decision"],
            notes=r["notes"],
            decided_by=r["decided_by"],
            decided_at=r["decided_at"],
        )

    def create_park(
        self,
        *,
        run_id: str,
        item_id: str,
        node_path: str,
        what: str,
        wake: dict[str, Any],
        reason: str,
        created_at: int,
    ) -> ParkRecord:
        cursor = self._db.execute(
            """INSERT INTO parks (run_id, item_id, node_path, what, wake, reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (run_id, item_id, node_path, what, json.dumps(wake), reason, created_at),
        )
        return self._park_by_id(cursor.lastrowid or 0)

    def _park_by_id(self, park_id: int) -> ParkRecord:
        row = self._db.execute("SELECT * FROM parks WHERE id = ?", (park_id,)).fetchone()
        if row is None:
            raise KeyError(f"No park {park_id}")
        return self._park_row(row)

    def decide_park(
        self,
        park_id: int,
        decision: Decision,
        *,
        decided_by: str,
        decided_at: int,
        notes: str | None,
    ) -> None:
        cursor = self._db.execute(
            "UPDATE parks SET decision = ?, decided_by = ?, decided_at = ?, notes = ? WHERE id = ?",
            (decision, decided_by, decided_at, notes, park_id),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"No park {park_id}")

    def list_parks(
        self,
        item_id: str | None = None,
        *,
        node_path: str | None = None,
        run_id: str | None = None,
        undecided_only: bool = False,
    ) -> list[ParkRecord]:
        where: list[str] = []
        args: list[Any] = []
        for column, value in (("item_id", item_id), ("node_path", node_path), ("run_id", run_id)):
            if value is not None:
                where.append(f"{column} = ?")
                args.append(value)
        if undecided_only:
            where.append("decision IS NULL")
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = self._db.execute(f"SELECT * FROM parks {clause} ORDER BY id", args).fetchall()
        return [self._park_row(r) for r in rows]

    @staticmethod
    def _comment_row(r: sqlite3.Row) -> CommentRecord:
        return CommentRecord(
            id=r["id"],
            item_id=r["item_id"],
            source=r["source"],
            external_id=r["external_id"],
            author=r["author"],
            body=r["body"],
            created_at=r["created_at"],
            thread=r["thread"],
            url=r["url"],
            kind=r["kind"],
            answered=bool(r["answered"]),
            reply=r["reply"],
        )

    def add_comment(
        self,
        *,
        item_id: str,
        source: str,
        external_id: str,
        author: str,
        body: str,
        created_at: int,
        thread: str | None = None,
        url: str | None = None,
        kind: Literal["human", "reviewer"] = "human",
    ) -> CommentRecord | None:
        try:
            cursor = self._db.execute(
                """INSERT INTO comments
                   (item_id, source, external_id, thread, author, body, url, kind, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (item_id, source, external_id, thread, author, body, url, kind, created_at),
            )
        except sqlite3.IntegrityError:
            return None
        row = self._db.execute(
            "SELECT * FROM comments WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return self._comment_row(row)

    def list_comments(self, item_id: str, *, unanswered_only: bool = False) -> list[CommentRecord]:
        extra = " AND answered = 0" if unanswered_only else ""
        rows = self._db.execute(
            f"SELECT * FROM comments WHERE item_id = ?{extra} ORDER BY id", (item_id,)
        ).fetchall()
        return [self._comment_row(r) for r in rows]

    def answer_comment(self, comment_id: int, reply: str) -> None:
        cursor = self._db.execute(
            "UPDATE comments SET answered = 1, reply = ? WHERE id = ?", (reply, comment_id)
        )
        if cursor.rowcount == 0:
            raise KeyError(f"No comment {comment_id}")

    def add_steer(
        self, *, run_id: str, item_id: str, step: str, prompt: str, created_at: int
    ) -> SteerRecord:
        cursor = self._db.execute(
            "INSERT INTO steers (run_id, item_id, step, prompt, created_at) VALUES (?, ?, ?, ?, ?)",
            (run_id, item_id, step, prompt, created_at),
        )
        return SteerRecord(cursor.lastrowid or 0, run_id, item_id, step, prompt, created_at)

    def list_steers(self, item_id: str) -> list[SteerRecord]:
        rows = self._db.execute(
            "SELECT * FROM steers WHERE item_id = ? ORDER BY id", (item_id,)
        ).fetchall()
        return [
            SteerRecord(r["id"], r["run_id"], r["item_id"], r["step"], r["prompt"], r["created_at"])
            for r in rows
        ]

    def effect_get(self, key: str) -> EffectRecord | None:
        row = self._db.execute("SELECT * FROM effects WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        return EffectRecord(
            row["key"], row["status"], row["run_id"], row["created_at"], _load(row["external_ref"])
        )

    def effect_put(
        self,
        key: str,
        *,
        run_id: str,
        status: Literal["started", "done"],
        ref: Any,
        created_at: int,
    ) -> None:
        self._db.execute(
            """INSERT INTO effects (key, run_id, status, external_ref, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET run_id = excluded.run_id, status = excluded.status,
                 external_ref = excluded.external_ref""",
            (key, run_id, status, _dump(ref), created_at),
        )

    @staticmethod
    def _learning_row(r: sqlite3.Row) -> LearningRecord:
        return LearningRecord(
            id=r["id"],
            scope=r["scope"],
            note=r["note"],
            status=r["status"],
            created_at=r["created_at"],
            run_id=r["run_id"],
            provenance=_load(r["provenance"]) or {},
        )

    def add_learning(
        self,
        *,
        scope: str,
        note: str,
        status: LearningStatus,
        created_at: int,
        run_id: str | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> LearningRecord:
        cursor = self._db.execute(
            """INSERT INTO learnings (run_id, scope, note, provenance, created_at, status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (run_id, scope, note, _dump(provenance or {}), created_at, status),
        )
        row = self._db.execute(
            "SELECT * FROM learnings WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return self._learning_row(row)

    def list_learnings(
        self, *, scope: str | None = None, status: LearningStatus | None = None
    ) -> list[LearningRecord]:
        where: list[str] = []
        args: list[Any] = []
        if scope is not None:
            where.append("scope = ?")
            args.append(scope)
        if status is not None:
            where.append("status = ?")
            args.append(status)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = self._db.execute(f"SELECT * FROM learnings {clause} ORDER BY id", args).fetchall()
        return [self._learning_row(r) for r in rows]

    def set_learning_status(self, learning_id: int, status: LearningStatus) -> None:
        cursor = self._db.execute(
            "UPDATE learnings SET status = ? WHERE id = ?", (status, learning_id)
        )
        if cursor.rowcount == 0:
            raise KeyError(f"No learning {learning_id}")

    def close(self) -> None:
        self._db.close()


def _split_statements(sql: str) -> list[str]:
    """Splits a migration script into statements (our migrations contain no `;` in literals)."""
    return [s.strip() for s in sql.split(";") if s.strip()]
