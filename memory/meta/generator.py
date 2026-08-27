# -*- coding: utf-8 -*-
"""Generator: сбор золотых траекторий."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from memory.meta.store import (
    DEFAULT_MIN_CONFIDENCE,
    _load_index,
    _load_index_unlocked,
    _next_traj_id,
    _now_iso,
    _trajectories_lock,
    _write_index_unlocked,
    load_config,
)

def harvest_from_handoff(
    handoff_path: Union[str, Path],
    cycle: int,
    outcome: str = "DONE",
    quality_signals: Optional[Dict[str, Any]] = None,
    agent_dir: Optional[Path] = None,
) -> Optional[str]:
    """
    Забирает данные из handoff JSON и сохраняет компактную траекторию,
    если качество достаточно высокое (по конфигу).

    Возвращает id траектории или None (если не harvested).
    """
    handoff_path = Path(handoff_path)
    if not handoff_path.exists():
        return None

    cfg = load_config(agent_dir)
    if not cfg.get("enabled"):
        return None

    try:
        data = json.loads(handoff_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    # Простая проверка качества
    min_q = cfg.get("min_quality", {})
    handoff_metrics = data.get("metrics", {}) or {}
    conf = data.get("confidence", 0.0)
    tests_failed = handoff_metrics.get("tests_failed", 999)
    proc_tags = data.get("process_tags", []) or []

    if conf < float(min_q.get("confidence", DEFAULT_MIN_CONFIDENCE)):
        return None
    if tests_failed > int(min_q.get("tests_failed", 0)):
        return None
    if len(proc_tags) > int(min_q.get("process_violations", 0)):
        return None
    if outcome != "DONE":
        # для начала собираем только успешные завершения; позже можно расширить на "BLOCKED с уроками"
        return None

    with _trajectories_lock(agent_dir):
        index = _load_index_unlocked(agent_dir)
        trajs = index.setdefault("trajectories", [])

        # Дедуп по cycle + похожему summary
        for existing in trajs:
            if existing.get("cycle") == cycle and existing.get("outcome") == outcome:
                return existing.get("id")

        tid = _next_traj_id(trajs, cycle)

        # Собираем компактную траекторию (прототип формата — см. META_OPTIMIZER_SPEC.md)
        trajectory = {
            "id": tid,
            "cycle": cycle,
            "timestamp": _now_iso(),
            "outcome": outcome,
            "task_ref": data.get("summary", "")[:120],
            "spec_ref": None,
            "quality_signals": {
                "confidence": conf,
                "tests_total": handoff_metrics.get("tests_total", 0),
                "tests_failed": tests_failed,
                "coverage": handoff_metrics.get("coverage", 0.0),
                "tool_calls": handoff_metrics.get("tool_calls", 0),
                "elapsed_minutes": handoff_metrics.get("elapsed_minutes", 0.0),
                "process_tags": proc_tags,
            },
            "compressed_handoff_chain": [
                # Берём только самое важное из текущего handoff (Reviewer обычно последний)
                {
                    "role": data.get("role", "Reviewer"),
                    "summary": data.get("summary", ""),
                    "context_delta": data.get("context_delta", ""),
                    "lessons": data.get("lessons_learned", [])[:3],
                }
            ],
            "lessons_learned": data.get("lessons_learned", []),
            "success_patterns": [],  # заполняется на этапе analyze или вручную Reviewer'ом
            "git_evidence": {
                "branch": data.get("git_branch", ""),
                "last_commit": data.get("last_commit", ""),
            },
        }

        # Если в будущем handoff будет содержать больше цепочки — здесь можно расширить.
        # Пока intentionally минималистично.

        trajs.append(trajectory)
        _write_index_unlocked(index, agent_dir)
        return tid


def get_recent_trajectories(limit: int = 5, *, agent_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Возвращает последние N траекторий (для анализа и памяти)."""
    index = _load_index(agent_dir)
    return list(reversed(index.get("trajectories", [])))[:limit]

def seed_example_trajectory(agent_dir: Optional[Path] = None) -> str:
    """
    Создаёт пример "золотой" траектории для демонстрации и сидинга.
    Полезно для первых запусков и тестов.
    Возвращает id созданной траектории.
    """
    with _trajectories_lock(agent_dir):
        index = _load_index_unlocked(agent_dir)
        trajs = index.setdefault("trajectories", [])

        # Простая проверка, чтобы не дублировать пример
        for t in trajs:
            if t.get("id", "").startswith("T-EXAMPLE"):
                return t["id"]

        tid = "T-EXAMPLE-001"
        example = {
            "id": tid,
            "cycle": 1,
            "timestamp": _now_iso(),
            "outcome": "DONE",
            "task_ref": "initial meta-harvester integration",
            "spec_ref": "META_OPTIMIZER_SPEC.md",
            "quality_signals": {
                "confidence": 0.92,
                "tests_total": 8,
                "tests_failed": 0,
                "coverage": 85.0,
                "tool_calls": 6,
                "elapsed_minutes": 7.0,
                "process_tags": []
            },
            "compressed_handoff_chain": [
                {
                    "role": "Reviewer",
                    "summary": "Успешно внедрил meta_harvester, применил первый harvested пример.",
                    "context_delta": "Добавлен модуль, обновлены стандарты и роли.",
                    "lessons": ["Meta-анализ даёт конкретные улучшения в few-shot и правилах"]
                }
            ],
            "lessons_learned": [
                "Запускать harvest на всех высококачественных DONE-циклах",
                "Safe apply позволяет быстро интегрировать выигрышные паттерны"
            ],
            "success_patterns": [
                "Явный machine-verifiable маркер в скриптах и handoff'ах",
                "Delta-first подход + ссылки на предыдущие волны для сжатия"
            ],
            "git_evidence": {
                "branch": "feature/meta-optimizer",
                "last_commit": "Внёс harvested пример из meta-анализа"
            },
            "compression_metrics": {
                "handoff_avg_chars": 980,
                "win": "delta + external evidence"
            }
        }
        trajs.append(example)
        _write_index_unlocked(index, agent_dir)
        return tid
