# -*- coding: utf-8 -*-
"""
Точный кэш ответов: ключ = sha256 канонического JSON {model, input|messages}.

Только когда известен корень проекта. TTL 24ч, не больше 1000 строк.
Семантический кэш и sqlite-vec не подключаем.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

CACHE_NAME = "proxy_cache.sqlite"
TTL = timedelta(hours=24)
MAX_ROWS = 1000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
  key TEXT PRIMARY KEY,
  response BLOB NOT NULL,
  status INTEGER,
  content_type TEXT,
  created_at TEXT,
  hits INTEGER DEFAULT 0
);
"""


def cache_path(project_root: Optional[Path]) -> Optional[Path]:
    if project_root is None:
        return None
    return Path(project_root) / ".agent" / CACHE_NAME


def canonical_key(obj: Dict[str, Any]) -> Optional[str]:
    if not isinstance(obj, dict):
        return None
    payload = obj.get("input")
    if payload is None:
        payload = obj.get("messages")
    if payload is None:
        return None
    blob = json.dumps(
        {"model": obj.get("model"), "input": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def request_sha256(body: bytes) -> str:
    return hashlib.sha256(body or b"").hexdigest()


@contextmanager
def _conn(path: Path) -> Iterator[sqlite3.Connection]:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=5.0)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(_SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


def _expired(created_at: str) -> bool:
    try:
        ts = datetime.fromisoformat(created_at)
    except Exception:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - ts > TTL


def lookup(
    project_root: Optional[Path],
    key: Optional[str],
) -> Optional[Tuple[int, str, bytes]]:
    path = cache_path(project_root)
    if path is None or not key:
        return None
    try:
        with _conn(path) as con:
            row = con.execute(
                "SELECT status, content_type, response, created_at FROM cache WHERE key = ?",
                (key,),
            ).fetchone()
            if not row:
                return None
            status, ctype, blob, created = row
            if _expired(str(created or "")):
                con.execute("DELETE FROM cache WHERE key = ?", (key,))
                return None
            con.execute(
                "UPDATE cache SET hits = hits + 1 WHERE key = ?",
                (key,),
            )
            return int(status or 200), str(ctype or "application/json"), bytes(blob)
    except Exception:
        return None


def store(
    project_root: Optional[Path],
    key: Optional[str],
    *,
    status: int,
    content_type: str,
    body: bytes,
) -> None:
    path = cache_path(project_root)
    if path is None or not key or body is None:
        return
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    try:
        with _conn(path) as con:
            con.execute(
                """
                INSERT INTO cache (key, response, status, content_type, created_at, hits)
                VALUES (?, ?, ?, ?, ?, 0)
                ON CONFLICT(key) DO UPDATE SET
                  response = excluded.response,
                  status = excluded.status,
                  content_type = excluded.content_type,
                  created_at = excluded.created_at
                """,
                (key, body, int(status), content_type or "application/json", now),
            )
            n = con.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
            if int(n) > MAX_ROWS:
                con.execute(
                    """
                    DELETE FROM cache WHERE key IN (
                      SELECT key FROM cache ORDER BY created_at ASC
                      LIMIT ?
                    )
                    """,
                    (int(n) - MAX_ROWS,),
                )
    except Exception:
        return


def stats(project_root: Optional[Path]) -> Dict[str, Any]:
    path = cache_path(project_root)
    if path is None or not path.is_file():
        return {"entries": 0, "hits": 0}
    try:
        with _conn(path) as con:
            row = con.execute(
                "SELECT COUNT(*), COALESCE(SUM(hits), 0) FROM cache"
            ).fetchone()
        return {"entries": int(row[0]), "hits": int(row[1])}
    except Exception:
        return {"entries": 0, "hits": 0}
