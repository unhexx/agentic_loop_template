# -*- coding: utf-8 -*-
"""
Демонстрационный скрипт использования Meta-Optimizer (meta_harvester).

Запуск:
  python memory/demo_meta.py

Показывает:
- Сидинг примера траектории
- Harvest из mock handoff
- Анализ и предложения
- Применение безопасных изменений
- Обновление performance ledger

Скрипт самодостаточный (прямой импорт модуля, без зависимости от полного пакета памяти).
"""

import json
import tempfile
from pathlib import Path
import importlib.util

# Прямой импорт meta_harvester.py
spec = importlib.util.spec_from_file_location("mh", Path(__file__).parent / "meta_harvester.py")
mh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mh)

def main():
    print("=== Демонстрация Meta-Optimizer & Trajectory Harvesting ===")
    print()

    # 1. Сидинг примера
    example_id = mh.seed_example_trajectory()
    print(f"1. Seeded example trajectory: {example_id}")

    # 2. Создаём временный mock handoff (высокое качество, с маркером)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        handoff = tmp_path / "demo_handoff.json"
        handoff.write_text(json.dumps({
            "role": "Reviewer",
            "summary": "Успешно завершил улучшение meta_harvester. Маркер SYNC_DONE работает.",
            "confidence": 0.94,
            "metrics": {
                "tests_total": 12,
                "tests_failed": 0,
                "coverage": 89.0,
                "tool_calls": 8,
                "elapsed_minutes": 6.5
            },
            "process_tags": [],
            "lessons_learned": [
                "Явный machine-verifiable маркер ускоряет верификацию",
                "Delta + ссылки на предыдущие волны дают хорошее сжатие"
            ],
            "last_commit": "Внёс harvested пример из meta-анализа",
            "git_branch": "feature/meta-demo"
        }, ensure_ascii=False), encoding="utf-8")

        # Перенаправляем хранилище на tmp для чистоты демо
        orig_index = mh.TRAJECTORIES_INDEX
        orig_md = mh.META_PROPOSALS_MD
        mh.TRAJECTORIES_INDEX = tmp_path / "TRAJECTORIES.json"
        mh.META_PROPOSALS_MD = tmp_path / "META_PROPOSALS.md"

        # 3. Harvest
        tid = mh.harvest_from_handoff(handoff, cycle=99, outcome="DONE")
        print(f"2. Harvested trajectory: {tid}")

        # 4. Analyze + Propose
        props = mh.analyze_for_proposals(recent=1, min_confidence=0.8)
        print(f"3. Generated {len(props)} proposal(s)")

        for p in props:
            print(f"   - {p['id']}: {p['title']} (safe={p['safe_to_auto']})")

        # 5. Apply (в демо — dry_run=False, но изменения только в tmp)
        applied = mh.apply_safe_proposals(dry_run=False)
        print(f"4. Applied {applied} safe proposal(s) (ledger updated)")

        # Показываем ledger
        ledger = Path(".agent/LOOP_PERFORMANCE.md")
        if ledger.exists():
            tail = ledger.read_text(encoding="utf-8").strip().splitlines()[-1]
            print(f"5. Performance ledger tail: {tail}")

        # Восстанавливаем пути
        mh.TRAJECTORIES_INDEX = orig_index
        mh.META_PROPOSALS_MD = orig_md

    print()
    print("=== Демо завершено успешно ===")
    print("См. META_OPTIMIZER_SPEC.md и memory/README.md для деталей.")
    print("В реальном цикле Reviewer вызывает эти команды после качественного DONE.")

if __name__ == "__main__":
    main()
