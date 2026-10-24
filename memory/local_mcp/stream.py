# -*- coding: utf-8 -*-
"""Stream a child. stdout and stderr are read together. Does not change run_cli."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from memory.local_mcp.redact import clip_message, contains_secret_marker, redact_text
from memory.logutil import get_logger

log = get_logger("memory.local_mcp")

# File cap. log_tail is a separate 8192-character window.
LOG_CAP = 20 * 1024 * 1024
_TAIL_LIMIT = 8192
_LINE_MAX = 1024 * 1024
_NOTIFY_PER_S = 20
# Lines waiting to be written. A stuck progress write must not grow past the file cap.
_LINE_Q_CAP = 1024 * 1024


@dataclass
class StreamResult:
    returncode: int | None = None
    timed_out: bool = False
    cancelled: bool = False
    truncated: bool = False
    lines_dropped: int = 0
    log_tail: str = ""
    missing: bool = False


def _bounded_tail(text: str, limit: int = _TAIL_LIMIT) -> str:
    """Last ``limit`` characters, starting on a line boundary when one exists."""
    if len(text) <= limit:
        return text
    window = text[-limit:]
    newline = window.find("\n")
    if newline == -1 or newline + 1 >= len(window):
        return window
    return window[newline + 1 :]


def _popen_group_kwargs() -> dict:
    """POSIX new session. Windows process group. ``start_new_session`` is not valid on win32."""
    if sys.platform == "win32":
        flag = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
        return {"creationflags": flag}
    return {"start_new_session": True}


def _notify_message(line: str, protocol: str) -> str | None:
    if protocol != "2025-11-25":
        return None
    # Marker check uses the full line. A hit past 500 characters is still redacted.
    flat = line.replace("\r", " ").replace("\n", " ")
    if contains_secret_marker(flat):
        return "[redacted]"
    return clip_message(flat).strip()


class _Sink:
    def __init__(
        self,
        log_fp: object,
        progress: Callable[..., None] | None,
        protocol: str,
    ) -> None:
        self._fp = log_fp
        self._progress = progress
        self._protocol = protocol
        self.written = 0
        self.truncated = False
        self.lines_dropped = 0
        self._parts: list[str] = []
        self._part_len = 0
        self._sent: list[float] = []
        # Latest byte count that did not get a notification. Flushed when a slot opens.
        self._pending: tuple[int, str | None] | None = None
        # redact_text only sees one line. A PEM body lives on the following lines.
        self._in_pem = False

    def _mask_pem(self, line: str) -> str:
        prefix = ""
        body = line
        for marker in ("stdout: ", "stderr: "):
            if line.startswith(marker):
                prefix = marker
                body = line[len(marker) :]
                break
        if self._in_pem:
            if "-----END " in body:
                self._in_pem = False
            return prefix + "****\n"
        if "-----BEGIN " in body:
            if "-----END " not in body:
                self._in_pem = True
            return prefix + "****\n"
        return line

    def add(self, line: str) -> None:
        redacted = redact_text(self._mask_pem(line))
        if not redacted.endswith("\n"):
            redacted += "\n"
        data = redacted.encode("utf-8", errors="replace")
        if not self.truncated:
            room = LOG_CAP - self.written
            if room <= 0:
                self.truncated = True
            elif len(data) > room:
                self._fp.write(data[:room])  # type: ignore[attr-defined]
                self.written += room
                self.truncated = True
            else:
                self._fp.write(data)  # type: ignore[attr-defined]
                self.written += len(data)
        self._remember(redacted)
        self._notify(redacted)

    def tail(self) -> str:
        return _bounded_tail("".join(self._parts))

    def _remember(self, text: str) -> None:
        self._parts.append(text)
        self._part_len += len(text)
        while self._part_len > _TAIL_LIMIT * 2 and len(self._parts) > 1:
            dropped = self._parts.pop(0)
            self._part_len -= len(dropped)

    def _allow(self) -> bool:
        now = time.monotonic()
        cutoff = now - 1.0
        while self._sent and self._sent[0] <= cutoff:
            self._sent.pop(0)
        if len(self._sent) >= _NOTIFY_PER_S:
            return False
        self._sent.append(now)
        return True

    def _send(self, amount: int, message: str | None) -> bool:
        """False means the write did not happen. A raising callback is not retried."""
        try:
            sent = self._progress(amount, message) if self._progress is not None else None
        except Exception:
            log.warning("progress notification failed")
            return True
        return sent is not False

    def _notify(self, line: str) -> None:
        if self._progress is None:
            return
        message = _notify_message(line, self._protocol)
        if not self._allow():
            self._pending = (self.written, message)
            self.lines_dropped += 1
            return
        if not self._send(self.written, message):
            self._sent.pop()
            self._pending = (self.written, message)
            self.lines_dropped += 1
            return
        self._pending = None

    def flush_progress(self, *, wait: bool) -> None:
        """One coalesced byte count when the rate window is open.

        ``wait`` is only for child exit: sleep until a slot opens, at most one
        second, and never send a 21st notification inside the same second.
        A progress callback that returns False did not write; do not spin on it.
        """
        if self._progress is None or self._pending is None:
            return
        deadline = time.monotonic() + 1.05
        while self._pending is not None:
            if self._allow():
                amount, message = self._pending
                if not self._send(amount, message):
                    self._sent.pop()
                    return
                self._pending = None
                if self.lines_dropped > 0:
                    self.lines_dropped -= 1
                return
            if not wait or time.monotonic() >= deadline:
                return
            delay = 0.01
            if self._sent:
                delay = self._sent[0] + 1.0 - time.monotonic()
            time.sleep(min(0.05, max(0.001, delay)))


def _open_log(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_TRUNC | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(path), flags, 0o600)
    os.chmod(fd, 0o600)
    return os.fdopen(fd, "wb")


class _LineQueue:
    """Line buffer with a byte cap. ``put`` never waits and never takes the stdout lock."""

    def __init__(self, max_bytes: int) -> None:
        self._items: queue.Queue[tuple[int, str]] = queue.Queue()
        self._max = max_bytes
        self._bytes = 0
        self._lock = threading.Lock()
        self.dropped = 0

    def put(self, item: str) -> None:
        size = len(item.encode("utf-8"))
        with self._lock:
            # The first line is kept even if it is larger than the cap.
            if self._bytes > 0 and self._bytes + size > self._max:
                self.dropped += 1
                return
            self._bytes += size
        self._items.put((size, item))

    def get(self, timeout: float) -> str:
        size, item = self._items.get(timeout=timeout)
        self._release(size)
        return item

    def get_nowait(self) -> str:
        size, item = self._items.get_nowait()
        self._release(size)
        return item

    def empty(self) -> bool:
        return self._items.empty()

    def _release(self, size: int) -> None:
        with self._lock:
            self._bytes -= size
            if self._bytes < 0:
                self._bytes = 0


def _reader(pipe: object, prefix: str, out_q: _LineQueue, done_q: queue.Queue) -> None:
    pending = b""
    try:
        while True:
            chunk = pipe.read(_LINE_MAX)  # type: ignore[attr-defined]
            if not chunk:
                break
            pending += chunk
            while b"\n" in pending:
                raw, pending = pending.split(b"\n", 1)
                out_q.put(prefix + raw.decode("utf-8", errors="replace") + "\n")
            while len(pending) >= _LINE_MAX:
                raw = pending[:_LINE_MAX]
                pending = pending[_LINE_MAX:]
                out_q.put(prefix + raw.decode("utf-8", errors="replace") + "\n")
        if pending:
            out_q.put(prefix + pending.decode("utf-8", errors="replace") + "\n")
    except Exception:
        log.warning("pipe read failed")
    finally:
        done_q.put(None)


def _kill(proc: subprocess.Popen) -> None:
    # Grace matches memory.adapters.proc._KILL_GRACE_S.
    from memory.local_mcp.server import kill_process_group

    kill_process_group(proc)


def stream_cli(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    log_path: Path,
    timeout_s: float,
    cancel: threading.Event | None = None,
    progress: Callable[..., None] | None = None,
    protocol: str = "2025-11-25",
    before_reap: Callable[[], None] | None = None,
) -> StreamResult:
    """Run ``argv`` with a new session. Merge redacted stdout and stderr."""
    log_fp = _open_log(log_path)
    try:
        try:
            proc = subprocess.Popen(
                list(argv),
                cwd=str(cwd),
                env={key: value for key, value in env.items() if isinstance(value, str)},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                **_popen_group_kwargs(),
            )
        except FileNotFoundError:
            return StreamResult(missing=True)
        except (OSError, ValueError):
            return StreamResult(returncode=1)
        assert proc.stdout is not None and proc.stderr is not None
        line_q = _LineQueue(_LINE_Q_CAP)
        done_q: queue.Queue = queue.Queue()
        sink = _Sink(log_fp, progress, protocol)
        threads = [
            threading.Thread(
                target=_reader,
                args=(proc.stdout, "stdout: ", line_q, done_q),
                name="local-mcp-stdout",
                daemon=True,
            ),
            threading.Thread(
                target=_reader,
                args=(proc.stderr, "stderr: ", line_q, done_q),
                name="local-mcp-stderr",
                daemon=True,
            ),
        ]
        for thread in threads:
            thread.start()
        finished = 0
        timed_out = False
        cancelled = False
        deadline = time.monotonic() + float(timeout_s)

        def _poll() -> int | None:
            # poll reaps. Snapshot ephemeral diag before that.
            if before_reap is not None:
                before_reap()
            return proc.poll()

        while finished < 2:
            if cancel is not None and cancel.is_set() and _poll() is None:
                cancelled = True
                break
            now = time.monotonic()
            if now >= deadline and _poll() is None:
                timed_out = True
                break
            wait = max(0.01, min(0.05, deadline - now))
            try:
                item = line_q.get(timeout=wait)
            except queue.Empty:
                # A pause opens the rate window. Do not wait here; the child may still be writing.
                sink.flush_progress(wait=False)
                while finished < 2:
                    try:
                        done_q.get_nowait()
                    except queue.Empty:
                        break
                    finished += 1
                if finished >= 2 and line_q.empty() and _poll() is not None:
                    break
                continue
            sink.add(item)
            while True:
                try:
                    done_q.get_nowait()
                except queue.Empty:
                    break
                finished += 1
        if timed_out or cancelled:
            _kill(proc)
        for thread in threads:
            thread.join(timeout=2)
        while True:
            try:
                item = line_q.get_nowait()
            except queue.Empty:
                break
            sink.add(item)
        # Queue drops are not file lines. Add them after the coalesced note so
        # that note cannot un-count a line the file never received.
        sink.flush_progress(wait=True)
        sink.lines_dropped += line_q.dropped
        if _poll() is None:
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                _kill(proc)
        return StreamResult(
            returncode=proc.returncode,
            timed_out=timed_out,
            cancelled=cancelled,
            truncated=sink.truncated,
            lines_dropped=sink.lines_dropped,
            log_tail=sink.tail(),
        )
    finally:
        try:
            log_fp.close()
        except OSError:
            pass
