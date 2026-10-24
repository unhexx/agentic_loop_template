# -*- coding: utf-8 -*-
"""Уведомления Slack: webhook или chat.postMessage. Секреты в лог не пишем."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from memory.audit_log import append_entry
from memory.integrations.config import load_project_cfg, slack_enabled, slack_section
from memory.integrations.http import HttpError, request_json
from memory.logutil import get_logger
from memory.state import snapshot

log = get_logger("memory.integrations")

_GITHUB_REMOTE = re.compile(
    r"(?:github\.com[:/])(?P<owner>[^/]+)/(?P<repo>[^/.]+)(?:\.git)?$",
    re.IGNORECASE,
)


def _env(environ: Optional[Mapping[str, str]], name: str) -> Optional[str]:
    src = os.environ if environ is None else environ
    raw = src.get(name)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _cycle_number(workdir: Path, handoff: Optional[Dict[str, Any]]) -> int:
    if isinstance(handoff, dict):
        try:
            return int(handoff.get("cycle_number") or 0)
        except (TypeError, ValueError):
            pass
    state_path = Path(workdir) / ".agent" / "LOOP_STATE.json"
    if state_path.is_file():
        try:
            import json

            data = json.loads(state_path.read_text(encoding="utf-8"))
            return int((data or {}).get("cycle_number") or 0)
        except (OSError, UnicodeError, ValueError, TypeError):
            return 0
    return 0


def _git_head(workdir: Path) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = (proc.stdout or "").strip()
    return sha if sha and proc.returncode == 0 else None


def _git_origin(workdir: Path) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    url = (proc.stdout or "").strip()
    return url or None


def commit_link(
    workdir: Path,
    *,
    sha: Optional[str] = None,
    remote: Optional[str] = None,
) -> Optional[str]:
    sha = sha or _git_head(workdir)
    if not sha:
        return None
    remote = remote if remote is not None else _git_origin(workdir)
    if remote:
        m = _GITHUB_REMOTE.search(remote.replace("ssh://git@", "git@"))
        if m:
            short = sha[:12]
            return f"https://github.com/{m.group('owner')}/{m.group('repo')}/commit/{short}"
    return sha[:12]


def ledger_metrics_line(workdir: Path) -> Optional[str]:
    try:
        from memory.performance_ledger import get_recent
    except Exception:
        return None
    rows = get_recent(1, agent_dir=Path(workdir) / ".agent")
    if not rows:
        return None
    last = rows[0]
    parts = []
    elapsed = last.get("elapsed_minutes")
    if elapsed is not None:
        parts.append(f"elapsed={elapsed}m")
    conf = last.get("confidence")
    if conf is not None:
        parts.append(f"confidence={conf}")
    failed = last.get("tests_failed")
    if failed is not None:
        parts.append(f"tests_failed={failed}")
    return " ".join(parts) if parts else None


def build_message(
    *,
    status: str,
    cycle: int,
    task_id: Optional[str],
    commit: Optional[str],
    metrics: Optional[str],
) -> str:
    status_u = (status or "DONE").upper()
    task = f" · task {task_id}" if task_id else ""
    lines = [f"Agentix Reviewer {status_u} · cycle {cycle}{task}"]
    if commit:
        lines.append(f"commit: {commit}")
    if metrics:
        lines.append(metrics)
    return "\n".join(lines)


def _destination(
    cfg: Dict[str, Any], environ: Optional[Mapping[str, str]]
) -> Dict[str, Optional[str]]:
    section = slack_section(cfg)
    webhook = None
    raw = section.get("webhook_url")
    if isinstance(raw, str) and raw.strip():
        webhook = raw.strip()
    webhook = _env(environ, "SLACK_WEBHOOK_URL") or webhook
    token = _env(environ, "SLACK_BOT_TOKEN")
    channel = None
    ch = section.get("channel")
    if isinstance(ch, str) and ch.strip():
        channel = ch.strip()
    channel = _env(environ, "SLACK_CHANNEL") or channel or "#agentix"
    return {"webhook": webhook, "token": token, "channel": channel}


def notify_slack(
    workdir: Path,
    *,
    status: str,
    handoff: Optional[Dict[str, Any]] = None,
    force: bool = False,
    environ: Optional[Mapping[str, str]] = None,
    dry_run: bool = False,
    sha: Optional[str] = None,
    remote: Optional[str] = None,
) -> Dict[str, Any]:
    """Пост в Slack. Пустой skipped, если гейт закрыт или нет webhook/token."""
    workdir = Path(workdir)
    cfg = load_project_cfg(workdir)
    report: Dict[str, Any] = {"ok": False, "skipped": True, "reason": "disabled"}
    if not slack_enabled(cfg, force=force):
        return report
    dest = _destination(cfg, environ)
    if not dest["webhook"] and not dest["token"]:
        report["reason"] = "no_destination"
        log.warning("slack enabled but no webhook/token")
        return report
    task_id = None
    if isinstance(handoff, dict):
        task_id = handoff.get("task_id") or handoff.get("current_task")
        if not isinstance(task_id, str):
            task_id = None
    cycle = _cycle_number(workdir, handoff)
    text = build_message(
        status=status,
        cycle=cycle,
        task_id=task_id,
        commit=commit_link(workdir, sha=sha, remote=remote),
        metrics=ledger_metrics_line(workdir),
    )
    report.update(
        {
            "skipped": False,
            "channel": dest["channel"],
            "text": text,
            "dry_run": dry_run,
        }
    )
    if dry_run:
        report["ok"] = True
        report["reason"] = "dry_run"
        return report
    try:
        if dest["webhook"]:
            st, payload = request_json(
                "POST",
                dest["webhook"],
                body={"text": text, "channel": dest["channel"]},
            )
            ok = st < 400
        else:
            st, payload = request_json(
                "POST",
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {dest['token']}"},
                body={"channel": dest["channel"], "text": text},
            )
            ok = st < 400 and isinstance(payload, dict) and payload.get("ok") is True
        report["ok"] = bool(ok)
        report["status_code"] = st
        report["reason"] = "posted" if ok else "http"
    except HttpError as exc:
        log.warning("slack notify failed: %s", exc)
        report["ok"] = False
        report["reason"] = "http"
    details = {
        "channel": dest["channel"],
        "status": (status or "").upper(),
        "ok": report["ok"],
    }
    try:
        append_entry(
            "slack_notify",
            "reviewer",
            cycle,
            details=details,
            agent_dir=workdir / ".agent",
        )
    except Exception as exc:
        log.warning("slack audit append failed: %s", exc)
    return report


def notify_text(
    workdir: Path,
    text: str,
    details: Mapping[str, Any],
    *,
    force: bool = False,
    environ: Optional[Mapping[str, str]] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Пост готового текста. Не Reviewer: без status и без build_message."""
    workdir = Path(workdir)
    cfg = load_project_cfg(workdir)
    report: Dict[str, Any] = {"ok": False, "skipped": True, "reason": "disabled"}
    if not slack_enabled(cfg, force=force):
        return report
    dest = _destination(cfg, environ)
    if not dest["webhook"] and not dest["token"]:
        report["reason"] = "no_destination"
        log.warning("slack enabled but no webhook/token")
        return report
    report.update(
        {
            "skipped": False,
            "channel": dest["channel"],
            "text": text,
            "dry_run": dry_run,
        }
    )
    if dry_run:
        report["ok"] = True
        report["reason"] = "dry_run"
        return report
    try:
        if dest["webhook"]:
            st, payload = request_json(
                "POST",
                dest["webhook"],
                body={"text": text, "channel": dest["channel"]},
            )
            ok = st < 400
        else:
            st, payload = request_json(
                "POST",
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {dest['token']}"},
                body={"channel": dest["channel"], "text": text},
            )
            ok = st < 400 and isinstance(payload, dict) and payload.get("ok") is True
        report["ok"] = bool(ok)
        report["status_code"] = st
        report["reason"] = "posted" if ok else "http"
    except HttpError as exc:
        log.warning("slack notify failed: %s", exc)
        report["ok"] = False
        report["reason"] = "http"
    # Bool is an int subclass. Same rule as the ci audit wrappers: only a real int.
    # The handoff cycle helper prefers a handoff this notice does not have.
    audit_details: Dict[str, Any] = {}
    source = details.get("source")
    backend = details.get("backend")
    exit_code = details.get("exit_code")
    if isinstance(source, str):
        audit_details["source"] = source
    if isinstance(backend, str):
        audit_details["backend"] = backend
    if isinstance(exit_code, int) and not isinstance(exit_code, bool):
        audit_details["exit_code"] = exit_code
    audit_details["channel"] = dest["channel"]
    audit_details["ok"] = report["ok"]
    try:
        snap = snapshot(agent_dir=workdir / ".agent")
        cycle_number = snap.get("cycle_number") if isinstance(snap, dict) else None
        if isinstance(cycle_number, bool) or not isinstance(cycle_number, int):
            cycle = 0
        else:
            cycle = cycle_number
        append_entry(
            "slack_notify",
            "ci",
            cycle,
            details=audit_details,
            agent_dir=workdir / ".agent",
        )
    except Exception as exc:
        log.warning("slack audit append failed: %s", exc)
    return report
