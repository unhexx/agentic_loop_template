# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from memory.handoff_io import save_handoff
from memory.logutil import get_logger

log = get_logger("memory.supervisor")


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
_KNOWLEDGE_BUDGET = 800
_PROMPT_TOKEN_CAP = 8000
HEARTBEAT_FILENAME = "supervisor.heartbeat"
HEARTBEAT_INTERVAL_S = 20.0
HEARTBEAT_JOIN_S = 1.0
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
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                log.warning("load_config failed for %s: %s", p, exc)
    return {}


def load_last_handoff(workdir: Path) -> Optional[Dict[str, Any]]:
    """Read workdir/.agent/last_handoff.json if present."""
    p = Path(workdir) / ".agent" / "last_handoff.json"
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        log.warning("load_last_handoff failed for %s: %s", p, exc)
        return None


def _heartbeat_path(workdir: Path) -> Path:
    return Path(workdir) / ".agent" / HEARTBEAT_FILENAME


def _heartbeat_ts() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_heartbeat(workdir: Path) -> Optional[Dict[str, Any]]:
    """Пульс процесса, не статус цикла: LOOP_STATE остаётся единственным источником."""
    p = _heartbeat_path(workdir)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_heartbeat(
    path: Path,
    role: str,
    status: str,
    stop: Optional[threading.Event] = None,
) -> None:
    if stop is not None and stop.is_set():
        return
    payload = {
        "pid": os.getpid(),
        "role": role,
        "status": status,
        "ts": _heartbeat_ts(),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.parent / (path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        # После set() не публикуем файл — иначе пульс может пережить unlink в halt.
        if stop is not None and stop.is_set():
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            return
        tmp.replace(path)
    except Exception:
        pass


def _heartbeat_ticker(
    path: Path, role: str, status: str, stop: threading.Event
) -> None:
    # wait() возвращает True сразу после Event.set — join не выжидает весь интервал.
    while not stop.wait(HEARTBEAT_INTERVAL_S):
        _write_heartbeat(path, role, status, stop)


def _stop_heartbeat_thread(
    stop: threading.Event,
    thread: Optional[threading.Thread],
    path: Path,
) -> None:
    stop.set()
    if thread is not None and thread.ident is not None:
        try:
            thread.join(timeout=HEARTBEAT_JOIN_S)
        except RuntimeError:
            pass
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass
    try:
        (path.parent / (path.name + ".tmp")).unlink(missing_ok=True)
    except Exception:
        pass
    # Join мог истечь по таймауту, пока поток ещё писал — убрать запоздалый replace.
    if thread is not None and thread.is_alive():
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


def _state_snapshot_for_workdir(workdir: Path) -> str:
    """Снимок LOOP_STATE по workdir/.agent, без chdir и без подмены глобалей."""
    try:
        from memory import state as state_mod

        snap_obj = state_mod.snapshot(window=3, agent_dir=Path(workdir) / ".agent")
        return json.dumps(snap_obj, ensure_ascii=False)[:_SNAP_JSON_CAP]
    except Exception as exc:
        log.warning("state snapshot failed: %s", exc)
        return "{}"


def _knowledge_block(
    role: str,
    handoff_in: Optional[Dict[str, Any]],
    workdir: Path,
) -> str:
    """Короткий блок из локальной SQLite. Нет базы — пустая строка, без догадок cwd шлюза."""
    try:
        from memory.compressor import compress_text
        from memory.knowledge import db_path, query

        db = db_path(cwd=workdir)
        if not db.is_file():
            return ""
        q = ""
        if handoff_in:
            q = str(handoff_in.get("summary") or "").strip()[:200]
        if not q:
            q = role
        rows = query(q=q, top=3, db=db)
        if not rows:
            rows = query(top=3, db=db)
        if not rows:
            return ""
        lines: List[str] = []
        for row in rows:
            title = str(row.get("title") or "").strip()
            source = str(row.get("source") or "").strip()
            content = str(row.get("content") or "").replace("\n", " ").strip()
            lines.append(f"- {title} ({source}): {content}")
        blob = "\n".join(lines)
        text = compress_text(blob, _KNOWLEDGE_BUDGET)["text"]
        return f"\n## Local knowledge (top 3)\n{text}\n"
    except Exception as exc:
        log.warning("knowledge inject failed: %s", exc)
        return ""


def _maybe_compress_prompt(text: str, workdir: Path) -> str:
    """Если compress_when_over и промпт жирный — правиловый компрессор, источники не трогаем."""
    cfg = load_config(workdir)
    budget_cfg = cfg.get("context_budget") if isinstance(cfg.get("context_budget"), dict) else {}
    if budget_cfg.get("compress_when_over") is False:
        return text
    try:
        from memory.compressor import compress_text
        from memory.context_budget import estimate_tokens

        if estimate_tokens(text) <= _PROMPT_TOKEN_CAP:
            return text
        return compress_text(text, _PROMPT_TOKEN_CAP)["text"]
    except Exception as exc:
        log.warning("compress skipped: %s", exc)
        return text


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
        except Exception as exc:
            log.warning("role prompt read failed for %s: %s", path, exc)
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
    knowledge = _knowledge_block(role, handoff_in, workdir)

    prompt = (
        f"You are the **{role}** in the Agentix loop. "
        "Driven by supervisor — do not wait for human «продолжай».\n"
        "End with exactly one JSON handoff object "
        "(HANDOFF_SCHEMA / schemas/handoff.schema.json / last_handoff).\n"
        "Do NOT read .agent/history/* archives. "
        "Use tools/select.py for tools (do not inline full tool docs).\n\n"
        f"{body}\n{prev}\n## State snapshot\n{snap}\n{knowledge}"
    )
    return _maybe_compress_prompt(prompt, workdir)


def maybe_create_pr(workdir: Path, sup: dict) -> Terminal:
    """
    Open a PR with ``gh pr create`` (never merge to main).

    Returns PR_READY on success, PR_READY_LOCAL if gh is missing or create fails.
    """
    pr = (sup or {}).get("pr") or {}
    if not isinstance(pr, dict):
        pr = {}
    base = pr.get("base") or "main"
    title = f"{pr.get('title_prefix') or 'agentix:'} unattended cycle"
    body = (
        "Opened by Agentix supervisor 3.5. "
        "Human: merge to main only after review."
    )
    if not shutil.which("gh"):
        return Terminal.PR_READY_LOCAL
    draft = ["--draft"] if pr.get("draft") else []
    cmd = [
        "gh",
        "pr",
        "create",
        "--base",
        str(base),
        "--title",
        title,
        "--body",
        body,
        *draft,
    ]
    # Never: gh pr merge
    r = subprocess.run(
        cmd,
        cwd=str(workdir),
        capture_output=True,
        text=True,
    )
    if r.returncode == 0:
        return Terminal.PR_READY
    return Terminal.PR_READY_LOCAL


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

    Пути состояния — ``agent_dir=workdir/.agent``. Процесс cwd не меняем:
    адаптер уже передаёт cwd=workdir в subprocess.
    """
    from memory import state as state_mod
    from memory.adapters import get_adapter
    from memory.proxy.policy import ProxyNotReady, assert_ready
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

    try:
        assert_ready(workdir, adapter_name=adapter_name)
    except ProxyNotReady as exc:
        return {
            "terminal": Terminal.BLOCKED,
            "exit_code": 1,
            "reason": str(exc),
            "role": "Orchestrator",
        }

    adapter = get_adapter(adapter_name, cfg)
    agent = workdir / ".agent"
    hb_path = _heartbeat_path(workdir)
    hb_stop: Optional[threading.Event] = None
    hb_thread: Optional[threading.Thread] = None

    def _halt_heartbeat() -> None:
        nonlocal hb_thread, hb_stop
        stop = hb_stop if hb_stop is not None else threading.Event()
        _stop_heartbeat_thread(stop, hb_thread, hb_path)
        hb_thread = None
        hb_stop = None

    try:
        state_mod._ensure_dirs(agent)
        st = state_mod.load_state(agent_dir=agent)
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
            state_mod.save_state(st, agent_dir=agent)

        turns = 0
        pr_ready_count = 0

        def _load() -> Dict[str, Any]:
            return state_mod.load_state(agent_dir=agent)

        def _save(patch: Dict[str, Any]) -> None:
            cur = _load()
            cur.update(patch)
            state_mod.save_state(cur, agent_dir=agent)

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
                # Свой Event на ход: clear() общего флага оживлял бы старый тикер.
                hb_stop = threading.Event()
                hb_thread = None
                try:
                    hb_status = str((_load().get("status") or "IN_PROGRESS"))
                    _write_heartbeat(hb_path, role, hb_status, hb_stop)
                    hb_thread = threading.Thread(
                        target=_heartbeat_ticker,
                        args=(hb_path, role, hb_status, hb_stop),
                        name="supervisor-heartbeat",
                        daemon=True,
                    )
                    hb_thread.start()
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
                finally:
                    _halt_heartbeat()

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
                f"{role}: {handoff.get('summary', '')}", role=role, agent_dir=agent
            )
            state_mod.log_metrics(
                {
                    "role": role,
                    "status": handoff.get("status"),
                    "adapter": adapter_name,
                },
                agent_dir=agent,
            )

            nxt = next_role(role, handoff)
            if _is_terminal_result(nxt):
                term: Terminal = nxt  # type: ignore[assignment]
                if term == Terminal.PR_READY:
                    try:
                        from memory.experience_harvester import maybe_cycle_on_done

                        maybe_cycle_on_done(workdir, apply=False)
                    except Exception as exc:
                        log.warning("maybe_cycle_on_done failed: %s", exc)
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
        _halt_heartbeat()


def main(argv: Optional[List[str]] = None) -> int:
    from memory.logutil import configure_logging

    configure_logging()
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

    par_p = sub.add_parser(
        "run-parallel",
        help="Run N disjoint streams then one integration PR",
    )
    par_p.add_argument(
        "--stream",
        action="append",
        dest="streams",
        required=True,
        help="name:owned/path1,path2 (repeatable)",
    )
    par_p.add_argument("--adapter", default=None)
    par_p.add_argument("--max-cycles-per-stream", type=int, default=1)
    par_p.add_argument("--workdir", type=Path, default=None)
    par_p.add_argument("--wt-base", type=Path, default=None)
    par_p.add_argument("--cycle-id", default=None)
    par_p.add_argument("--base", default="main")
    par_p.add_argument("--integration-branch", default=None)
    par_p.add_argument("--no-pr", action="store_true")
    par_p.add_argument(
        "--skip-provision",
        action="store_true",
        help="Use plans only for worktrees already present (testing)",
    )

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

    if args.cmd == "run-parallel":
        from memory.streams import parse_stream_specs
        from memory.supervisor_parallel import run_parallel

        plans = parse_stream_specs(args.streams)
        res = run_parallel(
            hub_workdir=workdir,
            plans=plans,
            adapter_name=args.adapter,
            max_cycles_per_stream=args.max_cycles_per_stream,
            create_pr=not args.no_pr,
            base_ref=args.base,
            cycle_id=args.cycle_id,
            wt_base=args.wt_base,
            skip_provision=args.skip_provision,
            integration_branch=args.integration_branch,
        )
        print(json.dumps(res, ensure_ascii=False, default=str, indent=2))
        return int(res.get("exit_code", 1))

    if args.cmd == "status":
        from memory import state as state_mod

        agent = workdir / ".agent"
        state_mod._ensure_dirs(agent)
        snap = state_mod.snapshot(agent_dir=agent)
        handoff = load_last_handoff(workdir)
        out = {
            "state": snap,
            "last_handoff_summary": (handoff or {}).get("summary"),
            "last_handoff_status": (handoff or {}).get("status"),
            "last_handoff_role": (handoff or {}).get("role"),
        }
        hb = load_heartbeat(workdir)
        if hb is not None:
            out["heartbeat"] = hb
        print(json.dumps(out, ensure_ascii=False, indent=2))
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
