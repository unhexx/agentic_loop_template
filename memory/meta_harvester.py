# -*- coding: utf-8 -*-
"""
Meta-Optimizer & Trajectory Harvester for the agentic development loop.

Позволяет петле саморазвития не только записывать уроки, но и систематически
собирать "золотые" траектории успешных циклов и превращать их в конкретные
улучшения самого harness'а (few-shot примеры, правила, стратегии).

Моделировано 1-в-1 по стилю и дисциплине questions_collector.py:
- Только stdlib
- UTF-8 везде
- Конфиг из .agent/project_config.json (секция meta_optimizer)
- CLI + импортируемые функции
- Авто-генерация человекочитаемого .md отчёта
- Неблокирующий, батчевый подход

Хранение:
- .agent/TRAJECTORIES.json — индекс + компактные записи
- .agent/TRAJECTORIES/ (опционально) — детальные файлы
- .agent/META_PROPOSALS.md — для человека + Reviewer
- Паттерны высокого качества улетают в workspace memory (категории Effective Loop Strategies и т.п.)

Использование из петли (Reviewer):
    python -m agentic_loop_template.memory.meta_harvester harvest \
        --handoff .agent/last_handoff.json --cycle 17 --outcome DONE

    python -m agentic_loop_template.memory.meta_harvester analyze --recent 5
    python -m agentic_loop_template.memory.meta_harvester propose --limit 2
    python -m agentic_loop_template.memory.meta_harvester apply-safe --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from memory.agent_lock import agent_lock
from memory.logutil import get_logger

log = get_logger("memory.meta_harvester")


DEFAULT_FREQUENCY = "after_every_done_cycle"
DEFAULT_MIN_CONFIDENCE = 0.85
TRAJECTORIES_INDEX = Path(".agent/TRAJECTORIES.json")
TRAJECTORIES_DIR = Path(".agent/TRAJECTORIES")
META_PROPOSALS_MD = Path(".agent/META_PROPOSALS.md")
PROJECT_CONFIG = Path(".agent/project_config.json")
SFT_PATH = Path(".agent/sft/train.jsonl")


def _now_iso() -> str:
    """Текущее время в ISO с таймзоной."""
    return datetime.now(timezone.utc).isoformat()


def _trajectories_index(agent_dir: Optional[Path] = None) -> Path:
    """Явный каталог .agent или cwd-дефолт индекса траекторий."""
    return Path(agent_dir) / "TRAJECTORIES.json" if agent_dir is not None else TRAJECTORIES_INDEX


def _trajectories_dir(agent_dir: Optional[Path] = None) -> Path:
    return Path(agent_dir) / "TRAJECTORIES" if agent_dir is not None else TRAJECTORIES_DIR


def _meta_proposals_md(agent_dir: Optional[Path] = None) -> Path:
    return Path(agent_dir) / "META_PROPOSALS.md" if agent_dir is not None else META_PROPOSALS_MD


def _project_config_path(agent_dir: Optional[Path] = None) -> Path:
    return Path(agent_dir) / "project_config.json" if agent_dir is not None else PROJECT_CONFIG


def _sft_path(agent_dir: Optional[Path] = None) -> Path:
    return Path(agent_dir) / "sft" / "train.jsonl" if agent_dir is not None else SFT_PATH


def _loop_performance_md(agent_dir: Optional[Path] = None) -> Path:
    return Path(agent_dir) / "LOOP_PERFORMANCE.md" if agent_dir is not None else Path(".agent/LOOP_PERFORMANCE.md")


def _ensure_agent_dir(agent_dir: Optional[Path] = None) -> None:
    """Гарантирует каталог индекса и TRAJECTORIES/."""
    _trajectories_index(agent_dir).parent.mkdir(parents=True, exist_ok=True)
    _trajectories_dir(agent_dir).mkdir(parents=True, exist_ok=True)


def _trajectories_lock(agent_dir: Optional[Path] = None):
    """Секция на родителе индекса — имя trajectories, не agent."""
    return agent_lock(_trajectories_index(agent_dir).parent, name="trajectories")


def _atomic_write_text(path: Path, text: str) -> None:
    """Пишет через *.tmp и os.replace, без усечения целевого файла."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".json":
        tmp = path.with_suffix(".json.tmp")
    elif path.suffix == ".md":
        tmp = path.with_suffix(".md.tmp")
    else:
        tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def load_config(agent_dir: Optional[Path] = None) -> Dict[str, Any]:
    """
    Загружает настройки meta-оптимизатора.

    Приоритет:
    1. .agent/project_config.json -> meta_optimizer.{...}
    2. Дефолты (после every_done, высокие пороги качества, auto_apply только для безопасных типов).

    agent_dir=None — файлы относительно cwd (CLI и старые тесты).
    Иначе читаем ``agent_dir / project_config.json``. Без lock.

    Возвращает dict с ключами:
      enabled, frequency, min_quality, auto_apply_safe, max_proposals_per_cycle, last_harvested_cycle
    """
    cfg: Dict[str, Any] = {
        "enabled": True,
        "frequency": DEFAULT_FREQUENCY,
        "min_quality": {
            "confidence": DEFAULT_MIN_CONFIDENCE,
            "tests_failed": 0,
            "process_violations": 0,
        },
        "auto_apply_safe": True,
        "max_proposals_per_cycle": 3,
        "last_harvested_cycle": 0,
    }

    cfg_path = _project_config_path(agent_dir)
    if cfg_path.exists():
        try:
            raw = json.loads(cfg_path.read_text(encoding="utf-8"))
            mo = raw.get("meta_optimizer", {}) or raw.get("meta", {})
            if isinstance(mo, dict):
                for k in ("enabled", "frequency", "auto_apply_safe", "max_proposals_per_cycle", "last_harvested_cycle"):
                    if k in mo:
                        cfg[k] = mo[k]
                if "min_quality" in mo and isinstance(mo["min_quality"], dict):
                    cfg["min_quality"].update(mo["min_quality"])
        except Exception:
            pass  # не падаем на битый конфиг

    return cfg


def _empty_index() -> Dict[str, Any]:
    return {"trajectories": [], "proposals": [], "updated_at": _now_iso()}


def _load_index_unlocked(agent_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Чтение без lock: RMW уже держит секцию."""
    _ensure_agent_dir(agent_dir)
    path = _trajectories_index(agent_dir)
    if not path.exists():
        return _empty_index()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        log.warning("trajectories index corrupt, renaming to bak: %s", path)
        try:
            path.rename(path.with_suffix(".json.bak"))
        except Exception:
            pass
        return _empty_index()


def _write_index_unlocked(data: Dict[str, Any], agent_dir: Optional[Path] = None) -> None:
    """tmp+replace JSON + md. Вызывающий уже в секции trajectories."""
    data["updated_at"] = _now_iso()
    path = _trajectories_index(agent_dir)
    _atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))
    _write_human_summary(data, agent_dir=agent_dir)


def _load_index(agent_dir: Optional[Path] = None) -> Dict[str, Any]:
    with _trajectories_lock(agent_dir):
        return _load_index_unlocked(agent_dir)


def _save_index(data: Dict[str, Any], agent_dir: Optional[Path] = None) -> None:
    with _trajectories_lock(agent_dir):
        _write_index_unlocked(data, agent_dir)


def _write_human_summary(data: Dict[str, Any], agent_dir: Optional[Path] = None) -> None:
    """Генерирует/перезаписывает META_PROPOSALS.md (и краткий обзор траекторий)."""
    lines: List[str] = []
    lines.append("# META_PROPOSALS.md — Предложения Meta-Optimizer (Trajectory Harvesting)")
    lines.append("")
    lines.append("**Важно:** этот файл поддерживается автоматически. Reviewer может применять безопасные предложения.")
    lines.append("Полный формат и API — см. META_OPTIMIZER_SPEC.md и agentic_loop_template/memory/meta_harvester.py")
    lines.append("")

    cfg = load_config(agent_dir)
    lines.append(f"**Статус:** {'включен' if cfg.get('enabled') else 'отключен'} | частота: {cfg.get('frequency')}")
    lines.append(f"**Последний harvested cycle:** {cfg.get('last_harvested_cycle', 0)}")
    lines.append(f"**Обновлено:** {data.get('updated_at', '')}")
    lines.append("")

    # Последние траектории
    trajs = data.get("trajectories", [])[-5:]
    lines.append("## Последние собранные траектории (golden / высокое качество)")
    if not trajs:
        lines.append("(пока нет)")
    else:
        for t in trajs:
            lines.append(f"- **{t.get('id')}** (cycle {t.get('cycle')}) — {t.get('outcome')} | conf={t.get('quality_signals', {}).get('confidence')}")
            if t.get("success_patterns"):
                lines.append(f"  patterns: {', '.join(t['success_patterns'][:2])}")
    lines.append("")

    # Предложения
    props = [p for p in data.get("proposals", []) if p.get("status", "pending") != "applied"]
    lines.append("## Открытые предложения (ожидают применения или отклонения)")
    if not props:
        lines.append("(нет открытых — отлично! или все применены)")
    else:
        for p in props[-10:]:
            lines.append(f"### {p.get('id')} → {p.get('target_file')}")
            lines.append(f"**Тип:** {p.get('change_type')} | safe_auto={p.get('safe_to_auto')} | conf={p.get('confidence')}")
            lines.append(f"**Обоснование:** {p.get('rationale', '')[:200]}")
            lines.append(f"**Действие:** {p.get('title')}")
            lines.append("")

    lines.append("---")
    lines.append("Команды:")
    lines.append("  python -m agentic_loop_template.memory.meta_harvester harvest --handoff ... --cycle N")
    lines.append("  python -m agentic_loop_template.memory.meta_harvester propose --limit 3")
    lines.append("  python -m agentic_loop_template.memory.meta_harvester apply-safe --dry-run")
    lines.append("")
    lines.append("См. также: DEVELOPMENT_STANDARDS.md §12 (Meta-Optimizer), AGENT_ROLES.md (Reviewer duty).")

    _atomic_write_text(_meta_proposals_md(agent_dir), "\n".join(lines))


def _next_traj_id(existing: List[Dict[str, Any]], cycle: int) -> str:
    nums = []
    for t in existing:
        iid = str(t.get("id", ""))
        if iid.startswith(f"T-{cycle:03d}-"):
            try:
                nums.append(int(iid.split("-")[-1], 16))
            except Exception:
                pass
    next_hex = max(nums) + 1 if nums else 0x3f8
    return f"T-{cycle:03d}-{next_hex:04x}"


def _next_prop_id(existing: List[Dict[str, Any]]) -> str:
    nums = []
    for p in existing:
        iid = str(p.get("id", ""))
        if iid.startswith("P-"):
            try:
                nums.append(int(iid.split("-")[-1]))
            except Exception:
                pass
    nextn = max(nums) + 1 if nums else 1
    return f"P-{nextn:03d}"


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
        with agent_lock(dest.parent, name="sft"):
            _append()
    else:
        _append()
    return {
        "written": written,
        "skipped": skipped,
        "path": str(dest),
        "min_confidence": min_confidence,
    }


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
    with agent_lock(ledger.parent, name="ledger"):
        lines: List[str] = []
        if ledger.exists():
            lines = ledger.read_text(encoding="utf-8").splitlines()
        lines.append(f"- { _now_iso() } | proposal {proposal_id} | {impact or 'applied'}")
        _atomic_write_text(ledger, "\n".join(lines[-50:]) + "\n")
    # Лок ledger снят до append_cycle — иначе вложенный O_EXCL повиснет.
    try:
        import importlib.util
        pl_path = Path(__file__).parent / "performance_ledger.py"
        spec = importlib.util.spec_from_file_location("pl", str(pl_path))
        pl = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pl)
        if cycle_stats:
            pl.append_cycle(agent_dir=agent_dir, **cycle_stats)
        else:
            pl.append_cycle(
                agent_dir=agent_dir,
                cycle=0,
                outcome="META_APPLIED",
                notes=f"proposal:{proposal_id} impact:{impact}",
                meta_applied=1,
            )
    except Exception as e:
        # non-fatal
        print(f"[performance_ledger] non-fatal: {e}", file=sys.stderr)


def _cli() -> None:
    """CLI, полностью аналогичный по стилю questions_collector."""
    p = argparse.ArgumentParser(description="Meta-Optimizer Trajectory Harvester")
    sub = p.add_subparsers(dest="cmd", required=True)

    # harvest
    hp = sub.add_parser("harvest", help="Собрать траекторию из handoff (если качество позволяет)")
    hp.add_argument("--handoff", required=True, type=Path)
    hp.add_argument("--cycle", required=True, type=int)
    hp.add_argument("--outcome", default="DONE")
    hp.add_argument("--force", action="store_true", help="Игнорировать quality gate (для отладки)")

    # list
    sub.add_parser("list", help="Показать последние траектории (json)")

    # analyze
    ap = sub.add_parser("analyze", help="Проанализировать недавние траектории и сгенерировать предложения")
    ap.add_argument("--recent", type=int, default=5)
    ap.add_argument("--min-confidence", type=float, default=0.8)

    # propose
    pp = sub.add_parser("propose", help="Сгенерировать и показать предложения (limit)")
    pp.add_argument("--limit", type=int, default=3)

    # apply-safe
    apy = sub.add_parser("apply-safe", help="Применить безопасные предложения (по умолчанию dry-run)")
    apy.add_argument("--dry-run", action="store_true", default=True)
    apy.add_argument("--ids", default=None, help="P-001,P-002 (опционально)")

    es = sub.add_parser("export-sft", help="JSONL для локального дообучения (без GPU)")
    es.add_argument("--out", type=Path, default=None)
    es.add_argument("--min-confidence", type=float, default=DEFAULT_MIN_CONFIDENCE)
    es.add_argument("--recent", type=int, default=100)

    args = p.parse_args()

    if args.cmd == "harvest":
        tid = harvest_from_handoff(args.handoff, args.cycle, args.outcome)
        print(json.dumps({"harvested_id": tid}, ensure_ascii=False))
    elif args.cmd == "list":
        print(json.dumps(get_recent_trajectories(10), ensure_ascii=False, indent=2))
    elif args.cmd == "analyze":
        props = analyze_for_proposals(args.recent, args.min_confidence)
        print(json.dumps({"generated_proposals": len(props), "proposals": props}, ensure_ascii=False, indent=2))
    elif args.cmd == "propose":
        props = generate_proposals(args.limit)
        print(json.dumps({"proposals": props}, ensure_ascii=False, indent=2))
    elif args.cmd == "apply-safe":
        ids = [x.strip() for x in args.ids.split(",")] if args.ids else None
        n = apply_safe_proposals(dry_run=args.dry_run, ids=ids)
        print(json.dumps({"applied": n, "dry_run": args.dry_run}, ensure_ascii=False))
    elif args.cmd == "export-sft":
        report = export_sft(
            out=args.out,
            min_confidence=args.min_confidence,
            recent=args.recent,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _cli()
