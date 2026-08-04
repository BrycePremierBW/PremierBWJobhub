from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Any, Iterable, Iterator

import pandas as pd

try:
    from psycopg2.pool import ThreadedConnectionPool
except Exception:  # pragma: no cover - SQLite/local installs
    ThreadedConnectionPool = None


class _CursorAdapter:
    def __init__(self, cursor: Any, database: "Database"):
        self._cursor = cursor
        self._database = database

    def execute(self, sql: str, params: Iterable[Any] = ()) -> Any:
        return self._cursor.execute(self._database._sql(sql), tuple(params))

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> Any:
        return self._cursor.executemany(
            self._database._sql(sql), [tuple(row) for row in rows]
        )

    def __iter__(self):
        return iter(self._cursor)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class _ConnectionAdapter:
    def __init__(self, conn: Any, database: "Database", pool: Any = None):
        self._conn = conn
        self._database = database
        self._pool = pool
        self._closed = False

    def cursor(self, *args: Any, **kwargs: Any) -> _CursorAdapter:
        return _CursorAdapter(self._conn.cursor(*args, **kwargs), self._database)

    def execute(self, sql: str, params: Iterable[Any] = ()) -> Any:
        return self.cursor().execute(sql, params)

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> Any:
        return self.cursor().executemany(sql, rows)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._pool is None:
            self._conn.close()
            return
        try:
            self._pool.putconn(self._conn)
        except Exception:
            self._pool.putconn(self._conn, close=True)

    def __enter__(self):
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


class Database:
    """Small portable database layer for SQLite and PostgreSQL."""

    def __init__(self, database_url: str, sqlite_path: str):
        self.database_url = str(database_url or "").strip()
        self.sqlite_path = Path(sqlite_path)
        self.postgres = bool(self.database_url)
        self._pool = None
        self._pool_lock = Lock()

    def _sql(self, sql: str) -> str:
        if not self.postgres:
            return sql
        statement = re.sub(r"AS\s+'([^']+)'", r'AS "\1"', sql, flags=re.IGNORECASE)
        statement = statement.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        if re.search(r"INSERT\s+OR\s+IGNORE\s+INTO", statement, flags=re.IGNORECASE):
            statement = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", statement, flags=re.IGNORECASE)
            if "ON CONFLICT" not in statement.upper():
                statement = statement.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
        statement = statement.replace("?", "%s")
        return statement

    def _postgres_pool(self):
        if not self.postgres:
            return None
        if ThreadedConnectionPool is None:
            raise RuntimeError("psycopg2-binary is required when DATABASE_URL is set.")
        if self._pool is None:
            with self._pool_lock:
                if self._pool is None:
                    self._pool = ThreadedConnectionPool(
                        minconn=1,
                        maxconn=8,
                        dsn=self.database_url,
                        sslmode="require",
                    )
        return self._pool

    def connect(self) -> Any:
        """Return a DB-API connection compatible with existing JobHub modules."""
        if self.postgres:
            pool = self._postgres_pool()
            return _ConnectionAdapter(pool.getconn(), self, pool)
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.sqlite_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA synchronous = NORMAL")
        return _ConnectionAdapter(conn, self)

    @contextmanager
    def connection(self) -> Iterator[Any]:
        pool = None
        if self.postgres:
            pool = self._postgres_pool()
            conn = pool.getconn()
        else:
            self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                self.sqlite_path,
                timeout=30,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 30000")
            conn.execute("PRAGMA synchronous = NORMAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            if pool is not None:
                try:
                    pool.putconn(conn)
                except Exception:
                    pool.putconn(conn, close=True)
            else:
                conn.close()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(self._sql(sql), tuple(params))
            return int(getattr(cur, "lastrowid", 0) or 0)

    def execute_many(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        prepared = [tuple(row) for row in rows]
        if not prepared:
            return
        with self.connection() as conn:
            cur = conn.cursor()
            cur.executemany(self._sql(sql), prepared)

    def insert_id(self, sql: str, params: Iterable[Any] = ()) -> int:
        if self.postgres:
            statement = sql.rstrip().rstrip(";")
            if " returning " not in statement.lower():
                statement += " RETURNING id"
            with self.connection() as conn:
                cur = conn.cursor()
                cur.execute(self._sql(statement), tuple(params))
                row = cur.fetchone()
                return int(row[0])
        return self.execute(sql, params)

    def query(self, sql: str, params: Iterable[Any] = ()) -> pd.DataFrame:
        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(self._sql(sql), tuple(params))
            rows = cur.fetchall()
            columns = [item[0] for item in cur.description] if cur.description else []
        return pd.DataFrame(rows, columns=columns)

    def scalar(self, sql: str, params: Iterable[Any] = (), default: Any = None) -> Any:
        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(self._sql(sql), tuple(params))
            row = cur.fetchone()
        return row[0] if row else default

    def table_exists(self, table: str) -> bool:
        if self.postgres:
            return bool(
                self.scalar(
                    """
                    SELECT COUNT(*) FROM information_schema.tables
                    WHERE table_schema='public' AND table_name=?
                    """,
                    (table,),
                    0,
                )
            )
        return bool(
            self.scalar(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
                0,
            )
        )

    def columns(self, table: str) -> set[str]:
        if not self.table_exists(table):
            return set()
        if self.postgres:
            frame = self.query(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema='public' AND table_name=?
                """,
                (table,),
            )
            return set(frame.get("column_name", pd.Series(dtype=str)).astype(str))
        frame = self.query(f"PRAGMA table_info({table})")
        return set(frame.get("name", pd.Series(dtype=str)).astype(str))

    def ensure_column(self, table: str, column: str, definition: str) -> None:
        if column not in self.columns(table):
            self.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
