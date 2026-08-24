# -*- coding: utf-8 -*-
"""
Bounded durable state for the agentic loop (.agent/LOOP_STATE).

Working set is intentionally small (JSON + slim MD projection).
Append-only history goes to .agent/history/ — never load full history into the prompt.

CLI (via python -m memory state ... or agentic_loop_template.memory):
  snapshot | compact | init | append-delta | tail | metrics-log
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("memory.state")

AGENT_DIR = Path(".agent")
STATE_JSON = AGENT_DIR / "LOOP_STATE.json"
STATE_MD = AGENT_DIR / "LOOP_STATE.md"
HISTORY_DIR = AGENT_DIR / "history"
METRICS_JSONL = AGENT_DIR / "metrics.jsonl"

# Hard caps (bytes / entries) — working set for agents
MAX_WORKING_JSON_BYTES = 8 * 1024
MAX_DELTAS = 5
MAX_OPEN_INVEST = 20
MAX_MD_PREVIEW_CHARS = 6000

# Files that may bloat and should be rotated/archived by compact
BLOAT_CANDIDATES = (
    "LOOP_STATE.md",
    "DONE.md",
    "LESSONS.md",
    "TODO.md",
    "EXECUTION_LOG.jsonl",
)


def _agent_dir(agent_dir: Optional[Path] = None) -> Path:
    # На вызове, не на import: иначе monkeypatch AGENT_DIR и чужой cwd не видны.
    return Path(agent_dir) if agent_dir is not None else AGENT_DIR


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dirs(agent_dir: Optional[Path] = None) -> None:
    agent = _agent_dir(agent_dir)
    agent.mkdir(parents=True, exist_ok=True)
    (agent / "history").mkdir(parents=True, exist_ok=True)


def default_state() -> Dict[str, Any]:
    return {
        "version": 1,
        "updated_at": _now_iso(),
        "cycle_number": 0,
        "active_role": "Orchestrator",
        "status": "READY",
        "branch": "",
        "last_commit": "",
        "git_sync": {
            "verified": False,
            "feature_pushed": False,
            "main_merged_commit": "",
            "timestamp": "",
            "commands_run": [],
        },
        "open_invest": [],
        "recent_deltas": [],
        "template_version": _read_template_version(),
        "notes": "Use `python -m memory state snapshot` — never read full archive history.",
    }


def _read_template_version() -> str:
    # Сначала дистрибутив: после pip install рядом с cwd может не быть VERSION.
    try:
        from importlib.metadata import version

        return version("agentix")
    except Exception:
        pass
    for candidate in (Path("VERSION"), Path(__file__).resolve().parents[1] / "VERSION"):
        if candidate.is_file():
            try:
                return candidate.read_text(encoding="utf-8").strip().splitlines()[0]
            except Exception:
                pass
    return "unknown"


def load_state(path: Optional[Path] = None, *, agent_dir: Optional[Path] = None) -> Dict[str, Any]:
    agent = _agent_dir(agent_dir)
    path = path or (agent / "LOOP_STATE.json")
    _ensure_dirs(agent)
    if not path.exists():
        # Migrate legacy free-form LOOP_STATE.md if present and huge
        md_path = agent / "LOOP_STATE.md"
        if md_path.exists():
            return _migrate_from_md(md_path, agent_dir=agent)
        st = default_state()
        save_state(st, path=path, agent_dir=agent)
        return st
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            log.error("load_state: %s is not a JSON object", path)
            return default_state()
        base = default_state()
        base.update(data)
        return base
    except Exception as exc:
        log.error("load_state failed for %s: %s", path, exc)
        return default_state()


def save_state(
    state: Dict[str, Any],
    path: Optional[Path] = None,
    *,
    agent_dir: Optional[Path] = None,
) -> None:
    agent = _agent_dir(agent_dir)
    path = path or (agent / "LOOP_STATE.json")
    _ensure_dirs(agent)
    state = dict(state)
    state["updated_at"] = _now_iso()
    # Cap lists
    deltas = list(state.get("recent_deltas") or [])
    if len(deltas) > MAX_DELTAS:
        # Archive overflow
        overflow = deltas[:-MAX_DELTAS]
        _append_history(
            {"type": "deltas_overflow", "items": overflow, "ts": _now_iso()},
            agent_dir=agent,
        )
        deltas = deltas[-MAX_DELTAS:]
    state["recent_deltas"] = deltas
    invest = list(state.get("open_invest") or [])
    state["open_invest"] = invest[:MAX_OPEN_INVEST]

    text = json.dumps(state, ensure_ascii=False, indent=2)
    if len(text.encode("utf-8")) > MAX_WORKING_JSON_BYTES:
        # Drop notes and trim command lists
        gs = state.get("git_sync") or {}
        if isinstance(gs, dict) and isinstance(gs.get("commands_run"), list):
            gs["commands_run"] = gs["commands_run"][-3:]
        state["notes"] = "truncated"
        text = json.dumps(state, ensure_ascii=False, indent=2)

    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    _write_md_projection(state, agent_dir=agent)


def _write_md_projection(state: Dict[str, Any], *, agent_dir: Optional[Path] = None) -> None:
    agent = _agent_dir(agent_dir)
    lines = [
        "# LOOP_STATE (working set — do not append free text here)",
        "",
        f"- **updated_at**: {state.get('updated_at', '')}",
        f"- **cycle**: {state.get('cycle_number', 0)}",
        f"- **role**: {state.get('active_role', '')}",
        f"- **status**: {state.get('status', '')}",
        f"- **branch**: {state.get('branch', '')}",
        f"- **last_commit**: {state.get('last_commit', '')}",
        f"- **template_version**: {state.get('template_version', '')}",
        "",
        "## git_sync",
        "```json",
        json.dumps(state.get("git_sync") or {}, ensure_ascii=False, indent=2),
        "```",
        "",
        "## open_invest",
    ]
    for item in state.get("open_invest") or []:
        lines.append(f"- {item}")
    if not state.get("open_invest"):
        lines.append("- (none)")
    lines.append("")
    lines.append("## recent_deltas (max 5)")
    for d in state.get("recent_deltas") or []:
        if isinstance(d, dict):
            lines.append(f"- [{d.get('ts', '')}] {d.get('text', d)}")
        else:
            lines.append(f"- {d}")
    lines.append("")
    lines.append(
        "> History: `.agent/history/`. Use `python -m memory state snapshot` / `compact`."
    )
    lines.append("")
    md = "\n".join(lines)
    if len(md) > MAX_MD_PREVIEW_CHARS:
        md = md[:MAX_MD_PREVIEW_CHARS] + "\n\n…truncated\n"
    (agent / "LOOP_STATE.md").write_text(md, encoding="utf-8")


def _append_history(record: Dict[str, Any], *, agent_dir: Optional[Path] = None) -> None:
    agent = _agent_dir(agent_dir)
    _ensure_dirs(agent)
    month = datetime.now(timezone.utc).strftime("%Y%m")
    path = agent / "history" / f"loop_state-{month}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _migrate_from_md(md_path: Path, *, agent_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Archive bloated LOOP_STATE.md and start clean JSON state."""
    agent = _agent_dir(agent_dir)
    _ensure_dirs(agent)
    size = md_path.stat().st_size if md_path.exists() else 0
    if size > 16 * 1024:
        archive = (
            agent
            / "history"
            / f"LOOP_STATE.legacy.{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.md"
        )
        shutil.copy2(md_path, archive)
        _append_history(
            {
                "type": "migrated_legacy_md",
                "bytes": size,
                "archive": str(archive),
                "ts": _now_iso(),
            },
            agent_dir=agent,
        )
    st = default_state()
    st["notes"] = f"Migrated from legacy LOOP_STATE.md ({size} bytes archived)."
    # Best-effort extract last_git_sync line
    try:
        text = md_path.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"last_git_sync:\s*(\S+)", text)
        if m:
            st["git_sync"]["timestamp"] = m.group(1)
        if "verified: true" in text.lower():
            st["git_sync"]["verified"] = True
    except Exception:
        pass
    save_state(st, agent_dir=agent)
    return st


def snapshot(window: int = 3, *, agent_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Compact snapshot for Orchestrator cold-start (no history body)."""
    agent = _agent_dir(agent_dir)
    st = load_state(agent_dir=agent)
    deltas = list(st.get("recent_deltas") or [])[-max(1, window) :]
    return {
        "workspace_hint": "call `python -m memory info` for memory file paths",
        "cycle_number": st.get("cycle_number"),
        "active_role": st.get("active_role"),
        "status": st.get("status"),
        "branch": st.get("branch"),
        "last_commit": st.get("last_commit"),
        "git_sync": st.get("git_sync"),
        "open_invest": (st.get("open_invest") or [])[:10],
        "recent_deltas": deltas,
        "template_version": st.get("template_version"),
        "updated_at": st.get("updated_at"),
        "working_bytes": len(json.dumps(st, ensure_ascii=False).encode("utf-8")),
        "history_dir": str(agent / "history"),
        "rule": "Do NOT read .agent/history/* or full legacy LOOP_STATE archives into context.",
    }


def append_delta(text: str, role: str = "", *, agent_dir: Optional[Path] = None) -> Dict[str, Any]:
    agent = _agent_dir(agent_dir)
    st = load_state(agent_dir=agent)
    entry = {"ts": _now_iso(), "role": role, "text": text.strip()[:500]}
    deltas = list(st.get("recent_deltas") or [])
    deltas.append(entry)
    st["recent_deltas"] = deltas
    save_state(st, agent_dir=agent)
    _append_history({"type": "delta", **entry}, agent_dir=agent)
    return {"ok": True, "delta": entry}


def compact(
    archive_bloat: bool = True,
    max_lessons_lines: int = 400,
    max_done_lines: int = 200,
    *,
    agent_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Ensure working LOOP_STATE is bounded; optionally archive oversized sibling files.
    Does not delete product code — only moves bloat under .agent/history/.
    """
    agent = _agent_dir(agent_dir)
    _ensure_dirs(agent)
    st = load_state(agent_dir=agent)
    actions: List[str] = []
    state_json = agent / "LOOP_STATE.json"
    state_md = agent / "LOOP_STATE.md"

    # Always rewrite projection from JSON
    save_state(st, agent_dir=agent)
    actions.append("rewrote LOOP_STATE.json + slim LOOP_STATE.md")

    if not archive_bloat:
        return {"ok": True, "actions": actions, "state_bytes": state_json.stat().st_size}

    for name in BLOAT_CANDIDATES:
        if name in ("LOOP_STATE.md",):
            # already slim projection
            continue
        path = agent / name
        if not path.is_file():
            continue
        size = path.stat().st_size
        # Soft threshold: archive if > 64KB for markdown/logs
        if size < 64 * 1024:
            continue
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        dest = agent / "history" / f"{path.stem}.{stamp}{path.suffix}"
        shutil.copy2(path, dest)
        actions.append(f"archived {name} ({size} bytes) -> {dest.name}")

        if path.suffix == ".md":
            # Keep a slim head for agents
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                keep = max_lessons_lines if "LESSON" in name.upper() else max_done_lines
                head = lines[:keep]
                slim = "\n".join(head)
                slim += (
                    f"\n\n---\n**Compacted** { _now_iso() }. Full archive: `{dest}`.\n"
                )
                path.write_text(slim, encoding="utf-8")
                actions.append(f"slimmed {name} to {keep} lines")
            except Exception as exc:
                actions.append(f"skip slim {name}: {exc}")
        elif path.suffix == ".jsonl":
            # Keep last 100 lines
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                tail = lines[-100:]
                path.write_text("\n".join(tail) + ("\n" if tail else ""), encoding="utf-8")
                actions.append(f"trimmed {name} to last 100 lines")
            except Exception as exc:
                actions.append(f"skip trim {name}: {exc}")

    _append_history({"type": "compact", "actions": actions, "ts": _now_iso()}, agent_dir=agent)
    return {
        "ok": True,
        "actions": actions,
        "state_bytes": state_json.stat().st_size if state_json.exists() else 0,
        "md_bytes": state_md.stat().st_size if state_md.exists() else 0,
    }


def log_metrics(metrics: Dict[str, Any], *, agent_dir: Optional[Path] = None) -> None:
    agent = _agent_dir(agent_dir)
    _ensure_dirs(agent)
    dest = agent / "metrics.jsonl"
    row = {"ts": _now_iso(), **metrics}
    with dest.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def tail_history(n: int = 5, *, agent_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    agent = _agent_dir(agent_dir)
    _ensure_dirs(agent)
    files = sorted((agent / "history").glob("loop_state-*.jsonl"))
    rows: List[Dict[str, Any]] = []
    for f in reversed(files):
        try:
            for line in reversed(f.read_text(encoding="utf-8", errors="replace").splitlines()):
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    rows.append({"raw": line[:200]})
                if len(rows) >= n:
                    return list(reversed(rows))
        except Exception:
            continue
    return list(reversed(rows))


def cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded agentic LOOP_STATE")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_snap = sub.add_parser("snapshot", help="Compact snapshot for prompts")
    p_snap.add_argument("--window", type=int, default=3)

    sub.add_parser("init", help="Create default bounded state")
    sub.add_parser("compact", help="Archive bloat + rewrite slim working set")

    p_delta = sub.add_parser("append-delta", help="Append short delta")
    p_delta.add_argument("--text", required=True)
    p_delta.add_argument("--role", default="")

    p_tail = sub.add_parser("tail", help="Last N history records (not for full prompt load)")
    p_tail.add_argument("-n", type=int, default=5)

    p_met = sub.add_parser("metrics-log", help="Append metrics JSON object")
    p_met.add_argument("--json", required=True)

    p_set = sub.add_parser("set", help="Set simple fields (JSON merge)")
    p_set.add_argument("--json", required=True)

    args = parser.parse_args(argv)

    if args.cmd == "snapshot":
        print(json.dumps(snapshot(window=args.window), ensure_ascii=False, indent=2))
    elif args.cmd == "init":
        st = default_state()
        save_state(st)
        print(json.dumps({"ok": True, "path": str(STATE_JSON)}, ensure_ascii=False, indent=2))
    elif args.cmd == "compact":
        print(json.dumps(compact(), ensure_ascii=False, indent=2))
    elif args.cmd == "append-delta":
        print(json.dumps(append_delta(args.text, role=args.role), ensure_ascii=False, indent=2))
    elif args.cmd == "tail":
        print(json.dumps(tail_history(args.n), ensure_ascii=False, indent=2))
    elif args.cmd == "metrics-log":
        log_metrics(json.loads(args.json))
        print(json.dumps({"ok": True, "file": str(METRICS_JSONL)}, ensure_ascii=False))
    elif args.cmd == "set":
        patch = json.loads(args.json)
        st = load_state()
        st.update(patch)
        save_state(st)
        print(json.dumps({"ok": True, "state": snapshot()}, ensure_ascii=False, indent=2))
    else:
        parser.print_help()
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(cli())
