"""SQLite persistence: pipeline runs, the activity log, and the content-angle
library the researcher fills ("your database" of winning angles)."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from .models import ContentAngle, RunState, now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    agent TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS content_angles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    title TEXT NOT NULL,
    hook TEXT NOT NULL,
    virality_score INTEGER NOT NULL,
    selected INTEGER NOT NULL DEFAULT 0,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_run ON events(run_id, id);
"""


class Store:
    def __init__(self, path: Path) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)

    def save_run(self, run: RunState) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO runs(id, created_at, status, state) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET status=excluded.status, state=excluded.state",
                (run.id, run.created_at, run.status, run.model_dump_json()),
            )

    def get_run(self, run_id: str) -> RunState | None:
        with self._lock:
            row = self._conn.execute("SELECT state FROM runs WHERE id = ?", (run_id,)).fetchone()
        return RunState.model_validate_json(row["state"]) if row else None

    def list_runs(self, limit: int = 50) -> list[RunState]:
        with self._lock:
            rows = self._conn.execute("SELECT state FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [RunState.model_validate_json(r["state"]) for r in rows]

    def runs_with_status(self, status: str) -> list[RunState]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT state FROM runs WHERE status = ? ORDER BY created_at", (status,)
            ).fetchall()
        return [RunState.model_validate_json(r["state"]) for r in rows]

    def log(self, run_id: str, agent: str, message: str, level: str = "info") -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO events(run_id, ts, agent, level, message) VALUES (?, ?, ?, ?, ?)",
                (run_id, now_iso(), agent, level, message),
            )

    def events(self, run_id: str, after_id: int = 0) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, ts, agent, level, message FROM events WHERE run_id = ? AND id > ? ORDER BY id",
                (run_id, after_id),
            ).fetchall()
        return [dict(r) for r in rows]

    def save_angles(self, run_id: str, angles: list[ContentAngle]) -> None:
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT INTO content_angles(run_id, created_at, title, hook, virality_score, data) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(run_id, now_iso(), a.title, a.hook, a.virality_score, a.model_dump_json()) for a in angles],
            )

    def mark_angle_selected(self, run_id: str, title: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE content_angles SET selected = 1 WHERE run_id = ? AND title = ?", (run_id, title)
            )

    def angle_library(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT run_id, created_at, title, hook, virality_score, selected, data FROM content_angles "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [{**dict(r), "data": json.loads(r["data"])} for r in rows]
