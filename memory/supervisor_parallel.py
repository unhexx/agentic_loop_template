# -*- coding: utf-8 -*-
"""Параллельные disjoint-потоки: serial по умолчанию, опциональный одновременный прогон."""
from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

from memory import streams as streams_mod
from memory.agent_lock import agent_lock
from memory.logutil import get_logger
from memory.stream_context import use_stream
from memory.stream_git import (
    IntegrationWorktreeError,
    ensure_integration_worktree,
    merge_stream_branch as merge_stream_branch_in_worktree,
    push_branch,
)
from memory.stream_lease import (
    DEFAULT_TTL_S,
    claim as claim_lease,
    release as release_lease,
    renew as renew_lease,
)
from memory.streams import StreamPlan, provision_stream_worktrees, validate_stream_plans
from memory.supervisor import Terminal, load_config, maybe_create_pr, run_loop, save_handoff

log = get_logger("memory.supervisor_parallel")

_HUB_STATE_LOCK = threading.Lock()


def merge_stream_branch(
    hub_workdir: Path,
    stream_branch: str,
    integration_branch: str,
    main_branch: str = "main",
    *,
    integration_workdir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Обёртка над stream_git: тесты патчат это имя, merge идёт в worktree, не в хаб."""
    # hub_workdir оставлен в сигнатуре — старые monkeypatch с **kwargs не ломаются
    wd = integration_workdir if integration_workdir is not None else hub_workdir
    return merge_stream_branch_in_worktree(
        integration_workdir=Path(wd),
        stream_branch=stream_branch,
        integration_branch=integration_branch,
        main_branch=main_branch,
    )


def maybe_create_integration_pr(
    hub_workdir: Path,
    sup: dict,
    integration_branch: str,
    integration_workdir: Optional[Path] = None,
) -> Terminal:
    """gh берёт текущую ветку cwd: хаб остаётся на main, поэтому PR — из integration-worktree."""
    cwd = Path(integration_workdir) if integration_workdir is not None else Path(hub_workdir)
    return maybe_create_pr(cwd, sup)


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
    require_owned_paths: bool = True,
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
        if require_owned_paths:
            # lookup через модуль — иначе monkeypatch list_changed_files не попадает в gate
            violations = streams_mod.check_owned_paths_gate(
                wt, plan.owned_paths, base_ref=base_ref
            )
        else:
            log.warning("require_owned_paths disabled for stream %s", plan.name)
            violations = []
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
    push: bool = False,
) -> dict:
    """
    Оркестрация disjoint-потоков:
      lease → provision (всегда последовательно) → прогон
      (serial fail-fast или одновременный) → push веток потока →
      merge в integration-worktree → push integration → один PR.
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
    want_push = bool(push) or bool(par.get("push"))
    require_owned = (
        True if par.get("require_owned_paths") is None else bool(par.get("require_owned_paths"))
    )
    try:
        lease_ttl_s = int(par.get("lease_ttl_s") or DEFAULT_TTL_S)
    except (TypeError, ValueError):
        lease_ttl_s = DEFAULT_TTL_S
    if lease_ttl_s <= 0:
        lease_ttl_s = DEFAULT_TTL_S
    mode = "concurrent" if concurrent else "serial"
    adapter_name = adapter_name or sup.get("adapter") or "mock"
    base_ref = par.get("base") or base_ref or "main"
    if integration_branch is None:
        integration_branch = par.get("integration_branch") or "feature/integration-parallel"

    stream_results: Dict[str, Any] = {}
    claimed: List[str] = []
    integration_workdir: Optional[Path] = None

    def _tick(payload: Dict[str, Any]) -> None:
        # TTL не мьютекс: продлеваем на каждом тике, чтобы status не врал
        _write_hub_streams_state(hub_workdir, payload)
        for name in claimed:
            try:
                renew_lease(hub_workdir, name, ttl_s=lease_ttl_s)
            except ValueError:
                log.warning("не удалось продлить lease потока %s", name)

    def _fail_blocked(reason: str) -> dict:
        _tick(
            {"streams": stream_results, "terminal": "BLOCKED"},
        )
        out: Dict[str, Any] = {
            "terminal": Terminal.BLOCKED,
            "exit_code": 1,
            "reason": reason,
            "streams": stream_results,
            "mode": mode,
            "push": want_push,
        }
        if integration_workdir is not None:
            out["integration_worktree"] = str(integration_workdir)
        return out

    try:
        for plan in plans:
            try:
                claim_lease(
                    hub_workdir,
                    plan.name,
                    plan.owned_paths,
                    worktree=plan.worktree,
                    ttl_s=lease_ttl_s,
                    branch=plan.branch,
                )
            except ValueError as exc:
                return _fail_blocked(f"lease overlap {exc}")
            claimed.append(plan.name)

        if not skip_provision:
            plans = provision_stream_worktrees(
                repo_root=hub_workdir,
                plans=plans,
                cycle_id=cycle_id,
                wt_base=wt_base,
                main_branch=base_ref,
            )

        for plan in plans:
            if not plan.worktree:
                stream_results[plan.name] = {
                    "status": "BLOCKED",
                    "reason": f"stream {plan.name} has no worktree",
                }
                return _fail_blocked(f"stream {plan.name} has no worktree")

        # дашборд должен увидеть fan-out сразу, а не после join
        for plan in plans:
            plan.status = "RUNNING"
            stream_results[plan.name] = {
                "status": "RUNNING",
                "worktree": str(plan.worktree),
                "branch": plan.branch,
            }
        _tick({"streams": stream_results, "terminal": "IN_PROGRESS"})

        if concurrent:
            def _worker(plan: StreamPlan) -> Dict[str, Any]:
                try:
                    return _run_one_stream(
                        plan,
                        adapter_name=adapter_name,
                        max_cycles_per_stream=max_cycles_per_stream,
                        base_ref=base_ref,
                        patch_environ=False,
                        require_owned_paths=require_owned,
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
                    _tick({"streams": stream_results, "terminal": "IN_PROGRESS"})

            blocked = [
                n for n, rec in stream_results.items() if rec.get("status") == "BLOCKED"
            ]
            if blocked:
                reason = "; ".join(
                    _blocked_outer_reason(n, stream_results[n]) for n in blocked
                )
                return _fail_blocked(reason)
        else:
            for plan in plans:
                rec = _run_one_stream(
                    plan,
                    adapter_name=adapter_name,
                    max_cycles_per_stream=max_cycles_per_stream,
                    base_ref=base_ref,
                    patch_environ=True,
                    require_owned_paths=require_owned,
                )
                stream_results[plan.name] = rec
                _tick({"streams": stream_results, "terminal": "IN_PROGRESS"})
                if rec.get("status") == "BLOCKED":
                    return _fail_blocked(_blocked_outer_reason(plan.name, rec))

        if want_push:
            for plan in plans:
                if not plan.branch or not plan.worktree:
                    continue
                pushed = push_branch(Path(plan.worktree), branch=plan.branch)
                if not pushed.get("ok"):
                    stream_results[plan.name]["status"] = "BLOCKED"
                    stream_results[plan.name]["push_error"] = pushed.get("error")
                    return _fail_blocked(
                        f"push failed for {plan.name}: {pushed.get('error')}"
                    )

        try:
            integration_workdir = ensure_integration_worktree(
                hub_workdir,
                integration_branch=integration_branch,
                main_branch=base_ref,
                wt_base=wt_base,
            )
        except IntegrationWorktreeError as exc:
            return _fail_blocked(str(exc))

        for plan in plans:
            if not plan.branch:
                continue
            m = merge_stream_branch(
                hub_workdir=hub_workdir,
                stream_branch=plan.branch,
                integration_branch=integration_branch,
                main_branch=base_ref,
                integration_workdir=integration_workdir,
            )
            if not m.get("ok"):
                stream_results[plan.name]["status"] = "BLOCKED"
                stream_results[plan.name]["merge_error"] = m.get("error")
                return _fail_blocked(
                    f"merge failed for {plan.name}: {m.get('error')}"
                )
            stream_results[plan.name]["status"] = "MERGED"

        if want_push:
            # create_pr+push: этот push — жёсткое условие перед gh, не «best effort»
            pushed = push_branch(integration_workdir, branch=integration_branch)
            if not pushed.get("ok"):
                return _fail_blocked(
                    f"push failed for {integration_branch}: {pushed.get('error')}"
                )

        final_term: Terminal = Terminal.PR_READY_LOCAL
        if create_pr:
            final_term = maybe_create_integration_pr(
                hub_workdir=hub_workdir,
                sup=sup,
                integration_branch=integration_branch,
                integration_workdir=integration_workdir,
            )

        payload = {
            "streams": stream_results,
            "terminal": final_term.value if isinstance(final_term, Terminal) else str(final_term),
            "integration_branch": integration_branch,
        }
        _tick(payload)
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
            "push": want_push,
            "integration_worktree": str(integration_workdir),
        }
    finally:
        for name in claimed:
            try:
                release_lease(hub_workdir, name)
            except Exception as exc:
                log.warning("не удалось снять lease потока %s: %s", name, exc)
