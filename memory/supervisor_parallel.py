# -*- coding: utf-8 -*-
"""Параллельные disjoint-потоки: serial по умолчанию, опциональный одновременный прогон."""
from __future__ import annotations

import json
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

from memory import streams as streams_mod
from memory.agent_lock import agent_lock
from memory.stream_context import use_stream
from memory.streams import StreamPlan, provision_stream_worktrees, validate_stream_plans
from memory.supervisor import Terminal, load_config, maybe_create_pr, run_loop, save_handoff

_HUB_STATE_LOCK = threading.Lock()


def merge_stream_branch(
    hub_workdir: Path,
    stream_branch: str,
    integration_branch: str,
    main_branch: str = "main",
) -> Dict[str, Any]:
    """
    Ensure integration_branch exists from main, merge stream_branch into it.
    Runs in hub_workdir (primary clone). Never merges to main.
    """
    hub = Path(hub_workdir)

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=str(hub), capture_output=True, text=True
        )

    # create integration branch if needed
    r = git("rev-parse", "--verify", integration_branch)
    if r.returncode != 0:
        c = git("checkout", "-B", integration_branch, main_branch)
        if c.returncode != 0:
            return {"ok": False, "error": c.stderr or c.stdout}
    else:
        c = git("checkout", integration_branch)
        if c.returncode != 0:
            return {"ok": False, "error": c.stderr or c.stdout}

    m = git("merge", "--no-ff", stream_branch, "-m", f"Integrate stream branch {stream_branch}")
    if m.returncode != 0:
        git("merge", "--abort")
        return {"ok": False, "error": m.stderr or m.stdout}
    return {"ok": True, "branch": integration_branch}


def maybe_create_integration_pr(
    hub_workdir: Path,
    sup: dict,
    integration_branch: str,
) -> Terminal:
    """Create PR from integration branch; never merge main."""
    # ensure we are on integration branch is caller's job; reuse maybe_create_pr
    return maybe_create_pr(Path(hub_workdir), sup)


def _write_hub_streams_state(hub: Path, payload: Dict[str, Any]) -> None:
    """Пишет streams_state.json через tmp+replace, без оборванного JSON."""
    agent = Path(hub) / ".agent"
    agent.mkdir(parents=True, exist_ok=True)
    path = agent / "streams_state.json"
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp = path.with_suffix(".json.tmp")
    with _HUB_STATE_LOCK:
        with agent_lock(agent, name="streams"):
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(path)


def _blocked_outer_reason(plan_name: str, rec: Dict[str, Any]) -> str:
    if rec.get("reason") == "owned_paths":
        return f"owned_paths violations in {plan_name}: {rec.get('violations')}"
    loop_res = rec.get("loop") or {}
    term = loop_res.get("terminal") if isinstance(loop_res, dict) else None
    if term is not None:
        term_s = term.value if isinstance(term, Terminal) else str(term)
        return f"stream {plan_name} terminal {term_s}"
    if rec.get("reason"):
        return str(rec["reason"])
    if rec.get("error"):
        return f"stream {plan_name}: {rec['error']}"
    return f"stream {plan_name} blocked"


def _run_one_stream(
    plan: StreamPlan,
    *,
    adapter_name: str,
    max_cycles_per_stream: int,
    base_ref: str,
    patch_environ: bool,
) -> Dict[str, Any]:
    """Один поток: контекст, прогон, проверка owned_paths, статус STREAM_READY или BLOCKED."""
    if not plan.worktree:
        plan.status = "BLOCKED"
        return {
            "status": "BLOCKED",
            "reason": f"stream {plan.name} has no worktree",
        }
    wt = Path(plan.worktree)
    plan.status = "RUNNING"
    owned_csv = ",".join(plan.owned_paths)

    def _execute() -> Dict[str, Any]:
        loop_res = run_loop(
            workdir=wt,
            adapter_name=adapter_name,
            max_cycles=max_cycles_per_stream,
            create_pr=False,  # one PR only at integration
        )
        term = loop_res.get("terminal")
        term_s = term.value if isinstance(term, Terminal) else str(term)
        if term_s not in (
            Terminal.PR_READY.value,
            Terminal.PR_READY_LOCAL.value,
            "PR_READY",
            "PR_READY_LOCAL",
        ):
            plan.status = "BLOCKED"
            return {
                "status": "BLOCKED",
                "loop": loop_res,
                "worktree": str(wt),
            }
        # Use module-level lookup so monkeypatch of list_changed_files applies
        violations = streams_mod.check_owned_paths_gate(
            wt, plan.owned_paths, base_ref=base_ref
        )
        if violations:
            plan.status = "BLOCKED"
            return {
                "status": "BLOCKED",
                "reason": "owned_paths",
                "violations": violations,
                "worktree": str(wt),
            }
        plan.status = "STREAM_READY"
        return {
            "status": "STREAM_READY",
            "loop": loop_res,
            "worktree": str(wt),
            "branch": plan.branch,
        }

    with use_stream(name=plan.name, owned_paths=owned_csv, worktree=str(wt)):
        if not patch_environ:
            return _execute()
        env_patch = {
            "AGENTIX_STREAM": plan.name,
            "AGENTIX_OWNED_PATHS": owned_csv,
            "AGENTIX_WORKTREE": str(wt),
        }
        old_env = {k: os.environ.get(k) for k in env_patch}
        try:
            os.environ.update(env_patch)
            return _execute()
        finally:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def run_parallel(
    hub_workdir: Path,
    plans: List[StreamPlan],
    adapter_name: Optional[str] = None,
    max_cycles_per_stream: int = 1,
    create_pr: bool = True,
    base_ref: str = "main",
    cycle_id: Optional[str] = None,
    wt_base: Optional[Path] = None,
    skip_provision: bool = False,
    integration_branch: Optional[str] = None,
    *,
    concurrent: bool = False,
) -> dict:
    """
    Оркестрация disjoint-потоков:
      provision (всегда последовательно) → прогон каждого потока
      (serial fail-fast или одновременный) → merge в integration-ветку → один PR.
    """
    hub_workdir = Path(hub_workdir).resolve()
    validate_stream_plans(plans)
    cfg = load_config(hub_workdir)
    sup = cfg.get("supervisor") or {}
    if not isinstance(sup, dict):
        sup = {}
    par = sup.get("parallel") or {}
    if not isinstance(par, dict):
        par = {}
    concurrent = concurrent or bool(par.get("concurrent"))
    mode = "concurrent" if concurrent else "serial"
    adapter_name = adapter_name or sup.get("adapter") or "mock"
    base_ref = par.get("base") or base_ref or "main"
    if integration_branch is None:
        integration_branch = par.get("integration_branch") or "feature/integration-parallel"

    if not skip_provision:
        plans = provision_stream_worktrees(
            repo_root=hub_workdir,
            plans=plans,
            cycle_id=cycle_id,
            wt_base=wt_base,
            main_branch=base_ref,
        )

    stream_results: Dict[str, Any] = {}

    def _fail_blocked(reason: str) -> dict:
        _write_hub_streams_state(
            hub_workdir,
            {"streams": stream_results, "terminal": "BLOCKED"},
        )
        return {
            "terminal": Terminal.BLOCKED,
            "exit_code": 1,
            "reason": reason,
            "streams": stream_results,
            "mode": mode,
        }

    def _missing_worktree(plan: StreamPlan) -> dict:
        return {
            "terminal": Terminal.BLOCKED,
            "exit_code": 1,
            "reason": f"stream {plan.name} has no worktree",
            "streams": stream_results,
            "mode": mode,
        }

    if concurrent:
        for plan in plans:
            if not plan.worktree:
                return _missing_worktree(plan)

        def _worker(plan: StreamPlan) -> Dict[str, Any]:
            try:
                return _run_one_stream(
                    plan,
                    adapter_name=adapter_name,
                    max_cycles_per_stream=max_cycles_per_stream,
                    base_ref=base_ref,
                    patch_environ=False,
                )
            except Exception as exc:
                plan.status = "BLOCKED"
                return {
                    "status": "BLOCKED",
                    "worktree": str(plan.worktree or ""),
                    "error": f"{type(exc).__name__}: {exc}",
                }

        with ThreadPoolExecutor(max_workers=max(1, len(plans))) as ex:
            future_map = {ex.submit(_worker, plan): plan for plan in plans}
            for fut in as_completed(future_map):
                plan = future_map[fut]
                try:
                    rec = fut.result()
                except Exception as exc:
                    plan.status = "BLOCKED"
                    rec = {
                        "status": "BLOCKED",
                        "worktree": str(plan.worktree or ""),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                stream_results[plan.name] = rec

        blocked = [n for n, rec in stream_results.items() if rec.get("status") == "BLOCKED"]
        if blocked:
            reason = "; ".join(_blocked_outer_reason(n, stream_results[n]) for n in blocked)
            return _fail_blocked(reason)
    else:
        for plan in plans:
            if not plan.worktree:
                return _missing_worktree(plan)
            rec = _run_one_stream(
                plan,
                adapter_name=adapter_name,
                max_cycles_per_stream=max_cycles_per_stream,
                base_ref=base_ref,
                patch_environ=True,
            )
            stream_results[plan.name] = rec
            if rec.get("status") == "BLOCKED":
                return _fail_blocked(_blocked_outer_reason(plan.name, rec))

    # Integration merges (order = plan order)
    for plan in plans:
        if not plan.branch:
            continue
        m = merge_stream_branch(
            hub_workdir=hub_workdir,
            stream_branch=plan.branch,
            integration_branch=integration_branch,
            main_branch=base_ref,
        )
        if not m.get("ok"):
            stream_results[plan.name]["status"] = "BLOCKED"
            stream_results[plan.name]["merge_error"] = m.get("error")
            _write_hub_streams_state(
                hub_workdir,
                {"streams": stream_results, "terminal": "BLOCKED"},
            )
            return {
                "terminal": Terminal.BLOCKED,
                "exit_code": 1,
                "reason": f"merge failed for {plan.name}: {m.get('error')}",
                "streams": stream_results,
                "mode": mode,
            }
        stream_results[plan.name]["status"] = "MERGED"

    final_term: Terminal = Terminal.PR_READY_LOCAL
    if create_pr:
        # Keyword call so tests can monkeypatch with lambda **kwargs
        final_term = maybe_create_integration_pr(
            hub_workdir=hub_workdir,
            sup=sup,
            integration_branch=integration_branch,
        )

    payload = {
        "streams": stream_results,
        "terminal": final_term.value if isinstance(final_term, Terminal) else str(final_term),
        "integration_branch": integration_branch,
    }
    _write_hub_streams_state(hub_workdir, payload)
    # Hub last_handoff summary for humans
    save_handoff(
        hub_workdir,
        {
            "handoff_to": "None",
            "role": "Reviewer",
            "current_phase": "finalization",
            "cycle_number": 0,
            "summary": f"parallel integration {integration_branch}: {list(stream_results)}",
            "status": "DONE",
            "confidence": 0.9,
            "lessons_learned": ["parallel streams"],
            "sync_waived": "integration PR path",
            "process_tags": ["parallel_integration"],
            "stream": "cross",
            "merge_gate": "after-tests-green",
        },
    )

    exit_code = 0 if final_term in (Terminal.PR_READY, Terminal.PR_READY_LOCAL) else 1
    return {
        "terminal": final_term,
        "exit_code": exit_code,
        "streams": stream_results,
        "integration_branch": integration_branch,
        "mode": mode,
    }
