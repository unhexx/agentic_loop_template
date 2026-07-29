# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import os
import sys
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


class Terminal(str, Enum):
    PR_READY = "PR_READY"
    PR_READY_LOCAL = "PR_READY_LOCAL"
    BLOCKED = "BLOCKED"
    STOPPED_LIMIT = "STOPPED_LIMIT"
    STOPPED = "STOPPED"


SupervisorStatus = Terminal
Next = Union[str, Terminal]

ROLE_PROMPT_FILES = {
    "Orchestrator": "prompts/short_orchestrator_prompt.md",
    "Coder": "prompts/short_coder_prompt.md",
    "Tester": "prompts/short_tester_prompt.md",
    "Debugger": "prompts/short_debugger_prompt.md",
    "Reviewer": "prompts/short_reviewer_prompt.md",
}

_PROMPT_BODY_CAP = 8000
_SNAP_JSON_CAP = 4000
_TERMINAL_STATE_STATUSES = frozenset(
    {
        Terminal.PR_READY.value,
        Terminal.PR_READY_LOCAL.value,
        Terminal.STOPPED.value,
        Terminal.STOPPED_LIMIT.value,
        "DONE",
    }
)


def next_role(current_role: str, handoff: Dict[str, Any]) -> Next:
    status = (handoff.get("status") or "").upper()
    if status == "BLOCKED":
        return Terminal.BLOCKED
    if current_role == "Reviewer" and status == "DONE":
        return Terminal.PR_READY
    if current_role == "Tester":
        metrics = handoff.get("metrics") or {}
        failed = int(metrics.get("tests_failed") or 0)
        if failed > 0:
            return "Debugger"
        to = handoff.get("handoff_to") or "Reviewer"
        if to == "Debugger":
            return "Debugger"
        return "Reviewer"
    to = handoff.get("handoff_to")
    if to and to != "None":
        return str(to)
    chain = {
        "Orchestrator": "Coder",
        "Coder": "Tester",
        "Debugger": "Tester",
        "Reviewer": "Orchestrator",
    }
    return chain.get(current_role, Terminal.BLOCKED)


def load_config(workdir: Path) -> Dict[str, Any]:
    """Load .agent/project_config.json, falling back to example, else {}."""
    workdir = Path(workdir)
    for name in ("project_config.json", "project_config.example.json"):
        p = workdir / ".agent" / name
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
    return {}


def load_last_handoff(workdir: Path) -> Optional[Dict[str, Any]]:
    """Read workdir/.agent/last_handoff.json if present."""
    p = Path(workdir) / ".agent" / "last_handoff.json"
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def save_handoff(workdir: Path, data: Dict[str, Any]) -> Path:
    """Persist handoff dict to workdir/.agent/last_handoff.json."""
    agent = Path(workdir) / ".agent"
    agent.mkdir(parents=True, exist_ok=True)
    p = agent / "last_handoff.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _bind_state_paths(state_mod: Any, workdir: Path) -> Dict[str, Any]:
    """Rebind state module paths to workdir/.agent; return previous values."""
    agent_dir = Path(workdir) / ".agent"
    orig = {
        "AGENT_DIR": state_mod.AGENT_DIR,
        "STATE_JSON": state_mod.STATE_JSON,
        "STATE_MD": state_mod.STATE_MD,
        "HISTORY_DIR": state_mod.HISTORY_DIR,
        "METRICS_JSONL": state_mod.METRICS_JSONL,
    }
    state_mod.AGENT_DIR = agent_dir
    state_mod.STATE_JSON = agent_dir / "LOOP_STATE.json"
    state_mod.STATE_MD = agent_dir / "LOOP_STATE.md"
    state_mod.HISTORY_DIR = agent_dir / "history"
    state_mod.METRICS_JSONL = agent_dir / "metrics.jsonl"
    return orig


def _restore_state_paths(state_mod: Any, orig: Dict[str, Any]) -> None:
    for key, value in orig.items():
        setattr(state_mod, key, value)


def _state_snapshot_for_workdir(workdir: Path) -> str:
    """Best-effort bounded LOOP_STATE snapshot with AGENT_DIR rebound to workdir/.agent."""
    try:
        from memory import state as state_mod

        orig = _bind_state_paths(state_mod, workdir)
        try:
            snap_obj = state_mod.snapshot(window=3)
            return json.dumps(snap_obj, ensure_ascii=False)[:_SNAP_JSON_CAP]
        finally:
            _restore_state_paths(state_mod, orig)
    except Exception:
        return "{}"


def build_role_prompt(
    role: str,
    handoff_in: Optional[Dict[str, Any]],
    workdir: Path,
) -> str:
    """
    Assemble cold prompt for one role turn:
    short role prompt + previous handoff delta + optional state snapshot.
    Instructs supervisor-driven JSON handoff; never dump .agent/history/*.
    """
    workdir = Path(workdir)
    rel = ROLE_PROMPT_FILES.get(role, "prompts/short_orchestrator_prompt.md")
    body = ""
    path = workdir / rel
    if path.is_file():
        try:
            body = path.read_text(encoding="utf-8")[:_PROMPT_BODY_CAP]
        except Exception:
            body = ""

    prev = ""
    if handoff_in:
        prev = (
            "\n\n## Previous handoff (delta only)\n"
            f"- summary: {handoff_in.get('summary', '')}\n"
            f"- context_delta: {handoff_in.get('context_delta', '')}\n"
            f"- status: {handoff_in.get('status', '')}\n"
            f"- role: {handoff_in.get('role', '')}\n"
            f"- handoff_to: {handoff_in.get('handoff_to', '')}\n"
        )

    snap = _state_snapshot_for_workdir(workdir)

    return (
        f"You are the **{role}** in the Agentix loop. "
        "Driven by supervisor — do not wait for human «продолжай».\n"
        "End with exactly one JSON handoff object "
        "(HANDOFF_SCHEMA / schemas/handoff.schema.json / last_handoff).\n"
        "Do NOT read .agent/history/* archives. "
        "Use tools/select.py for tools (do not inline full tool docs).\n\n"
        f"{body}\n{prev}\n## State snapshot\n{snap}\n"
    )


def maybe_create_pr(workdir: Path, sup: dict) -> Terminal:
    """Stub: real ``gh pr create`` lands in Task 5."""
    return Terminal.PR_READY


def _exit_code_for(term: Terminal) -> int:
    if term in (Terminal.PR_READY, Terminal.PR_READY_LOCAL):
        return 0
    if term in (Terminal.STOPPED, Terminal.STOPPED_LIMIT):
        return 2
    return 1


def _is_terminal_result(nxt: Next) -> bool:
    return isinstance(nxt, Terminal)


def _should_start_new_cycle(
    st: Dict[str, Any], handoff: Optional[Dict[str, Any]]
) -> bool:
    """After a terminal success (DONE / PR_READY*), start a fresh Orchestrator cycle."""
    handoff_status = ((handoff or {}).get("status") or "").upper()
    state_status = (st.get("status") or "").upper()
    if handoff_status == "DONE":
        return True
    if state_status in _TERMINAL_STATE_STATUSES:
        return True
    return False


def run_loop(
    workdir: Path,
    adapter_name: Optional[str] = None,
    max_cycles: Optional[int] = None,
    max_role_retries: Optional[int] = None,
    create_pr: bool = True,
    role_timeout_s: int = 900,
) -> dict:
    """
    Drive role turns via adapter until PR_READY / BLOCKED / STOP* terminal.

    ``max_cycles`` is the number of PR_READY completions allowed in this call
    (default 1 full O→C→T→R then stop). Inner turns are capped by
    ``max_turns = max(20, max_cycles * 8)``.

    State helpers use module-level relative defaults evaluated at def-time, so
    this function both rebinds ``memory.state`` paths and ``chdir``s into
    ``workdir`` for the duration of the run.
    """
    from memory import state as state_mod
    from memory.adapters import get_adapter
    from memory.validate_handoff import validate_handoff

    workdir = Path(workdir).resolve()
    cfg = load_config(workdir)
    sup = cfg.get("supervisor") or {}
    if not isinstance(sup, dict):
        sup = {}

    adapter_name = (adapter_name or sup.get("adapter") or "mock") or "mock"
    if max_cycles is None:
        max_cycles = int(sup.get("max_cycles") or 5)
    else:
        max_cycles = int(max_cycles)
    if max_role_retries is None:
        max_role_retries = int(sup.get("max_role_retries") or 2)
    else:
        max_role_retries = int(max_role_retries)
    role_timeout_s = int(sup.get("role_timeout_s") or role_timeout_s)
    max_turns = max(20, max_cycles * 8)

    adapter = get_adapter(adapter_name, cfg)
    prev_cwd = Path.cwd()
    orig_paths = _bind_state_paths(state_mod, workdir)
    try:
        os.chdir(workdir)
        state_mod._ensure_dirs()
        # Pass rebound paths explicitly — default args on load/save are def-time.
        st = state_mod.load_state(state_mod.STATE_JSON)
        handoff = load_last_handoff(workdir)
        role = st.get("active_role") or "Orchestrator"

        # Fresh cycle after terminal DONE/PR_READY — do not feed DONE into next_role.
        if _should_start_new_cycle(st, handoff):
            role = "Orchestrator"
            handoff = None
            st = dict(st)
            st["active_role"] = "Orchestrator"
            st["status"] = "IN_PROGRESS"
            st["cycle_number"] = int(st.get("cycle_number") or 0) + 1
            state_mod.save_state(st, state_mod.STATE_JSON)

        turns = 0
        pr_ready_count = 0

        def _load() -> Dict[str, Any]:
            return state_mod.load_state(state_mod.STATE_JSON)

        def _save(patch: Dict[str, Any]) -> None:
            cur = _load()
            cur.update(patch)
            state_mod.save_state(cur, state_mod.STATE_JSON)

        while turns < max_turns:
            if (workdir / ".agent" / "STOP").exists():
                _save({"status": Terminal.STOPPED.value})
                return {
                    "terminal": Terminal.STOPPED,
                    "exit_code": 2,
                    "role": role,
                }

            retries = 0
            while True:
                prompt = build_role_prompt(role, handoff, workdir)
                last_path = workdir / ".agent" / "last_handoff.json"
                try:
                    out_path = adapter.run_role_turn(
                        role=role,
                        prompt=prompt,
                        handoff_in_path=last_path if last_path.is_file() else None,
                        workdir=workdir,
                        timeout_s=role_timeout_s,
                    )
                    handoff = json.loads(Path(out_path).read_text(encoding="utf-8"))
                except Exception as exc:
                    retries += 1
                    if retries > max_role_retries:
                        _save(
                            {
                                "status": Terminal.BLOCKED.value,
                                "notes": str(exc),
                            }
                        )
                        return {
                            "terminal": Terminal.BLOCKED,
                            "exit_code": 1,
                            "reason": str(exc),
                            "role": role,
                        }
                    continue

                strict = (handoff.get("status") or "").upper() == "DONE"
                ok, errors = validate_handoff(handoff, strict_done=strict)
                if not ok:
                    retries += 1
                    if retries > max_role_retries:
                        _save(
                            {
                                "status": Terminal.BLOCKED.value,
                                "notes": "; ".join(errors),
                            }
                        )
                        return {
                            "terminal": Terminal.BLOCKED,
                            "exit_code": 1,
                            "reason": errors,
                            "role": role,
                        }
                    continue
                break

            turns += 1
            save_handoff(workdir, handoff)

            tags = handoff.get("process_tags") or []
            block_tags = set(sup.get("block_process_tags") or [])
            if block_tags.intersection(set(tags)):
                _save(
                    {
                        "status": Terminal.BLOCKED.value,
                        "notes": f"policy tags {tags}",
                    }
                )
                return {
                    "terminal": Terminal.BLOCKED,
                    "exit_code": 1,
                    "reason": f"policy tags {tags}",
                    "role": role,
                }

            state_mod.append_delta(
                f"{role}: {handoff.get('summary', '')}", role=role
            )
            state_mod.log_metrics(
                {
                    "role": role,
                    "status": handoff.get("status"),
                    "adapter": adapter_name,
                }
            )

            nxt = next_role(role, handoff)
            if _is_terminal_result(nxt):
                term: Terminal = nxt  # type: ignore[assignment]
                if term == Terminal.PR_READY:
                    if create_pr:
                        term = maybe_create_pr(workdir, sup)
                    pr_ready_count += 1
                    _save({"status": term.value, "active_role": role})
                    # One PR_READY completion ends the run when max_cycles exhausted
                    if pr_ready_count >= max_cycles:
                        return {
                            "terminal": term,
                            "exit_code": _exit_code_for(term),
                            "role": role,
                            "turns": turns,
                        }
                    # Multi-cycle within one call: start next O→… from Orchestrator
                    role = "Orchestrator"
                    handoff = None
                    cur = _load()
                    _save(
                        {
                            "active_role": "Orchestrator",
                            "status": "IN_PROGRESS",
                            "cycle_number": int(cur.get("cycle_number") or 0) + 1,
                        }
                    )
                    continue

                _save({"status": term.value, "active_role": role})
                return {
                    "terminal": term,
                    "exit_code": _exit_code_for(term),
                    "role": role,
                    "turns": turns,
                }

            role = str(nxt)
            _save({"active_role": role, "status": "IN_PROGRESS"})

        _save({"status": Terminal.STOPPED_LIMIT.value})
        return {
            "terminal": Terminal.STOPPED_LIMIT,
            "exit_code": 2,
            "turns": turns,
            "role": role,
        }
    finally:
        _restore_state_paths(state_mod, orig_paths)
        try:
            os.chdir(prev_cwd)
        except Exception:
            pass


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="memory.supervisor")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="Start or continue supervisor role loop")
    run_p.add_argument("--adapter", default=None)
    run_p.add_argument("--max-cycles", type=int, default=None)
    run_p.add_argument("--workdir", type=Path, default=None)
    run_p.add_argument(
        "--no-pr",
        action="store_true",
        help="Do not call gh pr create (still exit PR_READY)",
    )

    resume_p = sub.add_parser("resume", help="Alias of run (continue mid-cycle)")
    resume_p.add_argument("--adapter", default=None)
    resume_p.add_argument("--max-cycles", type=int, default=None)
    resume_p.add_argument("--workdir", type=Path, default=None)
    resume_p.add_argument("--no-pr", action="store_true")

    status_p = sub.add_parser("status", help="Print LOOP_STATE snapshot JSON")
    status_p.add_argument("--workdir", type=Path, default=None)

    stop_p = sub.add_parser("stop", help="Write .agent/STOP cooperative stop flag")
    stop_p.add_argument("--workdir", type=Path, default=None)

    args = parser.parse_args(argv)
    workdir = Path(args.workdir).resolve() if getattr(args, "workdir", None) else Path.cwd()

    if args.cmd in ("run", "resume"):
        res = run_loop(
            workdir=workdir,
            adapter_name=args.adapter,
            max_cycles=args.max_cycles,
            create_pr=not args.no_pr,
        )
        print(json.dumps(res, ensure_ascii=False, default=str, indent=2))
        return int(res.get("exit_code", 1))

    if args.cmd == "status":
        from memory import state as state_mod

        prev_cwd = Path.cwd()
        orig = _bind_state_paths(state_mod, workdir)
        try:
            os.chdir(workdir)
            state_mod._ensure_dirs()
            snap = state_mod.snapshot()
            handoff = load_last_handoff(workdir)
            out = {
                "state": snap,
                "last_handoff_summary": (handoff or {}).get("summary"),
                "last_handoff_status": (handoff or {}).get("status"),
                "last_handoff_role": (handoff or {}).get("role"),
            }
            print(json.dumps(out, ensure_ascii=False, indent=2))
        finally:
            _restore_state_paths(state_mod, orig)
            try:
                os.chdir(prev_cwd)
            except Exception:
                pass
        return 0

    if args.cmd == "stop":
        agent = workdir / ".agent"
        agent.mkdir(parents=True, exist_ok=True)
        stop_path = agent / "STOP"
        stop_path.write_text("1", encoding="utf-8")
        print(
            json.dumps(
                {"ok": True, "stop_flag": str(stop_path)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
