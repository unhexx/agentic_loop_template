# -*- coding: utf-8 -*-
"""
Тесты правилового компрессора.

Запуск:
  python -m memory.test_compressor
  pytest memory/test_compressor.py -q
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from memory.compressor import (
    compress_files,
    compress_text,
    distill_text,
    file_priority,
    truncate_to_budget,
)
from memory.context_budget import check_files, estimate_tokens


def test_file_priority_keeps_working_set():
    assert file_priority(Path(".agent/LOOP_STATE.json")) > file_priority(Path(".agent/history/old.md"))
    assert file_priority(Path("last_handoff.json")) > file_priority(Path("TRAJECTORIES.json"))
    assert file_priority(Path(".agent/PLAN.md")) >= 80
    assert file_priority(Path(".agent/history/LOOP_STATE.md")) < 20


def test_distill_collapses_blank_and_comments():
    raw = "# Title\n\n\n\n<!-- drop me -->\n\npara\n\n\n\n"
    out = distill_text(raw)
    assert "drop me" not in out
    assert "\n\n\n" not in out
    assert "Title" in out
    assert "para" in out
    assert estimate_tokens(out) < estimate_tokens(raw)


def test_distill_shrinks_long_fences():
    body = "\n".join(f"line_{i}" for i in range(80))
    raw = f"intro\n```python\n{body}\n```\n"
    out = distill_text(raw)
    assert "line_0" in out
    assert "omitted" in out
    assert out.count("line_") < 80
    assert estimate_tokens(out) < estimate_tokens(raw)


def test_truncate_respects_budget():
    text = ("lorem ipsum dolor sit amet " * 400)
    budget = 80
    cut = truncate_to_budget(text, budget)
    assert estimate_tokens(cut) <= budget
    assert "lorem" in cut


def test_compress_text_under_budget():
    text = "# A\n\n\n\nshort\n"
    r = compress_text(text, budget_tokens=500)
    assert r["within_budget"]
    assert r["tokens_out"] <= 500
    assert "short" in r["text"]


def test_compress_files_drops_history_keeps_plan():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        agent = root / ".agent"
        hist = agent / "history"
        hist.mkdir(parents=True)
        plan = agent / "PLAN.md"
        plan.write_text("# Plan\n\nkeep this working set\n" + ("x" * 200), encoding="utf-8")
        archive = hist / "dump.md"
        archive.write_text("ARCHIVE " * 5000, encoding="utf-8")
        noise = root / "TRAJECTORIES.json"
        noise.write_text(json.dumps({"huge": "y" * 8000}), encoding="utf-8")

        report = compress_files([plan, archive, noise], budget_tokens=120)
        assert plan.as_posix() in report["kept"] or str(plan) in report["kept"]
        dropped = set(report["dropped"])
        assert any("history" in p or "TRAJECTORIES" in p for p in dropped)
        # исходники не трогаем
        assert "ARCHIVE" in archive.read_text(encoding="utf-8")
        assert report["tokens_out"] <= report["tokens_in"]
        assert report["within_budget"]


def test_context_budget_compress_flag():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        fat = root / "fat.md"
        fat.write_text("word " * 4000, encoding="utf-8")
        slim = root / "LOOP_STATE.md"
        slim.write_text("# state\nok\n", encoding="utf-8")
        before = check_files([fat, slim], budget=50, compress=False)
        assert not before["within_budget"]
        after = check_files([fat, slim], budget=50, compress=True)
        assert "compression" in after
        assert after["total_tokens_after_compress"] <= after["total_tokens"]
        assert after["compression"]["tokens_out"] <= 50 or after["within_budget"]


def _run_all() -> None:
    tests = [
        test_file_priority_keeps_working_set,
        test_distill_collapses_blank_and_comments,
        test_distill_shrinks_long_fences,
        test_truncate_respects_budget,
        test_compress_text_under_budget,
        test_compress_files_drops_history_keeps_plan,
        test_context_budget_compress_flag,
    ]
    for fn in tests:
        fn()
        print(f"OK {fn.__name__}")
    print(f"all {len(tests)} compressor tests passed")


if __name__ == "__main__":
    _run_all()
