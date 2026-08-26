# -*- coding: utf-8 -*-
"""
Audit trail для enterprise governance (P5).

Фиксирует значимые действия цикла: git sync, policy decisions, approvals, tool calls.
Хранение: .agent/AUDIT_LOG.json + .agent/AUDIT_LOG.md
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from memory.agent_lock import agent_lock

AUDIT_JSON = Path(".agent/AUDIT_LOG.json")
AUDIT_MD = Path(".agent/AUDIT_LOG.md")
TORN_RETRIES = 3
TORN_RETRY_S = 0.020


def _audit_json(agent_dir: Optional[Path] = None) -> Path:
    """Явный agent_dir (дашборд) или модульный путь (CLI / тесты с патчем глобалей)."""
    return Path(agent_dir) / "AUDIT_LOG.json" if agent_dir is not None else AUDIT_JSON


def _audit_md(agent_dir: Optional[Path] = None) -> Path:
    return Path(agent_dir) / "AUDIT_LOG.md" if agent_dir is not None else AUDIT_MD


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dir(agent_dir: Optional[Path] = None) -> None:
    _audit_json(agent_dir).parent.mkdir(parents=True, exist_ok=True)


def _read_json_retry(path: Path) -> Optional[Dict[str, Any]]:
    """JSON с 3×20мс на обрыв. None — файл не читается, не затираем."""
    attempts = TORN_RETRIES + 1
    for attempt in range(attempts):
        try:
            text = path.read_text(encoding="utf-8")
            if not text.strip():
                raise json.JSONDecodeError("empty", text, 0)
            data = json.loads(text)
            if not isinstance(data, dict):
                raise json.JSONDecodeError("not object", text, 0)
            return data
        except (json.JSONDecodeError, OSError, UnicodeError):
            if attempt + 1 < attempts:
                time.sleep(TORN_RETRY_S)
    return None


def _load(agent_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    _ensure_dir(agent_dir)
    path = _audit_json(agent_dir)
    if not path.exists():
        return {"entries": [], "updated_at": _now_iso()}
    data = _read_json_retry(path)
    if data is None:
        return None
    entries = data.get("entries")
    if not isinstance(entries, list):
        data["entries"] = []
    return data


def _save(data: Dict[str, Any], agent_dir: Optional[Path] = None) -> None:
    data["updated_at"] = _now_iso()
    path = _audit_json(agent_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    # tmp+replace: прямой write_text оставляет обрезанный JSON, если процесс оборвётся.
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    _write_md(data, agent_dir=agent_dir)


def _write_md(data: Dict[str, Any], agent_dir: Optional[Path] = None) -> None:
    lines = ["# AUDIT_LOG.md — Enterprise Audit Trail", "", f"**Updated:** {data.get('updated_at')}", ""]
    for e in data.get("entries", [])[-20:]:
        lines.append(f"- [{e.get('ts')}] {e.get('action')} | role={e.get('role')} | cycle={e.get('cycle')} | sig={e.get('signature', '')[:12]}")
    md = _audit_md(agent_dir)
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text("\n".join(lines), encoding="utf-8")


def _sign(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def append_entry(
    action: str,
    role: str,
    cycle: int,
    details: Optional[Dict[str, Any]] = None,
    approval_required: bool = False,
    approved: Optional[bool] = None,
    agent_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Добавляет запись в audit trail.

    agent_dir=None — пути модуля (CLI и старые тесты с подменой AUDIT_JSON).
    Дашборд передаёт workdir/.agent, чтобы не зависеть от cwd.
    """
    # Лок на родителе реального JSON, не на cwd/.agent: иначе патч AUDIT_JSON уезжает мимо.
    with agent_lock(_audit_json(agent_dir).parent, name="audit"):
        data = _load(agent_dir=agent_dir)
        if data is None:
            # порванный журнал не затираем однострочной записью
            entry = {
                "id": "A-0000",
                "ts": _now_iso(),
                "action": action,
                "role": role,
                "cycle": cycle,
                "details": details or {},
                "approval_required": approval_required,
                "approved": approved,
            }
            entry["signature"] = _sign(entry)
            return entry
        entries = data.setdefault("entries", [])
        if not isinstance(entries, list):
            entries = []
            data["entries"] = entries
        entry = {
            "id": f"A-{len(entries) + 1:04d}",
            "ts": _now_iso(),
            "action": action,
            "role": role,
            "cycle": cycle,
            "details": details or {},
            "approval_required": approval_required,
            "approved": approved,
        }
        entry["signature"] = _sign(entry)
        entries.append(entry)
        _save(data, agent_dir=agent_dir)
        return entry


def list_entries(limit: int = 20, agent_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    data = _load(agent_dir=agent_dir)
    if data is None:
        return []
    return data.get("entries", [])[-limit:]


def _cli() -> None:
    p = argparse.ArgumentParser(description="Enterprise audit trail")
    sub = p.add_subparsers(dest="cmd")

    ap = sub.add_parser("append")
    ap.add_argument("--action", required=True)
    ap.add_argument("--role", required=True)
    ap.add_argument("--cycle", type=int, required=True)
    ap.add_argument("--details", default="{}")
    ap.add_argument("--approval-required", action="store_true")

    lp = sub.add_parser("list")
    lp.add_argument("--limit", type=int, default=20)

    args = p.parse_args()
    if args.cmd == "append":
        details = json.loads(args.details)
        e = append_entry(args.action, args.role, args.cycle, details, args.approval_required)
        print(json.dumps(e, ensure_ascii=False, indent=2))
    elif args.cmd == "list":
        print(json.dumps(list_entries(args.limit), ensure_ascii=False, indent=2))
    else:
        p.print_help()


if __name__ == "__main__":
    _cli()