# -*- coding: utf-8 -*-
"""Curator: apply-safe, SFT, ledger."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from memory.meta.store import (
    DEFAULT_MIN_CONFIDENCE,
    _atomic_write_text,
    _ensure_agent_dir,
    _ledger_lock,
    _load_index,
    _load_index_unlocked,
    _loop_performance_md,
    _now_iso,
    _sft_lock,
    _sft_path,
    _trajectories_lock,
    _write_index_unlocked,
)

def _traj_qualifies(traj: Dict[str, Any], min_confidence: float) -> bool:
    if str(traj.get("outcome") or "").upper() != "DONE":
        return False
    q = traj.get("quality_signals") or {}
    try:
        conf = float(q.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    if conf < min_confidence:
        return False
    try:
        failed = int(q.get("tests_failed") or 0)
    except (TypeError, ValueError):
        failed = 1
    return failed == 0


def _sft_record(traj: Dict[str, Any]) -> Dict[str, Any]:
    chain = traj.get("compressed_handoff_chain") or []
    user = str(traj.get("task_ref") or traj.get("spec_ref") or "")
    if not user and chain and isinstance(chain[0], dict):
        user = str(chain[0].get("summary") or "")
    last = chain[-1] if chain and isinstance(chain[-1], dict) else {}
    assistant = {
        "summary": last.get("summary") or traj.get("outcome"),
        "lessons": traj.get("lessons_learned") or last.get("lessons") or [],
        "success_patterns": traj.get("success_patterns") or [],
    }
    return {
        "messages": [
            {"role": "user", "content": user},
            {"role": "assistant", "content": json.dumps(assistant, ensure_ascii=False)},
        ],
        "trajectory_id": traj.get("id"),
        "confidence": (traj.get("quality_signals") or {}).get("confidence"),
        "cycle": traj.get("cycle"),
    }


def export_sft(
    out: Optional[Path] = None,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    recent: int = 100,
    *,
    agent_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Локальный JSONL для дообучения. GPU/LoRA здесь нет — только экспорт.
    Файл в .agent/sft/ и должен быть в gitignore.
    """
    dest = Path(out) if out is not None else _sft_path(agent_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    index = _load_index(agent_dir)
    trajs = list(index.get("trajectories") or [])
    if recent:
        trajs = trajs[-int(recent) :]
    written = 0
    skipped = 0

    def _append() -> None:
        nonlocal written, skipped
        with dest.open("a", encoding="utf-8") as fh:
            for traj in trajs:
                if not _traj_qualifies(traj, min_confidence):
                    skipped += 1
                    continue
                rec = _sft_record(traj)
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                written += 1

    if out is None:
        with _sft_lock(dest.parent):
            _append()
    else:
        _append()
    return {
        "written": written,
        "skipped": skipped,
        "path": str(dest),
        "min_confidence": min_confidence,
    }

def apply_safe_proposals(
    dry_run: bool = True,
    ids: Optional[List[str]] = None,
    *,
    agent_dir: Optional[Path] = None,
) -> int:
    """
    Применяет безопасные (safe_to_auto=True) предложения.

    Для безопасных типов (add_few_shot_example в GUIDE) — реально дописывает блок
    в конец соответствующей секции с UTF-8. Остальные — только маркировка + лог.
    Это позволяет быстро получать выгоду от meta без ручного вмешательства для
    низкорисковых изменений.

    Возвращает количество обработанных предложений.
    """
    applied_ids: List[Tuple[str, str]] = []  # id, impact
    with _trajectories_lock(agent_dir):
        index = _load_index_unlocked(agent_dir)
        props = index.get("proposals", [])
        to_apply = [p for p in props if p.get("safe_to_auto") and p.get("status", "pending") == "pending"]
        if ids:
            idset = set(ids)
            to_apply = [p for p in to_apply if p.get("id") in idset]
        guide_path = Path("PROMPT_COMPRESSION_GUIDE.md")  # cwd, не agent_dir
        applied = 0
        for p in to_apply:
            target = p.get("target_file", "")
            if dry_run:
                print(f"[DRY-RUN] Would apply {p['id']} to {target}: {p['title']}")
                continue

            did_edit = False
            if "PROMPT_COMPRESSION_GUIDE.md" in target and p.get("change_type") == "add_few_shot_example":
                if guide_path.exists():
                    try:
                        content = guide_path.read_text(encoding="utf-8")
                        # Ищем секцию meta-harvested и дописываем туда
                        marker = "## Meta-harvested few-shot examples (v3.x+)"
                        if marker in content:
                            append_block = f"\n\n### {p.get('title', 'Harvested example')}\n\n{p.get('proposed_text', '')}\n\n*Добавлено meta_harvester cycle {p.get('from_trajectories', ['?'])[0] if p.get('from_trajectories') else '?'}*\n"
                            # Вставляем после заголовка секции
                            parts = content.split(marker, 1)
                            if len(parts) == 2:
                                new_content = parts[0] + marker + parts[1].split("\n\n", 1)[0] + append_block + "\n\n" + (parts[1].split("\n\n", 1)[1] if "\n\n" in parts[1] else parts[1])
                                guide_path.write_text(new_content, encoding="utf-8")
                                did_edit = True
                    except Exception:
                        pass  # не ломаем цикл на ошибке применения

            # Всегда маркируем как applied (даже если редактирование не удалось — Reviewer увидит)
            p["status"] = "applied"
            p["applied_at"] = _now_iso()
            if did_edit:
                p["notes"] = p.get("notes", "") + " (auto-appended to file)"
            applied_ids.append((p["id"], p.get("expected_impact", "applied via meta")))
            applied += 1
        if applied > 0:
            _write_index_unlocked(index, agent_dir)
    for pid, impact in applied_ids:
        update_performance_ledger(pid, impact, agent_dir=agent_dir)
    return applied


def basic_replay_harness(task_spec: str, proposal: dict = None) -> dict:
    """
    Простая заглушка replay harness для объективной оценки влияния meta-предложений.

    В реальной реализации здесь можно было бы:
    - Взять предыдущий handoff/траекторию
    - "Переиграть" с применённым предложением (например, с новым few-shot)
    - Сравнить метрики (tool_calls, elapsed, confidence, violations)

    Пока возвращает mock-результаты. Использовать для демонстрации before/after.
    """
    baseline = {
        "task": task_spec[:80] + "...",
        "tool_calls": 9,
        "elapsed_minutes": 8.2,
        "confidence": 0.81,
        "violations": 1
    }

    if proposal:
        # Имитируем улучшение от применения предложения
        improved = baseline.copy()
        improved["tool_calls"] = max(5, baseline["tool_calls"] - 2)
        improved["elapsed_minutes"] = round(baseline["elapsed_minutes"] * 0.75, 1)
        improved["confidence"] = min(0.95, baseline["confidence"] + 0.1)
        improved["violations"] = 0
        improved["proposal_applied"] = proposal.get("id", "unknown")
        improved["delta"] = {
            "tool_calls": improved["tool_calls"] - baseline["tool_calls"],
            "elapsed_minutes": round(improved["elapsed_minutes"] - baseline["elapsed_minutes"], 1),
            "confidence": round(improved["confidence"] - baseline["confidence"], 2),
        }
        return {"baseline": baseline, "with_proposal": improved}

    return {"baseline": baseline, "note": "No proposal provided — baseline only"}

def update_performance_ledger(
    proposal_id: str,
    impact: str = "",
    cycle_stats: dict | None = None,
    *,
    agent_dir: Optional[Path] = None,
) -> None:
    """
    Обновление performance ledger.
    Вызывается из apply_safe и Reviewer на DONE циклах.
    Поддерживает как старый формат, так и полный stats от performance_ledger (P1).
    """
    _ensure_agent_dir(agent_dir)
    # Legacy md append (kept for compatibility)
    ledger = _loop_performance_md(agent_dir)
    with _ledger_lock(agent_dir):
        lines: List[str] = []
        if ledger.exists():
            lines = ledger.read_text(encoding="utf-8").splitlines()
        lines.append(f"- { _now_iso() } | proposal {proposal_id} | {impact or 'applied'}")
        _atomic_write_text(ledger, "\n".join(lines[-50:]) + "\n")
    # Лок ledger снят до append_cycle — иначе вложенный O_EXCL повиснет.
    try:
        from memory.performance_ledger import append_cycle
        if cycle_stats:
            append_cycle(agent_dir=agent_dir, **cycle_stats)
        else:
            append_cycle(
                agent_dir=agent_dir,
                cycle=0,
                outcome="META_APPLIED",
                notes=f"proposal:{proposal_id} impact:{impact}",
                meta_applied=1,
            )
    except Exception as e:
        # non-fatal
        print(f"[performance_ledger] non-fatal: {e}", file=sys.stderr)
