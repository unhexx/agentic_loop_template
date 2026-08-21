# -*- coding: utf-8 -*-
"""
Тесты локального SQLite-хранилища знаний.

Запуск:
  python -m memory.test_knowledge
  pytest memory/test_knowledge.py -q
"""

from __future__ import annotations

import io
import json
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from memory.knowledge import (
    compact,
    ingest_docs,
    ingest_if_empty,
    query,
    stats,
    upsert,
    cli,
)


def test_upsert_and_query_by_category_and_q():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "k.sqlite"
        upsert(
            source="docs/git.md",
            title="Git sync",
            content="preflight then worktree sync",
            category="playbook",
            db=db,
            provenance="test",
        )
        upsert(
            source="docs/other.md",
            title="Unrelated",
            content="docker compose up",
            category="doc",
            db=db,
            provenance="test",
        )
        rows = query(q="git sync", category="playbook", top=5, db=db)
        assert len(rows) == 1
        assert rows[0]["title"] == "Git sync"
        assert rows[0]["source"] == "docs/git.md"
        assert rows[0]["tokens"] > 0
        assert "embedding" not in rows[0]


def test_upsert_same_key_updates_content():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "k.sqlite"
        first = upsert(
            source="a.md",
            title="Same",
            content="old body",
            db=db,
            provenance="v1",
        )
        second = upsert(
            source="a.md",
            title="Same",
            content="new body with extra words",
            db=db,
            provenance="v2",
        )
        assert first["id"] == second["id"]
        assert second["content"] == "new body with extra words"
        assert second["provenance"] == "v2"
        assert len(query(top=10, db=db)) == 1


def test_ingest_docs_from_markdown_tree():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "k.sqlite"
        docs = root / "docs"
        docs.mkdir()
        (docs / "intro.md").write_text(
            "# Intro heading\n\nFirst paragraph about bounded state.\n\nMore.\n",
            encoding="utf-8",
        )
        nested = docs / "sub"
        nested.mkdir()
        (nested / "cli.md").write_text(
            "# CLI tools\n\npython -m memory.knowledge query\n",
            encoding="utf-8",
        )
        (docs / "empty.md").write_text("", encoding="utf-8")
        report = ingest_docs(docs, db=db, cwd=root, budget_tokens=400)
        assert report["ingested"] == 2
        assert report["skipped"] >= 1
        rows = query(q="bounded state", db=db)
        assert any(r["title"] == "Intro heading" for r in rows)
        again = ingest_docs(docs, db=db, cwd=root, budget_tokens=400)
        assert again["stats"]["entries"] == 2


def test_compact_drops_old_over_cap():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "k.sqlite"
        for i in range(5):
            upsert(
                source=f"s{i}.md",
                title=f"T{i}",
                content=f"body {i}",
                category="doc",
                db=db,
                provenance="t",
            )
        result = compact(max_per_category=2, db=db)
        assert result["dropped"] == 3
        rows = query(top=10, db=db)
        assert len(rows) == 2
        titles = {r["title"] for r in rows}
        assert titles == {"T3", "T4"}


def test_cli_query_and_stats_json():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "k.sqlite"
        upsert(
            source="x.md",
            title="Playbook git",
            content="git preflight",
            category="playbook",
            db=db,
            provenance="t",
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli(["query", "--db", str(db), "--q", "preflight", "--category", "playbook"])
        assert rc == 0
        rows = json.loads(buf.getvalue())
        assert rows[0]["title"] == "Playbook git"
        sbuf = io.StringIO()
        with redirect_stdout(sbuf):
            assert cli(["stats", "--db", str(db)]) == 0
        s = json.loads(sbuf.getvalue())
        assert s["entries"] == 1
        assert s["by_category"][0]["category"] == "playbook"


def test_query_fts_match_russian_and_english():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "k.sqlite"
        upsert(
            source="docs/ru.md",
            title="Синхронизация git",
            content="preflight then worktree sync и проверка SYNC_DONE",
            category="playbook",
            db=db,
            provenance="t",
        )
        rows = query(q="синхронизация git", db=db)
        assert rows
        assert rows[0]["title"] == "Синхронизация git"
        rows_en = query(q="SYNC_DONE", db=db)
        assert any("SYNC_DONE" in (r.get("content") or "") for r in rows_en)


def test_ingest_if_empty_skips_when_seeded_and_fills_when_empty():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "k.sqlite"
        docs = root / "docs"
        docs.mkdir()
        (docs / "a.md").write_text("# Alpha\n\nbounded state pattern\n", encoding="utf-8")
        first = ingest_if_empty(docs, db=db, cwd=root, budget_tokens=400)
        assert first["reason"] == "empty"
        assert first["ingested"] == 1
        second = ingest_if_empty(docs, db=db, cwd=root, budget_tokens=400)
        assert second["reason"] == "not_empty"
        assert second["ingested"] == 0
        assert stats(db=db)["entries"] == 1
        missing = ingest_if_empty(root / "nope", db=db, cwd=root)
        assert missing["reason"] == "root_missing"
        assert missing["ingested"] == 0


def test_cli_ingest_missing_root_is_error():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "k.sqlite"
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli(["ingest-docs", "--db", str(db), "--root", str(Path(tmp) / "nope")])
        assert rc == 1
        payload = json.loads(buf.getvalue())
        assert "error" in payload
        assert payload["ingested"] == 0


def _run_all() -> None:
    tests = [
        test_upsert_and_query_by_category_and_q,
        test_upsert_same_key_updates_content,
        test_ingest_docs_from_markdown_tree,
        test_compact_drops_old_over_cap,
        test_cli_query_and_stats_json,
        test_query_fts_match_russian_and_english,
        test_ingest_if_empty_skips_when_seeded_and_fills_when_empty,
        test_cli_ingest_missing_root_is_error,
    ]
    for fn in tests:
        fn()
        print(f"OK {fn.__name__}")
    print(f"all {len(tests)} knowledge tests passed")


if __name__ == "__main__":
    _run_all()
