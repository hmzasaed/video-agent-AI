"""SQLite persistence for analyses, workspaces, contacts, tasks, and emails.

Jobs still run in memory (``app.JOBS``) while they are in progress; a finished
analysis is written here so it survives pruning and restarts, and so the
workspace agent and meeting features have something durable to work with.

Every function opens its own short-lived connection, so this module is safe
to call from the Flask request threads and the analysis worker threads alike.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL DEFAULT 'video',
    source       TEXT NOT NULL,
    title        TEXT NOT NULL DEFAULT '',
    metadata     TEXT,
    transcript   TEXT NOT NULL DEFAULT '',
    summary      TEXT NOT NULL DEFAULT '',
    minutes      TEXT NOT NULL DEFAULT '',
    action_items TEXT NOT NULL DEFAULT '',
    decisions    TEXT NOT NULL DEFAULT '',
    questions    TEXT NOT NULL DEFAULT '',
    qa_ready     INTEGER NOT NULL DEFAULT 0,
    started_at   TEXT,
    finished_at  TEXT
);

CREATE TABLE IF NOT EXISTS workspaces (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    comparison  TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workspace_videos (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    analysis_id  TEXT NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    position     INTEGER NOT NULL,
    PRIMARY KEY (workspace_id, analysis_id)
);

CREATE TABLE IF NOT EXISTS contacts (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL,
    email    TEXT NOT NULL UNIQUE COLLATE NOCASE,
    aliases  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id TEXT NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    text        TEXT NOT NULL,
    owner_name  TEXT NOT NULL DEFAULT '',
    contact_id  INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    due         TEXT NOT NULL DEFAULT '',
    evidence    TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'proposed'
);

CREATE TABLE IF NOT EXISTS emails (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id TEXT NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    contact_id  INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    to_addr     TEXT NOT NULL,
    subject     TEXT NOT NULL,
    body        TEXT NOT NULL,
    task_ids    TEXT NOT NULL DEFAULT '[]',
    status      TEXT NOT NULL DEFAULT 'drafted',
    error       TEXT,
    created_at  TEXT NOT NULL,
    sent_at     TEXT
);
"""

ANALYSIS_FIELDS = (
    "kind", "source", "title", "metadata", "transcript", "summary", "minutes",
    "action_items", "decisions", "questions", "qa_ready", "started_at", "finished_at",
)

TASK_STATUSES = ("proposed", "approved", "drafted", "emailed", "failed", "dismissed", "done")


def _path() -> str:
    return os.getenv("DB_PATH", os.path.join("data", "app.db"))


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def connect():
    path = _path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with _lock, connect() as conn:
        conn.executescript(SCHEMA)


def _row(row) -> dict | None:
    return dict(row) if row is not None else None


# ── Analyses ─────────────────────────────────────────────────────────
def _decode_analysis(row) -> dict | None:
    data = _row(row)
    if data is None:
        return None
    data["metadata"] = json.loads(data["metadata"]) if data.get("metadata") else None
    data["qa_ready"] = bool(data["qa_ready"])
    return data


def save_analysis(analysis_id: str, **fields) -> None:
    """Insert or update an analysis. Unknown keys are ignored."""
    values = {key: fields[key] for key in ANALYSIS_FIELDS if key in fields}
    if "metadata" in values and not isinstance(values["metadata"], (str, type(None))):
        values["metadata"] = json.dumps(values["metadata"])
    if "qa_ready" in values:
        values["qa_ready"] = int(bool(values["qa_ready"]))
    if not values.get("title") and isinstance(fields.get("metadata"), dict):
        values["title"] = fields["metadata"].get("title") or ""

    columns = ["id", *values]
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{key}=excluded.{key}" for key in values) or "id=id"
    with connect() as conn:
        conn.execute(
            f"INSERT INTO analyses ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}",
            [analysis_id, *values.values()],
        )


def get_analysis(analysis_id: str) -> dict | None:
    with connect() as conn:
        return _decode_analysis(
            conn.execute("SELECT * FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
        )


def list_analyses(limit: int = 50) -> list[dict]:
    """Newest first, without the large text fields."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, kind, source, title, metadata, qa_ready, started_at, finished_at "
            "FROM analyses ORDER BY finished_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_decode_analysis(row) for row in rows]


# ── Workspaces ───────────────────────────────────────────────────────
def create_workspace(workspace_id: str, name: str, analysis_ids: list[str]) -> dict:
    with connect() as conn:
        conn.execute(
            "INSERT INTO workspaces (id, name, created_at) VALUES (?, ?, ?)",
            (workspace_id, name, now()),
        )
        for position, analysis_id in enumerate(analysis_ids):
            conn.execute(
                "INSERT OR IGNORE INTO workspace_videos VALUES (?, ?, ?)",
                (workspace_id, analysis_id, position),
            )
    return get_workspace(workspace_id)


def get_workspace(workspace_id: str) -> dict | None:
    with connect() as conn:
        workspace = _row(conn.execute(
            "SELECT * FROM workspaces WHERE id = ?", (workspace_id,)
        ).fetchone())
        if workspace is None:
            return None
        rows = conn.execute(
            "SELECT a.id, a.kind, a.source, a.title, a.metadata, a.summary, a.qa_ready "
            "FROM workspace_videos w JOIN analyses a ON a.id = w.analysis_id "
            "WHERE w.workspace_id = ? ORDER BY w.position",
            (workspace_id,),
        ).fetchall()
    workspace["videos"] = [_decode_analysis(row) for row in rows]
    return workspace


def list_workspaces() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT w.id, w.name, w.created_at, COUNT(v.analysis_id) AS video_count "
            "FROM workspaces w LEFT JOIN workspace_videos v ON v.workspace_id = w.id "
            "GROUP BY w.id ORDER BY w.created_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def add_workspace_video(workspace_id: str, analysis_id: str) -> None:
    with connect() as conn:
        position = conn.execute(
            "SELECT COALESCE(MAX(position) + 1, 0) FROM workspace_videos WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()[0]
        conn.execute(
            "INSERT OR IGNORE INTO workspace_videos VALUES (?, ?, ?)",
            (workspace_id, analysis_id, position),
        )
        conn.execute("UPDATE workspaces SET comparison = '' WHERE id = ?", (workspace_id,))


def remove_workspace_video(workspace_id: str, analysis_id: str) -> None:
    with connect() as conn:
        conn.execute(
            "DELETE FROM workspace_videos WHERE workspace_id = ? AND analysis_id = ?",
            (workspace_id, analysis_id),
        )
        conn.execute("UPDATE workspaces SET comparison = '' WHERE id = ?", (workspace_id,))


def set_comparison(workspace_id: str, comparison: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE workspaces SET comparison = ? WHERE id = ?", (comparison, workspace_id)
        )


def delete_workspace(workspace_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))


# ── Contacts ─────────────────────────────────────────────────────────
def list_contacts() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM contacts ORDER BY name COLLATE NOCASE").fetchall()
    return [dict(row) for row in rows]


def get_contact(contact_id: int) -> dict | None:
    with connect() as conn:
        return _row(conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone())


def create_contact(name: str, email: str, aliases: str = "") -> dict:
    """Raises ``sqlite3.IntegrityError`` when the email already exists."""
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO contacts (name, email, aliases) VALUES (?, ?, ?)",
            (name, email, aliases),
        )
        contact_id = cursor.lastrowid
    return get_contact(contact_id)


def update_contact(contact_id: int, **fields) -> dict | None:
    values = {key: fields[key] for key in ("name", "email", "aliases") if key in fields}
    if values:
        with connect() as conn:
            conn.execute(
                f"UPDATE contacts SET {', '.join(f'{key} = ?' for key in values)} WHERE id = ?",
                [*values.values(), contact_id],
            )
    return get_contact(contact_id)


def delete_contact(contact_id: int) -> bool:
    with connect() as conn:
        return conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,)).rowcount > 0


# ── Tasks ────────────────────────────────────────────────────────────
_TASK_SELECT = (
    "SELECT t.*, c.name AS contact_name, c.email AS contact_email "
    "FROM tasks t LEFT JOIN contacts c ON c.id = t.contact_id "
)


def replace_tasks(analysis_id: str, tasks: list[dict]) -> list[dict]:
    with connect() as conn:
        conn.execute("DELETE FROM tasks WHERE analysis_id = ?", (analysis_id,))
        for task in tasks:
            conn.execute(
                "INSERT INTO tasks (analysis_id, text, owner_name, contact_id, due, evidence) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (analysis_id, task["text"], task.get("owner_name", ""),
                 task.get("contact_id"), task.get("due", ""), task.get("evidence", "")),
            )
    return list_tasks(analysis_id)


def list_tasks(analysis_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            _TASK_SELECT + "WHERE t.analysis_id = ? ORDER BY t.id", (analysis_id,)
        ).fetchall()
    return [dict(row) for row in rows]


def get_task(task_id: int) -> dict | None:
    with connect() as conn:
        return _row(conn.execute(_TASK_SELECT + "WHERE t.id = ?", (task_id,)).fetchone())


def update_task(task_id: int, **fields) -> dict | None:
    allowed = ("text", "owner_name", "contact_id", "due", "evidence", "status")
    values = {key: fields[key] for key in allowed if key in fields}
    if values:
        with connect() as conn:
            conn.execute(
                f"UPDATE tasks SET {', '.join(f'{key} = ?' for key in values)} WHERE id = ?",
                [*values.values(), task_id],
            )
    return get_task(task_id)


def set_task_status(task_ids: list[int], status: str) -> None:
    if not task_ids:
        return
    with connect() as conn:
        conn.executemany(
            "UPDATE tasks SET status = ? WHERE id = ?", [(status, tid) for tid in task_ids]
        )


# ── Emails ───────────────────────────────────────────────────────────
def _decode_email(row) -> dict | None:
    data = _row(row)
    if data is not None:
        data["task_ids"] = json.loads(data["task_ids"] or "[]")
    return data


def create_email(analysis_id: str, contact_id: int, to_addr: str, subject: str,
                 body: str, task_ids: list[int]) -> dict:
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO emails (analysis_id, contact_id, to_addr, subject, body, task_ids, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (analysis_id, contact_id, to_addr, subject, body, json.dumps(task_ids), now()),
        )
        email_id = cursor.lastrowid
    return get_email(email_id)


def get_email(email_id: int) -> dict | None:
    with connect() as conn:
        return _decode_email(conn.execute("SELECT * FROM emails WHERE id = ?", (email_id,)).fetchone())


def list_emails(analysis_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM emails WHERE analysis_id = ? ORDER BY id", (analysis_id,)
        ).fetchall()
    return [_decode_email(row) for row in rows]


def update_email(email_id: int, **fields) -> dict | None:
    allowed = ("subject", "body", "status", "error", "sent_at")
    values = {key: fields[key] for key in allowed if key in fields}
    if values:
        with connect() as conn:
            conn.execute(
                f"UPDATE emails SET {', '.join(f'{key} = ?' for key in values)} WHERE id = ?",
                [*values.values(), email_id],
            )
    return get_email(email_id)


def delete_drafts(analysis_id: str, contact_id: int) -> None:
    """Drop unsent drafts for a person before re-drafting, so they don't pile up."""
    with connect() as conn:
        conn.execute(
            "DELETE FROM emails WHERE analysis_id = ? AND contact_id = ? AND status IN ('drafted', 'failed')",
            (analysis_id, contact_id),
        )
