# -*- coding: utf-8 -*-
"""
Локальное SQLite-хранилище знаний для петли.

Тонкая обёртка по схеме из skills/local-knowledge-ingestion:
upsert / query / ingest-docs / stats. Без сети, без эмбеддингов.
Контент перед записью прогоняем через правиловый компрессор.

Запуск:
  python -m memory.knowledge query --category playbook --top 5 --q "git sync"
  python -m memory.knowledge ingest-docs --root docs --budget 800
  python -m memory.knowledge stats
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

from memory.compressor import compress_text
from memory.context_budget import estimate_tokens
from memory.workspace import _run_git

DEFAULT_DB_REL = Path(".agent") / "knowledge" / "knowledge.sqlite"
MAX_PER_CATEGORY = 200
DEFAULT_INGEST_BUDGET = 800
MAX_INGEST_BYTES = 512 * 1024
_SKIP_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "history",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  category TEXT,
  title TEXT,
  content TEXT,
  embedding BLOB,
  provenance TEXT,
  tokens INTEGER,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_knowledge_cat ON knowledge(category);
CREATE INDEX IF NOT EXISTS idx_knowledge_src ON knowledge(source);
CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_src_title
  ON knowledge(source, title);
"""


def db_path(cwd: Optional[Path] = None, override: Optional[Path] = None) -> Path:
    """Путь к sqlite: --db, AGENTIX_KNOWLEDGE_DB, иначе .agent/knowledge/."""
    if override is not None:
        return Path(override)
    env = os.environ.get("AGENTIX_KNOWLEDGE_DB")
    if env:
        return Path(env)
    root = Path(cwd) if cwd is not None else Path.cwd()
    return (root / DEFAULT_DB_REL).resolve()


@contextmanager
def _conn(path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    """Открываем базу, накатываем схему, коммитим на выходе."""
    p = db_path(override=path)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=10.0)
    try:
        con.row_factory = sqlite3.Row
        con.executescript(_SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Row → dict без blob-эмбеддинга (он пока не используется)."""
    d = dict(row)
    d.pop("embedding", None)
    return d


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _provenance(cwd: Optional[Path] = None) -> str:
    """Коммит + метка времени; если git нет — только время."""
    sha = _run_git(["rev-parse", "--short", "HEAD"], cwd=cwd)
    stamp = _now_iso()
    return f"{sha} {stamp}" if sha else stamp


def upsert(
    *,
    source: str,
    title: str,
    content: str,
    category: str = "doc",
    provenance: Optional[str] = None,
    db: Optional[Path] = None,
    cwd: Optional[Path] = None,
    budget_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """Вставить или обновить запись по паре (source, title)."""
    source = (source or "").strip()
    title = (title or "").strip()
    if not source or not title:
        raise ValueError("source и title обязательны")
    text = content or ""
    if budget_tokens is not None and budget_tokens > 0:
        text = compress_text(text, budget_tokens)["text"]
    tokens = estimate_tokens(text) if text else 0
    prov = provenance if provenance is not None else _provenance(cwd=cwd)
    cat = (category or "doc").strip() or "doc"
    with _conn(db) as con:
        con.execute(
            """
            INSERT INTO knowledge (source, category, title, content, provenance, tokens, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, title) DO UPDATE SET
              category = excluded.category,
              content = excluded.content,
              provenance = excluded.provenance,
              tokens = excluded.tokens,
              created_at = excluded.created_at
            """,
            (source, cat, title, text, prov, tokens, _now_iso()),
        )
        row = con.execute(
            "SELECT id, source, category, title, content, provenance, tokens, created_at "
            "FROM knowledge WHERE source = ? AND title = ?",
            (source, title),
        ).fetchone()
    return _row_to_dict(row)


def query(
    *,
    q: Optional[str] = None,
    category: Optional[str] = None,
    source: Optional[str] = None,
    top: int = 5,
    db: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Поиск по LIKE (title/content), фильтры category/source, свежие сверху."""
    clauses: List[str] = []
    args: List[Any] = []
    if category:
        clauses.append("category = ?")
        args.append(category)
    if source:
        clauses.append("source = ?")
        args.append(source)
    if q:
        like = f"%{q}%"
        clauses.append("(title LIKE ? OR content LIKE ?)")
        args.extend([like, like])
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    limit = max(1, int(top))
    sql = (
        "SELECT id, source, category, title, content, provenance, tokens, created_at "
        f"FROM knowledge {where} ORDER BY id DESC LIMIT ?"
    )
    args.append(limit)
    with _conn(db) as con:
        rows = con.execute(sql, args).fetchall()
    return [_row_to_dict(r) for r in rows]


def stats(db: Optional[Path] = None) -> Dict[str, Any]:
    """Сводка: сколько записей и токенов, разбивка по категориям."""
    with _conn(db) as con:
        total = con.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(tokens), 0) AS tokens FROM knowledge"
        ).fetchone()
        by_cat = con.execute(
            "SELECT category, COUNT(*) AS n, COALESCE(SUM(tokens), 0) AS tokens "
            "FROM knowledge GROUP BY category ORDER BY n DESC"
        ).fetchall()
    return {
        "entries": int(total["n"]),
        "tokens": int(total["tokens"]),
        "by_category": [
            {"category": r["category"], "n": int(r["n"]), "tokens": int(r["tokens"])}
            for r in by_cat
        ],
    }


def compact(
    *,
    max_per_category: int = MAX_PER_CATEGORY,
    db: Optional[Path] = None,
) -> Dict[str, Any]:
    """Оставляем newest N записей в каждой категории, хвост удаляем."""
    cap = max(1, int(max_per_category))
    dropped = 0
    with _conn(db) as con:
        cats = [
            r[0]
            for r in con.execute(
                "SELECT DISTINCT category FROM knowledge"
            ).fetchall()
        ]
        for cat in cats:
            cur = con.execute(
                """
                DELETE FROM knowledge
                WHERE category IS ? AND id NOT IN (
                  SELECT id FROM knowledge
                  WHERE category IS ?
                  ORDER BY id DESC
                  LIMIT ?
                )
                """,
                (cat, cat, cap),
            )
            dropped += int(cur.rowcount or 0)
    return {"dropped": dropped, "max_per_category": cap, **stats(db=db)}


def _extract_title_and_body(text: str, fallback: str) -> tuple[str, str]:
    """Первый markdown-заголовок → title, остальное → body."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    title = fallback
    body_start = 0
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("#"):
            title = s.lstrip("#").strip() or fallback
            body_start = i + 1
            break
    body = "\n".join(lines[body_start:]).strip()
    return title[:200], body


def _should_skip(path: Path) -> bool:
    return any(part in _SKIP_DIR_NAMES for part in path.parts)


def _iter_markdown(root: Path, pattern: str) -> List[Path]:
    """Список markdown-файлов. **/*.md — рекурсивно, иначе glob как есть."""
    if pattern in {"**/*.md", "*.md"}:
        return sorted(p for p in root.rglob("*.md") if p.is_file())
    found = sorted(p for p in root.glob(pattern) if p.is_file())
    return found or sorted(p for p in root.rglob("*.md") if p.is_file())


def ingest_if_empty(
    root: Path,
    *,
    pattern: str = "**/*.md",
    category: str = "doc",
    budget_tokens: int = DEFAULT_INGEST_BUDGET,
    db: Optional[Path] = None,
    cwd: Optional[Path] = None,
    max_per_category: int = MAX_PER_CATEGORY,
) -> Dict[str, Any]:
    """Заливаем docs только если база ещё пустая. Повторный Init безопасен."""
    root = Path(root)
    if not root.exists():
        return {
            "ingested": 0,
            "skipped": 0,
            "reason": "root_missing",
            "root": str(root),
            "stats": stats(db=db),
        }
    snap = stats(db=db)
    if int(snap.get("entries") or 0) > 0:
        return {
            "ingested": 0,
            "skipped": 0,
            "reason": "not_empty",
            "root": str(root),
            "stats": snap,
        }
    report = ingest_docs(
        root,
        pattern=pattern,
        category=category,
        budget_tokens=budget_tokens,
        db=db,
        cwd=cwd,
        max_per_category=max_per_category,
    )
    report["reason"] = "empty"
    return report


def ingest_docs(
    root: Path,
    *,
    pattern: str = "**/*.md",
    category: str = "doc",
    budget_tokens: int = DEFAULT_INGEST_BUDGET,
    db: Optional[Path] = None,
    cwd: Optional[Path] = None,
    max_per_category: int = MAX_PER_CATEGORY,
) -> Dict[str, Any]:
    """Обойти markdown, сжать и upsert. Повторный прогон идемпотентен."""
    root = Path(root)
    if not root.exists():
        return {"ingested": 0, "skipped": 0, "error": f"root not found: {root}"}
    base = Path(cwd) if cwd is not None else Path.cwd()
    ingested = 0
    skipped = 0
    paths = _iter_markdown(root, pattern)
    for path in paths:
        if _should_skip(path):
            skipped += 1
            continue
        try:
            size = path.stat().st_size
        except OSError:
            skipped += 1
            continue
        if size > MAX_INGEST_BYTES or size == 0:
            skipped += 1
            continue
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            skipped += 1
            continue
        resolved = path.resolve()
        base_res = base.resolve()
        source = (
            resolved.relative_to(base_res).as_posix()
            if resolved.is_relative_to(base_res)
            else resolved.as_posix()
        )
        fallback = path.stem.replace("-", " ").replace("_", " ")
        title, body = _extract_title_and_body(raw, fallback)
        if not body and not title:
            skipped += 1
            continue
        upsert(
            source=source,
            title=title,
            content=body or title,
            category=category,
            db=db,
            cwd=cwd or base,
            budget_tokens=budget_tokens,
        )
        ingested += 1
    compacted = compact(max_per_category=max_per_category, db=db)
    return {
        "ingested": ingested,
        "skipped": skipped,
        "root": str(root),
        "compact": compacted,
        "stats": stats(db=db),
    }


def cli(argv: Optional[Sequence[str]] = None) -> int:
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Путь к sqlite (иначе .agent/knowledge/)",
    )

    parser = argparse.ArgumentParser(description="Локальное SQLite-хранилище знаний")
    sub = parser.add_subparsers(dest="cmd", required=True)

    pq = sub.add_parser("query", help="Поиск записей", parents=[shared])
    pq.add_argument("--q", default=None)
    pq.add_argument("--category", default=None)
    pq.add_argument("--source", default=None)
    pq.add_argument("--top", type=int, default=5)

    pu = sub.add_parser("upsert", help="Добавить/обновить запись", parents=[shared])
    pu.add_argument("--source", required=True)
    pu.add_argument("--title", required=True)
    pu.add_argument("--category", default="doc")
    src = pu.add_mutually_exclusive_group(required=True)
    src.add_argument("--content", default=None)
    src.add_argument("--content-file", type=Path)

    pi = sub.add_parser("ingest-docs", help="Обойти markdown и залить в базу", parents=[shared])
    pi.add_argument("--root", type=Path, default=Path("docs"))
    pi.add_argument("--category", default="doc")
    pi.add_argument("--budget", type=int, default=DEFAULT_INGEST_BUDGET)
    pi.add_argument("--max-per-category", type=int, default=MAX_PER_CATEGORY)

    pie = sub.add_parser(
        "ingest-if-empty",
        help="ingest-docs только если в базе 0 записей",
        parents=[shared],
    )
    pie.add_argument("--root", type=Path, default=Path("docs"))
    pie.add_argument("--category", default="doc")
    pie.add_argument("--budget", type=int, default=DEFAULT_INGEST_BUDGET)
    pie.add_argument("--max-per-category", type=int, default=MAX_PER_CATEGORY)

    sub.add_parser("stats", help="Сводка по базе", parents=[shared])

    args = parser.parse_args(list(argv) if argv is not None else None)
    db = args.db

    if args.cmd == "query":
        rows = query(
            q=args.q,
            category=args.category,
            source=args.source,
            top=args.top,
            db=db,
        )
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "upsert":
        if args.content_file is not None:
            content = args.content_file.read_text(encoding="utf-8", errors="replace")
        else:
            content = args.content or ""
        row = upsert(
            source=args.source,
            title=args.title,
            content=content,
            category=args.category,
            db=db,
        )
        print(json.dumps(row, ensure_ascii=False, indent=2))
        return 0
    if args.cmd in {"ingest-docs", "ingest-if-empty"}:
        fn = ingest_if_empty if args.cmd == "ingest-if-empty" else ingest_docs
        report = fn(
            args.root,
            category=args.category,
            budget_tokens=args.budget,
            db=db,
            max_per_category=args.max_per_category,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if args.cmd == "ingest-if-empty":
            return 0
        return 0 if report.get("ingested", 0) >= 0 and "error" not in report else 1
    report = stats(db=db)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    return cli(argv)


if __name__ == "__main__":
    raise SystemExit(cli())
