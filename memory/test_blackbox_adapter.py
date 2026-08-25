# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from pathlib import Path

import pytest

from memory.adapters import get_adapter
from memory.adapters.blackbox import (
    BlackboxAdapter,
    _probe_help,
    looks_like_ai_cli,
)
from memory.adapters.grok import HandoffExtractError

_SHEBANG = pytest.mark.skipif(sys.platform == "win32", reason="shebang PATH fake")

_AI_HELP = "Blackbox CLI\nconfigure\nheadless mode (-p)\nsession\nblackbox run <file>\n"
_WM_HELP = "Blackbox 0.77\nSean 'Shaleh' Perry\n-display\n-rc\n"
_NEITHER_HELP = "bb command [type] [options]\n"


def _valid_in_progress(**overrides):
    data = {
        "handoff_to": "Coder",
        "role": "Orchestrator",
        "current_phase": "planning",
        "cycle_number": 1,
        "summary": "plan",
        "status": "IN_PROGRESS",
        "confidence": 0.9,
    }
    data.update(overrides)
    return data


def _isolate_home(tmp_path: Path, monkeypatch) -> Path:
    fakehome = tmp_path / "fakehome"
    fakehome.mkdir(exist_ok=True)
    home_str = str(fakehome)

    def _home(cls):
        return cls(home_str)

    monkeypatch.setenv("HOME", home_str)
    monkeypatch.setattr(Path, "home", classmethod(_home))
    return fakehome


def _write_fake(
    dest: Path,
    *,
    help_text: str,
    stdout: str = "",
    rc: int = 0,
    sleep_s: float = 0,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    source = f"""#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import os
import sys
import time

HELP = {json.dumps(help_text)}
STDOUT = {json.dumps(stdout)}
RC = {int(rc)}
SLEEP = {float(sleep_s)}

argv_path = os.environ.get("FAKE_ARGV_PATH")
if argv_path:
    payload = {{"exe": os.path.realpath(sys.argv[0]), "args": sys.argv[1:]}}
    with open(argv_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)

if len(sys.argv) > 1 and sys.argv[1] == "--help":
    sys.stdout.write(HELP)
    sys.exit(0)

env_path = os.environ.get("FAKE_ENV_PATH")
if env_path:
    keys = [
        "BLACKBOX_NONINTERACTIVE",
        "CI",
        "TERM",
        "NO_COLOR",
        "AGENTIX_PROJECT_ROOT",
        "PYTHONIOENCODING",
    ]
    with open(env_path, "w", encoding="utf-8") as fh:
        json.dump({{k: os.environ.get(k) for k in keys}}, fh)

if SLEEP:
    time.sleep(SLEEP)

sys.stdout.write(STDOUT)
sys.exit(RC)
"""
    dest.write_text(source, encoding="utf-8")
    dest.chmod(0o755)
    return dest


def _install_fake_cli(
    tmp_path: Path,
    monkeypatch,
    mode: str,
    help_text=None,
    stdout=None,
    rc: int = 0,
    sleep_s: float = 0,
):
    _isolate_home(tmp_path, monkeypatch)
    if help_text is None:
        if mode == "wm":
            help_text = _WM_HELP
        elif mode == "neither":
            help_text = _NEITHER_HELP
        else:
            help_text = _AI_HELP
    if stdout is None:
        if mode == "invalid_json":
            stdout = "{not json"
        elif mode in ("empty_fail", "hang", "wm", "neither"):
            stdout = ""
        else:
            stdout = json.dumps(_valid_in_progress())
    if mode == "hang" and not sleep_s:
        sleep_s = 30.0
    if mode == "empty_fail" and rc == 0:
        rc = 1
    fake = _write_fake(
        tmp_path / "bin" / "blackbox",
        help_text=help_text,
        stdout=stdout,
        rc=rc,
        sleep_s=sleep_s,
    )
    argv_path = tmp_path / "fake_argv.json"
    monkeypatch.setenv("FAKE_ARGV_PATH", str(argv_path))
    monkeypatch.setenv("FAKE_MODE", mode)
    monkeypatch.setenv("PATH", str(fake.parent) + os.pathsep + os.environ.get("PATH", ""))
    return fake, argv_path


def _load_argv(argv_path: Path) -> tuple[str | None, list]:
    data = json.loads(argv_path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return None, data
    return data.get("exe"), list(data.get("args") or [])


def _turn(ad, workdir: Path, prompt: str = "plan this", timeout_s: int = 15) -> Path:
    return ad.run_role_turn(
        role="Orchestrator",
        prompt=prompt,
        handoff_in_path=None,
        workdir=workdir,
        timeout_s=timeout_s,
    )


def _handoff_path(workdir: Path) -> Path:
    return Path(workdir) / ".agent" / "last_handoff.json"


def test_blackbox_default_command_is_blackbox():
    assert BlackboxAdapter({}).command == "blackbox"
    assert BlackboxAdapter({"command": None}).command is None


@_SHEBANG
def test_blackbox_explicit_null_command_raises_not_configured(tmp_path, monkeypatch):
    _install_fake_cli(tmp_path, monkeypatch, "ok")
    argv_path = tmp_path / "fake_argv.json"
    ad = BlackboxAdapter({"command": None})
    with pytest.raises(RuntimeError, match="not configured"):
        _turn(ad, tmp_path)
    assert not argv_path.exists()


def test_blackbox_missing_binary_raises(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    monkeypatch.setenv("PATH", str(tmp_path / "emptybin"))
    ad = BlackboxAdapter({"command": "blackbox-not-a-real-bin"})
    with pytest.raises(RuntimeError, match="not on PATH"):
        _turn(ad, tmp_path)


@_SHEBANG
def test_blackbox_rejects_x11_window_manager_on_path(tmp_path, monkeypatch):
    _install_fake_cli(tmp_path, monkeypatch, "wm")
    ad = BlackboxAdapter({"command": "blackbox"})
    with pytest.raises(RuntimeError, match="window manager") as ei:
        _turn(ad, tmp_path)
    msg = str(ei.value)
    assert "install.sh" in msg
    assert "not on PATH" not in msg
    assert not _handoff_path(tmp_path).exists()


@_SHEBANG
def test_blackbox_rejects_neither_wm_nor_ai(tmp_path, monkeypatch):
    _install_fake_cli(tmp_path, monkeypatch, "neither")
    ad = BlackboxAdapter({"command": "blackbox"})
    with pytest.raises(RuntimeError, match="none look like") as ei:
        _turn(ad, tmp_path)
    msg = str(ei.value)
    assert "install.sh" in msg
    assert "not on PATH" not in msg


@_SHEBANG
def test_blackbox_empty_search_paths_skips_defaults(tmp_path, monkeypatch):
    fakehome = _isolate_home(tmp_path, monkeypatch)
    ai = _write_fake(
        fakehome / ".local" / "bin" / "blackbox",
        help_text=_AI_HELP,
        stdout=json.dumps(_valid_in_progress()),
    )
    wm = _write_fake(
        tmp_path / "bin" / "blackbox",
        help_text=_WM_HELP,
        stdout="",
    )
    monkeypatch.setenv("FAKE_ARGV_PATH", str(tmp_path / "fake_argv.json"))
    monkeypatch.setenv("PATH", str(wm.parent) + os.pathsep + os.environ.get("PATH", ""))
    ad = BlackboxAdapter({"command": "blackbox", "search_paths": []})
    with pytest.raises(RuntimeError, match="window manager"):
        _turn(ad, tmp_path)
    assert not _handoff_path(tmp_path).exists()
    # бинарник из домашнего каталога не должен вызываться
    argv_path = tmp_path / "fake_argv.json"
    if argv_path.exists():
        exe, _args = _load_argv(argv_path)
        if exe:
            assert Path(exe).resolve() != ai.resolve()


@_SHEBANG
def test_blackbox_prefers_search_path_ai_cli_over_usr_wm(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    ai = _write_fake(
        tmp_path / "ai_dir" / "blackbox",
        help_text=_AI_HELP,
        stdout=json.dumps(_valid_in_progress()),
    )
    _write_fake(
        tmp_path / "bin" / "blackbox",
        help_text=_WM_HELP,
        stdout="",
    )
    argv_path = tmp_path / "fake_argv.json"
    monkeypatch.setenv("FAKE_ARGV_PATH", str(argv_path))
    monkeypatch.setenv(
        "PATH", str(tmp_path / "bin") + os.pathsep + os.environ.get("PATH", "")
    )
    ad = BlackboxAdapter({"command": "blackbox", "search_paths": [str(ai.parent)]})
    out = _turn(ad, tmp_path)
    assert out.is_file()
    exe, _args = _load_argv(argv_path)
    assert exe is not None
    assert Path(exe).resolve() == ai.resolve()


@_SHEBANG
def test_blackbox_invokes_dash_p_by_default(tmp_path, monkeypatch):
    _install_fake_cli(tmp_path, monkeypatch, "ok")
    ad = BlackboxAdapter({"command": "blackbox"})
    prompt = "plan this"
    out = _turn(ad, tmp_path, prompt=prompt)
    _exe, args = _load_argv(tmp_path / "fake_argv.json")
    assert args == ["-p", prompt]
    assert _handoff_path(tmp_path).is_file()
    assert out == _handoff_path(tmp_path)
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["status"] == "IN_PROGRESS"


@_SHEBANG
def test_blackbox_extra_args_inserted_before_flag(tmp_path, monkeypatch):
    _install_fake_cli(tmp_path, monkeypatch, "ok")
    ad = BlackboxAdapter({"command": "blackbox", "extra_args": ["--yolo"]})
    prompt = "plan this"
    _turn(ad, tmp_path, prompt=prompt)
    _exe, args = _load_argv(tmp_path / "fake_argv.json")
    assert args[0] == "--yolo"
    assert args[1] == "-p"
    assert args[2] == prompt


@_SHEBANG
def test_blackbox_prompt_mode_run_writes_file_and_argv(tmp_path, monkeypatch):
    _install_fake_cli(tmp_path, monkeypatch, "ok")
    ad = BlackboxAdapter({"command": "blackbox", "prompt_mode": "run"})
    prompt = "run from file please"
    out = _turn(ad, tmp_path, prompt=prompt)
    _exe, args = _load_argv(tmp_path / "fake_argv.json")
    assert "run" in args
    assert any(str(a).endswith("blackbox_prompt.txt") for a in args)
    pf = tmp_path / ".agent" / "blackbox_prompt.txt"
    assert pf.read_text(encoding="utf-8") == prompt
    assert out.is_file()


@_SHEBANG
def test_blackbox_prompt_mode_positional_argv(tmp_path, monkeypatch):
    _install_fake_cli(tmp_path, monkeypatch, "ok")
    ad = BlackboxAdapter({"command": "blackbox", "prompt_mode": "positional"})
    prompt = "just the prompt"
    _turn(ad, tmp_path, prompt=prompt)
    _exe, args = _load_argv(tmp_path / "fake_argv.json")
    assert args == [prompt]
    assert "-p" not in args
    assert "run" not in args


@_SHEBANG
def test_blackbox_unknown_prompt_mode_raises(tmp_path, monkeypatch):
    _install_fake_cli(tmp_path, monkeypatch, "ok")
    ad = BlackboxAdapter({"command": "blackbox", "prompt_mode": "nope"})
    with pytest.raises(RuntimeError, match="prompt_mode"):
        _turn(ad, tmp_path)


@_SHEBANG
def test_blackbox_nonzero_empty_output_raises(tmp_path, monkeypatch):
    _install_fake_cli(tmp_path, monkeypatch, "empty_fail", stdout="", rc=1)
    ad = BlackboxAdapter({"command": "blackbox"})
    with pytest.raises(RuntimeError, match="failed rc=1"):
        _turn(ad, tmp_path)
    assert not _handoff_path(tmp_path).exists()


@_SHEBANG
def test_blackbox_nonzero_with_handoff_json_persists(tmp_path, monkeypatch):
    payload = _valid_in_progress()
    stdout = "noise before\n" + json.dumps(payload) + "\nnoise after"
    _install_fake_cli(tmp_path, monkeypatch, "ok", stdout=stdout, rc=1)
    ad = BlackboxAdapter({"command": "blackbox"})
    out = _turn(ad, tmp_path)
    assert out.is_file()
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["status"] == "IN_PROGRESS"


@_SHEBANG
def test_blackbox_timeout_raises_and_does_not_write_handoff(tmp_path, monkeypatch):
    _install_fake_cli(tmp_path, monkeypatch, "hang", sleep_s=30)
    ad = BlackboxAdapter({"command": "blackbox"})
    with pytest.raises(RuntimeError, match="timed out"):
        _turn(ad, tmp_path, timeout_s=1)
    assert not _handoff_path(tmp_path).exists()


@_SHEBANG
def test_blackbox_invalid_json_does_not_clobber_last_handoff(tmp_path, monkeypatch):
    _install_fake_cli(tmp_path, monkeypatch, "invalid_json", stdout="{not json")
    target = _handoff_path(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text('{"keep": true}', encoding="utf-8")
    ad = BlackboxAdapter({"command": "blackbox"})
    with pytest.raises(HandoffExtractError):
        _turn(ad, tmp_path)
    assert json.loads(target.read_text(encoding="utf-8")) == {"keep": True}


@_SHEBANG
def test_blackbox_valid_handoff_in_prose_persists(tmp_path, monkeypatch):
    inner = json.dumps(_valid_in_progress())
    stdout = f"Here you go:\n{inner}\nthanks"
    _install_fake_cli(tmp_path, monkeypatch, "ok", stdout=stdout)
    ad = BlackboxAdapter({"command": "blackbox"})
    out = _turn(ad, tmp_path)
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["role"] == "Orchestrator"


@_SHEBANG
def test_blackbox_does_not_call_assert_ready(tmp_path, monkeypatch):
    import memory.adapters.blackbox as bbmod
    import memory.proxy.policy as policy

    assert not hasattr(bbmod, "assert_ready")

    def _boom(*_a, **_k):
        raise AssertionError("assert_ready must not be called")

    monkeypatch.setattr(policy, "assert_ready", _boom)
    _install_fake_cli(tmp_path, monkeypatch, "ok")
    ad = BlackboxAdapter({"command": "blackbox"})
    out = _turn(ad, tmp_path)
    assert out.is_file()


@_SHEBANG
def test_blackbox_sets_noninteractive_env(tmp_path, monkeypatch):
    _install_fake_cli(tmp_path, monkeypatch, "ok")
    for key in (
        "BLACKBOX_NONINTERACTIVE",
        "CI",
        "TERM",
        "NO_COLOR",
        "PYTHONIOENCODING",
    ):
        monkeypatch.delenv(key, raising=False)
    env_path = tmp_path / "fake_env.json"
    monkeypatch.setenv("FAKE_ENV_PATH", str(env_path))
    ad = BlackboxAdapter({"command": "blackbox"})
    _turn(ad, tmp_path)
    dumped = json.loads(env_path.read_text(encoding="utf-8"))
    assert dumped["BLACKBOX_NONINTERACTIVE"] == "1"
    assert dumped["CI"] == "true"
    assert dumped["TERM"] == "dumb"
    assert dumped["NO_COLOR"] == "1"
    assert dumped["AGENTIX_PROJECT_ROOT"] == str(tmp_path.resolve())


@_SHEBANG
def test_blackbox_does_not_log_prompt_or_api_key(tmp_path, monkeypatch, caplog):
    _install_fake_cli(tmp_path, monkeypatch, "ok")
    monkeypatch.setenv("BLACKBOX_API_KEY", "sk-secret-canary-key")
    ad = BlackboxAdapter({"command": "blackbox"})
    prompt = "CANARY_PROMPT_XYZ please plan"
    with caplog.at_level(logging.INFO, logger="memory.adapters"):
        _turn(ad, tmp_path, prompt=prompt)
    text = caplog.text
    assert "CANARY_PROMPT_XYZ" not in text
    assert "sk-secret-canary-key" not in text


def _live_ai_cli() -> bool:
    p = shutil.which("blackbox")
    if not p:
        return False
    try:
        text = _probe_help(p)
    except Exception:
        return False
    return looks_like_ai_cli(text)


@pytest.mark.skipif(not _live_ai_cli(), reason="Blackbox AI CLI not installed")
def test_blackbox_live_binary_on_path_no_role_turn():
    ad = get_adapter(
        "blackbox",
        {"supervisor": {"adapters": {"blackbox": {"command": "blackbox"}}}},
    )
    assert ad.name == "blackbox"
