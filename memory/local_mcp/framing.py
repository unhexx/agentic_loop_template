# -*- coding: utf-8 -*-
"""Newline-delimited JSON-RPC frames. One stdout lock. No embedded newlines."""

from __future__ import annotations

import json
import os
import queue
import select
import socket
import stat
import threading
import time
import weakref
from typing import Any, Mapping, TextIO

# stdin fd, or the non-blocking wake socket (Windows select accepts sockets).
Wake = int | socket.socket

# Responses and progress notifications share this lock. Logging must not.
STDOUT_LOCK = threading.Lock()


class FramingError(ValueError):
    """A frame is not one JSON value on one line."""


def dumps_message(message: Mapping[str, Any]) -> str:
    """Serialize one JSON-RPC object. Reject a raw newline in the frame."""
    if not isinstance(message, dict):
        raise FramingError("message must be a JSON object")
    text = json.dumps(
        message, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    _reject_embedded_newline(text)
    return text


def loads_message(text: str) -> Any:
    """Parse one frame body. The caller strips the trailing delimiter first."""
    if not isinstance(text, str):
        raise FramingError("frame must be a string")
    _reject_embedded_newline(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise FramingError("invalid json") from exc


def read_message(fp: TextIO, *, wake_fd: Wake | None = None) -> Any:
    """Read the next non-empty frame. Raise EOFError when the stream ends.

    Lines come from a binary buffer. One non-UTF-8 line is a FramingError
    and does not wedge the codec or discard the following line. ``wake_fd``
    is a non-blocking socket or fd that ends the read the way EOF does.
    ``select`` failing is not end-of-stream. A would-block or interrupted
    read is retried. Only a zero-length read is EOF.
    """
    carrier = _carriers.get(fp)
    if carrier is None:
        carrier = _LineBuffer(fp)
        _carriers[fp] = carrier
    while True:
        raw = carrier.take_line(wake_fd)
        if raw is None or raw == b"":
            raise EOFError
        if raw.endswith(b"\n"):
            raw = raw[:-1]
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        if raw == b"":
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FramingError("invalid utf-8") from exc
        return loads_message(text)


class _LineBuffer:
    """Pending bytes for one stream. Lines are split before UTF-8 decoding."""

    def __init__(self, fp: TextIO) -> None:
        self.fp = fp
        self.pending = bytearray()
        self.fd = _fileno(fp)
        buffer = getattr(fp, "buffer", None)
        # BytesIO has no raw file. Its readline is already one binary line.
        self.buffer = buffer if self.fd is None and buffer is not None else None
        self._direct = False
        self._relay: _StdinRelay | None = None

    def take_line(self, wake_fd: Wake | None) -> bytes | None:
        """One line, ``b""`` at EOF, or None when ``wake_fd`` fires."""
        while True:
            nl = self.pending.find(b"\n")
            if nl >= 0:
                end = nl + 1
                line = bytes(self.pending[:end])
                del self.pending[:end]
                return line
            chunk = self._pull(wake_fd)
            if chunk is None:
                return None
            if chunk == b"":
                if not self.pending:
                    return b""
                line = bytes(self.pending)
                self.pending.clear()
                return line
            self.pending.extend(chunk)

    def _pull(self, wake_fd: Wake | None) -> bytes | None:
        fd = self.fd
        if fd is not None:
            return _read_fd(self, fd, wake_fd)
        if self.buffer is not None:
            data = self.buffer.readline()
        else:
            data = self.fp.readline()
        if isinstance(data, bytes):
            return data
        if not isinstance(data, str):
            raise FramingError("frame must be a string")
        if data == "":
            return b""
        return data.encode("utf-8")


def _fileno(fp: TextIO) -> int | None:
    try:
        return fp.fileno()
    except (AttributeError, OSError, ValueError):
        return None


def _read_fd(carrier: _LineBuffer, fd: int, wake: Wake | None) -> bytes | None:
    """Read one chunk. None means the wake fd fired with no stdin bytes.

    ``b""`` is only what ``os.read`` returns at end-of-stream. On Windows,
    ``select`` of a pipe or console raises ``OSError`` (WinError 10038).
    That is not EOF: the bytes are still read, and shutdown waits on a socket.
    """
    if carrier._direct or carrier._relay is not None:
        return _read_unselectable(carrier, fd, wake)
    watch: list[Wake] = [fd] if wake is None else [fd, wake]
    try:
        readable, _, _ = select.select(watch, [], [])
    except (ValueError, OSError):
        return _read_unselectable(carrier, fd, wake)
    if fd in readable:
        return _read_some(fd)
    if wake is not None and wake in readable:
        _drain(wake)
        return None
    return b""


def _read_unselectable(
    carrier: _LineBuffer, fd: int, wake: Wake | None
) -> bytes | None:
    """``select`` cannot watch stdin. Keep reading. Do not report EOF."""
    if carrier._relay is not None and wake is not None:
        return carrier._relay.read(wake)
    if carrier._direct or wake is None or not _can_select(wake):
        # No socket to wait on (or select itself is unusable). Block in read.
        carrier._direct = True
        return _read_some(fd)
    carrier._relay = _StdinRelay(fd)
    return carrier._relay.read(wake)


def _can_select(wake: Wake) -> bool:
    try:
        select.select([wake], [], [], 0)
    except (ValueError, OSError):
        return False
    return True


def _read_some(fd: int) -> bytes:
    """One chunk. Only a zero-length ``os.read`` is EOF.

    ``BlockingIOError`` (``EAGAIN`` on a non-blocking pipe) and
    ``InterruptedError`` are retried. Any other ``OSError`` propagates
    so a read error is not reported as stdin EOF.
    """
    while True:
        try:
            return os.read(fd, 65536)
        except InterruptedError:
            continue
        except BlockingIOError:
            # Not EOF. ``select`` may be unable to watch this fd; do not spin.
            try:
                select.select([fd], [], [], 0.05)
            except (ValueError, OSError):
                time.sleep(0.05)


def _open_wake_pair() -> tuple[socket.socket, socket.socket]:
    """Connected non-blocking sockets. Windows ``select`` can wait on these."""
    left, right = _socket_pair()
    left.setblocking(False)
    right.setblocking(False)
    return left, right


def _socket_pair() -> tuple[socket.socket, socket.socket]:
    try:
        return socket.socketpair()
    except (AttributeError, OSError):
        return _tcp_loopback_pair()


def _tcp_loopback_pair() -> tuple[socket.socket, socket.socket]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        client.connect(listener.getsockname())
        accepted, _addr = listener.accept()
    except BaseException:
        client.close()
        listener.close()
        raise
    listener.close()
    for sock in (accepted, client):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return accepted, client


class _StdinRelay:
    """Blocking ``os.read`` of stdin, with ``select`` only on sockets.

    Windows ``select`` cannot watch the pipe. A notify socket plus the wake
    socket can both be waited on, so shutdown still unblocks the reader.
    """

    def __init__(self, fd: int) -> None:
        self._fd = fd
        self._eof = False
        self._chunks: queue.Queue[bytes] = queue.Queue()
        self._notify_r, self._notify_w = _open_wake_pair()
        threading.Thread(
            target=self._pump, name="local-mcp-stdin-relay", daemon=True
        ).start()

    def _pump(self) -> None:
        while True:
            try:
                chunk = os.read(self._fd, 65536)
            except OSError:
                chunk = b""
            self._chunks.put(chunk)
            try:
                self._notify_w.send(b"\x01")
            except BlockingIOError:
                pass
            except OSError:
                return
            if chunk == b"":
                return

    def _pop(self) -> bytes | None:
        try:
            return self._chunks.get_nowait()
        except queue.Empty:
            return None

    def _finish(self, chunk: bytes) -> bytes:
        if chunk == b"":
            self._eof = True
        return chunk

    def read(self, wake: Wake) -> bytes | None:
        if self._eof:
            return b""
        while True:
            queued = self._pop()
            if queued is not None:
                return self._finish(queued)
            try:
                readable, _, _ = select.select([self._notify_r, wake], [], [])
            except (ValueError, OSError):
                return self._finish(self._chunks.get())
            if self._notify_r in readable:
                _drain(self._notify_r)
                queued = self._pop()
                if queued is not None:
                    return self._finish(queued)
            if wake in readable:
                # Leave the wake byte in place when stdin already has a chunk
                # so the next read still observes shutdown.
                queued = self._pop()
                if queued is not None:
                    return self._finish(queued)
                _drain(wake)
                return None


def _drain(wake: Wake) -> None:
    try:
        if isinstance(wake, socket.socket):
            while wake.recv(64):
                pass
            return
        while os.read(wake, 64):
            pass
    except (BlockingIOError, OSError):
        return


_carriers: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def write_frame(fp: TextIO, payload: str) -> None:
    """Write one NDJSON line under the stdout lock. ``payload`` has no newline."""
    if not isinstance(payload, str):
        raise FramingError("frame must be a string")
    _reject_embedded_newline(payload)
    with STDOUT_LOCK:
        fp.write(payload + "\n")
        fp.flush()


def write_message(fp: TextIO, message: Mapping[str, Any]) -> None:
    """Write one JSON-RPC object as a single NDJSON line."""
    write_frame(fp, dumps_message(message))


# POSIX atomic pipe write. A larger progress frame is skipped, not split.
_PIPE_BUF = 4096


def try_write_message(fp: TextIO, message: Mapping[str, Any]) -> bool:
    """Write one frame unless that would block. False means nothing was written.

    Progress uses this so a full stdout pipe cannot hold ``STDOUT_LOCK`` inside
    ``flush``. A FIFO write of at most ``_PIPE_BUF`` bytes is all-or-nothing.
    Responses still use ``write_message``.
    """
    payload = dumps_message(message)
    data = (payload + "\n").encode("utf-8")
    with STDOUT_LOCK:
        fd = _fileno(fp)
        if fd is None or not _is_fifo(fd):
            fp.write(payload + "\n")
            fp.flush()
            return True
        if len(data) > _PIPE_BUF:
            return False
        return _write_fifo_nowait(fp, fd, data)


def _is_fifo(fd: int) -> bool:
    try:
        return stat.S_ISFIFO(os.fstat(fd).st_mode)
    except OSError:
        return False


def _write_fifo_nowait(fp: TextIO, fd: int, data: bytes) -> bool:
    import fcntl

    fp.flush()
    try:
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    except OSError:
        fp.write(data.decode("utf-8"))
        fp.flush()
        return True
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    try:
        while True:
            try:
                written = os.write(fd, data)
            except InterruptedError:
                continue
            except BlockingIOError:
                return False
            # A short count is already on the wire. Do not write the frame again.
            return written == len(data) or written > 0
    finally:
        try:
            fcntl.fcntl(fd, fcntl.F_SETFL, flags)
        except OSError:
            pass


def _reject_embedded_newline(text: str) -> None:
    if "\n" in text or "\r" in text:
        raise FramingError("embedded newline")
