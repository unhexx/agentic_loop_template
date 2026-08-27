# -*- coding: utf-8 -*-
"""Пути, локи и RMW индекса траекторий. Единственный импорт agent_lock в meta."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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


def _sft_lock(dest_parent: Path):
    """Секция sft на родителе train.jsonl (дефолтный dest)."""
    return agent_lock(dest_parent, name="sft")


def _ledger_lock(agent_dir: Optional[Path] = None):
    """Секция ledger на родителе LOOP_PERFORMANCE.md."""
    return agent_lock(_loop_performance_md(agent_dir).parent, name="ledger")
