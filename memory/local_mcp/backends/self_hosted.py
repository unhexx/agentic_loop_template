# -*- coding: utf-8 -*-
"""Supervise an already-registered actions/runner. No enroll and no dispatch."""

from __future__ import annotations

import ctypes
import errno
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from memory.local_mcp import probe as probe_mod
from memory.local_mcp import stream as stream_mod
from memory.local_mcp.probe import _MESSAGES as _PROBE_MESSAGES
from memory.local_mcp.redact import clip_message, redact_text

_POLL_S = 0.05
_GROWTH_GAP_S = 0.1
_JOIN_S = 8.0
_FORBIDDEN = ("--token", "--url", "--pat")
_HOST_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "USER", "LOGNAME", "XDG_RUNTIME_DIR")

_EXTRA = {
    "registration_forbidden": "Refused.",
    "untrusted_pr_risk": "Probe only. Do not run fork PR code here.",
    "runner_start_unsupported": "Probe only. run.sh and run.cmd are not started.",
    "listener_idle": "listener up; no Worker log appeared before timeout_s",
    "worker_log_stopped": "Worker log appeared and stopped before timeout_s",
    "cancelled": "run cancelled",
    "job_failed": "runner exited non-zero",
    "timed_out": "local tail stopped; the GitHub job was not cancelled",
    "workspace_pollution": "Non-ephemeral _work is not deleted.",
}


@dataclass
class HostedOutcome:
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


def _message(code: str) -> str:
    if code in _EXTRA:
        return _EXTRA[code]
    return _PROBE_MESSAGES.get(code, "Refused.")


def _err(code: str) -> dict[str, str]:
    return {"code": code, "message": clip_message(_message(code))}


def _flag(section: Mapping[str, Any], key: str) -> bool:
    hosted = section.get("self_hosted")
    if not isinstance(hosted, dict):
        return False
    return hosted.get(key) is True


def _warnings(section: Mapping[str, Any]) -> list[dict[str, str]]:
    if _flag(section, "ephemeral"):
        return []
    return [{"code": "workspace_pollution", "message": _message("workspace_pollution")}]


def _outcome(
    code: str | None,
    *,
    exit_code: int,
    status: str,
    probe: Mapping[str, Any] | None = None,
    warnings: list | None = None,
    log_tail: str = "",
    timed_out: bool = False,
    cancelled: bool = False,
    truncated: bool = False,
    lines_dropped: int = 0,
) -> HostedOutcome:
    ok = exit_code == 0 and not timed_out and not cancelled and code is None
    if ok:
        status = "succeeded"
        error = None
    else:
        error = _err(code or "job_failed")
    return HostedOutcome(
        exit_code=exit_code,
        status=status,
        ok=ok,
        timed_out=timed_out,
        cancelled=cancelled,
        truncated=truncated,
        lines_dropped=lines_dropped if lines_dropped > 0 else 0,
        log_tail=log_tail,
        warnings=list(warnings or []),
        error=error,
        probe=dict(probe or {}),
    )


def _refused(
    code: str,
    *,
    probe: Mapping[str, Any] | None = None,
    warnings: list | None = None,
    log_tail: str = "",
) -> HostedOutcome:
    return _outcome(
        code,
        exit_code=2,
        status="refused",
        probe=probe,
        warnings=warnings,
        log_tail=log_tail,
    )


def _labels_code(section: Mapping[str, Any], requested: Sequence[str]) -> str | None:
    configured = probe_mod._string_list(probe_mod._map(section, "self_hosted").get("labels"))
    if not configured:
        return "runner_label_unknown"
    if not probe_mod._label_subset(requested, configured):
        return "runner_label_mismatch"
    return None


def _alive(processes: Sequence[Any], runner: Path) -> bool:
    # Windows has no scoped process table. An empty list is not a listener.
    if sys.platform == "win32":
        return False
    return any(probe_mod._process_matches(proc, runner) for proc in processes)


def _may_start(section: Mapping[str, Any]) -> bool:
    return _flag(section, "allow_start") and _flag(section, "supervise_listener") and (
        _flag(section, "ephemeral") or _flag(section, "isolated_work_folder")
    )


def _start_requested(section: Mapping[str, Any]) -> bool:
    # One flag does not ask to start. A shared runner then stays runner_offline.
    return _flag(section, "allow_start") and _flag(section, "supervise_listener")


def _shared(section: Mapping[str, Any]) -> bool:
    return not _flag(section, "ephemeral") and not _flag(section, "isolated_work_folder")


def _start_argv(script: Path) -> list[str] | None:
    """Only ``run.sh`` with no flags. ``config.sh`` or a token flag is forbidden."""
    try:
        target = script.resolve()
    except (OSError, ValueError):
        return None
    names = {script.name, target.name}
    if "config.sh" in names:
        return None
    argv = [str(script)]
    for arg in argv:
        if Path(arg).name == "config.sh":
            return None
        for flag in _FORBIDDEN:
            if arg == flag or arg.startswith(flag + "="):
                return None
    return argv


def _host_env(environ: Mapping[str, str]) -> dict[str, str]:
    """Server env only. Caller environment_variables are not copied onto run.sh."""
    child: dict[str, str] = {}
    for key in _HOST_KEYS:
        value = environ.get(key)
        if isinstance(value, str) and value and "\x00" not in value:
            child[key] = value
    return child


def _diag_dirs(runner: Path) -> tuple[Path, Path]:
    return (runner / "_diag", runner / "_diag" / "pages")


def _newest_named(runner: Path, prefix: str) -> Path | None:
    newest: Path | None = None
    newest_key: tuple[float, str] | None = None
    for folder in _diag_dirs(runner):
        if not folder.is_dir():
            continue
        for path in folder.glob(prefix + "_*.log"):
            if not path.is_file() or path.is_symlink():
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


def _worker_bytes(runner: Path) -> int:
    total = 0
    for folder in _diag_dirs(runner):
        if not folder.is_dir():
            continue
        for path in folder.glob("Worker_*.log"):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


def _still_growing(samples: list[tuple[float, int]]) -> bool:
    """True when size increased across a gap. Adjacent stats cannot show growth."""
    if len(samples) < 2:
        return False
    end_t, end_n = samples[-1]
    baseline: int | None = None
    for stamp, size in samples:
        if end_t - stamp >= _GROWTH_GAP_S:
            baseline = size
        else:
            break
    if baseline is None:
        return False
    return end_n > baseline


def _appeared_or_grew(samples: list[tuple[float, int]]) -> bool:
    """True when the Worker byte total increased during this window."""
    previous: int | None = None
    for _, size in samples:
        if previous is not None and size > previous:
            return True
        previous = size
    return False


def _read_tail(path: Path) -> tuple[str | None, bool]:
    """Last 8 MiB. The probe still reads the head for ``Listening for Jobs``."""
    limit = probe_mod._LOG_READ_MAX
    try:
        if path.is_symlink():
            return None, False
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > limit:
                handle.seek(size - limit)
            data = handle.read(limit)
    except OSError:
        return None, False
    return data.decode("utf-8", errors="replace"), size > limit


def _diag_text(runner: Path) -> tuple[str, bool]:
    """Tail of each diag file, then the last ``LOG_CAP`` bytes of that join."""
    runner_log = probe_mod._newest_runner_log(runner)
    paths: list[Path | None] = [runner_log, _newest_named(runner, "Worker")]
    # Tail flat and pages workers, not only the single newest path.
    for folder in _diag_dirs(runner):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("Worker_*.log")):
            if path.is_file() and not path.is_symlink() and path not in paths:
                paths.append(path)
    parts: list[bytes] = []
    truncated = False
    for path in paths:
        if path is None or path.is_symlink():
            continue
        text, cut = _read_tail(path)
        if cut:
            truncated = True
        if text:
            parts.append(text.encode("utf-8", errors="replace"))
    raw = b"\n".join(parts)
    if len(raw) > stream_mod.LOG_CAP:
        truncated = True
        raw = raw[-stream_mod.LOG_CAP :]
    return raw.decode("utf-8", errors="replace"), truncated


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _append_log(path: Path, text: str) -> bool:
    """Append up to ``LOG_CAP``. True when this write was cut."""
    redacted = redact_text(text)
    if not redacted:
        return False
    if not redacted.endswith("\n"):
        redacted += "\n"
    data = redacted.encode("utf-8", errors="replace")
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_APPEND | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(path), flags, 0o600)
    try:
        try:
            existing = os.fstat(fd).st_size
        except OSError:
            existing = 0
        room = stream_mod.LOG_CAP - existing
        if room <= 0:
            return True
        if len(data) > room:
            # The marker is at the end of a tail, not in the dropped head.
            os.write(fd, data[-room:])
            os.chmod(fd, 0o600)
            return True
        os.write(fd, data)
        os.chmod(fd, 0o600)
        return False
    finally:
        os.close(fd)


_FAN_CLOEXEC = 0x00000001
_FAN_NONBLOCK = 0x00000002
_FAN_CLASS_NOTIF = 0x00000000
_FAN_CLOSE_WRITE = 0x00000008
_FAN_EVENT_ON_CHILD = 0x08000000
_FAN_MARK_ADD = 0x00000001
_FAN_MARK_ONLYDIR = 0x00000008
_AT_FDCWD = -100


class _FanMeta(ctypes.Structure):
    _fields_ = [
        ("event_len", ctypes.c_uint32),
        ("vers", ctypes.c_uint8),
        ("reserved", ctypes.c_uint8),
        ("metadata_len", ctypes.c_uint16),
        ("mask", ctypes.c_uint64),
        ("fd", ctypes.c_int32),
        ("pid", ctypes.c_int32),
    ]


class _DiagSnap:
    """Newest ephemeral diag, captured before ``wait`` reaps the child.

    A fanotify CLOSE_WRITE fd holds the inode after ``run.sh`` unlinks
    ``_diag``. Bytes read before this call's write are replaced when the
    close is newer. Already-up listeners are snapshotted the same way.
    """

    def __init__(self, runner: Path, runs: Path, stem: str) -> None:
        self.runner = runner
        self.runs = runs
        self.stem = stem
        self._fd = -1
        self._marked: set[str] = set()
        # kind -> (mtime, path, text, cut)
        self._best: dict[str, tuple[float, str, str, bool]] = {}

    def open(self) -> None:
        self._fd = self._fan_init()
        self._mark_tree()
        # Seed with the logs that are already on disk. A later close replaces
        # them, so a pre-start fixture is not what we keep after run.sh writes.
        self._offer_live()

    def close(self) -> None:
        fd = self._fd
        self._fd = -1
        if fd >= 0:
            os.close(fd)

    def before_reap(self) -> None:
        # stream_cli calls this before poll, which reaps. Do not raise.
        try:
            self.drain_and_flush()
        except OSError:
            return

    def drain_and_flush(self) -> None:
        self._mark_tree()
        if self._fd >= 0:
            self._drain_events()
        else:
            self._offer_live()
        self._flush()

    def parts(self) -> tuple[str, bool]:
        chunks: list[str] = []
        cut = False
        for kind in ("runner", "worker"):
            held = self._best.get(kind)
            if held is None or not held[2]:
                continue
            chunks.append(held[2])
            cut = cut or held[3]
        return "\n".join(chunks), cut

    def _fan_init(self) -> int:
        if sys.platform == "win32":
            return -1
        try:
            init = ctypes.CDLL(None, use_errno=True).fanotify_init
        except AttributeError:
            return -1
        init.argtypes = [ctypes.c_uint, ctypes.c_uint]
        init.restype = ctypes.c_int
        flags = _FAN_CLOEXEC | _FAN_CLASS_NOTIF | _FAN_NONBLOCK
        mode = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_LARGEFILE", 0)
        fd = int(init(flags, mode))
        if fd < 0:
            return -1
        os.set_inheritable(fd, False)
        return fd

    def _mark_tree(self) -> None:
        if self._fd < 0:
            return
        diag = self.runner / "_diag"
        self._mark(diag)
        self._mark(diag / "pages")

    def _mark(self, folder: Path) -> None:
        if self._fd < 0 or not folder.is_dir() or folder.is_symlink():
            return
        key = str(folder)
        if key in self._marked:
            return
        try:
            mark = ctypes.CDLL(None, use_errno=True).fanotify_mark
        except AttributeError:
            return
        mark.argtypes = [
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_uint64,
            ctypes.c_int,
            ctypes.c_char_p,
        ]
        mark.restype = ctypes.c_int
        mask = _FAN_CLOSE_WRITE | _FAN_EVENT_ON_CHILD
        flags = _FAN_MARK_ADD | _FAN_MARK_ONLYDIR
        rc = int(mark(self._fd, flags, mask, _AT_FDCWD, os.fsencode(folder)))
        if rc == 0:
            self._marked.add(key)

    def _drain_events(self) -> None:
        while True:
            try:
                blob = os.read(self._fd, 8192)
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    return
                return
            if not blob:
                return
            offset = 0
            while offset + ctypes.sizeof(_FanMeta) <= len(blob):
                meta = _FanMeta.from_buffer_copy(blob[offset : offset + ctypes.sizeof(_FanMeta)])
                step = int(meta.event_len)
                if step < ctypes.sizeof(_FanMeta):
                    return
                fd = int(meta.fd)
                try:
                    if fd >= 0 and int(meta.mask) & _FAN_CLOSE_WRITE:
                        self._take(fd)
                finally:
                    if fd >= 0:
                        os.close(fd)
                offset += step

    def _take(self, fd: int) -> None:
        try:
            linked = os.readlink(f"/proc/self/fd/{fd}")
        except OSError:
            return
        if linked.endswith(" (deleted)"):
            linked = linked[: -len(" (deleted)")]
        path = Path(linked)
        kind = self._kind(path)
        if kind is None:
            return
        try:
            st = os.fstat(fd)
            limit = probe_mod._LOG_READ_MAX
            cut = st.st_size > limit
            os.lseek(fd, st.st_size - limit if cut else 0, os.SEEK_SET)
            data = os.read(fd, limit)
        except OSError:
            return
        text = data.decode("utf-8", errors="replace")
        self._offer(kind, path.resolve(), st.st_mtime, text, cut)

    def _kind(self, path: Path) -> str | None:
        if path.is_symlink():
            return None
        try:
            resolved = path.resolve()
            root = (self.runner / "_diag").resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            return None
        if resolved.parent != root and resolved.parent != root / "pages":
            return None
        name = resolved.name
        if name.startswith("Runner_") and name.endswith(".log"):
            return "runner"
        if name.startswith("Worker_") and name.endswith(".log"):
            return "worker"
        return None

    def _offer_live(self) -> None:
        pairs = (
            ("runner", probe_mod._newest_runner_log(self.runner)),
            ("worker", _newest_named(self.runner, "Worker")),
        )
        for kind, path in pairs:
            if path is None or path.is_symlink():
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            text, cut = _read_tail(path)
            if text is None:
                continue
            try:
                resolved = path.resolve()
            except OSError:
                continue
            self._offer(kind, resolved, mtime, text, cut)

    def _offer(self, kind: str, path: Path, mtime: float, text: str, cut: bool) -> None:
        ident = str(path)
        current = self._best.get(kind)
        if current is not None:
            cur_m, cur_path, cur_text, _cur_cut = current
            if ident == cur_path and text == cur_text:
                return
            if ident != cur_path and (mtime, ident) < (cur_m, cur_path):
                return
        self._best[kind] = (mtime, ident, text, cut)

    def _flush(self) -> None:
        names = {"runner": "runner.log", "worker": "worker.log"}
        for kind, suffix in names.items():
            held = self._best.get(kind)
            if held is None or not held[2]:
                continue
            redacted = redact_text(held[2])
            if not redacted:
                continue
            _write_private(
                self.runs / f"{self.stem}.{suffix}",
                redacted.encode("utf-8", errors="replace"),
            )


def _fresh_probe(probed: Mapping[str, Any]) -> dict[str, bool]:
    body = probed.get("probe")
    fresh = isinstance(body, dict) and body.get("runner_log_fresh") is True
    return {"runner_log_fresh": fresh}


def _offline_decision(
    section: Mapping[str, Any],
    *,
    alive: bool,
    warnings: list,
    probe: Mapping[str, Any],
) -> HostedOutcome | None:
    """None when this call should start run.sh. Otherwise the refusal."""
    if alive:
        return _refused("runner_offline", probe=probe, warnings=warnings)
    if _shared(section) and _start_requested(section):
        return _refused("untrusted_pr_risk", probe=probe, warnings=warnings)
    if not _may_start(section):
        return _refused("runner_offline", probe=probe, warnings=warnings)
    if sys.platform == "win32":
        return _refused("runner_start_unsupported", probe=probe, warnings=warnings)
    return None


def _window_timeout(
    *,
    seen: bool,
    grew: bool,
    warnings: list,
    probe: Mapping[str, Any],
    log_tail: str,
    truncated: bool = False,
    lines_dropped: int = 0,
    killed_child: bool = False,
) -> HostedOutcome:
    """124 if the Worker log is still growing, or this call killed run.sh.

    Idle only when a listener this call did not kill stayed up with no Worker log.
    """
    common = {
        "probe": probe,
        "warnings": warnings,
        "log_tail": log_tail,
        "truncated": truncated,
        "lines_dropped": lines_dropped,
    }
    if grew:
        return _outcome(
            "timed_out",
            exit_code=124,
            status="timed_out",
            timed_out=True,
            **common,
        )
    if seen:
        return _outcome(
            "worker_log_stopped",
            exit_code=1,
            status="failed",
            **common,
        )
    if killed_child:
        # Timeout killed the run.sh this call started. That listener is not up.
        return _outcome(
            "timed_out",
            exit_code=124,
            status="timed_out",
            timed_out=True,
            **common,
        )
    return _outcome(
        "listener_idle",
        exit_code=2,
        status="idle",
        **common,
    )


def _from_stream(
    streamed: stream_mod.StreamResult,
    *,
    seen: bool,
    grew: bool,
    warnings: list,
    probe: Mapping[str, Any],
    log_tail: str,
    truncated: bool = False,
    started: bool = False,
) -> HostedOutcome:
    cut = streamed.truncated or truncated
    if streamed.cancelled:
        return _outcome(
            "cancelled",
            exit_code=130,
            status="cancelled",
            probe=probe,
            warnings=warnings,
            log_tail=log_tail or streamed.log_tail,
            cancelled=True,
            truncated=cut,
            lines_dropped=streamed.lines_dropped,
        )
    if streamed.missing:
        return _refused("runner_offline", probe=probe, warnings=warnings, log_tail=log_tail)
    if not streamed.timed_out:
        if streamed.returncode == 0:
            return _outcome(
                None,
                exit_code=0,
                status="succeeded",
                probe=probe,
                warnings=warnings,
                log_tail=log_tail or streamed.log_tail,
                truncated=cut,
                lines_dropped=streamed.lines_dropped,
            )
        return _outcome(
            "job_failed",
            exit_code=1,
            status="failed",
            probe=probe,
            warnings=warnings,
            log_tail=log_tail or streamed.log_tail,
            truncated=cut,
            lines_dropped=streamed.lines_dropped,
        )
    return _window_timeout(
        seen=seen,
        grew=grew,
        warnings=warnings,
        probe=probe,
        log_tail=log_tail or streamed.log_tail,
        truncated=cut,
        lines_dropped=streamed.lines_dropped,
        killed_child=started,
    )


def _supervise(
    *,
    runner: Path,
    section: Mapping[str, Any],
    argv: list[str] | None,
    log_path: Path,
    runs: Path,
    stem: str,
    timeout_s: float,
    cancel: threading.Event | None,
    progress: Any,
    protocol: str,
    environ: Mapping[str, str],
    warnings: list,
    probe: Mapping[str, Any],
) -> HostedOutcome:
    owned = cancel if cancel is not None else threading.Event()
    holder: dict[str, stream_mod.StreamResult] = {}
    thread: threading.Thread | None = None
    snap: _DiagSnap | None = None
    try:
        if _flag(section, "ephemeral"):
            snap = _DiagSnap(runner, runs, stem)
            snap.open()
        if argv is not None:
            def _target() -> None:
                holder["stream"] = stream_mod.stream_cli(
                    argv,
                    cwd=runner,
                    env=_host_env(environ),
                    log_path=log_path,
                    timeout_s=timeout_s,
                    cancel=owned,
                    progress=progress,
                    protocol=protocol,
                    before_reap=None if snap is None else snap.before_reap,
                )

            thread = threading.Thread(target=_target, name="local-mcp-runner", daemon=True)
            thread.start()
        deadline = time.monotonic() + float(timeout_s)
        samples: list[tuple[float, int]] = []
        while True:
            samples.append((time.monotonic(), _worker_bytes(runner)))
            # Sample while the child is alive. Fanotify still covers a same-turn
            # unlink; a poll keeps a log that stays on disk for a moment when
            # fanotify_init is not permitted.
            if snap is not None:
                snap.drain_and_flush()
            if owned.is_set():
                break
            if thread is not None and not thread.is_alive():
                break
            now = time.monotonic()
            if now >= deadline:
                break
            time.sleep(min(_POLL_S, max(0.0, deadline - now)))
        if thread is not None:
            thread.join(_JOIN_S + float(timeout_s))
        if snap is not None:
            snap.drain_and_flush()
        live, diag_cut = _diag_text(runner)
        snap_text, snap_cut = ("", False) if snap is None else snap.parts()
        # Deleted _diag leaves the live read empty. The snapshot is the tail.
        if snap_text and not live.strip():
            tail, diag_cut = snap_text, snap_cut
        else:
            tail = live
        tail = redact_text(tail)
    finally:
        if snap is not None:
            snap.close()
    append_cut = False
    if tail:
        try:
            append_cut = _append_log(log_path, tail)
        except OSError:
            pass
    cut = diag_cut or append_cut
    grew = _still_growing(samples)
    seen = _appeared_or_grew(samples)
    if owned.is_set() or (cancel is not None and cancel.is_set()):
        return _outcome(
            "cancelled",
            exit_code=130,
            status="cancelled",
            probe=probe,
            warnings=warnings,
            log_tail=tail,
            cancelled=True,
            truncated=cut,
        )
    streamed = holder.get("stream")
    if streamed is None:
        return _window_timeout(
            seen=seen,
            grew=grew,
            warnings=warnings,
            probe=probe,
            log_tail=tail,
            truncated=cut,
        )
    return _from_stream(
        streamed,
        seen=seen,
        grew=grew,
        warnings=warnings,
        probe=probe,
        log_tail=tail,
        truncated=cut,
        started=argv is not None,
    )


def run(
    workdir: Path,
    section: Mapping[str, Any],
    *,
    timeout_s: int,
    log_path: Path,
    requested_labels: Sequence[str],
    progress: Any,
    protocol: str,
    cancel: threading.Event | None,
    environ: Mapping[str, str],
    runs_dir: Path,
    stem: str,
) -> HostedOutcome:
    """Probe, optionally start ``run.sh``, then tail ``_diag`` until timeout."""
    warnings = _warnings(section)
    if cancel is not None and cancel.is_set():
        return _outcome(
            "cancelled",
            exit_code=130,
            status="cancelled",
            warnings=warnings,
            cancelled=True,
        )
    label_code = _labels_code(section, requested_labels)
    if label_code is not None:
        return _refused(label_code, warnings=warnings)
    try:
        runner = probe_mod._runner_dir(Path(workdir), section)
    except (OSError, ValueError):
        return _refused("runner_log_missing", warnings=warnings)
    if runner is None or not runner.is_dir():
        return _refused("runner_log_missing", warnings=warnings)
    if not (runner / ".runner").is_file():
        return _refused("runner_not_registered", warnings=warnings)
    try:
        processes = list(probe_mod.list_linux_processes())
        probed = probe_mod.probe_self_hosted(
            workdir,
            processes,
            requested_labels=requested_labels,
            environ=environ,
        )
    except (OSError, ValueError):
        return _refused("runner_log_missing", warnings=warnings)
    probe = _fresh_probe(probed)
    error = probed.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    if code in (
        "runner_log_missing",
        "runner_not_registered",
        "runner_log_stale",
        "runner_label_unknown",
        "runner_label_mismatch",
    ):
        return _refused(str(code), probe=probe, warnings=warnings)
    alive = _alive(processes, runner)
    if code == "runner_offline":
        decision = _offline_decision(section, alive=alive, warnings=warnings, probe=probe)
        if decision is not None:
            return decision
        argv = _start_argv(runner / "run.sh")
        if argv is None:
            return _refused("registration_forbidden", probe=probe, warnings=warnings)
        return _supervise(
            runner=runner,
            section=section,
            argv=argv,
            log_path=log_path,
            runs=runs_dir,
            stem=stem,
            timeout_s=timeout_s,
            cancel=cancel,
            progress=progress,
            protocol=protocol,
            environ=environ,
            warnings=warnings,
            probe=probe,
        )
    # Listener is up. Do not start a second one.
    return _supervise(
        runner=runner,
        section=section,
        argv=None,
        log_path=log_path,
        runs=runs_dir,
        stem=stem,
        timeout_s=timeout_s,
        cancel=cancel,
        progress=progress,
        protocol=protocol,
        environ=environ,
        warnings=warnings,
        probe=probe,
    )
