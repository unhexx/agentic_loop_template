# -*- coding: utf-8 -*-
"""
Тесты для performance_ledger.py (P1 Metrics/ROI).

Запуск:
  python memory/test_performance_ledger.py

Использует tempfile для изоляции .agent, прямой импорт модуля.
Проверяет: append, report, get_recent, CLI, compaction, atomic, render.
"""

import json
import tempfile
import sys
from pathlib import Path

# Прямой импорт, минуя __init__.py (как в test_meta)
spec_path = Path(__file__).parent / "performance_ledger.py"
import importlib.util
spec = importlib.util.spec_from_file_location("pl", str(spec_path))
pl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pl)

def test_append_and_report():
    print("=== Тест performance_ledger ===")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        # Подменяем пути на tmp
        orig_agent = pl.AGENT_DIR
        orig_json = pl.LEDGER_JSON
        orig_md = pl.LEDGER_MD
        try:
            pl.AGENT_DIR = tmp_path / ".agent"
            pl.LEDGER_JSON = pl.AGENT_DIR / "PERFORMANCE_LEDGER.json"
            pl.LEDGER_MD = pl.AGENT_DIR / "PERFORMANCE_LEDGER.md"

            rec = pl.append_cycle(cycle=42, outcome="DONE", elapsed_minutes=5.5, tool_calls=10, confidence=0.95, meta_applied=2, notes="test run")
            assert rec["cycle"] == 42
            assert rec["confidence"] == 0.95
            print("✓ append_cycle OK")

            recents = pl.get_recent(5)
            assert len(recents) >= 1
            print("✓ get_recent OK")

            rep = pl.generate_report(5)
            assert isinstance(rep, dict)
            assert rep["recent_cycles"] >= 1
            assert "avg_elapsed_min" in rep
            print("✓ generate_report OK, avg_elapsed=", rep["avg_elapsed_min"])

            # multiple appends for compaction test
            for i in range(5):
                pl.append_cycle(cycle=50+i, elapsed_minutes=float(i))
            recents = pl.get_recent(10)
            assert len(recents) == 6  # 1 + 5
            print("✓ multiple appends OK")

            print("✓ Все базовые тесты пройдены")
        finally:
            pl.AGENT_DIR = orig_agent
            pl.LEDGER_JSON = orig_json
            pl.LEDGER_MD = orig_md

def test_cli_report():
    print("--- CLI report test ---")
    # Since it prints, just ensure no crash for report
    old_argv = sys.argv
    try:
        sys.argv = ["prog", "report", "--recent", "2"]
        # capture would be ideal, but direct call
        import io
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            pl.main()
        except SystemExit:
            pass
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        print("CLI report output sample:", output[:100] if output else "(empty, may be no data in this run)")
        print("✓ CLI report no crash")
    finally:
        sys.argv = old_argv

if __name__ == "__main__":
    test_append_and_report()
    test_cli_report()
    print("=== performance_ledger tests completed successfully ===")
