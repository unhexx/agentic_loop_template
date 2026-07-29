# -*- coding: utf-8 -*-
"""
Eval harness для траекторий (P4/P7): replay scoring по harvested trajectories.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

TRAJECTORIES = Path(".agent/TRAJECTORIES.json")


def _load_index() -> Dict[str, Any]:
    if not TRAJECTORIES.exists():
        return {"trajectories": []}
    return json.loads(TRAJECTORIES.read_text(encoding="utf-8"))


def score_trajectory(traj: Dict[str, Any]) -> Dict[str, Any]:
    """Простой скоринг траектории по метрикам качества."""
    conf = float(traj.get("confidence", 0))
    tests_failed = int(traj.get("tests_failed", 0))
    violations = int(traj.get("process_violations", 0))
    elapsed = float(traj.get("elapsed_minutes", 10))
    outcome = traj.get("outcome", "")

    score = conf * 40
    if tests_failed == 0:
        score += 25
    if violations == 0:
        score += 20
    if outcome in ("DONE", "REVIEWED"):
        score += 15
    if elapsed < 3:
        score += 10
    elif elapsed < 5:
        score += 5

    return {
        "trajectory_id": traj.get("id"),
        "cycle": traj.get("cycle"),
        "score": round(min(100, score), 1),
        "confidence": conf,
        "outcome": outcome,
        "elapsed_minutes": elapsed,
    }


def replay_recent(limit: int = 5) -> List[Dict[str, Any]]:
    index = _load_index()
    trajs = index.get("trajectories", [])
    recent = trajs[-limit:] if trajs else []
    return [score_trajectory(t) for t in recent]


def _cli() -> None:
    p = argparse.ArgumentParser(description="Trajectory eval harness")
    p.add_argument("--recent", type=int, default=5)
    args = p.parse_args()
    results = replay_recent(args.recent)
    avg = sum(r["score"] for r in results) / len(results) if results else 0
    print(json.dumps({"results": results, "avg_score": round(avg, 1)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _cli()