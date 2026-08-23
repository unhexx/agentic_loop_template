# -*- coding: utf-8 -*-
"""Parallel multi-stream supervisor: serial stream runs then one integration PR."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from memory import streams as streams_mod
from memory.streams import StreamPlan, provision_stream_worktrees, validate_stream_plans
from memory.supervisor import Terminal, load_config, maybe_create_pr, run_loop, save_handoff


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
    agent = Path(hub) / ".agent"
    agent.mkdir(parents=True, exist_ok=True)
    path = agent / "streams_state.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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
) -> dict:
    """
    Serial parallel orchestration:
      provision → for each stream: run_loop → owned_paths gate
      → merge into integration branch → one PR.
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
    for plan in plans:
        if not plan.worktree:
            return {
                "terminal": Terminal.BLOCKED,
                "exit_code": 1,
                "reason": f"stream {plan.name} has no worktree",
                "streams": stream_results,
            }
        wt = Path(plan.worktree)
        plan.status = "RUNNING"
        env_patch = {
            "AGENTIX_STREAM": plan.name,
            "AGENTIX_OWNED_PATHS": ",".join(plan.owned_paths),
            "AGENTIX_WORKTREE": str(wt),
        }
        old_env = {k: os.environ.get(k) for k in env_patch}
        try:
            os.environ.update(env_patch)
            loop_res = run_loop(
                workdir=wt,
                adapter_name=adapter_name,
                max_cycles=max_cycles_per_stream,
                create_pr=False,  # one PR only at integration
            )
        finally:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        term = loop_res.get("terminal")
        term_s = term.value if isinstance(term, Terminal) else str(term)
        if term_s not in (
            Terminal.PR_READY.value,
            Terminal.PR_READY_LOCAL.value,
            "PR_READY",
            "PR_READY_LOCAL",
        ):
            plan.status = "BLOCKED"
            stream_results[plan.name] = {
                "status": "BLOCKED",
                "loop": loop_res,
                "worktree": str(wt),
            }
            _write_hub_streams_state(
                hub_workdir,
                {"streams": stream_results, "terminal": "BLOCKED"},
            )
            return {
                "terminal": Terminal.BLOCKED,
                "exit_code": 1,
                "reason": f"stream {plan.name} terminal {term_s}",
                "streams": stream_results,
            }

        # Use module-level lookup so monkeypatch of list_changed_files applies
        violations = streams_mod.check_owned_paths_gate(
            wt, plan.owned_paths, base_ref=base_ref
        )
        if violations:
            plan.status = "BLOCKED"
            stream_results[plan.name] = {
                "status": "BLOCKED",
                "reason": "owned_paths",
                "violations": violations,
                "worktree": str(wt),
            }
            _write_hub_streams_state(
                hub_workdir,
                {"streams": stream_results, "terminal": "BLOCKED"},
            )
            return {
                "terminal": Terminal.BLOCKED,
                "exit_code": 1,
                "reason": f"owned_paths violations in {plan.name}: {violations}",
                "streams": stream_results,
            }

        plan.status = "STREAM_READY"
        stream_results[plan.name] = {
            "status": "STREAM_READY",
            "loop": loop_res,
            "worktree": str(wt),
            "branch": plan.branch,
        }

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
    }
