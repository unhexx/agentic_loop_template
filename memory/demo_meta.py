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
"""

import json
import tempfile
from pathlib import Path

import memory.meta_harvester as mh


def main():
    print("=== Демонстрация Meta-Optimizer & Trajectory Harvesting (с playbooks) ===")
    print()

    try:
        from memory import playbooks as pb
        bullets = pb.select_bullets("planning cross-platform playbook usage", scopes=["global", "phase:planning"], k=2)
        print("Playbooks selected for planning (runtime demo):")
        for b in bullets:
            print(f"  - {b.get('content', '')[:80]} (eff={b.get('effectiveness')})")
    except Exception as e:
        print(f"Playbooks select note: {e}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        agent = tmp_path / ".agent"
        agent.mkdir()

        example_id = mh.seed_example_trajectory(agent_dir=agent)
        print(f"1. Seeded example trajectory: {example_id}")

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

        tid = mh.harvest_from_handoff(handoff, cycle=99, outcome="DONE", agent_dir=agent)
        print(f"2. Harvested trajectory: {tid}")

        props = mh.analyze_for_proposals(recent=1, min_confidence=0.8, agent_dir=agent)
        print(f"3. Generated {len(props)} proposal(s)")

        for p in props:
            print(f"   - {p['id']}: {p['title']} (safe={p['safe_to_auto']})")

        applied = mh.apply_safe_proposals(dry_run=True, agent_dir=agent)
        print(f"4. Applied {applied} safe proposal(s) (dry-run)")

        mh.update_performance_ledger("P-DEMO-001", "demo impact", agent_dir=agent)
        ledger = agent / "LOOP_PERFORMANCE.md"
        if ledger.exists():
            tail = ledger.read_text(encoding="utf-8").strip().splitlines()[-1]
            print(f"5. Performance ledger tail: {tail}")

    print()
    print("=== Демо завершено успешно ===")
    print("См. META_OPTIMIZER_SPEC.md и memory/README.md для деталей.")
    print("В реальном цикле Reviewer вызывает эти команды после качественного DONE.")


if __name__ == "__main__":
    main()
