"""SQLite storage for conversations, long-term memory, reminders and the audit log."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage, messages_from_dict, messages_to_dict

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, title TEXT, created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
CREATE TABLE IF NOT EXISTS facts (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, created TEXT);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, due TEXT NOT NULL, fired INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, agent TEXT, tool TEXT, scope TEXT,
    risk TEXT, outcome TEXT, description TEXT
);
CREATE TABLE IF NOT EXISTS captures (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, source TEXT, page TEXT, method TEXT, url TEXT,
    host TEXT, status INTEGER, mime TEXT, duration_ms REAL, size INTEGER, error TEXT,
    request_headers TEXT, response_headers TEXT, request_body TEXT, response_body TEXT
);
CREATE INDEX IF NOT EXISTS idx_captures_host ON captures(host);
"""

CAPTURE_FIELDS = (
    "at", "source", "page", "method", "url", "host", "status", "mime", "duration_ms", "size", "error",
    "request_headers", "response_headers", "request_body", "response_body",
)  # fmt: skip


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(SCHEMA)

    def _write(self, sql: str, params: tuple = ()) -> int:
        with self._lock, self._db:
            return self._db.execute(sql, params).lastrowid or 0

    def _read(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._db.execute(sql, params).fetchall()]

    # Sessions
    def create_session(self, title: str = "New chat") -> str:
        session_id = uuid.uuid4().hex
        self._write("INSERT INTO sessions VALUES (?, ?, ?, ?)", (session_id, title[:80], now(), now()))
        return session_id

    def session_exists(self, session_id: str) -> bool:
        return bool(self._read("SELECT 1 FROM sessions WHERE id = ?", (session_id,)))

    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._read("SELECT * FROM sessions ORDER BY updated DESC LIMIT ?", (limit,))

    def delete_session(self, session_id: str) -> None:
        self._write("DELETE FROM sessions WHERE id = ?", (session_id,))

    def append_messages(self, session_id: str, messages: list[BaseMessage]) -> None:
        with self._lock, self._db:
            self._db.executemany(
                "INSERT INTO messages (session_id, body) VALUES (?, ?)",
                [(session_id, json.dumps(item)) for item in messages_to_dict(messages)],
            )
            self._db.execute("UPDATE sessions SET updated = ? WHERE id = ?", (now(), session_id))

    def load_messages(self, session_id: str) -> list[BaseMessage]:
        rows = self._read("SELECT body FROM messages WHERE session_id = ? ORDER BY id", (session_id,))
        return messages_from_dict([json.loads(row["body"]) for row in rows])

    # Long-term memory
    def add_fact(self, text: str) -> int:
        return self._write("INSERT INTO facts (text, created) VALUES (?, ?)", (text, now()))

    def recent_facts(self, limit: int = 30) -> list[dict[str, Any]]:
        return self._read("SELECT * FROM facts ORDER BY id DESC LIMIT ?", (limit,))

    def search_facts(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        words = [w.replace("%", "").replace("_", "") for w in query.split() if w.strip()]
        if not words:
            return self.recent_facts(limit)
        where = " OR ".join("text LIKE ?" for _ in words)
        return self._read(
            f"SELECT * FROM facts WHERE {where} ORDER BY id DESC LIMIT ?",
            (*[f"%{w}%" for w in words], limit),
        )

    def delete_fact(self, fact_id: int) -> None:
        self._write("DELETE FROM facts WHERE id = ?", (fact_id,))

    # Reminders
    def add_reminder(self, text: str, due: datetime) -> int:
        return self._write("INSERT INTO reminders (text, due) VALUES (?, ?)", (text, due.isoformat(timespec="seconds")))

    def pending_reminders(self) -> list[dict[str, Any]]:
        return self._read("SELECT * FROM reminders WHERE fired = 0 ORDER BY due")

    def due_reminders(self) -> list[dict[str, Any]]:
        return self._read("SELECT * FROM reminders WHERE fired = 0 AND due <= ? ORDER BY due", (now(),))

    def mark_reminder_fired(self, reminder_id: int) -> None:
        self._write("UPDATE reminders SET fired = 1 WHERE id = ?", (reminder_id,))

    def delete_reminder(self, reminder_id: int) -> None:
        self._write("DELETE FROM reminders WHERE id = ?", (reminder_id,))

    # Audit log
    def audit(self, agent: str, tool: str, scope: str, risk: str, outcome: str, description: str) -> None:
        self._write(
            "INSERT INTO audit (at, agent, tool, scope, risk, outcome, description) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (now(), agent, tool, scope, risk, outcome, description[:2000]),
        )

    def audit_log(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._read("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))

    # Network captures, sent in by a browser extension or imported from a HAR file
    def add_captures(self, rows: list[dict[str, Any]], keep: int) -> int:
        """Store already-cleaned rows, then drop the oldest beyond `keep`."""
        with self._lock, self._db:
            self._db.executemany(
                f"INSERT INTO captures ({', '.join(CAPTURE_FIELDS)}) VALUES ({', '.join('?' for _ in CAPTURE_FIELDS)})",
                [tuple(row.get(field) for field in CAPTURE_FIELDS) for row in rows],
            )
            self._db.execute(
                "DELETE FROM captures WHERE id <= (SELECT id FROM captures ORDER BY id DESC LIMIT 1 OFFSET ?)", (keep,)
            )
        return len(rows)

    def list_captures(
        self, url_contains: str = "", method: str = "", min_status: int = 0, limit: int = 50, with_bodies: bool = False
    ) -> list[dict[str, Any]]:
        columns = "*" if with_bodies else ", ".join(["id", *CAPTURE_FIELDS[:11]])
        where, params = ["1 = 1"], []
        if url_contains:
            where.append("url LIKE ?")
            params.append(f"%{url_contains}%")
        if method:
            where.append("method = ?")
            params.append(method.upper())
        if min_status:
            where.append("(status >= ? OR COALESCE(error, '') != '')")
            params.append(min_status)
        rows = self._read(
            f"SELECT {columns} FROM captures WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?", (*params, limit)
        )
        return rows[::-1]  # oldest first, the order they happened

    def get_capture(self, capture_id: int) -> dict[str, Any] | None:
        rows = self._read("SELECT * FROM captures WHERE id = ?", (capture_id,))
        return rows[0] if rows else None

    def count_captures(self) -> int:
        return self._read("SELECT COUNT(*) AS n FROM captures")[0]["n"]

    def clear_captures(self) -> None:
        self._write("DELETE FROM captures")

    # Bulk clearing, for the reset controls in the GUI. Each one is independent.
    def clear_sessions(self) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM messages")
            self._db.execute("DELETE FROM sessions")

    def clear_facts(self) -> None:
        self._write("DELETE FROM facts")

    def clear_reminders(self) -> None:
        self._write("DELETE FROM reminders")

    def clear_audit(self) -> None:
        self._write("DELETE FROM audit")


_stores: dict[Path, Store] = {}
_stores_lock = threading.Lock()


def get_store() -> Store:
    from fox.config import get_settings

    path = get_settings().fox_data_dir / "fox.db"
    with _stores_lock:
        if path not in _stores:
            _stores[path] = Store(path)
        return _stores[path]
