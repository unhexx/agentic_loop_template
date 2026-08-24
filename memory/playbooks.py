# -*- coding: utf-8 -*-
"""
Playbooks & Knowledge Objects System for the Agentic Loop (v3.x+).

Позволяет петле полноценно работать со всеми инструментами и фазами непрерывного цикла разработки,
используя структурированные, самоулучшающиеся playbooks (и связанные объекты: WorkflowBlueprint, ToolProfile и т.д.).

Следует лучшим практикам (ACE — Agentic Context Engineering, Reflexion, Generative Agents):
- Bullet = атомарная единица знания (стратегия/эвристика/пример/антипаттерн)
- Эффективность обновляется через рефлексию и курацию
- Селектор выбирает релевантные bullets по скорингу (эффективность + свежесть + релевантность)
- Куратор мутирует playbook на основе рефлексий и траекторий

Интегрируется со всеми существующими механизмами (memory, meta_harvester, handoffs, роли).
Экспортируемо, версионируемо, готово к Agentix Hub / marketplace.

Хранение:
- .agent/PLAYBOOKS.json — индекс + все playbooks
- .agent/PLAYBOOKS/<scope>.md — человекочитаемые представления
- Опционально .agent/PLAYBOOKS/ для детальных

Моделировано 1-в-1 по стилю questions_collector.py + meta_harvester.py:
- Только stdlib + UTF-8
- Конфиг из .agent/project_config.json (секция playbooks)
- CLI + Python API
- Авто .md отчёты
- Безопасные мутации + revert_hint

Использование (Reviewer / Orchestrator / Coder):
    python -m agentic_loop_template.memory.playbooks select --query "git sync" --scopes "phase:git-sync,tool:git" --k 5
    python -m agentic_loop_template.memory.playbooks curate --from-handoff .agent/last_handoff.json --cycle 35
    python -m agentic_loop_template.memory.playbooks seed --from-standards

Все мутации коммитятся естественным русским голосом живого разработчика (см. DEVELOPMENT_STANDARDS §1).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from memory.logutil import get_logger

log = get_logger("memory.playbooks")

DEFAULT_ENABLED = True
DEFAULT_AUTO_CURATE = True
DEFAULT_MAX_BULLETS = 50
DEFAULT_K = 5
DEFAULT_MIN_EFFECT = 0.5

PLAYBOOKS_INDEX = Path(".agent/PLAYBOOKS.json")
PLAYBOOKS_DIR = Path(".agent/PLAYBOOKS")
PROJECT_CONFIG = Path(".agent/project_config.json")


def _now_iso() -> str:
    """Текущее время в ISO с таймзоной."""
    return datetime.now(timezone.utc).isoformat()


def _ensure_agent_dir() -> None:
    """Гарантирует существование .agent/PLAYBOOKS/."""
    PLAYBOOKS_INDEX.parent.mkdir(parents=True, exist_ok=True)
    PLAYBOOKS_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> Dict[str, Any]:
    """
    Загружает настройки playbooks.

    Приоритет:
    1. .agent/project_config.json -> playbooks.{enabled, auto_curate, ...}
    2. Дефолты.

    Возвращает dict.
    """
    cfg: Dict[str, Any] = {
        "enabled": DEFAULT_ENABLED,
        "auto_curate": DEFAULT_AUTO_CURATE,
        "max_bullets_per_playbook": DEFAULT_MAX_BULLETS,
        "default_k": DEFAULT_K,
        "min_effectiveness": DEFAULT_MIN_EFFECT,
        "scopes": ["global", "role:*", "tool:*", "phase:*"],
    }

    if PROJECT_CONFIG.exists():
        try:
            raw = json.loads(PROJECT_CONFIG.read_text(encoding="utf-8"))
            pb = raw.get("playbooks", {}) or {}
            if isinstance(pb, dict):
                for k in ("enabled", "auto_curate", "max_bullets_per_playbook", "default_k", "min_effectiveness"):
                    if k in pb:
                        cfg[k] = pb[k]
                if "scopes" in pb and isinstance(pb["scopes"], list):
                    cfg["scopes"] = [str(x) for x in pb["scopes"]]
        except Exception:
            pass

    return cfg


def _load_index() -> Dict[str, Any]:
    """Внутренняя загрузка индекса playbooks."""
    _ensure_agent_dir()
    if not PLAYBOOKS_INDEX.exists():
        return {"playbooks": {}, "updated_at": _now_iso(), "version": "3.3-playbooks"}
    try:
        return json.loads(PLAYBOOKS_INDEX.read_text(encoding="utf-8"))
    except Exception:
        log.warning("playbooks index corrupt, renaming to bak: %s", PLAYBOOKS_INDEX)
        try:
            PLAYBOOKS_INDEX.rename(PLAYBOOKS_INDEX.with_suffix(".json.bak"))
        except Exception:
            pass
        return {"playbooks": {}, "updated_at": _now_iso(), "version": "3.3-playbooks"}


def _save_index(data: Dict[str, Any]) -> None:
    """Сохранение + обновление md + timestamp."""
    data["updated_at"] = _now_iso()
    PLAYBOOKS_INDEX.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_human_views(data)


def _write_human_views(data: Dict[str, Any]) -> None:
    """Генерирует человекочитаемые .md для каждого playbook'а + общий обзор."""
    _ensure_agent_dir()
    lines = [
        "# PLAYBOOKS.md — Структурированные Playbooks и Knowledge Objects",
        "",
        "Playbooks — основной слой знаний для полноценной работы со всеми инструментами и фазами цикла.",
        "Каждый цикл использует релевантные bullets через select, после рефлексии — curate.",
        "Следуем ACE (effectiveness + recency + similarity) + Reflexion.",
        "",
        f"**Обновлено:** {data.get('updated_at')}",
        "",
        "## Доступные Playbooks",
    ]

    pbs = data.get("playbooks", {})
    if not pbs:
        lines.append("(Пока нет playbooks — запустите seed)")
    else:
        for pid, pb in pbs.items():
            lines.append(f"- **{pid}** (scope={pb.get('scope')}, bullets={len(pb.get('bullets', []))})")
            lines.append(f"  last_curated: {pb.get('last_curated', 'never')}")

    # Пишем общий
    (PLAYBOOKS_DIR / "overview.md").write_text("\n".join(lines), encoding="utf-8")

    # Отдельные views
    for pid, pb in pbs.items():
        pb_lines = [f"# Playbook: {pid}", f"Scope: {pb.get('scope')}", ""]
        for b in pb.get("bullets", []):
            eff = b.get("effectiveness", 0.5)
            pb_lines.append(f"- [{eff:.2f}] {b.get('content', '')}  (tags: {b.get('tags', [])})")
        (PLAYBOOKS_DIR / f"{pid}.md").write_text("\n".join(pb_lines), encoding="utf-8")


def _score_bullet(bullet: Dict[str, Any], query_lower: str, now_ts: str) -> float:
    """ACE-style scoring: 0.5 effectiveness + 0.3 recency + 0.2 relevance."""
    eff = float(bullet.get("effectiveness", 0.5))
    # recency (простая)
    last = bullet.get("last_used") or "1970-01-01T00:00:00+00:00"
    recency = 0.7 if last > now_ts[:10] else 0.3   # очень грубо, достаточно для старта
    # relevance
    content = (bullet.get("content") or "").lower()
    tags = " ".join(bullet.get("tags", [])).lower()
    relevance = 0.9 if query_lower in content or query_lower in tags else 0.3
    return 0.5 * eff + 0.3 * recency + 0.2 * relevance


def select_bullets(query: str, scopes: Optional[List[str]] = None, k: int = 5, min_effect: float = 0.5) -> List[Dict[str, Any]]:
    """Выбирает лучшие bullets для запроса и скоупов. Возвращает отсортированные по score."""
    cfg = load_config()
    if not cfg.get("enabled"):
        return []

    index = _load_index()
    pbs = index.get("playbooks", {})
    now = _now_iso()
    q = query.lower()

    candidates: List[Tuple[float, Dict[str, Any], str]] = []

    for pid, pb in pbs.items():
        pb_scope = pb.get("scope", "")
        if scopes and not any(s in pb_scope or pb_scope.startswith(s.split(":")[0]) for s in scopes):
            continue
        for b in pb.get("bullets", []):
            if float(b.get("effectiveness", 0)) < min_effect:
                continue
            score = _score_bullet(b, q, now)
            candidates.append((score, b, pid))

    candidates.sort(key=lambda x: x[0], reverse=True)
    result = []
    for score, b, pid in candidates[:k]:
        r = dict(b)
        r["_score"] = round(score, 3)
        r["_playbook"] = pid
        result.append(r)
    return result


def curate_from_reflection(reflection: Dict[str, Any], playbook_id: str) -> Dict[str, Any]:
    """
    Курация playbook на основе рефлексии цикла.
    Добавляет/улучшает/демотирует bullets. Возвращает мутацию.
    """
    index = _load_index()
    pbs = index.setdefault("playbooks", {})
    if playbook_id not in pbs:
        pbs[playbook_id] = {"scope": "auto", "bullets": [], "last_curated": _now_iso()}

    pb = pbs[playbook_id]
    bullets = pb.setdefault("bullets", [])

    mutation = {"added": 0, "updated": 0, "demoted": 0, "playbook": playbook_id}

    # Простая эвристика: lessons -> новые bullets
    for lesson in reflection.get("lessons_learned", []):
        if len(lesson) < 10:
            continue
        # Ищем похожий
        found = False
        for b in bullets:
            if lesson.lower()[:30] in b.get("content", "").lower():
                b["effectiveness"] = min(1.0, b.get("effectiveness", 0.5) + 0.1)
                b["last_used"] = _now_iso()
                b["usage_count"] = b.get("usage_count", 0) + 1
                mutation["updated"] += 1
                found = True
                break
        if not found:
            bullets.append({
                "id": f"b-{len(bullets)+1:04d}",
                "content": lesson,
                "tags": ["auto-from-reflection"],
                "effectiveness": 0.65,
                "recency_ts": _now_iso(),
                "usage_count": 1,
                "source": reflection.get("cycle", "unknown"),
            })
            mutation["added"] += 1

    # Демотив за плохие исходы (если есть)
    for issue in reflection.get("issues_found", []):
        for b in bullets:
            if issue.get("pattern", "").lower() in b.get("content", "").lower():
                b["effectiveness"] = max(0.1, b.get("effectiveness", 0.5) - 0.15)
                mutation["demoted"] += 1

    # Ограничение размера
    if len(bullets) > load_config().get("max_bullets_per_playbook", 50):
        bullets.sort(key=lambda x: x.get("effectiveness", 0), reverse=True)
        bullets[:] = bullets[:50]

    pb["last_curated"] = _now_iso()
    _save_index(index)
    return mutation


def seed_initial_playbooks() -> int:
    """Сидирует начальные playbooks из текущих стандартов и паттернов (bootstrap)."""
    _ensure_agent_dir()
    index = _load_index()
    pbs = index.setdefault("playbooks", {})

    # Глобальный dev + git sync playbook (из STANDARDS + прошлого опыта)
    if "global-dev" not in pbs:
        pbs["global-dev"] = {
            "scope": "global",
            "name": "Global Development & Process Best Practices",
            "bullets": [
                {"id": "b-0001", "content": "Всегда начинай цикл с git self-cycle §11 и memory/playbooks snapshot.", "tags": ["orchestrator", "git"], "effectiveness": 0.95},
                {"id": "b-0002", "content": "Используй PLAN → ACT (≤3 calls) → REFLECT. Никогда не пропускай рефлексию.", "tags": ["all-roles"], "effectiveness": 0.9},
            ],
            "last_curated": _now_iso(),
        }

    if "tool-git" not in pbs:
        pbs["tool-git"] = {
            "scope": "tool:git",
            "name": "Git & Sync Playbook",
            "bullets": [
                {"id": "b-0101", "content": "Для кросс-клона используй git -C /path ... + явный маркер SYNC_DONE / VerifyOnly.", "tags": ["sync", "verification"], "effectiveness": 0.92},
            ],
            "last_curated": _now_iso(),
        }

    if "tool-ledger" not in pbs:
        pbs["tool-ledger"] = {
            "scope": "tool:performance_ledger",
            "name": "Performance & ROI Tracking",
            "bullets": [
                {"id": "b-0201", "content": "На высококачественном DONE всегда вызывай append в performance_ledger + включай 'performance' в handoff.", "tags": ["P1", "metrics"], "effectiveness": 0.88},
            ],
            "last_curated": _now_iso(),
        }

    # New: WorkflowBlueprint for full continuous dev cycle (per user request for other objects)
    if "continuous-dev-cycle" not in pbs:
        pbs["continuous-dev-cycle"] = {
            "scope": "workflow:full-cycle",
            "name": "Continuous Development Cycle Blueprint",
            "bullets": [
                {"id": "b-wf01", "content": "Orchestrator: always select playbooks for planning + tools before SPRINTPLAN.", "tags": ["orchestrator", "playbook"], "effectiveness": 0.9},
                {"id": "b-wf02", "content": "Every role: use playbooks for tool decisions. Record usage for curate.", "tags": ["all-roles"], "effectiveness": 0.88},
                {"id": "b-wf03", "content": "Reviewer: mandatory playbook curation + meta harvest on high quality DONE.", "tags": ["reviewer", "self-improvement"], "effectiveness": 0.92},
            ],
            "last_curated": _now_iso(),
        }

    _save_index(index)
    return len(pbs)


HUB_INDEX_PATH = Path(".agent/HUB_INDEX.json")


def list_playbooks() -> List[Dict[str, Any]]:
    """Возвращает каталог всех playbooks для Hub discovery."""
    index = _load_index()
    items: List[Dict[str, Any]] = []
    for pid, pb in index.get("playbooks", {}).items():
        bullets = pb.get("bullets", [])
        effs = [float(b.get("effectiveness", 0.5)) for b in bullets]
        avg_eff = sum(effs) / len(effs) if effs else 0.0
        items.append({
            "id": pid,
            "scope": pb.get("scope", ""),
            "name": pb.get("name", pid),
            "bullet_count": len(bullets),
            "avg_effectiveness": round(avg_eff, 3),
            "last_curated": pb.get("last_curated"),
            "install_path": f".agent/PLAYBOOKS/{pid}.md",
        })
    return items


def discover_items(query: str, scope: Optional[str] = None, k: int = 10) -> List[Dict[str, Any]]:
    """Поиск bullets по запросу для Hub search UI."""
    scopes = [scope] if scope else None
    results = select_bullets(query, scopes=scopes, k=k, min_effect=0.0)
    return [
        {
            "playbook_id": r.get("_playbook"),
            "bullet_id": r.get("id"),
            "content": r.get("content"),
            "tags": r.get("tags", []),
            "effectiveness": r.get("effectiveness"),
            "score": r.get("_score"),
        }
        for r in results
    ]


def export_hub_index(fmt: str = "hub", output: Optional[Path] = None) -> Dict[str, Any]:
    """Экспортирует индекс для Agentix Hub (discovery + install metadata)."""
    index = _load_index()
    items = list_playbooks()
    hub_data: Dict[str, Any] = {
        "version": "1.0",
        "generated_at": _now_iso(),
        "source": str(PLAYBOOKS_INDEX),
        "item_count": len(items),
        "items": items,
        "playbooks": index.get("playbooks", {}),
    }
    if fmt == "hub":
        out = output or HUB_INDEX_PATH
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(hub_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return hub_data


def _cli() -> None:
    p = argparse.ArgumentParser(description="Playbooks & Knowledge Objects for Agentix loop")
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("select", help="Выбрать релевантные bullets")
    sp.add_argument("--query", required=True)
    sp.add_argument("--scopes", default="global")
    sp.add_argument("--k", type=int, default=5)

    cp = sub.add_parser("curate", help="Прокурация из рефлексии/хандофа")
    cp.add_argument("--from-handoff", dest="handoff", required=True)
    cp.add_argument("--cycle", type=int, required=True)

    sp2 = sub.add_parser("seed", help="Засеять начальные playbooks из стандартов")
    sp2.add_argument("--from-standards", action="store_true")

    lp = sub.add_parser("list", help="Каталог playbooks для Hub")
    ep = sub.add_parser("export", help="Экспорт HUB_INDEX.json")
    ep.add_argument("--format", choices=["json", "hub"], default="hub")
    ep.add_argument("--output", default=str(HUB_INDEX_PATH))

    dp = sub.add_parser("discover", help="Поиск bullets для Hub")
    dp.add_argument("--query", required=True)
    dp.add_argument("--scope", default=None)
    dp.add_argument("--k", type=int, default=10)

    args = p.parse_args()
    if args.cmd == "select":
        scopes = [s.strip() for s in args.scopes.split(",")]
        res = select_bullets(args.query, scopes, args.k)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.cmd == "curate":
        with open(args.handoff, encoding="utf-8") as f:
            h = json.load(f)
        refl = {
            "lessons_learned": h.get("lessons_learned", []),
            "issues_found": h.get("issues_found", []),
            "cycle": args.cycle,
        }
        mut = curate_from_reflection(refl, "global-dev")
        print("Curated mutation:", mut)
    elif args.cmd == "seed":
        n = seed_initial_playbooks()
        print(f"Seeded {n} playbooks")
    elif args.cmd == "list":
        print(json.dumps(list_playbooks(), ensure_ascii=False, indent=2))
    elif args.cmd == "export":
        data = export_hub_index(fmt=args.format, output=Path(args.output))
        print(json.dumps({"exported": args.output, "item_count": data["item_count"]}, ensure_ascii=False))
    elif args.cmd == "discover":
        res = discover_items(args.query, scope=args.scope, k=args.k)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        p.print_help()


if __name__ == "__main__":
    _cli()
