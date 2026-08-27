# -*- coding: utf-8 -*-
"""Reflector: эвристики предложений по траекториям."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from memory.meta.store import (
    _load_index_unlocked,
    _next_prop_id,
    _now_iso,
    _trajectories_lock,
    _write_index_unlocked,
)

def analyze_for_proposals(
    recent: int = 5,
    min_confidence: float = 0.8,
    *,
    agent_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """
    Простой детерминированный анализ недавних траекторий.
    Извлекает паттерны успеха и генерирует черновики предложений.

    Настоящая версия — эвристики + заглушки. Позже можно добавить
    вызов модели с жёстким рубрикатором (только JSON на выходе).
    """
    with _trajectories_lock(agent_dir):
        index = _load_index_unlocked(agent_dir)
        trajs = list(reversed(index.get("trajectories", [])))[:recent]
        trajs = [t for t in trajs if t.get("quality_signals", {}).get("confidence", 0) >= min_confidence]
        proposals: List[Dict[str, Any]] = []

        if not trajs:
            return proposals

        # Эвристика 1: маркеры верификации -> few-shot в GUIDE
        marker_mentions = 0
        # Эвристика 2: хороший стиль коммитов
        good_commit_style = 0
        # Эвристика 3: уроки по компрессии / быстрым handoff
        compression_wins = 0
        # Эвристика 4: уроки -> кандидаты в permanent rules
        rule_candidates = []
        # Эвристика 5: P1 metrics/ledger в паттернах -> few-shot для handoff с метриками (full P4)
        metrics_ledger_wins = 0
        ledger_patterns = 0

        for t in trajs:
            text = json.dumps(t, ensure_ascii=False).lower()
            if "marker" in text or "verifyonly" in text or "sync_done" in text:
                marker_mentions += 1
            commit_msg = (t.get("git_evidence", {}).get("last_commit") or "").lower()
            if "улучшил" in commit_msg or "добавил" in commit_msg:
                good_commit_style += 1
            if t.get("compression_metrics") or "handoff" in text and "короче" in text:
                compression_wins += 1
            for lesson in t.get("lessons_learned", []):
                if "всегда" in lesson.lower() or "обязательно" in lesson.lower():
                    rule_candidates.append(lesson)
            # New P4 full heuristics for metrics/ledger
            if "performance" in text or "ledger" in text or "metrics" in text or "roi" in text:
                metrics_ledger_wins += 1
            sp = " ".join(t.get("success_patterns", [])).lower()
            if "performance" in sp or "ledger" in sp or "metrics" in sp:
                ledger_patterns += 1

        existing_props = index.setdefault("proposals", [])

        if marker_mentions >= 2:
            pid = _next_prop_id(existing_props)
            prop = {
                "id": pid,
                "from_trajectories": [t["id"] for t in trajs[-marker_mentions:]],
                "target_file": "agentic_loop_template/PROMPT_COMPRESSION_GUIDE.md",
                "change_type": "add_few_shot_example",
                "title": "Добавить verified few-shot с явным machine-verifiable маркером (SYNC_DONE / VerifyOnly)",
                "rationale": f"В {marker_mentions} высококачественных циклах успех коррелировал с явным маркером и ссылкой на него в сжатом handoff. Паттерн повторяется.",
                "proposed_text": "```markdown\n**Good compressed handoff with verification marker (harvested from cycle 17+):**\n- Cycle goal: improve sync-worktree (VerifyOnly + SYNC_DONE).\n- Evidence: grep for SYNC_DONE marker in script + test.\n- Commit style: natural Russian, human dev voice.\n```",
                "insertion_anchor": "После примера 'Good compressed handoff (delta + links + summary)'",
                "safe_to_auto": True,
                "confidence": 0.78,
                "expected_impact": "Ускорение планирования на infra/sync задачах; снижение размера handoff",
                "status": "pending",
                "created_at": _now_iso(),
            }
            proposals.append(prop)
            existing_props.append(prop)

        if good_commit_style >= 1:
            pid = _next_prop_id(existing_props)
            prop = {
                "id": pid,
                "from_trajectories": [t["id"] for t in trajs[-2:]],
                "target_file": "DEVELOPMENT_STANDARDS.md",
                "change_type": "add_permanent_rule_example",
                "title": "Рекомендация: в permanent rules добавить пример 'machine-checkable completion marker'",
                "rationale": "Успешные циклы, где использовался явный маркер, требовали меньше итераций Reviewer'а.",
                "proposed_text": "6. Для задач с кросс-репо/скриптами — всегда вводить machine-verifiable маркер (SYNC_DONE, VERIFIED, etc.) и проверять его в тестах.",
                "insertion_anchor": "В секции Permanent Rules",
                "safe_to_auto": False,
                "confidence": 0.65,
                "expected_impact": "Меньше возвратов от Reviewer на infra-работе",
                "status": "pending",
                "created_at": _now_iso(),
            }
            proposals.append(prop)
            existing_props.append(prop)

        if compression_wins >= 1:
            pid = _next_prop_id(existing_props)
            prop = {
                "id": pid,
                "from_trajectories": [t["id"] for t in trajs[-compression_wins:]],
                "target_file": "agentic_loop_template/PROMPT_COMPRESSION_GUIDE.md",
                "change_type": "add_few_shot_example",
                "title": "Добавить harvested пример компрессии с метриками выигрыша",
                "rationale": "В нескольких циклах явно фиксировался выигрыш по размеру handoff благодаря delta + внешним ссылкам.",
                "proposed_text": "**Meta: delta-first + external RAG links дают стабильное сокращение на 30-50% (см. compression_metrics в траекториях).**",
                "insertion_anchor": "В секции Concrete small-context examples",
                "safe_to_auto": True,
                "confidence": 0.72,
                "expected_impact": "Лучшая дисциплина сжатия у будущих ролей",
                "status": "pending",
                "created_at": _now_iso(),
            }
            proposals.append(prop)
            existing_props.append(prop)

        # Full P4 heuristics for metrics/ledger (P1 integration)
        if metrics_ledger_wins >= 1 or ledger_patterns >= 1:
            pid = _next_prop_id(existing_props)
            prop = {
                "id": pid,
                "from_trajectories": [t["id"] for t in trajs[-max(1, metrics_ledger_wins):]],
                "target_file": "agentic_loop_template/PROMPT_COMPRESSION_GUIDE.md",
                "change_type": "add_few_shot_example",
                "title": "Добавить harvested пример с performance/ledger метриками в handoff delta (P1+P4)",
                "rationale": f"В {max(metrics_ledger_wins, ledger_patterns)} циклах успех коррелировал с явным включением performance metrics (elapsed, tool_calls, confidence, meta_applied) + success_patterns в сжатые handoff'ы. Позволяет лучше отслеживать ROI и компрессию.",
                "proposed_text": "```markdown\n**Harvested: include 'performance' delta in every handoff (from 20+ loops)**\n- elapsed_minutes, tool_calls, confidence, meta_applied, tests_failed\n- success_patterns for ledger/metrics wins\n- Reduces verbose repeats, enables trend analysis in PROJECT_CONTEXT.\nExample: \"performance\": {\"cycle\": 21, \"elapsed_minutes\": 3.5, \"tool_calls\": 12, \"confidence\": 0.9, \"meta_applied\": 8}\n```",
                "insertion_anchor": "После примера с compression_metrics",
                "safe_to_auto": True,
                "confidence": 0.85,
                "expected_impact": "Лучшее отслеживание эффективности, автоматическая эволюция метрик в циклах",
                "status": "pending",
                "created_at": _now_iso(),
            }
            proposals.append(prop)
            existing_props.append(prop)

        _write_index_unlocked(index, agent_dir)
        return proposals


def generate_proposals(limit: int = 3, *, agent_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Обёртка: анализирует и возвращает до limit свежих предложений."""
    return analyze_for_proposals(agent_dir=agent_dir)[:limit]
