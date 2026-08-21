# -*- coding: utf-8 -*-
"""
Performance Ledger — Cycle metrics, trends and ROI tracking for the agentic loop.

Follows exactly the same patterns as questions_collector.py and meta_harvester.py:
- stdlib only + UTF-8
- CLI entry point
- .agent/ storage (PERFORMANCE_LEDGER.json + .md view)
- Integration with meta_harvester and Reviewer handoffs
- Atomic writes, compaction

Usage (inside proper .venv):
  python -m agentic_loop_template.memory.performance_ledger append --cycle 1 --elapsed 12.5 --tokens 4500 --outcome DONE ...
  python -m agentic_loop_template.memory.performance_ledger report --recent 5
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

AGENT_DIR = Path(".agent")
LEDGER_JSON = AGENT_DIR / "PERFORMANCE_LEDGER.json"
LEDGER_MD = AGENT_DIR / "PERFORMANCE_LEDGER.md"


def _ensure_agent_dir():
    AGENT_DIR.mkdir(parents=True, exist_ok=True)


def _atomic_write_json(path: Path, data: dict):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def _load_ledger():
    _ensure_agent_dir()
    if LEDGER_JSON.exists():
        try:
            with open(LEDGER_JSON, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"cycles": [], "summary": {"total_cycles": 0, "last_updated": None}}


def _save_ledger(data: dict):
    data["summary"]["last_updated"] = datetime.now(timezone.utc).isoformat()
    _atomic_write_json(LEDGER_JSON, data)
    _render_md_view(data)


def _render_md_view(data: dict):
    lines = [
        "# .agent/PERFORMANCE_LEDGER.md — Cycle Performance & ROI Tracking",
        "",
        "Auto-updated by performance_ledger module.",
        "",
        "## Recent Cycles",
    ]
    for c in reversed(data.get("cycles", [])[-10:]):
        lines.append(
            f"- Cycle {c.get('cycle')}: outcome={c.get('outcome')} elapsed={c.get('elapsed_minutes')}m "
            f"tools={c.get('tool_calls')} conf={c.get('confidence')} meta_applied={c.get('meta_applied', 0)}"
        )
    lines.append("")
    lines.append("## Summary")
    s = data.get("summary", {})
    lines.append(f"- Total tracked: {s.get('total_cycles', 0)}")
    lines.append(f"- Last updated: {s.get('last_updated')}")
    lines.append("")
    lines.append("See memory/performance_ledger.py and integration in meta_harvester / AGENT_ROLES.")
    with open(LEDGER_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def append_cycle(**kwargs):
    """Append a cycle record. Called by Reviewer / meta on DONE."""
    ledger = _load_ledger()
    record = {
        "cycle": kwargs.get("cycle"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "outcome": kwargs.get("outcome", "DONE"),
        "elapsed_minutes": kwargs.get("elapsed_minutes", 0.0),
        "tool_calls": kwargs.get("tool_calls", 0),
        "tokens_est": kwargs.get("tokens_est", 0),
        "confidence": kwargs.get("confidence", 0.0),
        "tests_total": kwargs.get("tests_total", 0),
        "tests_failed": kwargs.get("tests_failed", 0),
        "violations": kwargs.get("violations", 0),
        "meta_generated": kwargs.get("meta_generated", 0),
        "meta_applied": kwargs.get("meta_applied", 0),
        "success_patterns": kwargs.get("success_patterns", []),
        "notes": kwargs.get("notes", ""),
        "proxy_stats": kwargs.get("proxy_stats"),
        "details": kwargs.get("details"),
    }
    ledger["cycles"].append(record)
    ledger["summary"]["total_cycles"] = len(ledger["cycles"])
    # Simple compaction: keep last 50
    if len(ledger["cycles"]) > 50:
        ledger["cycles"] = ledger["cycles"][-50:]
    _save_ledger(ledger)
    return record


def get_recent(n: int = 5):
    ledger = _load_ledger()
    return list(reversed(ledger.get("cycles", [])))[:n]


def generate_report(recent: int = 5):
    cycles = get_recent(recent)
    if not cycles:
        return "No cycles recorded yet."
    total_elapsed = sum(c.get("elapsed_minutes", 0) for c in cycles)
    avg_conf = sum(c.get("confidence", 0) for c in cycles) / len(cycles)
    total_meta = sum(c.get("meta_applied", 0) for c in cycles)
    return {
        "recent_cycles": len(cycles),
        "avg_elapsed_min": round(total_elapsed / len(cycles), 1),
        "avg_confidence": round(avg_conf, 2),
        "total_meta_applied": total_meta,
        "trend_note": "Improving meta application and efficiency expected after P1+P4 work.",
    }


def main():
    import argparse
    p = argparse.ArgumentParser(description="Performance Ledger for Agentix loop")
    sub = p.add_subparsers(dest="cmd")

    ap = sub.add_parser("append", help="Append cycle stats")
    ap.add_argument("--cycle", type=int, required=True)
    ap.add_argument("--outcome", default="DONE")
    ap.add_argument("--elapsed", type=float, default=0.0, dest="elapsed_minutes")
    ap.add_argument("--tools", type=int, default=0, dest="tool_calls")
    ap.add_argument("--tokens", type=int, default=0, dest="tokens_est")
    ap.add_argument("--conf", type=float, default=0.8, dest="confidence")
    ap.add_argument("--tests_total", type=int, default=0)
    ap.add_argument("--tests_failed", type=int, default=0)
    ap.add_argument("--violations", type=int, default=0)
    ap.add_argument("--meta_gen", type=int, default=0, dest="meta_generated")
    ap.add_argument("--meta_app", type=int, default=0, dest="meta_applied")
    ap.add_argument("--notes", default="")

    rp = sub.add_parser("report", help="Show recent report")
    rp.add_argument("--recent", type=int, default=5)

    args = p.parse_args()
    if args.cmd == "append":
        rec = append_cycle(
            cycle=args.cycle,
            outcome=args.outcome,
            elapsed_minutes=args.elapsed_minutes,
            tool_calls=args.tool_calls,
            tokens_est=args.tokens_est,
            confidence=args.confidence,
            tests_total=args.tests_total,
            tests_failed=args.tests_failed,
            violations=args.violations,
            meta_generated=args.meta_generated,
            meta_applied=args.meta_applied,
            notes=args.notes,
        )
        print(json.dumps(rec, indent=2, ensure_ascii=False))
    elif args.cmd == "report":
        print(json.dumps(generate_report(args.recent), indent=2, ensure_ascii=False))
    else:
        p.print_help()


if __name__ == "__main__":
    main()
