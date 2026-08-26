# -*- coding: utf-8 -*-
"""Адаптер CLI Blackbox: поиск бинарника, отсев оконного менеджера, вызов через run_cli."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from memory.logutil import get_logger
from memory.stream_context import apply_stream_env

from .grok import extract_handoff
from .persist import persist_role_handoff
from .proc import CliTimeoutError, run_cli

_NOT_CONFIGURED = (
    "blackbox adapter not configured in project_config.supervisor.adapters.blackbox"
)

_WM_MARKERS = (
    "sean 'shaleh' perry",
    "bradley t hughes",
    "blackbox 0.77",
    "-display",
)
_AI_MARKERS = (
    "blackbox cli",
    "headless",
    "configure",
    "session",
    "blackbox run",
)

_INSTALL_HINT = (
    "Install: curl -fsSL https://blackbox.ai/install.sh | bash"
)


def _probe_help(path: str, timeout_s: float = 3.0) -> str:
    r = subprocess.run(
        [path, "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
        stdin=subprocess.DEVNULL,
        env={**os.environ, "TERM": "dumb", "NO_COLOR": "1", "CI": "true"},
    )
    return ((r.stdout or "") + "\n" + (r.stderr or "")).lower()


def looks_like_window_manager(help_text: str) -> bool:
    t = help_text.lower()
    return any(m in t for m in _WM_MARKERS)


def looks_like_ai_cli(help_text: str) -> bool:
    t = help_text.lower()
    if looks_like_window_manager(t):
        return False
    return any(m in t for m in _AI_MARKERS)


def _is_explicit_path(command: str) -> bool:
    if "/" in command:
        return True
    if os.sep in command:
        return True
    if os.altsep and os.altsep in command:
        return True
    return False


def _exe_in_dir(directory: Path, name: str) -> Optional[str]:
    if sys.platform == "win32":
        return shutil.which(name, path=str(directory))
    cand = directory / name
    try:
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    except OSError:
        return None
    return None


def _code_default_search_dirs(exe_name: str) -> list[Path]:
    home = Path.home()
    dirs = [home / ".local" / "bin"]
    # не вызываем node cli.js — только готовый файл с именем blackbox
    for extra in (home / ".blackbox" / "bin", home / ".blackbox-cli-v2"):
        if _exe_in_dir(extra, exe_name):
            dirs.append(extra)
    return dirs


def _search_dirs(command: str, cfg: dict | None) -> list[Path]:
    raw = None if cfg is None else cfg.get("search_paths")
    name = Path(command).name or command
    if raw is None or not isinstance(raw, list):
        return _code_default_search_dirs(name)
    if len(raw) == 0:
        return []
    return [Path(os.path.expanduser(str(p))) for p in raw]


def _wm_error(path: str) -> RuntimeError:
    return RuntimeError(
        f"Found {path} but it is the X11 window manager (Blackbox 0.77), "
        f"not Blackbox AI CLI. {_INSTALL_HINT}  and put the AI CLI on PATH "
        f"ahead of /usr/bin (e.g. ~/.local/bin)."
    )


def _neither_error(paths: list[str]) -> RuntimeError:
    shown = ", ".join(paths[:4])
    return RuntimeError(
        f"Found {shown} but none look like Blackbox AI CLI (not the X11 WM). "
        f"{_INSTALL_HINT}"
    )


def _classify_candidates(candidates: list[str], command: str) -> str:
    wm_paths: list[str] = []
    neither_paths: list[str] = []
    for path in candidates:
        try:
            text = _probe_help(path)
        except (subprocess.TimeoutExpired, OSError, UnicodeDecodeError):
            # файл был — это не «нет на PATH»
            neither_paths.append(path)
            continue
        if looks_like_window_manager(text):
            wm_paths.append(path)
            continue
        if looks_like_ai_cli(text):
            return path
        neither_paths.append(path)
    if wm_paths:
        raise _wm_error(wm_paths[0])
    if neither_paths:
        raise _neither_error(neither_paths)
    raise RuntimeError(f"{command} not on PATH")


def resolve_blackbox_command(command: str, cfg: dict | None = None) -> str:
    """Путь к CLI; отказ, если это оконный менеджер или посторонний бинарник."""
    if _is_explicit_path(command):
        path = os.path.expanduser(command)
        if sys.platform == "win32" and not os.path.isfile(path):
            found = shutil.which(Path(path).name, path=str(Path(path).parent) or ".")
            if not found:
                raise RuntimeError(f"{command} not on PATH")
            path = found
        elif not os.path.isfile(path):
            raise RuntimeError(f"{command} not on PATH")
        return _classify_candidates([path], command)

    name = Path(command).name or command
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(p: str) -> None:
        key = os.path.normcase(os.path.abspath(p))
        if key in seen:
            return
        seen.add(key)
        candidates.append(p)

    for directory in _search_dirs(command, cfg):
        found = _exe_in_dir(directory, name)
        if found:
            _add(found)
    which = shutil.which(command)
    if which:
        _add(which)
    return _classify_candidates(candidates, command)


def _child_env(workdir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["AGENTIX_PROJECT_ROOT"] = str(Path(workdir).resolve())
    env.setdefault("BLACKBOX_NONINTERACTIVE", "1")
    env.setdefault("CI", "true")
    env.setdefault("TERM", "dumb")
    env.setdefault("NO_COLOR", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return apply_stream_env(env)


class BlackboxAdapter:
    name = "blackbox"

    def __init__(self, cfg: dict | None = None) -> None:
        self.cfg = cfg or {}
        # null в конфиге — отказ; нет ключа даёт "blackbox"
        raw = self.cfg.get("command", "blackbox")
        self.command = raw
        self.prompt_mode = str(self.cfg.get("prompt_mode") or "p").strip().lower()
        extra = self.cfg.get("extra_args") or []
        self.extra_args = [str(x) for x in extra] if isinstance(extra, list) else []
        # None и [] различаем: пустой список отключает ~/.local/bin
        self.search_paths = self.cfg.get("search_paths")
        # повтор хода роли не должен снова звать --help
        self._resolved: str | None = None

    def _resolve(self) -> str:
        if self._resolved is None:
            self._resolved = resolve_blackbox_command(str(self.command), self.cfg)
        return self._resolved

    def _build_cmd(self, resolved: str, prompt: str, workdir: Path) -> list[str]:
        mode = self.prompt_mode
        extra = list(self.extra_args)
        if mode in ("p", "-p", "headless"):
            return [resolved, *extra, "-p", prompt]
        if mode == "positional":
            return [resolved, *extra, prompt]
        if mode == "run":
            prompt_file = Path(workdir) / ".agent" / "blackbox_prompt.txt"
            prompt_file.parent.mkdir(parents=True, exist_ok=True)
            prompt_file.write_text(prompt, encoding="utf-8")
            try:
                prompt_file.chmod(0o600)
            except OSError:
                pass  # нет chmod — права по umask; в индекс не попадёт
            return [resolved, *extra, "run", str(prompt_file)]
        raise RuntimeError(f"unknown blackbox prompt_mode={mode!r}")

    def run_role_turn(
        self,
        role: str,
        prompt: str,
        handoff_in_path: Optional[Path],
        workdir: Path,
        timeout_s: int,
    ) -> Path:
        log = get_logger("memory.adapters")
        if not self.command:
            raise RuntimeError(_NOT_CONFIGURED)
        resolved = self._resolve()
        cmd = self._build_cmd(resolved, prompt, Path(workdir))
        # в лог — путь к бинарнику и режим, не тело промпта
        log.info(
            "blackbox spawn exe=%s mode=%s extra=%s timeout_s=%s",
            resolved,
            self.prompt_mode,
            len(self.extra_args),
            timeout_s,
        )
        env = _child_env(Path(workdir))
        t0 = time.monotonic()
        try:
            r = run_cli(cmd, cwd=Path(workdir), timeout_s=timeout_s, env=env)
        except CliTimeoutError:
            log.warning("blackbox timed out after %ss exe=%s", timeout_s, resolved)
            raise RuntimeError(f"blackbox timed out after {timeout_s}s") from None
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        combined = (r.stdout or "") + "\n" + (r.stderr or "")
        log.info(
            "blackbox rc=%s bytes=%s elapsed_ms=%s exe=%s",
            r.returncode,
            len(combined),
            elapsed_ms,
            resolved,
        )
        if r.returncode != 0 and not combined.strip():
            # stderr в сообщение не кладём — там могут быть ключи
            raise RuntimeError(f"blackbox failed rc={r.returncode}")
        data = extract_handoff(combined)
        return persist_role_handoff(workdir, data)
