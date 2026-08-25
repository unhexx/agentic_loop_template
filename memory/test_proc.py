# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from memory.adapters.proc import CliTimeoutError, run_cli


def _write_script(tmp_path: Path, name: str, source: str) -> Path:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


def _pid_alive_win32(pid: int) -> bool:
    import ctypes

    # PROCESS_QUERY_LIMITED_INFORMATION; STILL_ACTIVE
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        return bool(ok) and code.value == 259
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _assert_pid_dead(pid: int, timeout_s: float = 2.0) -> None:
    # kill(pid,0) проходит на зомби, пока init не wait
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if sys.platform == "win32":
            if not _pid_alive_win32(pid):
                return
        else:
            try:
                os.kill(pid, 0)
            except (OSError, ProcessLookupError):
                return
        time.sleep(0.05)
    if sys.platform == "win32":
        raise AssertionError(f"pid {pid} ещё жив")
    with pytest.raises((OSError, ProcessLookupError)):
        os.kill(pid, 0)


def test_run_cli_ok_returns_completed_process(tmp_path: Path) -> None:
    script = _write_script(
        tmp_path,
        "ok.py",
        "import sys\nprint('hello')\nsys.exit(0)\n",
    )
    r = run_cli([sys.executable, str(script)], cwd=tmp_path, timeout_s=10)
    assert r.returncode == 0
    assert "hello" in (r.stdout or "")


def test_run_cli_nonzero_keeps_stdout(tmp_path: Path) -> None:
    script = _write_script(
        tmp_path,
        "fail.py",
        "import sys\nprint('nope')\nsys.exit(2)\n",
    )
    r = run_cli([sys.executable, str(script)], cwd=tmp_path, timeout_s=10)
    assert r.returncode == 2
    assert "nope" in (r.stdout or "")


def test_run_cli_uses_cwd(tmp_path: Path) -> None:
    script = _write_script(
        tmp_path,
        "cwd.py",
        "import os\nprint(os.getcwd())\n",
    )
    r = run_cli([sys.executable, str(script)], cwd=tmp_path, timeout_s=10)
    assert r.returncode == 0
    assert Path(r.stdout.strip()).resolve() == tmp_path.resolve()


def test_run_cli_env_override(tmp_path: Path) -> None:
    script = _write_script(
        tmp_path,
        "env.py",
        "import os\nprint(os.environ['MARKER'])\n",
    )
    env = {**os.environ, "MARKER": "from-caller"}
    r = run_cli(
        [sys.executable, str(script)],
        cwd=tmp_path,
        timeout_s=10,
        env=env,
    )
    assert r.returncode == 0
    assert "from-caller" in (r.stdout or "")


@pytest.mark.skipif(sys.platform == "win32", reason="killpg только POSIX")
def test_run_cli_timeout_kills_process_group(tmp_path: Path) -> None:
    pid_path = tmp_path / "pids.json"
    script = _write_script(
        tmp_path,
        "hang_group.py",
        "import json, os, time\n"
        "child = os.fork()\n"
        "if child == 0:\n"
        "    time.sleep(30)\n"
        "    os._exit(0)\n"
        "path = os.environ['FAKE_PID_PATH']\n"
        "with open(path, 'w') as f:\n"
        "    json.dump({'parent': os.getpid(), 'child': child}, f)\n"
        "    f.flush()\n"
        "    os.fsync(f.fileno())\n"
        "time.sleep(30)\n",
    )
    env = {**os.environ, "FAKE_PID_PATH": str(pid_path)}
    t0 = time.monotonic()
    with pytest.raises(CliTimeoutError):
        run_cli(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout_s=1,
            env=env,
        )
    assert time.monotonic() - t0 < 10
    data = json.loads(pid_path.read_text(encoding="utf-8"))
    _assert_pid_dead(int(data["parent"]))
    _assert_pid_dead(int(data["child"]))


@pytest.mark.skipif(sys.platform == "win32", reason="killpg только POSIX")
def test_run_cli_timeout_sigkill_grandchild_ignoring_sigterm(tmp_path: Path) -> None:
    pid_path = tmp_path / "pids.json"
    script = _write_script(
        tmp_path,
        "hang_ign.py",
        "import json, os, signal, time\n"
        "child = os.fork()\n"
        "if child == 0:\n"
        "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "    time.sleep(30)\n"
        "    os._exit(0)\n"
        "path = os.environ['FAKE_PID_PATH']\n"
        "with open(path, 'w') as f:\n"
        "    json.dump({'parent': os.getpid(), 'child': child}, f)\n"
        "    f.flush()\n"
        "    os.fsync(f.fileno())\n"
        "time.sleep(30)\n",
    )
    env = {**os.environ, "FAKE_PID_PATH": str(pid_path)}
    t0 = time.monotonic()
    with pytest.raises(CliTimeoutError):
        run_cli(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout_s=1,
            env=env,
        )
    assert time.monotonic() - t0 < 10
    data = json.loads(pid_path.read_text(encoding="utf-8"))
    _assert_pid_dead(int(data["parent"]))
    _assert_pid_dead(int(data["child"]))


def test_run_cli_timeout_kills_direct_child(tmp_path: Path) -> None:
    pid_path = tmp_path / "pids.json"
    script = _write_script(
        tmp_path,
        "hang.py",
        "import json, os, time\n"
        "path = os.environ['FAKE_PID_PATH']\n"
        "with open(path, 'w') as f:\n"
        "    json.dump({'parent': os.getpid()}, f)\n"
        "    f.flush()\n"
        "    os.fsync(f.fileno())\n"
        "time.sleep(30)\n",
    )
    env = {**os.environ, "FAKE_PID_PATH": str(pid_path)}
    t0 = time.monotonic()
    with pytest.raises(CliTimeoutError):
        run_cli(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout_s=1,
            env=env,
        )
    assert time.monotonic() - t0 < 10
    data = json.loads(pid_path.read_text(encoding="utf-8"))
    _assert_pid_dead(int(data["parent"]))


def test_run_cli_stdin_closed(tmp_path: Path) -> None:
    script = _write_script(
        tmp_path,
        "stdin.py",
        "import sys\n"
        "data = sys.stdin.read()\n"
        "raise SystemExit(0 if data == '' else 1)\n",
    )
    r = run_cli([sys.executable, str(script)], cwd=tmp_path, timeout_s=2)
    assert r.returncode == 0


def test_run_cli_rejects_nonpositive_timeout(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        run_cli([sys.executable, "-c", "pass"], cwd=tmp_path, timeout_s=0)
