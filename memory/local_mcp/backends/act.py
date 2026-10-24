# -*- coding: utf-8 -*-
"""Run one workflow job with nektos/act. No GitHub dispatch and no pull by default."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from memory.local_mcp import stream as stream_mod
from memory.local_mcp.redact import clip_message
from memory.local_mcp.server import kill_process_group
from memory.logutil import get_logger

log = get_logger("memory.local_mcp")

DEFAULT_RUNNER_IMAGE = "catthehacker/ubuntu:act-22.04"
_HELP_TIMEOUT_S = 5.0
_CID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,128}$")
_RUN_ID_RE = re.compile(r"^[0-9a-f]{8}$")
_USES_RE = re.compile(r"uses:\s*['\"]?([^'\"#\s]+)")
_WORKFLOW_CAP = 1024 * 1024

_LEGACY_MSG = "Upgrade act, or set the legacy acknowledgement knowingly."
_DIND_MSG = "Leave DinD off. This workflow does not need it."
_STRICT_MSG = "Split the workflow or turn strict off and read warnings."
_OVERSIZE_MSG = "Workflow exceeds 1 MiB. Split it so the whole file is scanned."
_PATH_MSG = "Choose a path inside the project."
_WARN_MSG = "workflow uses a composite or reusable workflow"
_JOB_MSG = "act job failed"
_TIMEOUT_MSG = "act exceeded timeout_s"
_CANCEL_MSG = "run cancelled"
_MISSING_ACT = "Install nektos/act. Do not fall back to gh workflow run."
_HELP_FAIL_MSG = "act --help failed before the job started."
_DOCKER_TIMEOUT_MSG = "docker image inspect timed out."
_DOCKER_UNREACHABLE_MSG = "Install and start the Docker daemon. act cannot run."
_IMAGE_ABSENT_RE = re.compile(r"no such image|no such object", re.IGNORECASE)


@dataclass
class ActOutcome:
    exit_code: int
    status: str
    ok: bool
    timed_out: bool = False
    cancelled: bool = False
    truncated: bool = False
    lines_dropped: int = 0
    log_tail: str = ""
    warnings: list = field(default_factory=list)
    error: dict | None = None
    probe: dict = field(default_factory=dict)


def _flag(section: Mapping[str, Any], key: str) -> bool:
    act = section.get("act")
    if not isinstance(act, dict):
        return False
    return act.get(key) is True


def _act_map(section: Mapping[str, Any]) -> Mapping[str, Any]:
    act = section.get("act")
    if isinstance(act, dict):
        return act
    return {}


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _err(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": clip_message(message)}


def _refused(
    code: str,
    message: str,
    probe: Mapping[str, Any],
    *,
    warnings: list | None = None,
) -> ActOutcome:
    return ActOutcome(
        exit_code=2,
        status="refused",
        ok=False,
        warnings=list(warnings or []),
        error=_err(code, message),
        probe=dict(probe),
    )


def _path_env(environ: Mapping[str, str]) -> dict[str, str]:
    env: dict[str, str] = {}
    for key in ("PATH", "DOCKER_HOST"):
        value = environ.get(key)
        if isinstance(value, str):
            env[key] = value
    return env


def _drain(proc: subprocess.Popen) -> None:
    try:
        proc.communicate(timeout=1)
    except (subprocess.TimeoutExpired, OSError):
        pass


def _capture(
    argv: Sequence[str],
    env: Mapping[str, str],
    timeout_s: float,
    cancel: threading.Event | None = None,
) -> tuple[int, str]:
    """Run a short helper. 130 when cancel is set. 124 on timeout. ValueError is a tool code."""
    if cancel is not None and cancel.is_set():
        return 130, ""
    try:
        proc = subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(env),
            shell=False,
            **stream_mod._popen_group_kwargs(),
        )
    except FileNotFoundError:
        return 127, ""
    except (OSError, ValueError):
        return 1, ""
    deadline = time.monotonic() + timeout_s
    while True:
        if cancel is not None and cancel.is_set():
            kill_process_group(proc)
            _drain(proc)
            return 130, ""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            kill_process_group(proc)
            _drain(proc)
            return 124, ""
        try:
            out, err = proc.communicate(timeout=min(0.05, remaining))
        except subprocess.TimeoutExpired:
            continue
        except OSError:
            kill_process_group(proc)
            return 1, ""
        if cancel is not None and cancel.is_set():
            return 130, ""
        text = (out or b"").decode("utf-8", errors="replace")
        text += "\n"
        text += (err or b"").decode("utf-8", errors="replace")
        code = proc.returncode if proc.returncode is not None else 1
        return code, text


def _regular_under(raw: str, root: Path, base: Path) -> Path | None:
    if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = base / path
    try:
        resolved = path.resolve()
        root_resolved = root.resolve()
    except (OSError, ValueError):
        return None
    if not _is_within(resolved, root_resolved):
        return None
    try:
        mode = resolved.stat().st_mode
    except OSError:
        return None
    if not stat.S_ISREG(mode):
        return None
    return resolved


def _risky_workflow(text: str) -> bool:
    for line in text.splitlines():
        if "workflow_call" in line:
            return True
        match = _USES_RE.search(line)
        if match is None:
            continue
        target = match.group(1)
        if target.startswith("./") or ".github/workflows/" in target:
            return True
    return False


def _read_workflow(path: Path) -> tuple[str | None, bool]:
    """Text kept for the scan, and whether a byte past the cap was left unread."""
    try:
        with path.open("rb") as handle:
            data = handle.read(_WORKFLOW_CAP + 1)
    except OSError:
        return None, False
    oversized = len(data) > _WORKFLOW_CAP
    return data[:_WORKFLOW_CAP].decode("utf-8", errors="replace"), oversized


def _touch(path: Path) -> None:
    fd = os.open(str(path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    os.close(fd)
    os.chmod(path, 0o600)


def _secret_path(
    environ: Mapping[str, str],
    section: Mapping[str, Any],
    workdir: Path,
    allow_root: Path,
    scratch: Path,
) -> Path:
    blank = scratch / "empty.secrets"
    _touch(blank)
    if "ACT_SECRETS_FILE" in environ:
        found = _regular_under(str(environ.get("ACT_SECRETS_FILE") or ""), allow_root, workdir)
        return found if found is not None else blank
    configured = _act_map(section).get("secret_file", ".secrets")
    if not isinstance(configured, str) or not configured:
        configured = ".secrets"
    found = _regular_under(configured, allow_root, workdir)
    return found if found is not None else blank


def _child_env(environ: Mapping[str, str], scratch: Path) -> dict[str, str]:
    """Host env for act. Caller variables are --env only, never this mapping."""
    child: dict[str, str] = {}
    for key in ("PATH", "LANG", "USER", "LOGNAME"):
        value = environ.get(key)
        if isinstance(value, str):
            child[key] = value
    docker_host = environ.get("DOCKER_HOST")
    if isinstance(docker_host, str):
        child["DOCKER_HOST"] = docker_host
    # Scratch wins so a caller cannot point actrc back at the operator home.
    child["HOME"] = str(scratch)
    child["XDG_CONFIG_HOME"] = str(scratch)
    return child


def _docker_env(environ: Mapping[str, str], scratch: Path) -> dict[str, str]:
    """Same daemon context as the act child: PATH, DOCKER_HOST, and the scratch home."""
    env = _path_env(environ)
    env["HOME"] = str(scratch)
    env["XDG_CONFIG_HOME"] = str(scratch)
    return env


def _cancelled(probe: Mapping[str, Any], warnings: list | None = None) -> ActOutcome:
    return ActOutcome(
        exit_code=130,
        status="cancelled",
        ok=False,
        cancelled=True,
        warnings=list(warnings or []),
        error=_err("cancelled", _CANCEL_MSG),
        probe=dict(probe),
    )


def _from_stream(
    streamed: stream_mod.StreamResult,
    *,
    exit_code: int,
    status: str,
    ok: bool,
    error: dict | None,
    warnings: list,
    probe: Mapping[str, Any],
    timed_out: bool = False,
    cancelled: bool = False,
) -> ActOutcome:
    return ActOutcome(
        exit_code=exit_code,
        status=status,
        ok=ok,
        timed_out=timed_out,
        cancelled=cancelled,
        truncated=streamed.truncated,
        lines_dropped=streamed.lines_dropped,
        log_tail=streamed.log_tail,
        warnings=warnings,
        error=error,
        probe=dict(probe),
    )


def _image_name(probe: Mapping[str, Any]) -> str:
    image = probe.get("runner_image")
    if isinstance(image, str) and image:
        return image
    return DEFAULT_RUNNER_IMAGE


def _cleanup_containers(run_id: str, env: Mapping[str, str]) -> None:
    if _RUN_ID_RE.fullmatch(run_id) is None:
        return
    code, text = _capture(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label=agentix.local_mcp.run={run_id}",
        ],
        env,
        _HELP_TIMEOUT_S,
    )
    if code != 0:
        return
    ids = [line.strip() for line in text.splitlines() if _CID_RE.fullmatch(line.strip())]
    if not ids:
        return
    _capture(["docker", "rm", "-f", *ids], env, 10.0)


def _argv(
    *,
    workdir: Path,
    workflow: Path,
    job_name: str,
    image: str,
    pull: bool,
    cache_server: bool,
    bind: bool,
    socket_dash: bool,
    secret: Path,
    scratch: Path,
    run_id: str,
    extra: Mapping[str, str],
) -> list[str]:
    argv = [
        "act",
        "push",
        "-C",
        str(workdir),
        "-W",
        str(workflow),
        "-j",
        job_name,
        "-P",
        f"ubuntu-latest={image}",
    ]
    if socket_dash:
        argv.extend(["--container-daemon-socket", "-"])
    argv.append("--pull=true" if pull else "--pull=false")
    argv.extend(["--network", "bridge"])
    if not cache_server:
        argv.append("--no-cache-server")
    if bind:
        argv.append("--bind")
    argv.extend(
        [
            "--secret-file",
            str(secret),
            "--env-file",
            str(scratch / "empty.env"),
            "--var-file",
            str(scratch / "empty.vars"),
            "--input-file",
            str(scratch / "empty.input"),
            f"--container-options=--label=agentix.local_mcp.run={run_id}",
        ]
    )
    for key, value in extra.items():
        argv.append(f"--env={key}={value}")
    return argv


def run(
    workdir: Path,
    section: Mapping[str, Any],
    *,
    allow_root: Path,
    timeout_s: int,
    probe: Mapping[str, Any],
    log_path: Path,
    run_id: str,
    workflow_file: str,
    job_name: str,
    extra_env: Mapping[str, str],
    progress: Callable[..., None] | None,
    protocol: str,
    cancel: threading.Event | None,
    environ: Mapping[str, str],
) -> ActOutcome:
    """Probe already succeeded. Refuse unsafe workflows before ``act push``."""
    probe_body = dict(probe)
    if cancel is not None and cancel.is_set():
        return _cancelled(probe_body)
    image = _image_name(probe_body)
    workflow = _regular_under(workflow_file, workdir, workdir)
    if workflow is None:
        return _refused("path_escape", _PATH_MSG, probe_body)
    text, oversized = _read_workflow(workflow)
    if text is None:
        return _refused("path_escape", _PATH_MSG, probe_body)
    warnings: list[dict[str, str]] = []
    if oversized:
        return _refused(
            "unsupported_workflow_feature",
            _OVERSIZE_MSG,
            probe_body,
        )
    if _risky_workflow(text):
        warnings.append({"code": "reusable_or_composite", "message": _WARN_MSG})
        if _flag(section, "strict_workflows"):
            return _refused(
                "unsupported_workflow_feature",
                _STRICT_MSG,
                probe_body,
                warnings=warnings,
            )
    if cancel is not None and cancel.is_set():
        return _cancelled(probe_body, warnings)
    path_env = _path_env(environ)
    help_code, help_text = _capture(["act", "--help"], path_env, _HELP_TIMEOUT_S, cancel)
    if help_code == 130:
        return _cancelled(probe_body, warnings)
    if help_code != 0:
        return _refused("act_help_failed", _HELP_FAIL_MSG, probe_body, warnings=warnings)
    has_socket_flag = "--container-daemon-socket" in help_text
    if not has_socket_flag and not _flag(section, "allow_legacy_socket_mount"):
        return _refused("act_legacy_mounts_socket", _LEGACY_MSG, probe_body, warnings=warnings)
    mount = _flag(section, "mount_docker_sock")
    acknowledged = _flag(section, "acknowledge_dind")
    if mount and not acknowledged:
        return _refused("dind_not_acknowledged", _DIND_MSG, probe_body, warnings=warnings)
    socket_dash = has_socket_flag and not (mount and acknowledged)
    pull = _flag(section, "pull")
    # docker image inspect --help and -h exit 0. An empty reference or a
    # leading dash is not an image, so do not start act.
    if not pull and (not image or image.startswith("-")):
        return _refused(
            "act_image_missing",
            f"Image {image} is not present locally. docker pull the configured image, or set act.pull true.",
            probe_body,
            warnings=warnings,
        )
    scratch: Path | None = None
    docker_env: dict[str, str] | None = None
    try:
        scratch = Path(tempfile.mkdtemp(prefix="agentix-act-"))
        os.chmod(scratch, 0o700)
        docker_env = _docker_env(environ, scratch)
        if not pull:
            code, output = _capture(
                ["docker", "image", "inspect", "--", image],
                docker_env,
                _HELP_TIMEOUT_S,
                cancel,
            )
            if code == 130:
                return _cancelled(probe_body, warnings)
            if code == 124:
                return _refused(
                    "docker_unreachable",
                    _DOCKER_TIMEOUT_MSG,
                    probe_body,
                    warnings=warnings,
                )
            if code != 0:
                # Exit 1 is both a missing tag and a dead socket. Only the
                # daemon's absence text is a pull hint. A connect failure is not.
                if _IMAGE_ABSENT_RE.search(output):
                    return _refused(
                        "act_image_missing",
                        f"Image {image} is not present locally. docker pull the configured image, or set act.pull true.",
                        probe_body,
                        warnings=warnings,
                    )
                return _refused(
                    "docker_unreachable",
                    _DOCKER_UNREACHABLE_MSG,
                    probe_body,
                    warnings=warnings,
                )
        for name in ("empty.env", "empty.vars", "empty.input"):
            _touch(scratch / name)
        if cancel is not None and cancel.is_set():
            return _cancelled(probe_body, warnings)
        secret = _secret_path(environ, section, workdir, allow_root, scratch)
        argv = _argv(
            workdir=workdir,
            workflow=workflow,
            job_name=job_name,
            image=image,
            pull=pull,
            cache_server=_flag(section, "cache_server"),
            bind=_flag(section, "bind_workdir"),
            socket_dash=socket_dash,
            secret=secret,
            scratch=scratch,
            run_id=run_id,
            extra=extra_env,
        )
        child = _child_env(environ, scratch)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        streamed = stream_mod.stream_cli(
            argv,
            cwd=scratch,
            env=child,
            log_path=log_path,
            timeout_s=timeout_s,
            cancel=cancel,
            progress=progress,
            protocol=protocol,
        )
    finally:
        if docker_env is not None:
            try:
                _cleanup_containers(run_id, docker_env)
            except Exception:
                log.warning("container cleanup failed")
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)
    if streamed.timed_out:
        return _from_stream(
            streamed,
            exit_code=124,
            status="timed_out",
            ok=False,
            error=_err("timed_out", _TIMEOUT_MSG),
            warnings=warnings,
            probe=probe_body,
            timed_out=True,
        )
    if streamed.cancelled:
        return _from_stream(
            streamed,
            exit_code=130,
            status="cancelled",
            ok=False,
            error=_err("cancelled", _CANCEL_MSG),
            warnings=warnings,
            probe=probe_body,
            cancelled=True,
        )
    if streamed.returncode == 0:
        return _from_stream(
            streamed,
            exit_code=0,
            status="succeeded",
            ok=True,
            error=None,
            warnings=warnings,
            probe=probe_body,
        )
    if streamed.missing:
        return _refused("act_missing", _MISSING_ACT, probe_body, warnings=warnings)
    # OSError on spawn is returncode 1 with an empty tail and no timeout.
    # A real job failure also uses returncode 1, so only the missing flag is special.
    return _from_stream(
        streamed,
        exit_code=1,
        status="failed",
        ok=False,
        error=_err("job_failed", _JOB_MSG),
        warnings=warnings,
        probe=probe_body,
    )
