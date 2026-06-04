# -*- coding: utf-8 -*-
"""
Module for collecting and managing the clarification questions pool (Clarification Questions Pool).

Allows the self-improvement loop to continue without blocking on every ambiguity.
Questions are accumulated and escalated in batches at the regularity the user specified
in project settings (by cycles, sprints or phases).

Storage:
- .agent/QUESTIONS_POOL.json — machine state (list of questions + metadata)
- .agent/QUESTIONS_POOL.md — human-readable report for product owner and project lead

Cadence is loaded from .agent/project_config.json (question_pool section).
If the file is missing — defaults from DEVELOPMENT_STANDARDS.md §10 are used.

Usage from loop code (inside .venv):
    from agentic_loop_template.memory.questions_collector import (
        append_question, get_open_questions, should_escalate, mark_reviewed
    )

From command line (after venv activation or via python from .venv):
    python -m agentic_loop_template.memory.questions_collector list
    python -m agentic_loop_template.memory.questions_collector append --question "..." --context "..." --priority high --cycle 12 --source_role reviewer
    python -m agentic_loop_template.memory.questions_collector resolve --ids Q-001,Q-002 --notes "Resolved: use Ubuntu 24.04 + Arch as primary."

All files UTF-8. (Code comments/messages in natural Russian per project rules — see DEVELOPMENT_STANDARDS §1.)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_FREQUENCY = "every_3_cycles"
DEFAULT_N = 3
POOL_JSON = Path(".agent/QUESTIONS_POOL.json")
POOL_MD = Path(".agent/QUESTIONS_POOL.md")
PROJECT_CONFIG = Path(".agent/project_config.json")


def _now_iso() -> str:
    """Текущее время в ISO с таймзоной."""
    return datetime.now(timezone.utc).isoformat()


def _ensure_agent_dir() -> None:
    """Гарантирует существование .agent/ (создаётся при первом append)."""
    POOL_JSON.parent.mkdir(parents=True, exist_ok=True)


def load_config() -> Dict[str, Any]:
    """
    Загружает настройки частоты обработки пула.

    Приоритет:
    1. .agent/project_config.json -> question_pool.{frequency, N, processors...}
    2. Дефолты: every_3_cycles (N=3), processors = ["product_owner", "project_manager"]

    Возвращает dict с ключами:
      frequency: str (every_N_cycles | end_of_sprint | end_of_phase | manual)
      N: int (для every_N_cycles)
      processors: list[str]
      last_processed_cycle: int
    """
    cfg: Dict[str, Any] = {
        "frequency": DEFAULT_FREQUENCY,
        "N": DEFAULT_N,
        "processors": ["product_owner", "project_manager"],
        "last_processed_cycle": 0,
    }

    if PROJECT_CONFIG.exists():
        try:
            raw = json.loads(PROJECT_CONFIG.read_text(encoding="utf-8"))
            qp = raw.get("question_pool", {}) or raw.get("questions_pool_config", {})
            if isinstance(qp, dict):
                if "frequency" in qp:
                    cfg["frequency"] = str(qp["frequency"])
                if "N" in qp:
                    cfg["N"] = int(qp["N"])
                elif "every_n" in qp:
                    cfg["N"] = int(qp["every_n"])
                if "processors" in qp and isinstance(qp["processors"], list):
                    cfg["processors"] = [str(x) for x in qp["processors"]]
                if "last_processed_cycle" in qp:
                    cfg["last_processed_cycle"] = int(qp["last_processed_cycle"])
        except Exception:
            # Не падаем на битый конфиг — используем дефолт и продолжаем
            pass

    # Нормализация every_5_cycles -> N=5
    freq = cfg["frequency"]
    if freq.startswith("every_") and freq.endswith("_cycles"):
        try:
            n_part = freq.split("_")[1]
            cfg["N"] = int(n_part)
            cfg["frequency"] = "every_N_cycles"
        except Exception:
            cfg["frequency"] = DEFAULT_FREQUENCY
            cfg["N"] = DEFAULT_N

    return cfg


def _load_pool_raw() -> Dict[str, Any]:
    """Внутренняя загрузка json пула. Создаёт пустую структуру при отсутствии."""
    _ensure_agent_dir()
    if not POOL_JSON.exists():
        return {"questions": [], "last_escalated_cycle": 0, "updated_at": _now_iso()}
    try:
        return json.loads(POOL_JSON.read_text(encoding="utf-8"))
    except Exception:
        # Повреждённый файл — начинаем заново, старое сохраняем как .bak
        try:
            POOL_JSON.rename(POOL_JSON.with_suffix(".json.bak"))
        except Exception:
            pass
        return {"questions": [], "last_escalated_cycle": 0, "updated_at": _now_iso()}


def _save_pool_raw(data: Dict[str, Any]) -> None:
    """Сохранение + обновление updated_at + генерация человекочитаемого md."""
    data["updated_at"] = _now_iso()
    POOL_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_human_md(data)


def _write_human_md(data: Dict[str, Any]) -> None:
    """Генерирует/перезаписывает .agent/QUESTIONS_POOL.md — для владельцев продукта и PM."""
    lines: List[str] = []
    lines.append("# QUESTIONS_POOL.md — Пул вопросов на уточнение (Clarification Questions Pool)")
    lines.append("")
    lines.append("**Важно:** этот файл обрабатывается владельцем продукта и руководителем проекта.")
    lines.append("Регулярность определяется настройками проекта (см. project_config.json / PROJECT_CONTEXT.md).")
    lines.append("После обработки — используйте `python -m agentic_loop_template.memory.questions_collector resolve ...`")
    lines.append("")
    cfg = load_config()
    lines.append(f"**Текущая частота:** {cfg.get('frequency', DEFAULT_FREQUENCY)} (N={cfg.get('N', DEFAULT_N)})")
    lines.append(f"**Последняя эскалация:** cycle {data.get('last_escalated_cycle', 0)}")
    lines.append(f"**Обновлено:** {data.get('updated_at', '')}")
    lines.append("")
    lines.append("## Открытые вопросы (требуют внимания)")
    lines.append("")

    open_qs = [q for q in data.get("questions", []) if q.get("status", "open") == "open"]
    if not open_qs:
        lines.append("(Пока нет открытых вопросов — петля работает без блокировок)")
    else:
        for q in open_qs:
            qid = q.get("id", "?")
            prio = q.get("priority", "medium")
            src = q.get("source_role", "unknown")
            cyc = q.get("created_cycle", "?")
            quest = q.get("question", "")
            ctx = q.get("context", "")
            lines.append(f"### {qid} [prio:{prio}] (cycle {cyc}, from {src})")
            lines.append(f"**Вопрос:** {quest}")
            if ctx:
                lines.append(f"**Контекст:** {ctx}")
            lines.append("")

    lines.append("## Разрешённые вопросы (история)")
    lines.append("")
    resolved = [q for q in data.get("questions", []) if q.get("status") == "resolved"]
    if not resolved:
        lines.append("(Нет)")
    else:
        for q in resolved[-10:]:  # последние 10
            qid = q.get("id", "?")
            res = q.get("resolution", "")
            by = q.get("resolved_by", "")
            lines.append(f"- {qid}: {res} (by {by})")

    lines.append("")
    lines.append("---")
    lines.append("См. DEVELOPMENT_STANDARDS.md §10 и HANDOFF_SCHEMA.md (поле clarification_questions).")
    lines.append("Настройки частоты: every_N_cycles | end_of_sprint | end_of_phase | manual.")

    POOL_MD.write_text("\n".join(lines), encoding="utf-8")


def _next_id(existing: List[Dict[str, Any]]) -> str:
    """Генерирует следующий id вида Q-003."""
    nums = []
    for q in existing:
        iid = str(q.get("id", ""))
        if iid.startswith("Q-"):
            try:
                nums.append(int(iid[2:]))
            except Exception:
                pass
    nextn = max(nums) + 1 if nums else 1
    return f"Q-{nextn:03d}"


def append_question(
    question: str,
    context: str = "",
    priority: str = "medium",
    source_role: str = "unknown",
    cycle: Optional[int] = None,
    sprint: Optional[str] = None,
    phase: Optional[str] = None,
    suggested_recipient: str = "product_owner",
) -> str:
    """
    Добавляет вопрос в пул (если похожего ещё нет).

    Возвращает id созданного/найденного вопроса.
    Не дублирует по точному тексту вопроса + контексту.
    """
    pool = _load_pool_raw()
    qs = pool.setdefault("questions", [])

    # Простая дедупликация
    for existing in qs:
        if existing.get("status") == "open" and existing.get("question") == question:
            return existing.get("id", "")

    qid = _next_id(qs)
    item = {
        "id": qid,
        "question": question,
        "context": context,
        "priority": priority,
        "source_role": source_role,
        "created_cycle": cycle,
        "created_sprint": sprint,
        "created_phase": phase,
        "suggested_recipient": suggested_recipient,
        "status": "open",
        "created_at": _now_iso(),
        "resolution": None,
        "resolved_at": None,
        "resolved_by": None,
    }
    qs.append(item)
    _save_pool_raw(pool)
    return qid


def get_open_questions() -> List[Dict[str, Any]]:
    """Возвращает список открытых вопросов (для эскалации / отчёта)."""
    pool = _load_pool_raw()
    return [q for q in pool.get("questions", []) if q.get("status", "open") == "open"]


def get_all_questions() -> List[Dict[str, Any]]:
    """Полный список (для отладки и тестов)."""
    pool = _load_pool_raw()
    return list(pool.get("questions", []))


def should_escalate(
    current_cycle: int = 0,
    current_sprint: Optional[str] = None,
    current_phase: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Проверяет, пора ли эскалировать пул по настроенной частоте.

    Возвращает (нужно_эскалировать: bool, причина: str)
    """
    cfg = load_config()
    pool = _load_pool_raw()
    last = int(pool.get("last_escalated_cycle", 0))
    freq = cfg.get("frequency", DEFAULT_FREQUENCY)
    n = int(cfg.get("N", DEFAULT_N))

    open_count = len(get_open_questions())
    if open_count == 0:
        return False, "нет открытых вопросов"

    if freq == "manual":
        return False, "manual (эскалация только по явному запросу)"

    if freq == "end_of_sprint":
        # Простая эвристика: если sprint изменился или открытых > 0 на границе
        if current_sprint and current_sprint != pool.get("_last_sprint"):
            return True, "end_of_sprint (спринт сменился)"
        return False, "end_of_sprint (ждём смены спринта)"

    if freq == "end_of_phase":
        if current_phase and current_phase != pool.get("_last_phase"):
            return True, "end_of_phase (фаза сменилась)"
        return False, "end_of_phase (ждём смены фазы)"

    # every_N_cycles (в т.ч. every_3_cycles)
    if current_cycle > 0 and (current_cycle - last) >= n:
        return True, f"every_{n}_cycles (cycle {current_cycle} - last {last} >= {n})"

    return False, f"ждём ещё {(n - (current_cycle - last)) if current_cycle > last else n} цикл(ов)"


def mark_reviewed(
    ids: List[str],
    resolution_notes: str,
    reviewed_by: str = "product_owner",
) -> int:
    """
    Помечает вопросы как resolved, записывает резолюцию.

    Возвращает количество реально обновлённых записей.
    После этого — уроки рекомендуется положить в LESSONS.md и память.
    """
    if not ids:
        return 0
    pool = _load_pool_raw()
    qs = pool.get("questions", [])
    updated = 0
    now = _now_iso()
    idset = set(ids)
    for q in qs:
        if q.get("id") in idset and q.get("status") != "resolved":
            q["status"] = "resolved"
            q["resolution"] = resolution_notes
            q["resolved_at"] = now
            q["resolved_by"] = reviewed_by
            updated += 1
    if updated > 0:
        pool["last_escalated_cycle"] = pool.get("last_escalated_cycle", 0)
        _save_pool_raw(pool)
    return updated


def sync_from_handoff(handoff_path: Path, current_cycle: Optional[int] = None) -> List[str]:
    """
    Утилита: забирает clarification_questions из handoff JSON и добавляет в пул.
    Полезно для Reviewer в конце цикла.

    Возвращает список добавленных id.
    """
    if not handoff_path.exists():
        return []
    try:
        data = json.loads(handoff_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    added: List[str] = []
    for item in data.get("clarification_questions", []) or []:
        qid = append_question(
            question=item.get("question", ""),
            context=item.get("context", ""),
            priority=item.get("priority", "medium"),
            source_role=item.get("source_role", "reviewer"),
            cycle=current_cycle or item.get("created_cycle"),
            sprint=item.get("created_sprint"),
            phase=item.get("created_phase") or item.get("suggested_phase"),
            suggested_recipient=item.get("suggested_recipient", "product_owner"),
        )
        if qid:
            added.append(qid)
    return added


def escalate_if_needed(
    current_cycle: int = 0,
    current_sprint: Optional[str] = None,
    current_phase: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Если should_escalate() — возвращает compact batch-summary для handoff/уведомления.
    Не меняет last_escalated (это делает mark_reviewed или явный вызов).
    """
    need, reason = should_escalate(current_cycle, current_sprint, current_phase)
    if not need:
        return None
    open_qs = get_open_questions()
    summary = {
        "escalate": True,
        "reason": reason,
        "cycle": current_cycle,
        "open_questions_count": len(open_qs),
        "questions": [
            {
                "id": q["id"],
                "question": q["question"],
                "priority": q.get("priority"),
                "source_role": q.get("source_role"),
            }
            for q in open_qs
        ],
        "suggested_processors": load_config().get("processors"),
    }
    return summary


def _cli() -> None:
    """Простой CLI для использования из петли / владельцами."""
    p = argparse.ArgumentParser(description="Clarification Questions Pool collector")
    sub = p.add_subparsers(dest="cmd", required=True)

    # append
    ap = sub.add_parser("append", help="Добавить вопрос в пул")
    ap.add_argument("--question", required=True)
    ap.add_argument("--context", default="")
    ap.add_argument("--priority", default="medium", choices=["low", "medium", "high", "blocking"])
    ap.add_argument("--source_role", default="unknown")
    ap.add_argument("--cycle", type=int, default=None)
    ap.add_argument("--sprint", default=None)
    ap.add_argument("--phase", default=None)
    ap.add_argument("--recipient", default="product_owner")

    # list
    sub.add_parser("list", help="Показать открытые вопросы (json)")

    # resolve
    rp = sub.add_parser("resolve", help="Пометить вопросы решёнными")
    rp.add_argument("--ids", required=True, help="Q-001,Q-002")
    rp.add_argument("--notes", required=True)
    rp.add_argument("--by", default="product_owner")

    # check
    cp = sub.add_parser("check-escalate", help="Проверить, пора ли эскалировать")
    cp.add_argument("--cycle", type=int, default=0)
    cp.add_argument("--sprint", default=None)
    cp.add_argument("--phase", default=None)

    # sync
    sp = sub.add_parser("sync-handoff", help="Забрать вопросы из handoff json и добавить в пул")
    sp.add_argument("--handoff", required=True, type=Path)
    sp.add_argument("--cycle", type=int, default=None)

    args = p.parse_args()

    if args.cmd == "append":
        qid = append_question(
            args.question, args.context, args.priority, args.source_role,
            args.cycle, args.sprint, args.phase, args.recipient
        )
        print(json.dumps({"id": qid}, ensure_ascii=False))
    elif args.cmd == "list":
        print(json.dumps(get_open_questions(), ensure_ascii=False, indent=2))
    elif args.cmd == "resolve":
        ids = [x.strip() for x in args.ids.split(",") if x.strip()]
        n = mark_reviewed(ids, args.notes, args.by)
        print(json.dumps({"updated": n}, ensure_ascii=False))
    elif args.cmd == "check-escalate":
        need, reason = should_escalate(args.cycle, args.sprint, args.phase)
        print(json.dumps({"escalate": need, "reason": reason}, ensure_ascii=False))
    elif args.cmd == "sync-handoff":
        added = sync_from_handoff(args.handoff, args.cycle)
        print(json.dumps({"added": added}, ensure_ascii=False))


if __name__ == "__main__":
    _cli()
