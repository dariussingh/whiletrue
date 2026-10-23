"""Ordered, append-only schema migrations. Never edit a shipped migration; add a new one."""

MIGRATIONS: tuple[str, ...] = (
    """
  CREATE TABLE runs (
    id TEXT PRIMARY KEY,
    pipeline_hash TEXT NOT NULL,
    lockfile TEXT,
    status TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    ended_at INTEGER
  );

  CREATE TABLE items (
    run_id TEXT NOT NULL REFERENCES runs(id),
    id TEXT NOT NULL,
    source_key TEXT NOT NULL,
    state TEXT NOT NULL,
    payload TEXT,
    depends_on TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (run_id, id)
  );

  CREATE TABLE steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id),
    item_id TEXT NOT NULL,
    node_path TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    status TEXT NOT NULL,
    port TEXT,
    input TEXT,
    output TEXT,
    cost_usd REAL NOT NULL DEFAULT 0,
    error TEXT,
    started_at INTEGER NOT NULL,
    ended_at INTEGER
  );
  CREATE INDEX steps_by_item ON steps (run_id, item_id);

  CREATE TABLE parks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id),
    item_id TEXT NOT NULL,
    step_id INTEGER,
    wake TEXT,
    reason TEXT,
    notes TEXT,
    decided_by TEXT,
    decided_at INTEGER,
    created_at INTEGER NOT NULL
  );

  CREATE TABLE sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id),
    item_id TEXT,
    runner TEXT NOT NULL,
    session_id TEXT,
    scope TEXT,
    tmux_pane TEXT,
    status TEXT NOT NULL
  );

  CREATE TABLE effects (
    key TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    status TEXT NOT NULL,
    external_ref TEXT,
    created_at INTEGER NOT NULL
  );

  CREATE TABLE learnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    scope TEXT NOT NULL,
    note TEXT NOT NULL,
    provenance TEXT,
    created_at INTEGER NOT NULL
  );

  CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    item_id TEXT,
    step_id INTEGER,
    type TEXT NOT NULL,
    data TEXT NOT NULL,
    ts INTEGER NOT NULL
  );
  CREATE INDEX events_by_run ON events (run_id, id);

  CREATE TABLE cache (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    created_at INTEGER NOT NULL
  );
  """,
    """
  ALTER TABLE parks ADD COLUMN node_path TEXT NOT NULL DEFAULT '';
  ALTER TABLE parks ADD COLUMN what TEXT NOT NULL DEFAULT '';
  ALTER TABLE parks ADD COLUMN decision TEXT;
  CREATE INDEX parks_by_item ON parks (item_id, node_path, run_id);

  ALTER TABLE learnings ADD COLUMN status TEXT NOT NULL DEFAULT 'approved';

  CREATE TABLE comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    thread TEXT,
    author TEXT NOT NULL,
    body TEXT NOT NULL,
    url TEXT,
    kind TEXT NOT NULL DEFAULT 'human',
    answered INTEGER NOT NULL DEFAULT 0,
    reply TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE (item_id, source, external_id)
  );

  CREATE TABLE steers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    step TEXT NOT NULL,
    prompt TEXT NOT NULL,
    created_at INTEGER NOT NULL
  );
  """,
)
