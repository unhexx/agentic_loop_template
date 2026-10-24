# -*- coding: utf-8 -*-
"""Dispatch act, a self-hosted listener, or Jenkins."""

from __future__ import annotations

import errno
import json
import os
import re
import secrets
import stat
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from memory.agent_lock import agent_lock
from memory.local_mcp.audit import (
    local_pipeline_cancelled,
    local_pipeline_probe_failed,
    local_pipeline_run,
)
from memory.local_mcp.backends import act as act_backend
from memory.local_mcp.backends import jenkins as jenkins_backend
from memory.local_mcp.backends import self_hosted as hosted_backend
from memory.local_mcp.config import disabled_result, load_local_pipeline
from memory.local_mcp.notify import notify_pipeline_result
from memory.local_mcp.probe import probe_act
from memory.local_mcp.redact import check_environment, clip_message, redact_text
from memory.local_mcp.stream import _bounded_tail
from memory.local_mcp.validate import result_errors
from memory.logutil import get_logger

log = get_logger("memory.local_mcp")

_DEFAULT_WORKFLOW = ".github/workflows/agentix-loop.yml"
_DEFAULT_JOB = "harness"
_ENV_KEY = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
_KEY_MSG = "environment variable key is not allowed"
_VALUE_MSG = "environment variable value is not allowed"
_PATH_MSG = "Choose a path inside the project."
_BUSY_MSG = "A run is in progress in this workdir."
# Host process control. These must not ride --env into the job either.
_HOST_EXACT = frozenset(
    {
        "PATH",
        "DOCKER_HOST",
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
        "PYTHONINSPECT",
        "PYTHONEXECUTABLE",
    }
)


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _logical_job(arguments: Mapping[str, Any], section: Mapping[str, Any]) -> dict[str, str]:
    act = section.get("act")
    act_map = act if isinstance(act, dict) else {}
    workflow = arguments.get("workflow_file") or act_map.get("workflow_file") or _DEFAULT_WORKFLOW
    job_name = arguments.get("job_name") or act_map.get("job_name") or _DEFAULT_JOB
    if not isinstance(workflow, str) or not workflow:
        workflow = _DEFAULT_WORKFLOW
    if not isinstance(job_name, str) or not job_name:
        job_name = _DEFAULT_JOB
    return {"workflow_file": workflow, "job_name": job_name}


def _timeout_s(
    arguments: Mapping[str, Any],
    section: Mapping[str, Any],
    backend_key: str = "act",
) -> int:
    # A present 0 must not fall through to config. Bool is an int subclass.
    if "timeout_s" in arguments:
        chosen = arguments["timeout_s"]
    else:
        block = section.get(backend_key)
        block_map = block if isinstance(block, dict) else {}
        chosen = block_map.get("timeout_s", 1800)
    if isinstance(chosen, bool) or not isinstance(chosen, int):
        chosen = 1800
    if chosen < 1:
        chosen = 1800
    return min(chosen, 7200)


def _resolve_workdir(raw: object, allow_root: Path) -> Path | None:
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        return None
    try:
        root = Path(allow_root).resolve()
        resolved = Path(raw).resolve()
    except (OSError, ValueError):
        return None
    if not _is_within(resolved, root):
        return None
    try:
        if not resolved.is_dir():
            return None
    except (OSError, ValueError):
        return None
    return resolved


def _is_real_dir(path: Path) -> bool:
    """True when lstat says directory. A symlink is not a directory."""
    try:
        st = path.lstat()
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and not stat.S_ISLNK(st.st_mode)


def _real_dirs_within(path: Path, root: Path) -> bool:
    """Every component from root to path is a real directory. Symlinks fail."""
    try:
        base = Path(root).resolve()
        relative = Path(path).relative_to(base)
    except (OSError, ValueError):
        return False
    if any(part in ("", ".", "..") for part in relative.parts):
        return False
    current = base
    if not _is_real_dir(current):
        return False
    for part in relative.parts:
        current = current / part
        if not _is_real_dir(current):
            return False
    return True


def _agent_blocks_load(workdir: Path, allow_root: Path) -> bool:
    """A missing .agent is not an escape. A symlink or non-directory is."""
    agent = workdir / ".agent"
    try:
        agent.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return not _real_dirs_within(agent, allow_root)


def _ensure_pipeline_dirs(workdir: Path, allow_root: Path) -> bool:
    """Require real .agent and local-pipeline-runs inside allow-root.

    Does not follow, replace, or unlink a symlink. Creates only a missing
    runs directory, and only after .agent is already a real directory.
    """
    agent = workdir / ".agent"
    if not _real_dirs_within(agent, allow_root):
        return False
    runs = agent / "local-pipeline-runs"
    try:
        runs.lstat()
    except FileNotFoundError:
        try:
            os.mkdir(runs, 0o755)
        except FileExistsError:
            pass
        except OSError:
            return False
    except OSError:
        return False
    return _real_dirs_within(runs, allow_root)


def _load(raw: object):
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        return None
    try:
        return load_local_pipeline(Path(raw))
    except (OSError, ValueError):
        return None


def _host_control_key(key: str) -> bool:
    if key in _HOST_EXACT:
        return True
    return key.startswith("LD_") or key.startswith("DYLD_")


def _checked_env(arguments: Mapping[str, Any]) -> tuple[dict[str, str] | None, tuple[str, str] | None]:
    raw = arguments.get("environment_variables")
    if raw is None:
        return {}, None
    if not isinstance(raw, Mapping):
        return None, ("secret_key_rejected", _KEY_MSG)
    rejection = check_environment(raw)
    if rejection is not None:
        return None, (rejection.code, rejection.message)
    extra: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or _ENV_KEY.fullmatch(key) is None or _host_control_key(key):
            return None, ("secret_key_rejected", _KEY_MSG)
        if not isinstance(value, str) or "\x00" in value:
            return None, ("secret_value_rejected", _VALUE_MSG)
        extra[key] = value
    return extra, None


def _result(
    *,
    config_backend: str,
    job: Mapping[str, str],
    exit_code: int,
    duration_ms: int,
    status: str,
    ok: bool,
    error: dict | None,
    log_path: str,
    probe: Mapping[str, Any],
    warnings: list,
    log_tail: str = "",
    timed_out: bool = False,
    cancelled: bool = False,
    truncated: bool = False,
    lines_dropped: int = 0,
    skipped: bool = False,
) -> dict:
    if duration_ms < 0:
        duration_ms = 0
    body = {
        "ok": ok,
        "skipped": skipped,
        "backend": "act",
        "config_backend": config_backend,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "timed_out": timed_out,
        "cancelled": cancelled,
        "truncated": truncated,
        "lines_dropped": lines_dropped if lines_dropped > 0 else 0,
        "queue_risk": False,
        "status": status,
        "job": {"workflow_file": job["workflow_file"], "job_name": job["job_name"]},
        "log_tail": _bounded_tail(log_tail),
        "log_path": log_path,
        "probe": dict(probe),
        "warnings": list(warnings)[:32],
        "status_target": "local",
        "status_posted_by": None,
        "scm": {"kind": None, "host": "local", "ref": None, "commit": None},
        "error": error,
    }
    problems = result_errors(body)
    if problems:
        raise RuntimeError("act result failed schema: " + "; ".join(problems))
    return body


def _refusal(
    *,
    config_backend: str,
    job: Mapping[str, str],
    code: str,
    message: str,
    log_name: str,
    probe: Mapping[str, Any] | None = None,
    warnings: list | None = None,
    duration_ms: int = 0,
) -> dict:
    return _result(
        config_backend=config_backend,
        job=job,
        exit_code=2,
        duration_ms=duration_ms,
        status="refused",
        ok=False,
        error={"code": code, "message": clip_message(message)},
        log_path=f".agent/local-pipeline-runs/{log_name}",
        probe=probe or {},
        warnings=warnings or [],
    )


def _info(result: Mapping[str, Any]) -> None:
    error = result.get("error")
    code = ""
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        code = error["code"]
    log.info(
        "backend=%s exit_code=%s duration_ms=%s error_code=%s",
        result.get("backend"),
        result.get("exit_code"),
        result.get("duration_ms"),
        code,
    )


def _audit(
    action: str,
    workdir: Path,
    result: Mapping[str, Any],
    job_name: object,
    job_path: object = None,
) -> None:
    # snapshot() and the audit file follow a .agent symlink. Skip both.
    if _real_dirs_within(workdir / ".agent", workdir):
        error = result.get("error")
        error_code = error.get("code") if isinstance(error, dict) else None
        kwargs = {
            "backend": result.get("backend"),
            "exit_code": result.get("exit_code"),
            "duration_ms": result.get("duration_ms"),
            "error_code": error_code,
            "job_name": job_name,
            "job_path": job_path,
        }
        if action == "cancelled":
            local_pipeline_cancelled(workdir, **kwargs)
        elif action == "probe":
            local_pipeline_probe_failed(workdir, **kwargs)
        else:
            local_pipeline_run(workdir, **kwargs)
    _info(result)


def _write_private(path: Path, data: bytes) -> None:
    parent = path.parent
    # mkdir(parents=True) follows a parent symlink. The runs dir already exists.
    if not _is_real_dir(parent) or not _is_real_dir(parent.parent):
        raise OSError(errno.ELOOP, "run directory is not a real directory", os.fspath(parent))
    flags = os.O_CREAT | os.O_TRUNC | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(str(path), flags, 0o600)
    except OSError:
        if not path.is_symlink():
            raise
        path.unlink()
        fd = os.open(str(path), flags, 0o600)
    try:
        os.write(fd, data)
        os.chmod(fd, 0o600)
    finally:
        os.close(fd)


def _prune(runs: Path) -> None:
    # O_NOFOLLOW applies to the last component only. A symlink .agent still
    # opens a real runs directory, so the parent has to be a real directory.
    if not _is_real_dir(runs.parent) or not _is_real_dir(runs):
        return
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        dirfd = os.open(os.fspath(runs), flags)
    except OSError:
        return
    try:
        names: list[str] = []
        for name in os.listdir(dirfd):
            if not name.endswith(".json") or "/" in name or name in (".", ".."):
                continue
            try:
                st = os.lstat(name, dir_fd=dirfd)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
                continue
            names.append(name)
        names.sort()
        # Room for the file this call is about to write.
        while len(names) >= 20:
            oldest = names.pop(0)
            try:
                os.unlink(oldest, dir_fd=dirfd)
            except OSError:
                continue
            sibling = oldest[: -len(".json")] + ".log"
            try:
                st = os.lstat(sibling, dir_fd=dirfd)
            except OSError:
                continue
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                continue
            try:
                os.unlink(sibling, dir_fd=dirfd)
            except OSError:
                pass
    finally:
        os.close(dirfd)


def _commit(workdir: Path, result: dict, rel_log: str) -> None:
    runs = workdir / ".agent" / "local-pipeline-runs"
    if not _real_dirs_within(workdir / ".agent", workdir) or not _real_dirs_within(runs, workdir):
        return
    log_path = workdir / rel_log
    if not log_path.exists() and not log_path.is_symlink():
        _write_private(log_path, b"")
    _prune(runs)
    rel_json = str(Path(rel_log).with_suffix(".json")).replace("\\", "/")
    _write_private(workdir / rel_json, json.dumps(result).encode("utf-8"))
    job = result.get("job")
    job_name = job.get("job_name") if isinstance(job, dict) else None
    job_path = job.get("job_path") if isinstance(job, dict) else None
    action = "cancelled" if result.get("cancelled") else "run"
    _audit(action, workdir, result, job_name, job_path)


def _run_locked(
    arguments: Mapping[str, Any],
    workdir: Path,
    cfg,
    *,
    allow_root: Path,
    progress: Callable[..., None] | None,
    protocol: str,
    cancel,
    environ: Mapping[str, str],
) -> dict:
    started = time.monotonic()
    job = _logical_job(arguments, cfg.section)
    if "\x00" in job["workflow_file"] or "\x00" in job["job_name"]:
        result = _refusal(
            config_backend=cfg.config_backend,
            job=job,
            code="path_escape",
            message=_PATH_MSG,
            log_name="path-escape.log",
            duration_ms=_elapsed_ms(started),
        )
        _commit(workdir, result, result["log_path"])
        return result
    extra, rejected = _checked_env(arguments)
    if rejected is not None or extra is None:
        code, message = rejected or ("secret_key_rejected", _KEY_MSG)
        result = _refusal(
            config_backend=cfg.config_backend,
            job=job,
            code=code,
            message=message,
            log_name="env-rejected-act.log",
            duration_ms=_elapsed_ms(started),
        )
        _commit(workdir, result, result["log_path"])
        return result
    probed = probe_act(workdir, environ=environ)
    problems = result_errors(probed)
    if problems:
        raise RuntimeError("probe result failed schema: " + "; ".join(problems))
    if not probed.get("ok"):
        job_name = None
        probed_job = probed.get("job")
        if isinstance(probed_job, dict):
            job_name = probed_job.get("job_name")
        _audit("probe", workdir, probed, job_name)
        return probed
    probe_body = probed.get("probe")
    if not isinstance(probe_body, dict):
        probe_body = {}
    run_id = secrets.token_hex(4)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    stem = f"{stamp}-act-{run_id}"
    rel_log = f".agent/local-pipeline-runs/{stem}.log"
    timeout_s = _timeout_s(arguments, cfg.section)
    outcome = act_backend.run(
        workdir,
        cfg.section,
        allow_root=allow_root,
        timeout_s=timeout_s,
        probe=probe_body,
        log_path=workdir / rel_log,
        run_id=run_id,
        workflow_file=job["workflow_file"],
        job_name=job["job_name"],
        extra_env=extra,
        progress=progress,
        protocol=protocol,
        cancel=cancel,
        environ=environ,
    )
    result = _result(
        config_backend=cfg.config_backend,
        job=job,
        exit_code=outcome.exit_code,
        duration_ms=_elapsed_ms(started),
        status=outcome.status,
        ok=outcome.ok,
        error=outcome.error,
        log_path=rel_log,
        probe=outcome.probe,
        warnings=outcome.warnings,
        log_tail=outcome.log_tail,
        timed_out=outcome.timed_out,
        cancelled=outcome.cancelled,
        truncated=outcome.truncated,
        lines_dropped=outcome.lines_dropped,
    )
    _commit(workdir, result, rel_log)
    return result


def _elapsed_ms(started: float) -> int:
    elapsed = int((time.monotonic() - started) * 1000)
    if elapsed < 0:
        return 0
    return elapsed


def _runner_job(arguments: Mapping[str, Any]) -> dict[str, list[str]]:
    raw = arguments.get("runner_labels")
    if not isinstance(raw, list):
        return {"runner_labels": []}
    labels = [item for item in raw if isinstance(item, str)]
    return {"runner_labels": labels}


def _clean_warnings(warnings: list) -> list[dict[str, str]]:
    cleaned: list[dict[str, str]] = []
    for item in list(warnings)[:32]:
        if not isinstance(item, dict):
            continue
        code = item.get("code")
        message = item.get("message")
        if not isinstance(code, str) or not code:
            continue
        if not isinstance(message, str):
            message = ""
        cleaned.append({"code": code[:64], "message": clip_message(message)})
    return cleaned


def _sh_result(
    *,
    config_backend: str,
    job: Mapping[str, Any],
    exit_code: int,
    duration_ms: int,
    status: str,
    error: dict | None,
    log_path: str,
    probe: Mapping[str, Any],
    warnings: list,
    log_tail: str = "",
    timed_out: bool = False,
    cancelled: bool = False,
    truncated: bool = False,
    lines_dropped: int = 0,
) -> dict:
    if duration_ms < 0:
        duration_ms = 0
    ok = exit_code == 0 and not timed_out and not cancelled and error is None
    if ok:
        status = "succeeded"
        error = None
    code = error.get("code") if isinstance(error, dict) else None
    labels = job.get("runner_labels")
    if not isinstance(labels, list):
        labels = []
    body = {
        "ok": ok,
        "skipped": False,
        "backend": "self_hosted",
        "config_backend": config_backend,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "timed_out": timed_out,
        "cancelled": cancelled,
        "truncated": truncated,
        "lines_dropped": lines_dropped if lines_dropped > 0 else 0,
        "queue_risk": code == "runner_offline",
        "status": status,
        "job": {"runner_labels": [item for item in labels if isinstance(item, str)]},
        "log_tail": _bounded_tail(redact_text(log_tail)),
        "log_path": log_path,
        "probe": dict(probe),
        "warnings": _clean_warnings(warnings),
        "status_target": "github_actions_ui",
        "status_posted_by": None,
        "scm": {"kind": None, "host": "github", "ref": None, "commit": None},
        "error": error,
    }
    problems = result_errors(body)
    if problems:
        raise RuntimeError("self-hosted result failed schema: " + "; ".join(problems))
    return body


def _sh_refusal(
    *,
    config_backend: str,
    job: Mapping[str, Any],
    code: str,
    message: str,
    log_name: str,
    duration_ms: int = 0,
    probe: Mapping[str, Any] | None = None,
    warnings: list | None = None,
) -> dict:
    return _sh_result(
        config_backend=config_backend,
        job=job,
        exit_code=2,
        duration_ms=duration_ms,
        status="refused",
        error={"code": code, "message": clip_message(message)},
        log_path=f".agent/local-pipeline-runs/{log_name}",
        probe=probe or {},
        warnings=warnings or [],
    )


def _run_self_hosted_locked(
    arguments: Mapping[str, Any],
    workdir: Path,
    cfg,
    *,
    progress: Callable[..., None] | None,
    protocol: str,
    cancel,
    environ: Mapping[str, str],
) -> dict:
    started = time.monotonic()
    job = _runner_job(arguments)
    # Checked and then dropped. These values must not reach run.sh.
    extra, rejected = _checked_env(arguments)
    if rejected is not None or extra is None:
        code, message = rejected or ("secret_key_rejected", _KEY_MSG)
        result = _sh_refusal(
            config_backend=cfg.config_backend,
            job=job,
            code=code,
            message=message,
            log_name="env-rejected-self-hosted.log",
            duration_ms=_elapsed_ms(started),
        )
        _commit(workdir, result, result["log_path"])
        return result
    del extra
    timeout_s = _timeout_s(arguments, cfg.section, "self_hosted")
    run_id = secrets.token_hex(4)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    stem = f"{stamp}-self-hosted-{run_id}"
    rel_log = f".agent/local-pipeline-runs/{stem}.log"
    outcome = hosted_backend.run(
        workdir,
        cfg.section,
        timeout_s=timeout_s,
        log_path=workdir / rel_log,
        requested_labels=job["runner_labels"],
        progress=progress,
        protocol=protocol,
        cancel=cancel,
        environ=environ,
        runs_dir=workdir / ".agent" / "local-pipeline-runs",
        stem=stem,
    )
    result = _sh_result(
        config_backend=cfg.config_backend,
        job=job,
        exit_code=outcome.exit_code,
        duration_ms=_elapsed_ms(started),
        status=outcome.status,
        error=outcome.error,
        log_path=rel_log,
        probe=outcome.probe,
        warnings=outcome.warnings,
        log_tail=outcome.log_tail,
        timed_out=outcome.timed_out,
        cancelled=outcome.cancelled,
        truncated=outcome.truncated,
        lines_dropped=outcome.lines_dropped,
    )
    _commit(workdir, result, rel_log)
    return result


def _dispatch_self_hosted(
    arguments: Mapping[str, Any],
    *,
    allow_root: Path,
    progress: Callable[..., None] | None,
    protocol: str,
    cancel,
    environ: Mapping[str, str] | None,
) -> dict:
    env = os.environ if environ is None else environ
    raw_dir = arguments.get("working_directory")
    job = _runner_job(arguments)
    # Confine before load. An outside, missing, or non-directory path must not
    # be opened, and its config_backend must not be copied. A .agent symlink
    # is the same escape: do not read, lock, or audit through it.
    workdir = _resolve_workdir(raw_dir, allow_root)
    if workdir is None or _agent_blocks_load(workdir, allow_root):
        return _sh_refusal(
            config_backend="act",
            job=job,
            code="path_escape",
            message=_PATH_MSG,
            log_name="path-escape.log",
        )
    cfg = _load(str(workdir))
    if cfg is None:
        return _sh_refusal(
            config_backend="act",
            job=job,
            code="path_escape",
            message=_PATH_MSG,
            log_name="path-escape.log",
        )
    if not cfg.enabled:
        return disabled_result(arguments, cfg.config_backend)
    if not _ensure_pipeline_dirs(workdir, allow_root):
        return _sh_refusal(
            config_backend=cfg.config_backend,
            job=job,
            code="path_escape",
            message=_PATH_MSG,
            log_name="path-escape.log",
        )
    started_body = False
    try:
        with agent_lock(workdir / ".agent", name="local_pipeline", timeout=0.2):
            started_body = True
            return _run_self_hosted_locked(
                arguments,
                workdir,
                cfg,
                progress=progress,
                protocol=protocol,
                cancel=cancel,
                environ=env,
            )
    except TimeoutError:
        if started_body:
            raise
        return _sh_refusal(
            config_backend=cfg.config_backend,
            job=job,
            code="pipeline_busy",
            message=_BUSY_MSG,
            log_name="pipeline-busy.log",
        )


def _scm_kind(section: Mapping[str, Any]) -> str | None:
    scm = section.get("scm")
    if not isinstance(scm, dict):
        return None
    kind = scm.get("kind")
    if kind in ("server", "cloud"):
        return kind
    return None


def _jenkins_job(arguments: Mapping[str, Any]) -> dict[str, str]:
    has_job = "job_path" in arguments
    has_file = "jenkinsfile_path" in arguments
    if has_job and not has_file:
        value = arguments.get("job_path")
        if isinstance(value, str):
            return {"job_path": value}
        return {"job_path": ""}
    if has_file and not has_job:
        value = arguments.get("jenkinsfile_path")
        if isinstance(value, str):
            return {"jenkinsfile_path": value}
        return {"jenkinsfile_path": ""}
    # An empty path is not a job name, so it cannot be posted.
    return {"job_path": ""}


def _jk_result(
    *,
    config_backend: str,
    section: Mapping[str, Any],
    job: Mapping[str, str],
    exit_code: int,
    duration_ms: int,
    status: str,
    error: dict | None,
    log_path: str,
    probe: Mapping[str, Any],
    warnings: list,
    log_tail: str = "",
    timed_out: bool = False,
    cancelled: bool = False,
    truncated: bool = False,
    lines_dropped: int = 0,
    posted_by: str | None = None,
    scm_commit: str | None = None,
    scm_ref: str | None = None,
) -> dict:
    if duration_ms < 0:
        duration_ms = 0
    ok = exit_code == 0 and not timed_out and not cancelled and error is None
    if ok:
        status = "succeeded"
        error = None
    elif error is None:
        error = {"code": "job_failed", "message": clip_message("Jenkins job failed")}
    if "jenkinsfile_path" in job and "job_path" not in job:
        job_out: dict[str, str] = {"jenkinsfile_path": job["jenkinsfile_path"]}
    elif "job_path" in job and "jenkinsfile_path" not in job:
        job_out = {"job_path": job["job_path"]}
    else:
        raise RuntimeError("jenkins result needs exactly one path")
    body = {
        "ok": ok,
        "skipped": False,
        "backend": "jenkins",
        "config_backend": config_backend,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "timed_out": timed_out,
        "cancelled": cancelled,
        "truncated": truncated,
        "lines_dropped": lines_dropped if lines_dropped > 0 else 0,
        "queue_risk": False,
        "status": status,
        "job": job_out,
        "log_tail": _bounded_tail(redact_text(log_tail)),
        "log_path": log_path,
        "probe": dict(probe),
        "warnings": _clean_warnings(warnings),
        "status_target": "bitbucket_commit_status",
        "status_posted_by": posted_by if posted_by in ("jenkins", "harness") else None,
        "scm": {
            "kind": _scm_kind(section),
            "host": "bitbucket",
            "ref": scm_ref
            if isinstance(scm_ref, str) and scm_ref.startswith("refs/") and "\n" not in scm_ref
            else None,
            "commit": scm_commit
            if isinstance(scm_commit, str) and re.fullmatch(r"[0-9a-f]{7,40}", scm_commit)
            else None,
        },
        "error": error,
    }
    problems = result_errors(body)
    if problems:
        raise RuntimeError("jenkins result failed schema: " + "; ".join(problems))
    return body


def _jk_refusal(
    *,
    config_backend: str,
    section: Mapping[str, Any],
    job: Mapping[str, str],
    code: str,
    message: str,
    log_name: str,
    duration_ms: int = 0,
    probe: Mapping[str, Any] | None = None,
) -> dict:
    return _jk_result(
        config_backend=config_backend,
        section=section,
        job=job,
        exit_code=2,
        duration_ms=duration_ms,
        status="refused",
        error={"code": code, "message": clip_message(message)},
        log_path=f".agent/local-pipeline-runs/{log_name}",
        probe=probe or {},
        warnings=[],
    )


def _run_jenkins_locked(
    arguments: Mapping[str, Any],
    workdir: Path,
    cfg,
    *,
    progress: Callable[..., None] | None,
    protocol: str,
    cancel,
    environ: Mapping[str, str],
) -> dict:
    started = time.monotonic()
    job = _jenkins_job(arguments)
    extra, rejected = _checked_env(arguments)
    if rejected is not None or extra is None:
        code, message = rejected or ("secret_key_rejected", _KEY_MSG)
        result = _jk_refusal(
            config_backend=cfg.config_backend,
            section=cfg.section,
            job=job,
            code=code,
            message=message,
            log_name="env-rejected-jenkins.log",
            duration_ms=_elapsed_ms(started),
        )
        _commit(workdir, result, result["log_path"])
        return result
    timeout_s = _timeout_s(arguments, cfg.section, "jenkins")
    run_id = secrets.token_hex(4)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    stem = f"{stamp}-jenkins-{run_id}"
    rel_log = f".agent/local-pipeline-runs/{stem}.log"
    outcome = jenkins_backend.run(
        workdir,
        cfg.section,
        timeout_s=timeout_s,
        log_path=workdir / rel_log,
        job=job,
        extra_env=extra,
        progress=progress,
        protocol=protocol,
        cancel=cancel,
        environ=environ,
    )
    if outcome.probe_failure is not None:
        probed = outcome.probe_failure
        problems = result_errors(probed)
        if problems:
            raise RuntimeError("probe result failed schema: " + "; ".join(problems))
        probed_job = probed.get("job") if isinstance(probed.get("job"), dict) else {}
        _audit(
            "probe",
            workdir,
            probed,
            probed_job.get("job_name"),
            probed_job.get("job_path"),
        )
        return probed
    result = _jk_result(
        config_backend=cfg.config_backend,
        section=cfg.section,
        job=job,
        exit_code=outcome.exit_code,
        duration_ms=_elapsed_ms(started),
        status=outcome.status,
        error=outcome.error,
        log_path=rel_log,
        probe=outcome.probe,
        warnings=outcome.warnings,
        log_tail=outcome.log_tail,
        timed_out=outcome.timed_out,
        cancelled=outcome.cancelled,
        truncated=outcome.truncated,
        lines_dropped=outcome.lines_dropped,
        posted_by=outcome.posted_by,
        scm_commit=outcome.scm_commit,
        scm_ref=outcome.scm_ref,
    )
    _commit(workdir, result, rel_log)
    return result


def _dispatch_jenkins(
    arguments: Mapping[str, Any],
    *,
    allow_root: Path,
    progress: Callable[..., None] | None,
    protocol: str,
    cancel,
    environ: Mapping[str, str] | None,
) -> dict:
    env = os.environ if environ is None else environ
    raw_dir = arguments.get("working_directory")
    job = _jenkins_job(arguments)
    # Confine before load. An outside, missing, or non-directory path must not
    # be opened, and its config_backend must not be copied. A .agent symlink
    # is the same escape: do not read, lock, or audit through it.
    workdir = _resolve_workdir(raw_dir, allow_root)
    if workdir is None or _agent_blocks_load(workdir, allow_root):
        return _jk_refusal(
            config_backend="act",
            section={},
            job=job,
            code="path_escape",
            message=_PATH_MSG,
            log_name="path-escape.log",
        )
    cfg = _load(str(workdir))
    if cfg is None:
        return _jk_refusal(
            config_backend="act",
            section={},
            job=job,
            code="path_escape",
            message=_PATH_MSG,
            log_name="path-escape.log",
        )
    if not cfg.enabled:
        return disabled_result(arguments, cfg.config_backend)
    section: Mapping[str, Any] = cfg.section
    if not _ensure_pipeline_dirs(workdir, allow_root):
        return _jk_refusal(
            config_backend=cfg.config_backend,
            section=section,
            job=job,
            code="path_escape",
            message=_PATH_MSG,
            log_name="path-escape.log",
        )
    started_body = False
    try:
        with agent_lock(workdir / ".agent", name="local_pipeline", timeout=0.2):
            started_body = True
            return _run_jenkins_locked(
                arguments,
                workdir,
                cfg,
                progress=progress,
                protocol=protocol,
                cancel=cancel,
                environ=env,
            )
    except TimeoutError:
        if started_body:
            raise
        return _jk_refusal(
            config_backend=cfg.config_backend,
            section=section,
            job=job,
            code="pipeline_busy",
            message=_BUSY_MSG,
            log_name="pipeline-busy.log",
        )


def _dispatch_act(
    arguments: Mapping[str, Any],
    *,
    allow_root: Path,
    progress: Callable[..., None] | None,
    protocol: str,
    cancel,
    environ: Mapping[str, str] | None,
) -> dict:
    env = os.environ if environ is None else environ
    raw_dir = arguments.get("working_directory")
    # Confine before load. An outside, missing, or non-directory path must not
    # be opened, and its config_backend must not be copied. A .agent symlink
    # is the same escape: do not read, lock, or audit through it.
    workdir = _resolve_workdir(raw_dir, allow_root)
    if workdir is None or _agent_blocks_load(workdir, allow_root):
        return _refusal(
            config_backend="act",
            job=_logical_job(arguments, {}),
            code="path_escape",
            message=_PATH_MSG,
            log_name="path-escape.log",
        )
    cfg = _load(str(workdir))
    if cfg is None:
        return _refusal(
            config_backend="act",
            job=_logical_job(arguments, {}),
            code="path_escape",
            message=_PATH_MSG,
            log_name="path-escape.log",
        )
    if not cfg.enabled:
        return disabled_result(arguments, cfg.config_backend)
    if not _ensure_pipeline_dirs(workdir, allow_root):
        return _refusal(
            config_backend=cfg.config_backend,
            job=_logical_job(arguments, cfg.section),
            code="path_escape",
            message=_PATH_MSG,
            log_name="path-escape.log",
        )
    # Lock TimeoutError means the lock was not taken. A body TimeoutError must surface.
    started_body = False
    try:
        with agent_lock(workdir / ".agent", name="local_pipeline", timeout=0.2):
            started_body = True
            return _run_locked(
                arguments,
                workdir,
                cfg,
                allow_root=Path(allow_root),
                progress=progress,
                protocol=protocol,
                cancel=cancel,
                environ=env,
            )
    except TimeoutError:
        if started_body:
            raise
        return _refusal(
            config_backend=cfg.config_backend,
            job=_logical_job(arguments, cfg.section),
            code="pipeline_busy",
            message=_BUSY_MSG,
            log_name="pipeline-busy.log",
        )


def run_local_pipeline(
    arguments: Mapping[str, Any],
    *,
    allow_root: Path,
    progress: Callable[..., None] | None = None,
    protocol: str = "2025-11-25",
    cancel=None,
    environ: Mapping[str, str] | None = None,
) -> dict:
    """Act, a self-hosted listener, or Jenkins. Disabled and busy do not spawn."""
    if arguments.get("backend") == "jenkins":
        result = _dispatch_jenkins(
            arguments,
            allow_root=allow_root,
            progress=progress,
            protocol=protocol,
            cancel=cancel,
            environ=environ,
        )
    elif arguments.get("backend") == "self_hosted":
        result = _dispatch_self_hosted(
            arguments,
            allow_root=allow_root,
            progress=progress,
            protocol=protocol,
            cancel=cancel,
            environ=environ,
        )
    else:
        result = _dispatch_act(
            arguments,
            allow_root=allow_root,
            progress=progress,
            protocol=protocol,
            cancel=cancel,
            environ=environ,
        )
    # Slack failure must not replace this result or its exit code.
    notify_pipeline_result(arguments, result, allow_root=allow_root)
    return result
