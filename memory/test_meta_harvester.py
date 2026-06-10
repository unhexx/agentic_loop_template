# -*- coding: utf-8 -*-
"""
Простой тест/валидация для meta_harvester.

Запуск (из корня шаблона):
  python memory/test_meta_harvester.py

Проверяет основные пути: конфиг, harvest с mock, analyze, apply dry-run.
Не зависит от отсутствующих в этом snapshot модулей workspace/store/schema.
Не трогает реальный .agent при тесте.
"""

import json
import tempfile
import sys
from pathlib import Path

# Прямой импорт файла, минуя __init__.py пакета (чтобы не тянуть отсутствующие workspace и т.д.)
spec_path = Path(__file__).parent / "meta_harvester.py"
import importlib.util
spec = importlib.util.spec_from_file_location("mh", str(spec_path))
mh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mh)


def test_basic():
    print("=== Тест meta_harvester ===")

    cfg = mh.load_config()
    assert cfg.get("enabled") is True
    print("✓ load_config OK, enabled=", cfg["enabled"])

    # Создаём временный handoff для теста harvest
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        handoff = tmp_path / "test_handoff.json"
        handoff.write_text(json.dumps({
            "role": "Reviewer",
            "summary": "Успешно завершил задачу по sync",
            "confidence": 0.91,
            "metrics": {"tests_total": 12, "tests_failed": 0, "coverage": 88.0, "tool_calls": 7, "elapsed_minutes": 9.5},
            "process_tags": [],
            "lessons_learned": ["Всегда использовать явный маркер завершения"],
            "git_branch": "feature/sync-verify",
            "last_commit": "Улучшил верификацию с маркером SYNC_DONE"
        }, ensure_ascii=False), encoding="utf-8")

        # Временно подменим пути хранения на tmp, чтобы не трогать реальный .agent
        orig_index = mh.TRAJECTORIES_INDEX
        orig_md = mh.META_PROPOSALS_MD
        mh.TRAJECTORIES_INDEX = tmp_path / "TRAJECTORIES.json"
        mh.META_PROPOSALS_MD = tmp_path / "META_PROPOSALS.md"

        tid = mh.harvest_from_handoff(handoff, cycle=42, outcome="DONE")
        print("✓ harvest вернул id:", tid)
        assert tid is not None

        recent = mh.get_recent_trajectories(1)
        assert len(recent) == 1
        print("✓ get_recent_trajectories OK")

        props = mh.analyze_for_proposals(recent=1, min_confidence=0.8)
        print("✓ analyze_for_proposals сгенерировал", len(props), "предложений")

        n = mh.apply_safe_proposals(dry_run=True)
        print("✓ apply_safe_proposals (dry) обработал", n)

        # Тест новых функций (seed + ledger)
        seeded = mh.seed_example_trajectory()
        print("✓ seed_example_trajectory создал", seeded)

        mh.update_performance_ledger("P-DEMO-001", "demo impact on compression")
        ledger = Path(".agent/LOOP_PERFORMANCE.md")
        assert ledger.exists()
        print("✓ update_performance_ledger записал метрику")

        # Восстанавливаем пути
        mh.TRAJECTORIES_INDEX = orig_index
        mh.META_PROPOSALS_MD = orig_md

    print("=== Все базовые тесты пройдены ===")


if __name__ == "__main__":
    test_basic()
