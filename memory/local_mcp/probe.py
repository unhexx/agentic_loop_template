# -*- coding: utf-8 -*-
"""Read-only reachability checks. No act run, no run.sh, no Jenkins POST."""

from __future__ import annotations

import base64
import http.client
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from urllib.parse import urljoin, urlsplit, urlunsplit

from memory.local_mcp.config import (
    _DEFAULT_JOB,
    _DEFAULT_WORKFLOW,
    _SCM_HOST,
    _STATUS_TARGET,
    load_local_pipeline,
)
from memory.local_mcp.server import kill_process_group

# CLI default. Callers (tests) may pass a shorter timeout_s.
PROBE_TIMEOUT_S = 5.0


class _BudgetExpired(Exception):
    """The runner probe spent its deadline. Do not start the next step."""

_DEFAULT_RUNNER_IMAGE = "catthehacker/ubuntu:act-22.04"
_LISTENER_NAMES = frozenset({"Runner.Listener", "run.sh", "runsvc.sh"})
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
# Startup line is early; do not slurp a multi-gig _diag file.
_LOG_READ_MAX = 8 * 1024 * 1024
# Non-200 bodies only. A 200 is read to EOF so a cut prefix is never parsed.
_HTTP_READ_MAX = 1024 * 1024
_HTTP_CHUNK = 64 * 1024
# Copied only when set. Not a prefix match and not the rest of the parent env.
_DOCKER_SOCKET_KEYS = (
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_CONFIG",
    "DOCKER_CERT_PATH",
)

_MESSAGES = {
    "docker_unreachable": "Install and start the Docker daemon. act cannot run.",
    "act_missing": "Install nektos/act. Do not fall back to gh workflow run.",
    "act_version_unreadable": "Binary is not act, or it crashed.",
    "act_requires_linux": (
        "Run act on Linux. Jenkins HTTP from Windows is still allowed."
    ),
    "runner_offline": (
        "Runner is down. GitHub's queue can stall. Register and start it by hand. "
        "This tool will not enroll it."
    ),
    "runner_log_missing": "Runner dir is wrong, or the listener has never started.",
    "runner_log_stale": (
        "Listener may be stuck. Read the log. Do not mint a new token."
    ),
    "runner_label_mismatch": (
        "Fix runner_labels or the labels recorded in config from the GitHub UI."
    ),
    "runner_label_unknown": (
        "Copy the runner's labels into config. The log is not parsed for them."
    ),
    "runner_not_registered": (
        "Human runs config.sh with a token from the GitHub UI."
    ),
    "jenkins_unreachable": "Controller down, or URL/DNS/firewall.",
    "jenkins_auth": "Fix JENKINS_USER / JENKINS_API_TOKEN in the environment.",
    "jenkins_redirect_rejected": "Pin jenkins.url to the controller origin.",
}


class _Ran:
    __slots__ = ("missing", "timed_out", "code", "stdout")

    def __init__(
        self,
        *,
        missing: bool = False,
        timed_out: bool = False,
        code: int = 1,
        stdout: str = "",
    ) -> None:
        self.missing = missing
        self.timed_out = timed_out
        self.code = code
        self.stdout = stdout


class _NoRedirect(urllib.request.HTTPErrorProcessor):
    """Return 3xx and 4xx. The default urlopen follows Location."""

    def http_response(self, request, response):
        del request
        return response

    https_response = http_response


def probe_backend(
    backend: str,
    workdir: Path,
    *,
    timeout_s: float = PROBE_TIMEOUT_S,
    environ: Optional[Mapping[str, str]] = None,
    processes: Optional[Sequence[Any]] = None,
    requested_labels: Sequence[str] = (),
    now: Optional[float] = None,
) -> dict:
    """One backend. ``processes is None`` lists Linux /proc (CLI only)."""
    if backend == "act":
        return probe_act(workdir, timeout_s=timeout_s, environ=environ)
    if backend == "self_hosted":
        # One budget for the /proc walk and the filesystem probe. A caller
        # that shortens timeout_s must also shorten systemctl.
        deadline = time.monotonic() + float(timeout_s)
        try:
            listed = (
                list_linux_processes(deadline=deadline)
                if processes is None
                else processes
            )
        except _BudgetExpired:
            remaining = 0.0
            listed = () if processes is None else processes
        else:
            remaining = max(0.0, deadline - time.monotonic())
        return probe_self_hosted(
            workdir,
            listed,
            requested_labels=requested_labels,
            now=now,
            environ=environ,
            timeout_s=remaining,
        )
    if backend == "jenkins":
        return probe_jenkins(workdir, timeout_s=timeout_s, environ=environ)
    raise ValueError("unknown backend")


def probe_act(
    workdir: Path,
    *,
    timeout_s: float = PROBE_TIMEOUT_S,
    environ: Optional[Mapping[str, str]] = None,
) -> dict:
    """``docker info`` then ``act --version``. Does not pull or start a job."""
    started = time.monotonic()
    cfg = load_local_pipeline(Path(workdir))
    image = _runner_image(cfg.section)
    job = _job("act", cfg.section)
    probe = {
        "docker": False,
        "docker_server": None,
        "act_version": None,
        "runner_image": image,
    }

    def finish(code: Optional[str], body: dict) -> dict:
        return _result(
            backend="act",
            cfg_backend=cfg.config_backend,
            job=job,
            probe=body,
            started=started,
            error_code=code,
        )

    # act is Linux-only. Do not spawn docker or act on Windows.
    if sys.platform == "win32":
        return finish("act_requires_linux", probe)
    env = os.environ if environ is None else environ
    # Rootless and remote daemons are not the default socket. Forward only
    # these four, the same way systemctl forwards the session bus.
    ran = _spawn(
        ["docker", "info"],
        timeout_s=timeout_s,
        environ=env,
        extra_keys=_DOCKER_SOCKET_KEYS,
    )
    if ran.missing or ran.timed_out or ran.code != 0:
        return finish("docker_unreachable", probe)
    probe = {
        "docker": True,
        "docker_server": _server_version(ran.stdout),
        "act_version": None,
        "runner_image": image,
    }
    act = _spawn(
        ["act", "--version"],
        timeout_s=timeout_s,
        environ=env,
        extra_keys=_DOCKER_SOCKET_KEYS,
    )
    if act.missing:
        return finish("act_missing", probe)
    version = act.stdout.strip()
    if act.timed_out or act.code != 0 or not version:
        return finish("act_version_unreadable", probe)
    probe["act_version"] = version
    return finish(None, probe)


def probe_self_hosted(
    workdir: Path,
    processes: Sequence[Any],
    *,
    requested_labels: Sequence[str] = (),
    now: Optional[float] = None,
    environ: Optional[Mapping[str, str]] = None,
    systemctl_fn: Optional[Callable[[Sequence[str]], int]] = None,
    timeout_s: float = PROBE_TIMEOUT_S,
) -> dict:
    """Filesystem and an injected process list. Does not open /proc.

    Spawns ``systemctl`` only when ``systemd_unit`` is set and the platform
    is not ``win32``. ``systemctl_fn`` replaces that spawn so tests do not
    call a live systemctl. ``timeout_s`` bounds the stat, the log read,
    ``Path.resolve``, and that spawn.
    """
    started = time.monotonic()
    cfg = load_local_pipeline(Path(workdir))
    section = cfg.section
    job = _job("self_hosted", section)
    clock = time.time() if now is None else float(now)
    deadline = started + float(timeout_s)

    def finish(code: Optional[str], fresh: bool) -> dict:
        return _result(
            backend="self_hosted",
            cfg_backend=cfg.config_backend,
            job=job,
            probe={"runner_log_fresh": fresh},
            started=started,
            error_code=code,
        )

    try:
        return _probe_self_hosted_body(
            workdir,
            processes,
            section=section,
            clock=clock,
            deadline=deadline,
            environ=environ,
            systemctl_fn=systemctl_fn,
            requested_labels=requested_labels,
            finish=finish,
        )
    except _BudgetExpired:
        # Same refusal as a systemctl timeout: the listener was not confirmed.
        return finish("runner_offline", False)


def _ensure_budget(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise _BudgetExpired


def _probe_self_hosted_body(
    workdir: Path,
    processes: Sequence[Any],
    *,
    section: Mapping[str, Any],
    clock: float,
    deadline: float,
    environ: Optional[Mapping[str, str]],
    systemctl_fn: Optional[Callable[[Sequence[str]], int]],
    requested_labels: Sequence[str],
    finish: Callable[[Optional[str], bool], dict],
) -> dict:
    _ensure_budget(deadline)
    runner = _runner_dir(Path(workdir), section)
    _ensure_budget(deadline)
    if runner is None or not runner.is_dir():
        return finish("runner_log_missing", False)
    # Existence only. Never read .runner, credentials, or .credentials.
    _ensure_budget(deadline)
    if not (runner / ".runner").is_file():
        return finish("runner_not_registered", False)
    log_path = _newest_runner_log(runner, deadline=deadline)
    if log_path is None:
        return finish("runner_log_missing", False)
    text = _read_log(log_path, deadline=deadline)
    if text is None:
        return finish("runner_log_missing", False)
    # Sample size before the unit and process checks. A later stat can see
    # growth during the probe even when mtime is older than log_stale_s.
    _ensure_budget(deadline)
    try:
        size_before = log_path.stat().st_size
    except OSError:
        return finish("runner_log_missing", False)
    # A down unit is offline even when a stray process matches. Scope is the
    # configured field, not a guess from the unit name.
    _ensure_budget(deadline)
    remaining = deadline - time.monotonic()
    if _systemd_active(section, environ, systemctl_fn, timeout_s=remaining) is False:
        return finish("runner_offline", False)
    # No /proc on Windows. An empty process list is not an offline listener.
    _ensure_budget(deadline)
    if sys.platform != "win32" and not any(
        _process_matches(proc, runner, deadline=deadline) for proc in processes
    ):
        return finish("runner_offline", False)
    _ensure_budget(deadline)
    try:
        st = log_path.stat()
    except OSError:
        return finish("runner_log_missing", False)
    # Spec line 923: a fresh mtime or growth during the probe. Do not re-read.
    if st.st_size <= size_before and clock - st.st_mtime > _log_stale_s(section):
        return finish("runner_log_stale", False)
    if "Listening for Jobs" not in text:
        return finish("runner_offline", False)
    labels = _string_list(_map(section, "self_hosted").get("labels"))
    # Stale check and "Listening for Jobs" already passed. The label error
    # is not a stale log.
    if not labels:
        return finish("runner_label_unknown", True)
    if requested_labels and not _label_subset(requested_labels, labels):
        return finish("runner_label_mismatch", True)
    return finish(None, True)


def probe_jenkins(
    workdir: Path,
    *,
    timeout_s: float = PROBE_TIMEOUT_S,
    environ: Optional[Mapping[str, str]] = None,
) -> dict:
    """GET /api/json. Does not follow redirects or request a crumb."""
    started = time.monotonic()
    cfg = load_local_pipeline(Path(workdir))
    job = _job("jenkins", cfg.section)
    env = os.environ if environ is None else environ

    def finish(code: Optional[str]) -> dict:
        return _result(
            backend="jenkins",
            cfg_backend=cfg.config_backend,
            job=job,
            probe={"jenkins": code is None},
            started=started,
            error_code=code,
        )

    url = _jenkins_url(cfg.section, env)
    if url is None:
        return finish("jenkins_unreachable")
    code = _jenkins_get(url, timeout_s=timeout_s, environ=env)
    return finish(code)


def list_linux_processes(
    proc_root: Path = Path("/proc"),
    *,
    deadline: Optional[float] = None,
) -> list[dict[str, Optional[str]]]:
    """Read comm/cwd/exe under ``proc_root``. Default root is live /proc."""
    root = Path(proc_root)
    found: list[dict[str, Optional[str]]] = []
    if deadline is not None:
        _ensure_budget(deadline)
    if not root.is_dir():
        return found
    try:
        entries = list(root.iterdir())
    except OSError:
        return found
    for entry in entries:
        if deadline is not None:
            _ensure_budget(deadline)
        if not entry.name.isdigit():
            continue
        try:
            if not entry.is_dir():
                continue
            found.append(
                {
                    "comm": _read_comm(entry / "comm"),
                    "exe": _read_link(entry / "exe"),
                    "cwd": _read_link(entry / "cwd"),
                }
            )
        except OSError:
            continue
    return found


def _result(
    *,
    backend: str,
    cfg_backend: str,
    job: dict,
    probe: dict,
    started: float,
    error_code: Optional[str],
) -> dict:
    ok = error_code is None
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if elapsed_ms < 0:
        elapsed_ms = 0
    return {
        "ok": ok,
        "skipped": False,
        "backend": backend,
        "config_backend": cfg_backend,
        "exit_code": 0 if ok else 2,
        "duration_ms": elapsed_ms,
        "timed_out": False,
        "cancelled": False,
        "truncated": False,
        "lines_dropped": 0,
        "queue_risk": error_code == "runner_offline",
        "status": "succeeded" if ok else "refused",
        "job": job,
        "log_tail": "",
        "log_path": f".agent/local-pipeline-runs/probe-{backend}.log",
        "probe": probe,
        "warnings": [],
        "status_target": _STATUS_TARGET[backend],
        "status_posted_by": None,
        "scm": {
            "kind": None,
            "host": _SCM_HOST[backend],
            "ref": None,
            "commit": None,
        },
        "error": None
        if ok
        else {"code": error_code, "message": _MESSAGES[error_code]},
    }


def _spawn(
    argv: Sequence[str],
    *,
    timeout_s: float,
    environ: Mapping[str, str],
    extra_keys: Sequence[str] = (),
) -> _Ran:
    # PATH only, plus caller-named keys (session bus or the Docker socket
    # variables). Secrets stay in the parent. Do not copy the whole environment.
    child_env: dict[str, str] = {}
    path = environ.get("PATH")
    if isinstance(path, str):
        child_env["PATH"] = path
    for key in extra_keys:
        value = environ.get(key)
        if isinstance(value, str) and value and "\x00" not in value:
            child_env[key] = value
    try:
        proc = subprocess.Popen(
            list(argv),
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=child_env,
        )
    except FileNotFoundError:
        return _Ran(missing=True)
    except OSError:
        return _Ran(code=1)
    try:
        stdout, _stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        kill_process_group(proc)
        _drain(proc)
        return _Ran(timed_out=True)
    except OSError:
        kill_process_group(proc)
        return _Ran(code=1)
    text = stdout.decode("utf-8", errors="replace") if stdout else ""
    code = proc.returncode if proc.returncode is not None else 1
    return _Ran(code=code, stdout=text)


def _drain(proc: subprocess.Popen) -> None:
    try:
        proc.communicate(timeout=1)
    except (subprocess.TimeoutExpired, OSError):
        pass


def _server_version(stdout: str) -> Optional[str]:
    text = stdout.strip()
    parsed = _maybe_json(text)
    if isinstance(parsed, dict):
        return _version_token(parsed.get("ServerVersion"))
    if isinstance(parsed, str):
        token = _version_token(parsed)
        if token is not None:
            return token
        return _version_from_lines(parsed)
    return _version_from_lines(stdout)


def _maybe_json(text: str) -> Any:
    if not text or text[0] not in '{["':
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _version_from_lines(text: str) -> Optional[str]:
    # docker info text is "Server Version:"; JSON uses "ServerVersion".
    prefixes = ("Server Version:", "ServerVersion:")
    for line in text.splitlines():
        stripped = line.strip()
        for prefix in prefixes:
            if stripped.startswith(prefix):
                return _version_token(stripped[len(prefix) :])
    return None


def _version_token(value: object) -> Optional[str]:
    """Keep a version token. Drop paths (Docker Root Dir, Name, home)."""
    if not isinstance(value, str):
        return None
    parts = value.split()
    if not parts:
        return None
    token = parts[0]
    if not token or len(token) > 128 or "/" in token or "\\" in token:
        return None
    return token


def _map(section: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = section.get(key)
    if isinstance(value, dict):
        return value
    return {}


def _job(backend: str, section: Mapping[str, Any]) -> dict[str, Any]:
    if backend == "act":
        act = _map(section, "act")
        workflow = act.get("workflow_file")
        job_name = act.get("job_name")
        return {
            "workflow_file": workflow
            if isinstance(workflow, str) and workflow
            else _DEFAULT_WORKFLOW,
            "job_name": job_name if isinstance(job_name, str) and job_name else _DEFAULT_JOB,
        }
    if backend == "self_hosted":
        labels = _string_list(_map(section, "self_hosted").get("labels"))
        return {"runner_labels": labels if labels is not None else []}
    jenkins = _map(section, "jenkins")
    job_path = jenkins.get("job_path")
    jenkinsfile = jenkins.get("jenkinsfile_path")
    if isinstance(job_path, str):
        return {"job_path": job_path}
    if isinstance(jenkinsfile, str):
        return {"jenkinsfile_path": jenkinsfile}
    return {"job_path": ""}


def _runner_image(section: Mapping[str, Any]) -> str:
    image = _map(section, "act").get("runner_image")
    if isinstance(image, str) and image:
        return image
    return _DEFAULT_RUNNER_IMAGE


def _string_list(value: object) -> Optional[list[str]]:
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    return None


def _runner_dir(workdir: Path, section: Mapping[str, Any]) -> Optional[Path]:
    raw = _map(section, "self_hosted").get("runner_dir")
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = workdir / path
    return path


def _systemd_argv(section: Mapping[str, Any]) -> Optional[list[str]]:
    hosted = _map(section, "self_hosted")
    unit = hosted.get("systemd_unit")
    # Blank is unset: do not call systemctl. A leading dash is systemctl's own
    # flag (--help, --version, -h), which exits 0 and looks active. An empty
    # argv is not active and is not spawned. NUL is the same class.
    if not isinstance(unit, str) or not unit.strip():
        return None
    if unit.startswith("-") or "\x00" in unit:
        return []
    # ``--`` stops systemctl from reading the unit as a flag.
    # ``user`` is the only scope that passes --user. Do not infer it from the name.
    if hosted.get("systemd_scope") == "user":
        return ["systemctl", "--user", "is-active", "--", unit]
    return ["systemctl", "is-active", "--", unit]


def _systemd_active(
    section: Mapping[str, Any],
    environ: Optional[Mapping[str, str]],
    systemctl_fn: Optional[Callable[[Sequence[str]], int]],
    *,
    timeout_s: float = PROBE_TIMEOUT_S,
) -> Optional[bool]:
    """None when no unit is configured or the platform is win32.

    False when the unit is not active. Windows is a file check and does not
    spawn ``systemctl`` (spec line 933).
    """
    if sys.platform == "win32":
        return None
    argv = _systemd_argv(section)
    if argv is None:
        return None
    if not argv:
        return False
    # A spent probe budget is not an active unit, and it must not spawn.
    if timeout_s <= 0:
        return False
    if systemctl_fn is not None:
        try:
            code = systemctl_fn(argv)
        except (OSError, ValueError):
            return False
        return code == 0
    env = os.environ if environ is None else environ
    try:
        # systemctl --user talks to the session bus. PATH alone is not enough.
        ran = _spawn(
            argv,
            timeout_s=timeout_s,
            environ=env,
            extra_keys=("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"),
        )
    except ValueError:
        return False
    if ran.missing or ran.timed_out or ran.code != 0:
        return False
    return True


def _log_stale_s(section: Mapping[str, Any]) -> float:
    raw = _map(section, "self_hosted").get("log_stale_s", 900)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return 900.0
    return float(raw)


def _newest_runner_log(
    runner_dir: Path,
    *,
    deadline: Optional[float] = None,
) -> Optional[Path]:
    # Runner builds also write _diag/pages/Runner_*.log. A flat glob misses them.
    folders = (
        runner_dir / "_diag",
        runner_dir / "_diag" / "pages",
    )
    newest: Optional[Path] = None
    newest_key: Optional[tuple[float, str]] = None
    for folder in folders:
        if deadline is not None:
            _ensure_budget(deadline)
        if not folder.is_dir():
            continue
        for path in folder.glob("Runner_*.log"):
            if deadline is not None:
                _ensure_budget(deadline)
            if not path.is_file():
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            key = (mtime, str(path))
            if newest_key is None or key > newest_key:
                newest = path
                newest_key = key
    return newest


def _read_log(path: Path, *, deadline: Optional[float] = None) -> Optional[str]:
    if deadline is not None:
        _ensure_budget(deadline)
    try:
        with path.open("rb") as handle:
            chunks: list[bytes] = []
            total = 0
            while total < _LOG_READ_MAX:
                if deadline is not None:
                    _ensure_budget(deadline)
                block = handle.read(min(_HTTP_CHUNK, _LOG_READ_MAX - total))
                if not block:
                    break
                chunks.append(block)
                total += len(block)
            data = b"".join(chunks)
    except OSError:
        return None
    return data.decode("utf-8", errors="replace")


def _read_comm(path: Path) -> Optional[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    name = text.split("\n", 1)[0].strip("\x00")
    return name or None


def _read_link(path: Path) -> Optional[str]:
    try:
        return os.readlink(path)
    except OSError:
        return None


def _proc_str(proc: Any, name: str) -> Optional[str]:
    if isinstance(proc, Mapping):
        value = proc.get(name)
    else:
        value = getattr(proc, name, None)
    if isinstance(value, str):
        return value
    return None


def _is_under(
    candidate: Optional[str],
    root: Path,
    *,
    deadline: Optional[float] = None,
) -> bool:
    if not candidate:
        return False
    if deadline is not None:
        _ensure_budget(deadline)
    try:
        leaf = Path(candidate).resolve()
        base = root.resolve()
    except OSError:
        return False
    if leaf == base:
        return True
    try:
        leaf.relative_to(base)
    except ValueError:
        return False
    return True


def _process_matches(
    proc: Any,
    runner_dir: Path,
    *,
    deadline: Optional[float] = None,
) -> bool:
    if deadline is not None:
        _ensure_budget(deadline)
    comm = _proc_str(proc, "comm")
    exe = _proc_str(proc, "exe")
    cwd = _proc_str(proc, "cwd")
    if comm is not None:
        comm = comm.strip("\r\n")
    base = Path(exe).name if exe else None
    if comm not in _LISTENER_NAMES and base not in _LISTENER_NAMES:
        return False
    return _is_under(exe, runner_dir, deadline=deadline) or _is_under(
        cwd, runner_dir, deadline=deadline
    )


def _label_subset(requested: Sequence[Any], labels: Sequence[str]) -> bool:
    pool = set(labels)
    for item in requested:
        if not isinstance(item, str) or item not in pool:
            return False
    return True


def _jenkins_url(
    section: Mapping[str, Any],
    environ: Mapping[str, str],
) -> Optional[str]:
    configured = _map(section, "jenkins").get("url")
    if isinstance(configured, str) and configured.strip():
        raw = configured.strip()
    else:
        fallback = environ.get("JENKINS_URL")
        if not isinstance(fallback, str) or not fallback.strip():
            return None
        raw = fallback.strip()
    return _controller_base(raw)


def _controller_base(url: str) -> Optional[str]:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    port = _url_port(parts)
    if port is False:
        return None
    host = parts.hostname
    if ":" in host:
        host = f"[{host}]"
    netloc = host if port is None else f"{host}:{port}"
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, netloc, path, "", ""))


def _origin(url: str) -> Optional[tuple[str, str, int]]:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    port = _url_port(parts)
    if port is False:
        return None
    if port is None:
        port = 443 if parts.scheme == "https" else 80
    return (parts.scheme.lower(), parts.hostname.lower(), port)


def _url_port(parts: Any) -> Any:
    """Explicit port, None if omitted, or False when the port is not usable."""
    try:
        return parts.port
    except ValueError:
        return False


def _basic_header(environ: Mapping[str, str]) -> Optional[str]:
    user = environ.get("JENKINS_USER")
    token = environ.get("JENKINS_API_TOKEN")
    if not isinstance(user, str) or not user:
        return None
    if not isinstance(token, str) or not token:
        return None
    raw = f"{user}:{token}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _response_socket(source: object):
    current = source
    for _ in range(3):
        raw = getattr(getattr(current, "fp", None), "raw", None)
        sock = getattr(raw, "_sock", None) if raw is not None else None
        if sock is not None:
            return sock
        nxt = getattr(current, "fp", None)
        if nxt is None:
            return None
        current = nxt
    return None


def _read_body(source: object, deadline: float, *, limit: Optional[int] = None) -> bytes:
    """Read until EOF inside ``deadline``. A short read is not a body."""
    chunks: list[bytes] = []
    total = 0
    while True:
        if limit is not None and total >= limit:
            return b"".join(chunks)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        sock = _response_socket(source)
        if sock is not None:
            sock.settimeout(remaining)
        block = source.read(_HTTP_CHUNK)  # type: ignore[attr-defined]
        if not block:
            return b"".join(chunks)
        chunks.append(block)
        total += len(block)


def _jenkins_exchange(
    base: str,
    *,
    deadline: float,
    timeout_s: float,
    environ: Mapping[str, str],
) -> Optional[str]:
    target = base + "/api/json"
    headers = {"Accept": "application/json"}
    auth = _basic_header(environ)
    if auth is not None:
        headers["Authorization"] = auth
    request = urllib.request.Request(target, headers=headers, method="GET")
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect(),
    )
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return "jenkins_unreachable"
    try:
        with opener.open(request, timeout=min(timeout_s, remaining)) as response:
            status = int(response.status)
            header_map = response.headers
            try:
                if status == 200:
                    body = _read_body(response, deadline)
                else:
                    _read_body(response, deadline, limit=_HTTP_READ_MAX)
                    body = b""
            except (TimeoutError, OSError, http.client.HTTPException):
                if status == 200:
                    return "jenkins_unreachable"
                body = b""
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        header_map = exc.headers
        try:
            if status == 200:
                body = _read_body(exc, deadline)
            else:
                _read_body(exc, deadline, limit=_HTTP_READ_MAX)
                body = b""
        except (TimeoutError, OSError, http.client.HTTPException):
            if status == 200:
                return "jenkins_unreachable"
            body = b""
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        ValueError,
        UnicodeError,
        http.client.HTTPException,
    ):
        return "jenkins_unreachable"
    return _classify_jenkins(target, status, header_map, body)


def _jenkins_get(
    base: str,
    *,
    timeout_s: float,
    environ: Mapping[str, str],
) -> Optional[str]:
    """GET /api/json inside one deadline. The socket timeout is not the budget."""
    if timeout_s <= 0:
        return "jenkins_unreachable"
    deadline = time.monotonic() + timeout_s
    holder: dict[str, Any] = {}

    def _run() -> None:
        try:
            holder["code"] = _jenkins_exchange(
                base,
                deadline=deadline,
                timeout_s=timeout_s,
                environ=environ,
            )
        except Exception as exc:
            holder["exc"] = exc

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        return "jenkins_unreachable"
    if "exc" in holder:
        raise holder["exc"]
    return holder.get("code", "jenkins_unreachable")


def _classify_jenkins(
    request_url: str,
    status: int,
    headers: Any,
    body: bytes,
) -> Optional[str]:
    if status in _REDIRECTS:
        location = None
        if headers is not None:
            location = headers.get("Location")
        if not location:
            return "jenkins_unreachable"
        target = urljoin(request_url, location)
        here = _origin(request_url)
        there = _origin(target)
        if here is None or there is None or here != there:
            return "jenkins_redirect_rejected"
        return "jenkins_unreachable"
    if status in (401, 403):
        return "jenkins_auth"
    if status != 200:
        return "jenkins_unreachable"
    try:
        text = body.decode("utf-8")
    except UnicodeError:
        return "jenkins_unreachable"
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return "jenkins_unreachable"
    if not isinstance(payload, dict):
        return "jenkins_unreachable"
    return None
