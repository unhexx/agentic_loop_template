# -*- coding: utf-8 -*-
"""Bounded harness tools. In-process calls return values; nothing prints."""

from __future__ import annotations

import copy
import errno
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import memory.knowledge as knowledge
import memory.playbooks as playbooks
import memory.proxy.config as proxy_config
import memory.proxy.health as proxy_health
import memory.proxy.stats as proxy_stats
import memory.state as state
import memory.store as store
import memory.validate_handoff as validate_handoff_mod
import memory.workspace as workspace
from memory.agent_lock import lock_path
from memory.integrations.config import (
    load_project_cfg,
    slack_enabled,
    tracker_enabled,
    tracker_project_id,
    tracker_provider,
)
from memory.integrations.state import load_state, state_path
from memory.local_mcp.audit import local_mcp_memory_update
from memory.local_mcp.redact import contains_secret_marker, redact_text
from memory.local_mcp.server import ToolResult, kill_process_group

# UTF-8 cap. Drop whole keys; a sliced byte string is not JSON.
_SNAPSHOT_LIMIT = 32768
_METRICS_LIMIT = 2048
_HANDOFF_MAX = 64 * 1024
_PYTEST_TIMEOUT_DEFAULT = 600
_PYTEST_TIMEOUT_MAX = 1800
_GIT_TOPLEVEL_TIMEOUT_S = 5
_GIT_PREFLIGHT_TIMEOUT_S = 60
_DOCKER_TIMEOUT_S = 60
_DOCKER_TAIL_DEFAULT = 200
_PLAYBOOK_LIST_CAP = 100

_DOCKER_OK = frozenset({"ps", "logs", "config"})
# Copied only when set. Same names as the act probe. Not a prefix match.
_DOCKER_SOCKET_KEYS = (
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_CONFIG",
    "DOCKER_CERT_PATH",
)
_PROXY_FIRST = ("pxpipe",)
_TAIL_LINES = 6
_TAIL_CHARS = 400
_PROXY_KEEP = frozenset(
    {
        "proxy_mode",
        "gateway_ok",
        "pxpipe_ok",
        "project_events",
        "cache_hits",
        "cache_entries",
    }
)
_SNAPSHOT_KEEP = frozenset({"workspace_id", "file"})
_K_RE = re.compile(r"^[A-Za-z0-9_\[\]\-:* ]{1,200}$")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_SQLITE_MAGIC = b"SQLite format 3\x00"


@dataclass
class _Ran:
    code: int | None = None
    stdout: str = ""
    stderr: str = ""
    missing: bool = False
    timed_out: bool = False
    cancelled: bool = False


def _refuse(code: str, message: str, *, exit_code: int | None = None) -> ToolResult:
    body: dict[str, Any] = {"ok": False, "error": {"code": code, "message": message}}
    if exit_code is not None:
        body["exit_code"] = exit_code
    return ToolResult(text=f"{code}: {message}", structured=body, is_error=True)


def _ok(name: str, structured: dict[str, Any]) -> ToolResult:
    return ToolResult(text=f"{name} ok", structured=structured, is_error=False)


def _utf8_len(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _resolve_within(candidate: Path, root: Path, message: str) -> Path | ToolResult:
    # resolve() collapses symlinks. An embedded NUL raises ValueError.
    try:
        resolved = candidate.resolve()
    except (OSError, ValueError):
        return _refuse("path_escape", message, exit_code=2)
    if not _is_within(resolved, root):
        return _refuse("path_escape", message, exit_code=2)
    return resolved


def _resolve_pair(workdir: Path, allow_root: Path) -> tuple[Path, Path] | ToolResult:
    try:
        root = allow_root.resolve()
    except (OSError, ValueError):
        return _refuse("path_escape", "workdir escapes the allow-root", exit_code=2)
    resolved = _resolve_within(workdir, root, "workdir escapes the allow-root")
    if isinstance(resolved, ToolResult):
        return resolved
    if not (resolved / ".agent").exists() and not (resolved / ".git").exists():
        return _refuse("path_escape", "workdir has no .agent or .git", exit_code=2)
    return resolved, root


def _contained_agent(workdir: Path) -> Path | ToolResult:
    # A symlink at .agent must not name a directory outside the workdir.
    return _resolve_within(
        workdir / ".agent", workdir, "agent dir escapes the workdir"
    )


def _path_env() -> dict[str, str]:
    """PATH only. The rest of the parent environment stays out of the child."""
    env: dict[str, str] = {}
    path = os.environ.get("PATH")
    if isinstance(path, str):
        env["PATH"] = path
    return env


def _compose_env() -> dict[str, str]:
    """PATH plus the Docker socket variables. HOME stays out."""
    env = _path_env()
    for key in _DOCKER_SOCKET_KEYS:
        value = os.environ.get(key)
        if isinstance(value, str) and value and "\x00" not in value:
            env[key] = value
    return env


def _read_pipe(pipe: Any, sink: list[bytes], limit: int = 65536) -> None:
    # The failure line is at the end. A head-only buffer would drop it.
    buf = bytearray()
    try:
        while True:
            chunk = pipe.read(65536)
            if not chunk:
                break
            buf.extend(chunk)
            if len(buf) > limit:
                del buf[:-limit]
    except Exception:
        pass
    if buf:
        sink.append(bytes(buf))


def _spawn(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout_s: float,
    env: Mapping[str, str],
    cancel: threading.Event | None,
) -> _Ran:
    try:
        proc = subprocess.Popen(
            list(argv),
            cwd=str(cwd),
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            shell=False,
        )
    except FileNotFoundError:
        return _Ran(missing=True)
    except OSError:
        return _Ran(code=1)
    # communicate() cannot be polled, and a full pipe would stall wait().
    out_sink: list[bytes] = []
    err_sink: list[bytes] = []
    threads: list[threading.Thread] = []
    if proc.stdout is not None:
        threads.append(
            threading.Thread(
                target=_read_pipe, args=(proc.stdout, out_sink), daemon=True
            )
        )
    if proc.stderr is not None:
        threads.append(
            threading.Thread(
                target=_read_pipe, args=(proc.stderr, err_sink), daemon=True
            )
        )
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + float(timeout_s)
    status = "ok"
    try:
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                status = "cancelled"
                kill_process_group(proc)
                break
            if time.monotonic() >= deadline:
                status = "timed_out"
                kill_process_group(proc)
                break
            try:
                proc.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                continue
    except Exception:
        kill_process_group(proc)
        raise
    for thread in threads:
        thread.join(timeout=2)
    text = b"".join(out_sink).decode("utf-8", errors="replace")
    err = b"".join(err_sink).decode("utf-8", errors="replace")
    if status == "cancelled":
        return _Ran(cancelled=True, code=130, stdout=text, stderr=err)
    if status == "timed_out":
        return _Ran(timed_out=True, code=124, stdout=text, stderr=err)
    code = proc.returncode if proc.returncode is not None else 1
    return _Ran(code=code, stdout=text, stderr=err)


def _redacted_stream(raw: str) -> str | None:
    """Pipe-capped child text. None when empty or a secret marker remains."""
    if not raw or not raw.strip():
        return None
    # Redact the whole buffer. A later slice could keep a token fragment.
    redacted = redact_text(raw)
    if not redacted or not redacted.strip() or contains_secret_marker(redacted):
        return None
    return redacted


def _failure_tail(stdout: str, stderr: str) -> str | None:
    raw = stderr.strip() or stdout.strip()
    if not raw:
        return None
    # Redact before slicing so a cut cannot leave a token tail.
    redacted = redact_text(raw)
    if not redacted or contains_secret_marker(redacted):
        return None
    snippet = "\n".join(redacted.splitlines()[-_TAIL_LINES:]).strip()
    if len(snippet) > _TAIL_CHARS:
        snippet = snippet[-_TAIL_CHARS:]
    return snippet or None


def _with_tail(result: ToolResult, ran: _Ran) -> ToolResult:
    if not result.is_error:
        return result
    tail = _failure_tail(ran.stdout, ran.stderr)
    if tail:
        result.structured["tail"] = tail
    return result


def _from_spawn(name: str, ran: _Ran) -> ToolResult:
    if ran.cancelled:
        return _with_tail(
            _refuse("cancelled", f"{name} cancelled", exit_code=130), ran
        )
    if ran.timed_out:
        return _with_tail(
            _refuse("timed_out", f"{name} timed out", exit_code=124), ran
        )
    if ran.missing:
        return _refuse("spawn_failed", f"{name} command not found", exit_code=127)
    code = 1 if ran.code is None else ran.code
    if code == 0:
        return ToolResult(
            text=f"{name} exit=0",
            structured={"ok": True, "error": None, "exit_code": 0},
            is_error=False,
        )
    return _with_tail(
        ToolResult(
            text=f"{name} exit={code}",
            structured={
                "ok": False,
                "error": {
                    "code": f"{name}_failed",
                    "message": f"{name} exited {code}",
                },
                "exit_code": code,
            },
            is_error=True,
        ),
        ran,
    )


def _pid_alive(pid: int) -> bool:
    """Same liveness rule as agent_lock: PermissionError means the pid is alive."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        err = getattr(exc, "errno", None)
        if err == errno.ESRCH:
            return False
        if os.name == "nt" and err in (errno.EINVAL, errno.ENOENT):
            return False
        return True
    return True


def _pipeline_lock_held(agent: Path) -> bool:
    # Do not mkdir .agent just to see whether the lock file is there.
    path = lock_path(agent, "local_pipeline")
    try:
        raw = path.read_text(encoding="ascii", errors="replace")
    except OSError:
        return False
    stripped = raw.strip()
    if not stripped:
        return False
    token = stripped.split()[0]
    try:
        pid = int(token)
    except ValueError:
        return False
    return _pid_alive(pid)


def _pack(
    data: Mapping[str, Any],
    dropped: list[str],
    *,
    truncated: bool,
) -> dict[str, Any]:
    packed = dict(data)
    packed["truncated"] = truncated
    packed["dropped"] = list(dropped)
    return packed


def _remember_drop(
    data: Mapping[str, Any],
    dropped: list[str],
    keep: frozenset[str],
    name: str,
) -> None:
    # Stop when the name list alone would push the identity object over the cap.
    if name in dropped:
        return
    identity = {key: data[key] for key in keep if key in data}
    try:
        size = _utf8_len(_pack(identity, [*dropped, name], truncated=True))
    except (TypeError, ValueError):
        return
    if size <= _SNAPSHOT_LIMIT:
        dropped.append(name)


def _bound_payload(
    payload: dict[str, Any],
    *,
    keep: frozenset[str],
    first: Sequence[str] = (),
    shrink: (
        Callable[[dict[str, Any], list[str], Callable[[], bool]], None] | None
    ) = None,
) -> dict[str, Any]:
    if _utf8_len(_pack(payload, [], truncated=False)) <= _SNAPSHOT_LIMIT:
        return _pack(payload, [], truncated=False)
    data = copy.deepcopy(payload)
    dropped: list[str] = []

    def fits() -> bool:
        return _utf8_len(_pack(data, dropped, truncated=True)) <= _SNAPSHOT_LIMIT

    for key in first:
        if fits():
            break
        if key in data:
            data.pop(key, None)
            _remember_drop(data, dropped, keep, str(key))
    if shrink is not None and not fits():
        shrink(data, dropped, fits)
    if not fits():
        extras = [key for key in list(data) if key not in keep]
        extras.sort(key=lambda key: _utf8_len(data.get(key)), reverse=True)
        for key in extras:
            if fits():
                break
            data.pop(key, None)
            _remember_drop(data, dropped, keep, str(key))
    return _pack(data, dropped, truncated=True)


def _bound_memory(snap: dict[str, Any]) -> dict[str, Any]:
    def shrink(
        data: dict[str, Any],
        dropped: list[str],
        fits: Callable[[], bool],
    ) -> None:
        dists = data.get("recent_distillations")
        if isinstance(dists, list):
            removed = False
            while dists and not fits():
                dists.pop()
                removed = True
                _remember_drop(
                    data, dropped, _SNAPSHOT_KEEP, "recent_distillations"
                )
            if not dists:
                data.pop("recent_distillations", None)
            if removed:
                _remember_drop(
                    data, dropped, _SNAPSHOT_KEEP, "recent_distillations"
                )
        patterns = data.get("patterns")
        if isinstance(patterns, dict):
            while patterns and not fits():
                name = min(
                    patterns,
                    key=lambda key: (
                        len(patterns[key]) if isinstance(patterns[key], list) else 0,
                        str(key),
                    ),
                )
                patterns.pop(name, None)
                _remember_drop(data, dropped, _SNAPSHOT_KEEP, str(name))
            if not patterns:
                data.pop("patterns", None)
                _remember_drop(data, dropped, _SNAPSHOT_KEEP, "patterns")

    return _bound_payload(
        snap,
        keep=_SNAPSHOT_KEEP,
        first=("llm_ontology",),
        shrink=shrink,
    )


def _bound_proxy(stats: dict[str, Any]) -> dict[str, Any]:
    return _bound_payload(stats, keep=_PROXY_KEEP, first=_PROXY_FIRST)


def _git_preflight(
    arguments: Mapping[str, Any],
    workdir: Path,
    allow_root: Path,
    cancel: threading.Event | None,
) -> ToolResult:
    ran = _spawn(
        ["git", "-C", str(workdir), "rev-parse", "--show-toplevel"],
        cwd=workdir,
        timeout_s=_GIT_TOPLEVEL_TIMEOUT_S,
        env=_path_env(),
        cancel=cancel,
    )
    if ran.cancelled:
        return _from_spawn("git_preflight", ran)
    if ran.missing or ran.timed_out or ran.code != 0 or not ran.stdout.strip():
        return _with_tail(
            _refuse(
                "git_toplevel_failed",
                "git toplevel could not be resolved",
                exit_code=2,
            ),
            ran,
        )
    top = Path(ran.stdout.strip().splitlines()[0].strip())
    if not top.is_absolute():
        top = workdir / top
    resolved_top = _resolve_within(
        top, allow_root, "git toplevel escapes the allow-root"
    )
    if isinstance(resolved_top, ToolResult):
        return resolved_top
    # bash follows a symlink. HOME stays unset unless the script is a
    # regular file inside the allow-root.
    script = _resolve_within(
        workdir / "scripts" / "preflight_git.sh",
        allow_root,
        "preflight script escapes the allow-root",
    )
    if isinstance(script, ToolResult):
        return script
    if not script.is_file():
        return _refuse(
            "path_escape",
            "preflight script escapes the allow-root",
            exit_code=2,
        )
    env = _path_env()
    home = os.environ.get("HOME")
    if isinstance(home, str):
        env["HOME"] = home
    strict = arguments.get("strict_multi_repo", False)
    env["STRICT_MULTI_REPO"] = "1" if strict is True else "0"
    ran = _spawn(
        ["bash", str(script)],
        cwd=workdir,
        timeout_s=_GIT_PREFLIGHT_TIMEOUT_S,
        env=env,
        cancel=cancel,
    )
    return _from_spawn("git_preflight", ran)


def _load_handoff_file(raw: object, workdir: Path) -> Any:
    if not isinstance(raw, str) or not raw:
        return _refuse("path_escape", "handoff path escapes the workdir", exit_code=2)
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = workdir / candidate
    resolved = _resolve_within(
        candidate, workdir, "handoff path escapes the workdir"
    )
    if isinstance(resolved, ToolResult):
        return resolved
    if not resolved.is_file():
        return _refuse("handoff_not_file", "handoff path is not a regular file")
    try:
        if resolved.stat().st_size > _HANDOFF_MAX:
            return _refuse("handoff_too_large", "handoff exceeds 64 KiB")
        text = resolved.read_text(encoding="utf-8")
    except OSError:
        return _refuse("handoff_unreadable", "handoff file could not be read")
    except UnicodeError:
        return _refuse("handoff_invalid_json", "handoff file is not JSON")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return _refuse("handoff_invalid_json", "handoff file is not JSON")


def _validate_handoff(
    arguments: Mapping[str, Any],
    workdir: Path,
    _allow_root: Path,
    _cancel: threading.Event | None,
) -> ToolResult:
    has_path = "path" in arguments
    has_obj = "handoff" in arguments
    if has_path == has_obj:
        return _refuse("handoff_input", "exactly one of path or handoff is required")
    strict = arguments.get("strict_done", True)
    if not isinstance(strict, bool):
        strict = True
    if has_obj:
        handoff = arguments["handoff"]
        if not isinstance(handoff, dict):
            return _refuse("handoff_input", "handoff must be a JSON object")
        try:
            size = _utf8_len(handoff)
        except (TypeError, ValueError):
            return _refuse("handoff_too_large", "handoff exceeds 64 KiB")
        if size > _HANDOFF_MAX:
            return _refuse("handoff_too_large", "handoff exceeds 64 KiB")
        data = handoff
    else:
        data = _load_handoff_file(arguments.get("path"), workdir)
        if isinstance(data, ToolResult):
            return data
    valid, errors = validate_handoff_mod.validate_handoff(data, strict_done=strict)
    return ToolResult(
        text=f"validate_handoff valid={str(bool(valid)).lower()}",
        structured={
            "ok": True,
            "valid": bool(valid),
            "errors": list(errors),
            "error": None,
        },
        is_error=False,
    )


def _state_snapshot(
    arguments: Mapping[str, Any],
    workdir: Path,
    _allow_root: Path,
    _cancel: threading.Event | None,
) -> ToolResult:
    window = arguments.get("window", 3)
    if isinstance(window, bool) or not isinstance(window, int) or not 1 <= window <= 20:
        return _refuse("invalid_window", "window must be an integer from 1 to 20")
    agent = _contained_agent(workdir)
    if isinstance(agent, ToolResult):
        return agent
    return _ok("state_snapshot", state.snapshot(window, agent_dir=agent))


def _state_append_delta(
    arguments: Mapping[str, Any],
    workdir: Path,
    _allow_root: Path,
    _cancel: threading.Event | None,
) -> ToolResult:
    text = arguments.get("text")
    role = arguments.get("role")
    if not isinstance(text, str) or not isinstance(role, str):
        return _refuse("state_append_rejected", "text and role are required")
    agent = _contained_agent(workdir)
    if isinstance(agent, ToolResult):
        return agent
    return _ok(
        "state_append_delta",
        state.append_delta(text, role, agent_dir=agent),
    )


def _state_metrics_log(
    arguments: Mapping[str, Any],
    workdir: Path,
    _allow_root: Path,
    _cancel: threading.Event | None,
) -> ToolResult:
    metrics = arguments.get("metrics")
    if not isinstance(metrics, dict):
        return _refuse("metrics_too_large", "metrics object exceeds 2048 bytes")
    try:
        size = _utf8_len(metrics)
    except (TypeError, ValueError):
        return _refuse("metrics_too_large", "metrics object exceeds 2048 bytes")
    if size > _METRICS_LIMIT:
        return _refuse("metrics_too_large", "metrics object exceeds 2048 bytes")
    agent = _contained_agent(workdir)
    if isinstance(agent, ToolResult):
        return agent
    state.log_metrics(metrics, agent_dir=agent)
    return ToolResult(
        text="state_metrics_log ok",
        structured={"ok": True, "error": None},
        is_error=False,
    )


def _memory_info(
    _arguments: Mapping[str, Any],
    workdir: Path,
    _allow_root: Path,
    _cancel: threading.Event | None,
) -> ToolResult:
    paths = workspace.memory_paths(cwd=workdir)
    return _ok(
        "memory_info",
        {
            "workspace_id": str(paths["workspace_id"]),
            "dir": str(paths["dir"]),
            "file": str(paths["file"]),
            "lock": str(paths["lock"]),
        },
    )


def _memory_query(
    arguments: Mapping[str, Any],
    workdir: Path,
    _allow_root: Path,
    _cancel: threading.Event | None,
) -> ToolResult:
    top = arguments.get("top", 5)
    if isinstance(top, bool) or not isinstance(top, int):
        top = 5
    rows = store.query_memory(
        category=arguments.get("category"),
        top_n=top,
        contains=arguments.get("contains"),
        cwd=workdir,
    )
    return _ok("memory_query", {"ok": True, "error": None, "results": rows})


def _memory_snapshot(
    _arguments: Mapping[str, Any],
    workdir: Path,
    _allow_root: Path,
    _cancel: threading.Event | None,
) -> ToolResult:
    snap = store.snapshot(cwd=workdir)
    if not isinstance(snap, dict):
        return _refuse("memory_snapshot_failed", "snapshot was not an object")
    bounded = _bound_memory(snap)
    label = "truncated" if bounded.get("truncated") else "ok"
    return ToolResult(
        text=f"memory_snapshot {label}",
        structured=bounded,
        is_error=False,
    )


def _memory_update(
    arguments: Mapping[str, Any],
    workdir: Path,
    _allow_root: Path,
    _cancel: threading.Event | None,
) -> ToolResult:
    category = arguments.get("category")
    description = arguments.get("description")
    if not isinstance(category, str) or not isinstance(description, str):
        return _refuse(
            "memory_update_rejected", "category and description are required"
        )
    agent = _contained_agent(workdir)
    if isinstance(agent, ToolResult):
        return agent
    result = store.update_memory(
        new_patterns=[{"category": category, "description": description}],
        cwd=workdir,
    )
    local_mcp_memory_update(workdir, category=category, description=description)
    if not isinstance(result, dict):
        result = {"ok": True}
    return _ok("memory_update", result)


def _knowledge_file(workdir: Path) -> Path | ToolResult:
    # _conn would mkdir the parent and create an empty database.
    db = _resolve_within(
        workdir / ".agent" / "knowledge" / "knowledge.sqlite",
        workdir,
        "knowledge database escapes the workdir",
    )
    if isinstance(db, ToolResult):
        return db
    if not db.is_file():
        return _refuse("knowledge_db_missing", "knowledge database is missing")
    try:
        with db.open("rb") as handle:
            header = handle.read(len(_SQLITE_MAGIC))
    except OSError:
        return _refuse("knowledge_db_missing", "knowledge database is missing")
    # Empty or non-SQLite bytes are not opened, so connect cannot rewrite them.
    if header != _SQLITE_MAGIC:
        return _refuse("knowledge_db_missing", "knowledge database is missing")
    return db


def _read_knowledge(load: Callable[[], Any]) -> Any | ToolResult:
    try:
        return load()
    except sqlite3.DatabaseError:
        # Includes OperationalError. A corrupt file stays a tool result.
        return _refuse("knowledge_db_missing", "knowledge database is missing")


def _knowledge_query(
    arguments: Mapping[str, Any],
    workdir: Path,
    _allow_root: Path,
    _cancel: threading.Event | None,
) -> ToolResult:
    db = _knowledge_file(workdir)
    if isinstance(db, ToolResult):
        return db
    top = arguments.get("top", 5)
    if isinstance(top, bool) or not isinstance(top, int):
        top = 5
    rows = _read_knowledge(
        lambda: knowledge.query(
            q=arguments.get("q"), top=top, db=db, readonly=True
        )
    )
    if isinstance(rows, ToolResult):
        return rows
    return _ok("knowledge_query", {"ok": True, "error": None, "results": rows})


def _knowledge_stats(
    _arguments: Mapping[str, Any],
    workdir: Path,
    _allow_root: Path,
    _cancel: threading.Event | None,
) -> ToolResult:
    db = _knowledge_file(workdir)
    if isinstance(db, ToolResult):
        return db
    body = _read_knowledge(lambda: knowledge.stats(db=db, readonly=True))
    if isinstance(body, ToolResult):
        return body
    if not isinstance(body, dict):
        body = {"ok": True}
    return _ok("knowledge_stats", body)


def _playbooks_blocked(agent_dir: Path) -> ToolResult | None:
    # select_bullets returns [] when disabled and does not say why.
    cfg = playbooks.load_config(agent_dir=agent_dir)
    if not cfg.get("enabled"):
        return _refuse("playbooks_disabled", "playbooks are disabled")
    relevance = str(cfg.get("relevance") or "").strip().lower()
    if relevance == "embed":
        return _refuse(
            "playbooks_embed_refused", "playbook embed relevance is refused"
        )
    return None


def _playbooks_select(
    arguments: Mapping[str, Any],
    workdir: Path,
    _allow_root: Path,
    _cancel: threading.Event | None,
) -> ToolResult:
    agent_dir = _contained_agent(workdir)
    if isinstance(agent_dir, ToolResult):
        return agent_dir
    blocked = _playbooks_blocked(agent_dir)
    if blocked is not None:
        return blocked
    k = arguments.get("k", 5)
    if isinstance(k, bool) or not isinstance(k, int):
        k = 5
    query = arguments.get("query")
    rows = playbooks.select_bullets(
        query if isinstance(query, str) else "",
        scopes=arguments.get("scopes"),
        k=k,
        agent_dir=agent_dir,
    )
    return _ok("playbooks_select", {"ok": True, "error": None, "results": rows})


def _playbooks_list(
    _arguments: Mapping[str, Any],
    workdir: Path,
    _allow_root: Path,
    _cancel: threading.Event | None,
) -> ToolResult:
    agent_dir = _contained_agent(workdir)
    if isinstance(agent_dir, ToolResult):
        return agent_dir
    blocked = _playbooks_blocked(agent_dir)
    if blocked is not None:
        return blocked
    rows: list[dict[str, Any]] = []
    for item in playbooks.list_playbooks(agent_dir=agent_dir)[:_PLAYBOOK_LIST_CAP]:
        if not isinstance(item, dict):
            continue
        rows.append(
            {"name": item.get("name"), "count": item.get("bullet_count", 0)}
        )
    return _ok("playbooks_list", {"ok": True, "error": None, "playbooks": rows})


def _require_proxy_loopback(workdir: Path, *, include_dial: bool) -> None:
    """Raise ValueError before a dial when a hop host is not loopback.

    The message stays generic so a refused tool result cannot echo the URL.
    """
    cfg = proxy_config.load_proxy_config(workdir)
    hops = [
        (cfg.get("pxpipe_base"), proxy_config.PXPIPE_PORT),
        (cfg.get("gateway_base"), proxy_config.GATEWAY_PORT),
    ]
    if include_dial:
        hops.append((cfg.get("chat_proxy"), proxy_config.GATEWAY_PORT))
    for raw, default_port in hops:
        host, _port = proxy_config.split_host_port(str(raw or ""), default_port)
        if host.strip("[]").lower() not in _LOOPBACK_HOSTS:
            raise ValueError("proxy host is not loopback")


def _proxy_health(
    _arguments: Mapping[str, Any],
    workdir: Path,
    _allow_root: Path,
    _cancel: threading.Event | None,
) -> ToolResult:
    agent = _contained_agent(workdir)
    if isinstance(agent, ToolResult):
        return agent
    # strict=True would fail a closed loopback.
    try:
        # health_report also dials chat_proxy. Check that hop too.
        _require_proxy_loopback(workdir, include_dial=True)
        report = proxy_health.health_report(workdir)
    except ValueError:
        return _refuse("proxy_probe_failed", "proxy probe failed")
    if not isinstance(report, dict):
        report = {"ok": False}
    return _ok("proxy_health", report)


def _proxy_stats(
    _arguments: Mapping[str, Any],
    workdir: Path,
    _allow_root: Path,
    _cancel: threading.Event | None,
) -> ToolResult:
    # cache_stats follows a symlink, then _conn commits the schema.
    cache = _resolve_within(
        workdir / ".agent" / "proxy_cache.sqlite",
        workdir,
        "proxy cache escapes the workdir",
    )
    if isinstance(cache, ToolResult):
        return cache
    try:
        # collect_stats dials pxpipe and the gateway, not chat_proxy.
        _require_proxy_loopback(workdir, include_dial=False)
        report = proxy_stats.collect_stats(workdir)
    except ValueError:
        return _refuse("proxy_probe_failed", "proxy probe failed")
    if not isinstance(report, dict):
        report = {}
    return _ok("proxy_stats", _bound_proxy(report))


def _integrations_status(
    _arguments: Mapping[str, Any],
    workdir: Path,
    _allow_root: Path,
    _cancel: threading.Event | None,
) -> ToolResult:
    agent = _contained_agent(workdir)
    if isinstance(agent, ToolResult):
        return agent
    cfg = load_project_cfg(workdir)
    provider = tracker_provider(cfg) or ""
    project_id = tracker_project_id(cfg)
    st: dict[str, Any] = {}
    if provider:
        st = load_state(agent, provider=provider, project_id=project_id)
    return _ok(
        "integrations_status",
        {
            "tracker_enabled": tracker_enabled(cfg),
            "slack_enabled": slack_enabled(cfg),
            "provider": provider or None,
            "project_id": project_id or None,
            "state_file": str(state_path(agent)),
            "mapped": len((st.get("items") or {})),
        },
    )


def _maxfail_ok(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return 1 <= value <= 50
    if isinstance(value, str) and value.isdigit():
        return 1 <= int(value) <= 50
    return False


def _pytest_flags(flags: object) -> list[str] | ToolResult:
    if flags is None:
        return []
    if not isinstance(flags, list):
        return _refuse("pytest_flag_rejected", "pytest flag rejected", exit_code=2)
    argv: list[str] = []
    index = 0
    while index < len(flags):
        token = flags[index]
        if not isinstance(token, str):
            return _refuse("pytest_flag_rejected", "pytest flag rejected", exit_code=2)
        if token in ("-q", "--collect-only"):
            argv.append(token)
            index += 1
            continue
        if token == "--maxfail":
            nxt = flags[index + 1] if index + 1 < len(flags) else None
            if not _maxfail_ok(nxt):
                return _refuse(
                    "pytest_flag_rejected", "pytest flag rejected", exit_code=2
                )
            argv.extend(["--maxfail", str(nxt)])
            index += 2
            continue
        if token.startswith("--maxfail="):
            if not _maxfail_ok(token.split("=", 1)[1]):
                return _refuse(
                    "pytest_flag_rejected", "pytest flag rejected", exit_code=2
                )
            argv.append(token)
            index += 1
            continue
        if token == "-k":
            nxt = flags[index + 1] if index + 1 < len(flags) else None
            if not isinstance(nxt, str) or _K_RE.fullmatch(nxt) is None:
                return _refuse(
                    "pytest_flag_rejected", "pytest flag rejected", exit_code=2
                )
            argv.extend(["-k", nxt])
            index += 2
            continue
        return _refuse("pytest_flag_rejected", "pytest flag rejected", exit_code=2)
    return argv


def _pytest_paths(raw_paths: object, workdir: Path) -> list[str] | ToolResult:
    if raw_paths is None:
        return []
    if not isinstance(raw_paths, list):
        return _refuse("path_escape", "pytest path escapes the workdir", exit_code=2)
    paths: list[str] = []
    for raw in raw_paths:
        if not isinstance(raw, str) or not raw:
            return _refuse(
                "path_escape", "pytest path escapes the workdir", exit_code=2
            )
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = workdir / candidate
        resolved = _resolve_within(
            candidate, workdir, "pytest path escapes the workdir"
        )
        if isinstance(resolved, ToolResult):
            return resolved
        paths.append(str(resolved))
    return paths


def _pytest_run(
    arguments: Mapping[str, Any],
    workdir: Path,
    _allow_root: Path,
    cancel: threading.Event | None,
) -> ToolResult:
    paths = _pytest_paths(arguments.get("paths"), workdir)
    if isinstance(paths, ToolResult):
        return paths
    flags = _pytest_flags(arguments.get("flags"))
    if isinstance(flags, ToolResult):
        return flags
    agent = _contained_agent(workdir)
    if isinstance(agent, ToolResult):
        return agent
    if _pipeline_lock_held(agent):
        return _refuse("pipeline_busy", "local pipeline lock is held", exit_code=2)
    venv_python = workdir / ".venv" / "bin" / "python"
    exe = str(venv_python) if venv_python.is_file() else sys.executable
    timeout = arguments.get("timeout_s", _PYTEST_TIMEOUT_DEFAULT)
    if isinstance(timeout, bool) or not isinstance(timeout, int):
        timeout = _PYTEST_TIMEOUT_DEFAULT
    if timeout < 1:
        timeout = 1
    if timeout > _PYTEST_TIMEOUT_MAX:
        timeout = _PYTEST_TIMEOUT_MAX
    argv = [exe, "-m", "pytest", *paths, *flags]
    ran = _spawn(
        argv,
        cwd=workdir,
        timeout_s=float(timeout),
        env=_path_env(),
        cancel=cancel,
    )
    return _from_spawn("pytest_run", ran)


def _docker_compose(
    arguments: Mapping[str, Any],
    workdir: Path,
    _allow_root: Path,
    cancel: threading.Event | None,
) -> ToolResult:
    action = arguments.get("action")
    if not isinstance(action, str) or action not in _DOCKER_OK:
        return _refuse(
            "docker_compose_rejected",
            "docker compose action is not allowed",
            exit_code=2,
        )
    if action == "logs":
        tail = arguments.get("tail", _DOCKER_TAIL_DEFAULT)
        if isinstance(tail, bool) or not isinstance(tail, int) or not 1 <= tail <= 200:
            return _refuse(
                "docker_compose_rejected",
                "docker compose tail is not allowed",
                exit_code=2,
            )
        argv = ["docker", "compose", "logs", "--tail", str(tail)]
    else:
        argv = ["docker", "compose", action]
    ran = _spawn(
        argv,
        cwd=workdir,
        timeout_s=_DOCKER_TIMEOUT_S,
        env=_compose_env(),
        cancel=cancel,
    )
    result = _from_spawn("docker_compose", ran)
    # logs, ps, and config are the only actions that reach spawn.
    # git_preflight and pytest_run stay on the exit-code success shape.
    if result.is_error:
        return result
    stdout = _redacted_stream(ran.stdout)
    if stdout is not None:
        result.structured["stdout"] = stdout
    stderr = _redacted_stream(ran.stderr)
    if stderr is not None:
        result.structured["stderr"] = stderr
    return result


_HANDLERS = {
    "git_preflight": _git_preflight,
    "validate_handoff": _validate_handoff,
    "state_snapshot": _state_snapshot,
    "state_append_delta": _state_append_delta,
    "state_metrics_log": _state_metrics_log,
    "memory_info": _memory_info,
    "memory_query": _memory_query,
    "memory_snapshot": _memory_snapshot,
    "memory_update": _memory_update,
    "knowledge_query": _knowledge_query,
    "knowledge_stats": _knowledge_stats,
    "playbooks_select": _playbooks_select,
    "playbooks_list": _playbooks_list,
    "proxy_health": _proxy_health,
    "proxy_stats": _proxy_stats,
    "integrations_status": _integrations_status,
    "pytest_run": _pytest_run,
    "docker_compose": _docker_compose,
}


def dispatch(
    name: str,
    arguments: Mapping[str, Any] | None = None,
    *,
    workdir: Path | None = None,
    allow_root: Path | None = None,
    cancel: threading.Event | None = None,
) -> ToolResult:
    """Run one catalog tool. ``workdir`` defaults to ``allow_root`` and the reverse."""
    if workdir is None and allow_root is None:
        raise TypeError("workdir or allow_root is required")
    if workdir is None:
        workdir = allow_root
    if allow_root is None:
        allow_root = workdir
    assert workdir is not None and allow_root is not None
    bound = _resolve_pair(Path(workdir), Path(allow_root))
    if isinstance(bound, ToolResult):
        return bound
    resolved, root = bound
    handler = _HANDLERS.get(name)
    if handler is None:
        return _refuse("not_implemented", f"{name} is not implemented")
    return handler(dict(arguments or {}), resolved, root, cancel)
