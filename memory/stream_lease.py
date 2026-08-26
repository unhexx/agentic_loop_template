# -*- coding: utf-8 -*-
"""Эксклюзивные lease на owned_paths для параллельных сессий оператора."""
from __future__ import annotations

import argparse
import errno
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from memory.agent_lock import agent_lock
from memory.streams import owned_covers, parse_stream_specs

LEASES_FILE = "stream_leases.json"
DEFAULT_TTL_S = 7200
_LOCK_NAME = "leases"


def _agent_dir(hub: Path | str) -> Path:
    return Path(hub) / ".agent"


def _leases_path(agent: Path) -> Path:
    return agent / LEASES_FILE


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _norm_name(name: str) -> str:
    n = (name or "").strip()
    if not n:
        raise ValueError("empty stream name")
    return n


def _norm_paths(owned_paths: Sequence[str]) -> List[str]:
    out: List[str] = []
    for raw in owned_paths:
        p = (raw or "").strip().replace("\\", "/")
        if not p:
            raise ValueError("empty owned path")
        out.append(p)
    if not out:
        raise ValueError("no owned_paths")
    return out


def _pid_dead(pid: int) -> bool:
    """True, если процесса нет. PermissionError считаем живым — как agent_lock."""
    if pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    except OSError as exc:
        if getattr(exc, "errno", None) == errno.ESRCH:
            return True
        if os.name == "nt" and getattr(exc, "errno", None) in (errno.EINVAL, errno.ENOENT):
            return True
        return False
    return False


def _lease_pid(rec: Any) -> Optional[int]:
    if not isinstance(rec, dict) or "pid" not in rec:
        return None
    raw = rec.get("pid")
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _lease_pid_dead(rec: Any) -> bool:
    """Мёртвый или нечитаемый PID можно забрать; живой — никогда."""
    pid = _lease_pid(rec)
    if pid is None:
        return True
    return _pid_dead(pid)


def _overlap_pair(
    a: Sequence[str], b: Sequence[str]
) -> Optional[Tuple[str, str]]:
    for oa in a:
        for ob in b:
            if owned_covers(oa, ob) or owned_covers(ob, oa):
                return oa, ob
    return None


def _overlap_error(name_a: str, name_b: str, oa: str, ob: str) -> ValueError:
    return ValueError(
        f"overlap between streams {name_a!r} and {name_b!r}: {oa!r} vs {ob!r}"
    )


def _load(agent: Path, *, strict: bool = True) -> Dict[str, Any]:
    """Прочитать реестр. strict=True — битый JSON не затираем (мутации)."""
    path = _leases_path(agent)
    if not path.is_file():
        return {"leases": {}}

    def _fail(exc: Optional[BaseException] = None) -> Dict[str, Any]:
        if strict:
            msg = f"unreadable stream_leases.json: {path}"
            if exc is not None:
                raise ValueError(msg) from exc
            raise ValueError(msg)
        return {"leases": {}}

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return _fail(exc)
    if not raw.strip():
        return _fail()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _fail(exc)
    if not isinstance(data, dict) or not isinstance(data.get("leases"), dict):
        return _fail()
    return data


def _save(agent: Path, data: Dict[str, Any]) -> None:
    agent.mkdir(parents=True, exist_ok=True)
    path = _leases_path(agent)
    tmp = path.with_suffix(".json.tmp")
    text = json.dumps(data, ensure_ascii=False, indent=2)
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _public(name: str, rec: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(rec)
    out["name"] = name
    return out


def _drop_dead_overlapping(
    leases: Dict[str, Any], name: str, paths: Sequence[str]
) -> None:
    """Убрать мёртвые чужие записи, пересекающиеся с paths. Живых не трогаем."""
    for other_name, rec in list(leases.items()):
        if other_name == name:
            continue
        if not isinstance(rec, dict):
            del leases[other_name]
            continue
        if not _lease_pid_dead(rec):
            continue
        if _overlap_pair(paths, rec.get("owned_paths") or []) is not None:
            del leases[other_name]


def _new_record(
    owned_paths: List[str],
    *,
    pid: int,
    worktree: Optional[str],
    branch: Optional[str],
    ttl_s: int,
    claimed_at: Optional[str] = None,
) -> Dict[str, Any]:
    now = _now()
    return {
        "owned_paths": list(owned_paths),
        "worktree": worktree,
        "pid": pid,
        "claimed_at": claimed_at or _iso(now),
        "expires_at": _iso(now + timedelta(seconds=int(ttl_s))),
        "branch": branch,
    }


def claim(
    hub: Path | str,
    name: str,
    owned_paths: Sequence[str],
    *,
    worktree: Optional[str] = None,
    ttl_s: int = DEFAULT_TTL_S,
    branch: Optional[str] = None,
) -> Dict[str, Any]:
    """Занять owned_paths. Живой PID не крадём, TTL только для отображения."""
    name = _norm_name(name)
    paths = _norm_paths(owned_paths)
    my_pid = os.getpid()
    wt = str(worktree) if worktree else None
    br = str(branch) if branch else None
    agent = _agent_dir(hub)
    with agent_lock(agent, name=_LOCK_NAME):
        data = _load(agent)
        leases: Dict[str, Any] = data.setdefault("leases", {})

        for other_name, rec in leases.items():
            if other_name == name:
                continue
            if not isinstance(rec, dict) or _lease_pid_dead(rec):
                continue
            pair = _overlap_pair(paths, rec.get("owned_paths") or [])
            if pair is not None:
                raise _overlap_error(name, other_name, pair[0], pair[1])

        existing = leases.get(name)
        if isinstance(existing, dict) and not _lease_pid_dead(existing):
            holder = _lease_pid(existing)
            if holder != my_pid:
                raise ValueError(f"lease {name!r} held by live pid {holder}")
            existing["owned_paths"] = paths
            existing["pid"] = my_pid
            existing["expires_at"] = _iso(_now() + timedelta(seconds=int(ttl_s)))
            if wt is not None:
                existing["worktree"] = wt
            if br is not None:
                existing["branch"] = br
            _drop_dead_overlapping(leases, name, paths)
            _save(agent, data)
            return _public(name, existing)

        _drop_dead_overlapping(leases, name, paths)

        rec = _new_record(paths, pid=my_pid, worktree=wt, branch=br, ttl_s=ttl_s)
        leases[name] = rec
        _save(agent, data)
        return _public(name, rec)


def renew(
    hub: Path | str,
    name: str,
    *,
    ttl_s: int = DEFAULT_TTL_S,
) -> Dict[str, Any]:
    """Продлить expires_at, только если lease наш и PID жив."""
    name = _norm_name(name)
    my_pid = os.getpid()
    agent = _agent_dir(hub)
    with agent_lock(agent, name=_LOCK_NAME):
        data = _load(agent)
        rec = data.get("leases", {}).get(name)
        if not isinstance(rec, dict):
            raise ValueError(f"no lease for stream {name!r}")
        if _lease_pid_dead(rec):
            raise ValueError(f"no live lease for stream {name!r}")
        holder = _lease_pid(rec)
        if holder != my_pid:
            raise ValueError(f"lease {name!r} held by live pid {holder}")
        rec["pid"] = my_pid
        rec["expires_at"] = _iso(_now() + timedelta(seconds=int(ttl_s)))
        _save(agent, data)
        return _public(name, rec)


def release(hub: Path | str, name: str) -> bool:
    """Снять lease, если PID наш или уже мёртв. Чужой живой не трогаем."""
    name = _norm_name(name)
    my_pid = os.getpid()
    agent = _agent_dir(hub)
    with agent_lock(agent, name=_LOCK_NAME):
        data = _load(agent)
        leases: Dict[str, Any] = data.setdefault("leases", {})
        rec = leases.get(name)
        if rec is None:
            return False
        if not _lease_pid_dead(rec) and _lease_pid(rec) != my_pid:
            raise ValueError(f"lease {name!r} held by live pid {_lease_pid(rec)}")
        del leases[name]
        _save(agent, data)
        return True


def status(hub: Path | str) -> Dict[str, Any]:
    """Снимок .agent/stream_leases.json."""
    agent = _agent_dir(hub)
    with agent_lock(agent, name=_LOCK_NAME):
        return _load(agent, strict=False)


def _stream_name_arg(raw: str) -> str:
    raw = (raw or "").strip()
    if ":" in raw:
        return _norm_name(raw.split(":", 1)[0])
    return _norm_name(raw)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m memory.stream_lease",
        description="Эксклюзивные lease на owned_paths",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_workdir(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--workdir", default=".", help="Корень хаба (каталог с .agent)")

    cp = sub.add_parser("claim", help="Занять owned_paths")
    add_workdir(cp)
    cp.add_argument("--stream", required=True, help="name:path1,path2")
    cp.add_argument("--worktree", default=None)
    cp.add_argument("--branch", default=None)
    cp.add_argument("--ttl", type=int, default=DEFAULT_TTL_S)

    rp = sub.add_parser("renew", help="Продлить TTL своего lease")
    add_workdir(rp)
    rp.add_argument("--stream", required=True, help="имя потока")
    rp.add_argument("--ttl", type=int, default=DEFAULT_TTL_S)

    rel = sub.add_parser("release", help="Освободить lease")
    add_workdir(rel)
    rel.add_argument("--stream", required=True, help="имя потока")

    st = sub.add_parser("status", help="Показать реестр")
    add_workdir(st)

    args = parser.parse_args(argv)
    hub = Path(args.workdir)
    try:
        if args.cmd == "claim":
            plans = parse_stream_specs([args.stream])
            plan = plans[0]
            result: Any = claim(
                hub,
                plan.name,
                plan.owned_paths,
                worktree=args.worktree,
                ttl_s=args.ttl,
                branch=args.branch,
            )
        elif args.cmd == "renew":
            result = renew(hub, _stream_name_arg(args.stream), ttl_s=args.ttl)
        elif args.cmd == "release":
            released = release(hub, _stream_name_arg(args.stream))
            result = {"released": released, "name": _stream_name_arg(args.stream)}
        elif args.cmd == "status":
            result = status(hub)
        else:
            parser.print_help()
            return 2
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
