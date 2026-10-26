"""Database persistence layer: supports Neon Cloud PostgreSQL and SQLite."""
import json
import os
from pathlib import Path
import threading

import psycopg2
from psycopg2.extras import RealDictCursor
import sqlite3

from app.parse import Symbol

PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS repos (
    id VARCHAR(64) PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    name VARCHAR(255) NOT NULL,
    source_url VARCHAR(500) NOT NULL,
    status VARCHAR(50) NOT NULL,
    error TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS files (
    id SERIAL PRIMARY KEY,
    repo_id VARCHAR(64) NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    language VARCHAR(50) NOT NULL,
    size INTEGER NOT NULL,
    summary TEXT,
    outgoing_routes JSONB,
    incoming_routes JSONB,
    external_dependencies JSONB,
    how_routed JSONB,
    content TEXT,
    UNIQUE(repo_id, path)
);
CREATE TABLE IF NOT EXISTS symbols (
    id SERIAL PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    kind VARCHAR(50) NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    called_by JSONB
);
"""

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    hashed_password TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS repos (
    id TEXT PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id TEXT NOT NULL REFERENCES repos(id),
    path TEXT NOT NULL,
    language TEXT NOT NULL,
    size INTEGER NOT NULL,
    summary TEXT,
    outgoing_routes TEXT,
    incoming_routes TEXT,
    external_dependencies TEXT,
    how_routed TEXT,
    content TEXT,
    UNIQUE(repo_id, path)
);
CREATE TABLE IF NOT EXISTS symbols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES files(id),
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    called_by TEXT
);
"""


class Store:
    def __init__(self, db_url_or_path: str):
        self.db_url = db_url_or_path
        self.is_pg = db_url_or_path.startswith("postgres://") or db_url_or_path.startswith("postgresql://")
        self._lock = threading.Lock()

        if self.is_pg:
            self._init_pg()
        else:
            self._init_sqlite()

    def _init_pg(self):
        # Allow multi-threaded apps by opening new connections or thread-safe pooling
        self.conn = psycopg2.connect(self.db_url)
        self.conn.autocommit = True
        with self.conn.cursor() as cur:
            for stmt in PG_SCHEMA.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    cur.execute(stmt)

    def _init_sqlite(self):
        self.conn = sqlite3.connect(self.db_url, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.executescript(SQLITE_SCHEMA)
            self._upgrade_sqlite_schema()
            self.conn.commit()

    def _upgrade_sqlite_schema(self):
        file_cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(files)").fetchall()}
        new_cols = [
            ("summary", "TEXT"),
            ("outgoing_routes", "TEXT"),
            ("incoming_routes", "TEXT"),
            ("external_dependencies", "TEXT"),
            ("how_routed", "TEXT"),
            ("content", "TEXT"),
        ]
        for col, col_type in new_cols:
            if col not in file_cols:
                self.conn.execute(f"ALTER TABLE files ADD COLUMN {col} {col_type}")

        sym_cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(symbols)").fetchall()}
        if "called_by" not in sym_cols:
            self.conn.execute("ALTER TABLE symbols ADD COLUMN called_by TEXT")

        repo_cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(repos)").fetchall()}
        if "user_id" not in repo_cols:
            self.conn.execute("ALTER TABLE repos ADD COLUMN user_id INTEGER")

    def _ensure_pg_connection(self):
        if not self.is_pg:
            return
        needs_reconnect = False
        if getattr(self, "conn", None) is None or self.conn.closed != 0:
            needs_reconnect = True
        else:
            try:
                with self.conn.cursor() as cur:
                    cur.execute("SELECT 1;")
            except (psycopg2.OperationalError, psycopg2.InterfaceError, Exception):
                needs_reconnect = True

        if needs_reconnect:
            try:
                if getattr(self, "conn", None) and self.conn.closed == 0:
                    self.conn.close()
            except Exception:
                pass
            self.conn = psycopg2.connect(self.db_url)
            self.conn.autocommit = True

    def _execute(self, sql: str, params=()):
        with self._lock:
            if self.is_pg:
                self._ensure_pg_connection()
                pg_sql = sql.replace("?", "%s")
                try:
                    cur = self.conn.cursor(cursor_factory=RealDictCursor)
                    cur.execute(pg_sql, params)
                    return cur
                except (psycopg2.OperationalError, psycopg2.InterfaceError):
                    self._ensure_pg_connection()
                    cur = self.conn.cursor(cursor_factory=RealDictCursor)
                    cur.execute(pg_sql, params)
                    return cur
            else:
                cur = self.conn.execute(sql, params)
                self.conn.commit()
                return cur

    # User Management
    def create_user(self, email: str, hashed_password: str) -> dict:
        cur = self._execute(
            "INSERT INTO users (email, hashed_password) VALUES (?, ?)",
            (email.lower().strip(), hashed_password),
        )
        user_row = self._execute("SELECT id, email, created_at FROM users WHERE email = ?", (email.lower().strip(),)).fetchone()
        return dict(user_row)

    def get_user_by_email(self, email: str) -> dict | None:
        row = self._execute("SELECT * FROM users WHERE email = ?", (email.lower().strip(),)).fetchone()
        return dict(row) if row else None

    def get_user_by_id(self, user_id: int) -> dict | None:
        row = self._execute("SELECT id, email, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    # Repo Management
    def create_repo(self, repo_id: str, name: str, source_url: str, status: str, user_id: int | None = None) -> None:
        self._execute(
            "INSERT INTO repos (id, name, source_url, status, user_id) VALUES (?, ?, ?, ?, ?)",
            (repo_id, name, source_url, status, user_id),
        )

    def set_status(self, repo_id: str, status: str, error: str | None = None) -> None:
        self._execute("UPDATE repos SET status = ?, error = ? WHERE id = ?", (status, error, repo_id))

    def get_repo(self, repo_id: str) -> dict | None:
        row = self._execute(
            """SELECT r.*, (SELECT COUNT(*) FROM files f WHERE f.repo_id = r.id) AS file_count
               FROM repos r WHERE r.id = ?""",
            (repo_id,),
        ).fetchone()
        return dict(row) if row else None

    def find_existing_repo(self, name: str, source_url: str) -> dict | None:
        """Find an existing repository (completed or in-progress) by name or source URL."""
        clean_name = name.lower().strip()
        clean_url = source_url.lower().strip()
        row = self._execute(
            """SELECT r.*, (SELECT COUNT(*) FROM files f WHERE f.repo_id = r.id) AS file_count
               FROM repos r
               WHERE (LOWER(r.name) = ? OR LOWER(r.source_url) = ?)
                 AND r.status IN ('done', 'processing', 'downloading', 'queued')
               ORDER BY (CASE WHEN r.status = 'done' THEN 1 ELSE 2 END) ASC,
                        (SELECT COUNT(*) FROM files f WHERE f.repo_id = r.id) DESC,
                        r.created_at DESC
               LIMIT 1""",
            (clean_name, clean_url),
        ).fetchone()
        return dict(row) if row else None

    def link_repo_to_user(self, repo_id: str, user_id: int) -> None:
        """Associate a repository with a user account."""
        self._execute("UPDATE repos SET user_id = ? WHERE id = ?", (user_id, repo_id))

    def list_user_repos(self, user_id: int) -> list[dict]:
        rows = self._execute(
            """SELECT r.*, (SELECT COUNT(*) FROM files f WHERE f.repo_id = r.id) AS file_count
               FROM repos r WHERE r.user_id = ? ORDER BY r.created_at DESC""",
            (user_id,),
        ).fetchall()
        seen = set()
        deduped = []
        for r in rows:
            d = dict(r)
            norm = (d.get("name") or "").lower().strip()
            if norm not in seen:
                seen.add(norm)
                deduped.append(d)
        return deduped

    # File & Symbol Management
    def add_file(self, repo_id: str, path: str, language: str, size: int, content: str | None = None) -> int:
        self._execute(
            "INSERT INTO files (repo_id, path, language, size, content) VALUES (?, ?, ?, ?, ?)",
            (repo_id, path, language, size, content),
        )
        row = self._execute("SELECT id FROM files WHERE repo_id = ? AND path = ?", (repo_id, path)).fetchone()
        return row["id"]

    def update_file_analysis(
        self,
        repo_id: str,
        path: str,
        summary: str,
        outgoing_routes: list[dict],
        incoming_routes: list[dict],
        external_dependencies: list[str],
        how_routed: dict,
    ) -> None:
        if self.is_pg:
            # Pass as python objects/json strings for JSONB
            self._execute(
                """UPDATE files
                   SET summary = ?,
                       outgoing_routes = ?,
                       incoming_routes = ?,
                       external_dependencies = ?,
                       how_routed = ?
                   WHERE repo_id = ? AND path = ?""",
                (
                    summary,
                    json.dumps(outgoing_routes),
                    json.dumps(incoming_routes),
                    json.dumps(external_dependencies),
                    json.dumps(how_routed),
                    repo_id,
                    path,
                ),
            )
        else:
            self._execute(
                """UPDATE files
                   SET summary = ?,
                       outgoing_routes = ?,
                       incoming_routes = ?,
                       external_dependencies = ?,
                       how_routed = ?
                   WHERE repo_id = ? AND path = ?""",
                (
                    summary,
                    json.dumps(outgoing_routes),
                    json.dumps(incoming_routes),
                    json.dumps(external_dependencies),
                    json.dumps(how_routed),
                    repo_id,
                    path,
                ),
            )

    def add_symbols(self, file_id: int, symbols: list[Symbol]) -> None:
        with self._lock:
            if self.is_pg:
                self._ensure_pg_connection()
            for s in symbols:
                called_json = json.dumps(s.called_by) if s.called_by else "[]"
                if self.is_pg:
                    try:
                        cur = self.conn.cursor()
                        cur.execute(
                            """INSERT INTO symbols (file_id, name, kind, start_line, end_line, called_by)
                               VALUES (%s, %s, %s, %s, %s, %s)""",
                            (file_id, s.name, s.kind, s.start_line, s.end_line, called_json),
                        )
                    except (psycopg2.OperationalError, psycopg2.InterfaceError):
                        self._ensure_pg_connection()
                        cur = self.conn.cursor()
                        cur.execute(
                            """INSERT INTO symbols (file_id, name, kind, start_line, end_line, called_by)
                               VALUES (%s, %s, %s, %s, %s, %s)""",
                            (file_id, s.name, s.kind, s.start_line, s.end_line, called_json),
                        )
                else:
                    self.conn.execute(
                        """INSERT INTO symbols (file_id, name, kind, start_line, end_line, called_by)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (file_id, s.name, s.kind, s.start_line, s.end_line, called_json),
                    )
            if not self.is_pg:
                self.conn.commit()

    def list_files(self, repo_id: str) -> list[dict]:
        rows = self._execute(
            "SELECT path, language, size, summary FROM files WHERE repo_id = ? ORDER BY path", (repo_id,)
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["name"] = Path(d["path"]).name
            result.append(d)
        return result

    def get_file_symbols(self, repo_id: str, path: str) -> list[dict] | None:
        file_row = self._execute(
            "SELECT id FROM files WHERE repo_id = ? AND path = ?", (repo_id, path)
        ).fetchone()
        if file_row is None:
            return None
        rows = self._execute(
            "SELECT name, kind, start_line, end_line, called_by FROM symbols WHERE file_id = ? ORDER BY start_line",
            (file_row["id"],),
        ).fetchall()
        res = []
        for r in rows:
            d = dict(r)
            raw = d.get("called_by")
            d["called_by"] = raw if isinstance(raw, list) else json.loads(raw or "[]")
            res.append(d)
        return res

    def get_file_details(self, repo_id: str, path: str) -> dict | None:
        file_row = self._execute(
            """SELECT id, path, language, size, summary, outgoing_routes,
                      incoming_routes, external_dependencies, how_routed, content
               FROM files WHERE repo_id = ? AND path = ?""",
            (repo_id, path),
        ).fetchone()
        if file_row is None:
            return None

        sym_rows = self._execute(
            "SELECT name, kind, start_line, end_line, called_by FROM symbols WHERE file_id = ? ORDER BY start_line",
            (file_row["id"],),
        ).fetchall()

        key_parts = []
        for s in sym_rows:
            raw = s["called_by"]
            called = raw if isinstance(raw, list) else json.loads(raw or "[]")
            key_parts.append(
                {
                    "name": s["name"],
                    "kind": s["kind"],
                    "start_line": s["start_line"],
                    "end_line": s["end_line"],
                    "called_by": called,
                }
            )

        def decode_json(val, default):
            if val is None:
                return default
            if isinstance(val, (list, dict)):
                return val
            try:
                return json.loads(val)
            except Exception:
                return default

        return {
            "path": file_row["path"],
            "name": Path(file_row["path"]).name,
            "language": file_row["language"],
            "size": file_row["size"],
            "summary": file_row["summary"] or "Summary not determined.",
            "outgoing_routes": decode_json(file_row["outgoing_routes"], []),
            "incoming_routes": decode_json(file_row["incoming_routes"], []),
            "external_dependencies": decode_json(file_row["external_dependencies"], []),
            "how_routed": decode_json(
                file_row["how_routed"],
                {"entry_point": None, "chain": [], "explanation": "Route not determined."},
            ),
            "key_parts": key_parts,
            "content": file_row.get("content"),
        }
