# -*- coding: utf-8 -*-
"""Публичный загрузчик Meta-Optimizer. Тела — memory.meta.{generator,reflector,curator}."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

__all__ = [
    "harvest_from_handoff",
    "get_recent_trajectories",
    "analyze_for_proposals",
    "generate_proposals",
    "apply_safe_proposals",
    "seed_example_trajectory",
    "update_performance_ledger",
    "export_sft",
    "load_config",
    "basic_replay_harness",
    "DEFAULT_MIN_CONFIDENCE",
    "DEFAULT_FREQUENCY",
    "TRAJECTORIES_INDEX",
    "TRAJECTORIES_DIR",
    "META_PROPOSALS_MD",
    "PROJECT_CONFIG",
    "SFT_PATH",
]

_GENERATOR = {
    "harvest_from_handoff",
    "get_recent_trajectories",
    "seed_example_trajectory",
}
_REFLECTOR = {"analyze_for_proposals", "generate_proposals"}
_CURATOR = {
    "apply_safe_proposals",
    "export_sft",
    "update_performance_ledger",
    "basic_replay_harness",
}
_STORE = {
    "load_config",
    "DEFAULT_MIN_CONFIDENCE",
    "DEFAULT_FREQUENCY",
    "TRAJECTORIES_INDEX",
    "TRAJECTORIES_DIR",
    "META_PROPOSALS_MD",
    "PROJECT_CONFIG",
    "SFT_PATH",
}


def __getattr__(name: str):
    """PEP 562: тело подгружается при первом обращении к имени."""
    if name in _STORE:
        from memory.meta import store as mod
    elif name in _GENERATOR:
        from memory.meta import generator as mod
    elif name in _REFLECTOR:
        from memory.meta import reflector as mod
    elif name in _CURATOR:
        from memory.meta import curator as mod
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(mod, name)
    globals()[name] = value  # cache
    return value


def __dir__():
    return sorted(list(globals()) + __all__)


def _cli() -> None:
    """CLI, полностью аналогичный по стилю questions_collector."""
    p = argparse.ArgumentParser(description="Meta-Optimizer Trajectory Harvester")
    sub = p.add_subparsers(dest="cmd", required=True)

    hp = sub.add_parser("harvest", help="Собрать траекторию из handoff (если качество позволяет)")
    hp.add_argument("--handoff", required=True, type=Path)
    hp.add_argument("--cycle", required=True, type=int)
    hp.add_argument("--outcome", default="DONE")
    hp.add_argument("--force", action="store_true", help="Игнорировать quality gate (для отладки)")

    sub.add_parser("list", help="Показать последние траектории (json)")

    ap = sub.add_parser("analyze", help="Проанализировать недавние траектории и сгенерировать предложения")
    ap.add_argument("--recent", type=int, default=5)
    ap.add_argument("--min-confidence", type=float, default=0.8)

    pp = sub.add_parser("propose", help="Сгенерировать и показать предложения (limit)")
    pp.add_argument("--limit", type=int, default=3)

    apy = sub.add_parser("apply-safe", help="Применить безопасные предложения (по умолчанию dry-run)")
    apy.add_argument("--dry-run", action="store_true", default=True)
    apy.add_argument("--ids", default=None, help="P-001,P-002 (опционально)")

    es = sub.add_parser("export-sft", help="JSONL для локального дообучения (без GPU)")
    es.add_argument("--out", type=Path, default=None)
    es.add_argument("--min-confidence", type=float, default=0.85)
    es.add_argument("--recent", type=int, default=100)

    args = p.parse_args()

    if args.cmd == "harvest":
        from memory.meta.generator import harvest_from_handoff
        tid = harvest_from_handoff(args.handoff, args.cycle, args.outcome)
        print(json.dumps({"harvested_id": tid}, ensure_ascii=False))
    elif args.cmd == "list":
        from memory.meta.generator import get_recent_trajectories
        print(json.dumps(get_recent_trajectories(10), ensure_ascii=False, indent=2))
    elif args.cmd in {"analyze", "propose"}:
        from memory.meta.reflector import analyze_for_proposals, generate_proposals
        if args.cmd == "analyze":
            props = analyze_for_proposals(args.recent, args.min_confidence)
            print(json.dumps({"generated_proposals": len(props), "proposals": props}, ensure_ascii=False, indent=2))
        else:
            props = generate_proposals(args.limit)
            print(json.dumps({"proposals": props}, ensure_ascii=False, indent=2))
    elif args.cmd in {"apply-safe", "export-sft"}:
        from memory.meta.curator import apply_safe_proposals, export_sft
        if args.cmd == "apply-safe":
            ids = [x.strip() for x in args.ids.split(",")] if args.ids else None
            n = apply_safe_proposals(dry_run=args.dry_run, ids=ids)
            print(json.dumps({"applied": n, "dry_run": args.dry_run}, ensure_ascii=False))
        else:
            report = export_sft(
                out=args.out,
                min_confidence=args.min_confidence,
                recent=args.recent,
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _cli()
