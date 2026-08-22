from __future__ import annotations

import re

from src.config import settings

# ---------------------------------------------------------------------------
# SQL dialect conversion: PostgreSQL → SQLite
# ---------------------------------------------------------------------------

_PARAM_RE = re.compile(r"\$(\d+)")
_ANY_RE = re.compile(r"ANY\(\$(\d+)\)")


def _convert_sql_for_sqlite(sql: str, args: tuple) -> tuple[str, tuple]:
    """Convert PostgreSQL-style SQL to SQLite-compatible SQL.

    Handles:
    - $N → ? positional placeholders
    - ANY($N) → expanded IN (?, ?, ...)
    - NOW() → datetime('now')
    - UUID objects → str for SQLite compatibility
    """
    from uuid import UUID

    # 0. Convert UUID objects to strings (SQLite doesn't support UUID type)
    args = tuple(str(a) if isinstance(a, UUID) else a for a in args)

    # 1. Handle ANY($N) first — expand list args into IN (?, ...)
    def _replace_any(m: re.Match) -> str:
        idx = int(m.group(1)) - 1  # $N is 1-indexed
        arg = args[idx]
        if isinstance(arg, (list, tuple)):
            return "IN (" + ", ".join("?" for _ in arg) + ")"
        return "IN (?)"

    any_indices = {int(m.group(1)) for m in _ANY_RE.finditer(sql)}
    new_sql = _ANY_RE.sub(_replace_any, sql)

    # Expand list arguments for ANY
    expanded_args: list = []
    for i, arg in enumerate(args):
        if (i + 1) in any_indices and isinstance(arg, (list, tuple)):
            expanded_args.extend(arg)
        else:
            expanded_args.append(arg)

    # 2. Replace remaining $N → ?
    new_sql = _PARAM_RE.sub("?", new_sql)

    # 3. Replace NOW() → datetime('now')
    new_sql = new_sql.replace("NOW()", "datetime('now')")

    return new_sql, tuple(expanded_args)


# ---------------------------------------------------------------------------
# SQLite adapter — mimics asyncpg Pool interface
# ---------------------------------------------------------------------------


class _SQLiteRecord:
    """Dict-like row wrapper that mimics asyncpg.Record attribute access."""

    __slots__ = ("_data",)

    def __init__(self, row: dict):
        self._data = row

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self._data.values())[key]
        return self._data[key]

    def __getattr__(self, name: str):
        try:
            return self._data[name]
        except KeyError:
            raise AttributeError(name)

    def __contains__(self, key):
        return key in self._data

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    def __repr__(self):
        return f"_SQLiteRecord({self._data})"

    def keys(self):
        return self._data.keys()


class _SQLiteConnection:
    """Wraps an aiosqlite connection with asyncpg-compatible methods."""

    def __init__(self, conn):
        self._conn = conn

    async def fetch(self, query: str, *args) -> list[_SQLiteRecord]:
        sql, params = _convert_sql_for_sqlite(query, args)
        async with self._conn.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            return [_SQLiteRecord(dict(zip(columns, row))) for row in rows]

    async def fetchrow(self, query: str, *args) -> _SQLiteRecord | None:
        rows = await self.fetch(query, *args)
        return rows[0] if rows else None

    async def fetchval(self, query: str, *args):
        row = await self.fetchrow(query, *args)
        if row is None:
            return None
        return row[0]

    async def execute(self, query: str, *args) -> str:
        sql, params = _convert_sql_for_sqlite(query, args)
        cursor = await self._conn.execute(sql, params)
        await self._conn.commit()
        # Detect operation type from SQL to return PostgreSQL-compatible status
        sql_upper = sql.strip().upper()
        if sql_upper.startswith("DELETE"):
            return f"DELETE {cursor.rowcount}"
        elif sql_upper.startswith("UPDATE"):
            return f"UPDATE {cursor.rowcount}"
        return f"INSERT 0 {cursor.rowcount}"

    async def executemany(self, query: str, args_list: list) -> None:
        sql, _ = _convert_sql_for_sqlite(query, ())
        await self._conn.executemany(sql, args_list)
        await self._conn.commit()


class _SQLitePoolContext:
    """Mimics asyncpg pool.acquire() context manager."""

    def __init__(self, pool: SQLitePool):
        self._pool = pool

    async def __aenter__(self) -> _SQLiteConnection:
        conn = await self._pool._get_conn()
        return _SQLiteConnection(conn)

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass  # Connection managed by pool


class SQLitePool:
    def __init__(self):
        self._conn = None

    async def connect(self):
        import aiosqlite

        db_path = settings.database_url.replace("sqlite:///", "").replace("sqlite://", "")
        self._conn = await aiosqlite.connect(db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")

    async def disconnect(self):
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def _get_conn(self):
        if not self._conn:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._conn

    def acquire(self):
        return _SQLitePoolContext(self)

    async def fetch(self, query: str, *args) -> list[_SQLiteRecord]:
        async with self.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args) -> _SQLiteRecord | None:
        async with self.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def fetchval(self, query: str, *args):
        async with self.acquire() as conn:
            return await conn.fetchval(query, *args)

    async def execute(self, query: str, *args) -> str:
        async with self.acquire() as conn:
            return await conn.execute(query, *args)

    async def executemany(self, query: str, args_list: list) -> None:
        async with self.acquire() as conn:
            await conn.executemany(query, args_list)


# ---------------------------------------------------------------------------
# PostgreSQL adapter (original asyncpg)
# ---------------------------------------------------------------------------


class PostgresPool:
    def __init__(self):
        self._pool = None

    async def connect(self):
        import asyncpg

        self._pool = await asyncpg.create_pool(
            settings.database_url.replace("postgresql+asyncpg://", "postgresql://"),
            min_size=2,
            max_size=20,
        )

    async def disconnect(self):
        if self._pool:
            await self._pool.close()
            self._pool = None

    @property
    def pool(self):
        if not self._pool:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._pool

    def acquire(self):
        return self.pool.acquire()

    async def fetch(self, query: str, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def fetchval(self, query: str, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetchval(query, *args)

    async def execute(self, query: str, *args) -> str:
        async with self.pool.acquire() as conn:
            return await conn.execute(query, *args)

    async def executemany(self, query: str, args_list: list) -> None:
        async with self.pool.acquire() as conn:
            await conn.executemany(query, args_list)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def _create_pool():
    if settings.database_url.startswith("sqlite"):
        return SQLitePool()
    return PostgresPool()


db_pool = _create_pool()
