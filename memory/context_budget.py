# -*- coding: utf-8 -*-
"""
Context budget estimator and gate for agentic loop cold-start / next_input_files.

Heuristic: tokens ≈ chars / 4 (override with tiktoken if installed).

Usage:
  python -m memory.context_budget check --files a.md b.md --budget 12000
  python -m memory.context_budget cold-start --budget 16000
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def estimate_tokens(text: str) -> int:
    try:
        import tiktoken  # type: ignore

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text) // 4)


def file_tokens(path: Path, max_read: int = 2_000_000) -> Dict[str, Any]:
    if not path.is_file():
        return {"path": str(path), "exists": False, "bytes": 0, "tokens": 0}
    data = path.read_bytes()[:max_read]
    text = data.decode("utf-8", errors="replace")
    return {
        "path": str(path),
        "exists": True,
        "bytes": path.stat().st_size,
        "tokens": estimate_tokens(text),
        "truncated_read": path.stat().st_size > max_read,
    }


def check_files(files: List[Path], budget: int) -> Dict[str, Any]:
    rows = [file_tokens(p) for p in files]
    total = sum(int(r["tokens"]) for r in rows)
    return {
        "budget_tokens": budget,
        "total_tokens": total,
        "within_budget": total <= budget,
        "files": rows,
        "over_by": max(0, total - budget),
    }


def cold_start_default_files(root: Path | None = None) -> List[Path]:
    root = root or Path.cwd()
    candidates = [
        root / "SYSTEM_PROMPT.md",
        root / "prompts" / "short_orchestrator_prompt.md",
        root / ".agent" / "LOOP_STATE.json",
        root / "VERSION",
    ]
    return [p for p in candidates if p.exists()]


def cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Context budget checker")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check")
    p_check.add_argument("--files", nargs="+", type=Path, required=True)
    p_check.add_argument("--budget", type=int, default=12000)
    p_check.add_argument("--strict", action="store_true", help="Exit 1 if over budget")

    p_cold = sub.add_parser("cold-start")
    p_cold.add_argument("--budget", type=int, default=16000)
    p_cold.add_argument("--strict", action="store_true")
    p_cold.add_argument("--root", type=Path, default=None)

    args = parser.parse_args(argv)

    if args.cmd == "check":
        report = check_files(args.files, args.budget)
    else:
        report = check_files(cold_start_default_files(args.root), args.budget)
        report["profile"] = "cold-start"

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.strict and not report["within_budget"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(cli())
