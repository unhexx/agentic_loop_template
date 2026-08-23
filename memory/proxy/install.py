# -*- coding: utf-8 -*-
"""Export'ы в .venv activate; merge ~/.grok/config.toml — только install-host."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

from memory.proxy.config import DEFAULT_INSTALL_CHAT_PROXY, DEFAULT_PXPIPE_BASE

MARKER = "# agentix-proxy"
MARKER_END = "# end-agentix-proxy"


def _bash_block(chat_proxy: str, pxpipe: str) -> str:
    return (
        f"{MARKER}\n"
        f'export GROK_CLI_CHAT_PROXY_BASE_URL="${{GROK_CLI_CHAT_PROXY_BASE_URL:-{chat_proxy}}}"\n'
        f'export AGENTIX_PROXY="${{AGENTIX_PROXY:-1}}"\n'
        f'export AGENTIX_PXPIPE_URL="${{AGENTIX_PXPIPE_URL:-{pxpipe}}}"\n'
        f"{MARKER_END}\n"
    )


def _ps1_block(chat_proxy: str, pxpipe: str) -> str:
    return (
        f"{MARKER}\n"
        f'if (-not $env:GROK_CLI_CHAT_PROXY_BASE_URL) {{ $env:GROK_CLI_CHAT_PROXY_BASE_URL = "{chat_proxy}" }}\n'
        f'if (-not $env:AGENTIX_PROXY) {{ $env:AGENTIX_PROXY = "1" }}\n'
        f'if (-not $env:AGENTIX_PXPIPE_URL) {{ $env:AGENTIX_PXPIPE_URL = "{pxpipe}" }}\n'
        f"{MARKER_END}\n"
    )


def _upsert_block(text: str, block: str) -> str:
    """Идемпотентная вставка блока между маркерами."""
    pattern = re.compile(
        re.escape(MARKER) + r".*?" + re.escape(MARKER_END) + r"\n?",
        re.DOTALL,
    )
    if pattern.search(text):
        return pattern.sub(block, text, count=1)
    if MARKER in text and MARKER_END not in text:
        # старый кусок без закрывающего маркера — вырезаем до конца файла
        text = text[: text.index(MARKER)].rstrip() + "\n"
    if text and not text.endswith("\n"):
        text += "\n"
    return text + "\n" + block


def _write_if_changed(path: Path, new_text: str) -> bool:
    old = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    if old == new_text:
        return False
    path.write_text(new_text, encoding="utf-8")
    return True


def install_venv(
    root: Optional[Path] = None,
    chat_proxy_url: Optional[str] = None,
    pxpipe_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Дописываем export'ы в .venv/bin/activate и Scripts/Activate.ps1."""
    base = Path(root) if root is not None else Path.cwd()
    chat = (chat_proxy_url or DEFAULT_INSTALL_CHAT_PROXY).rstrip("/")
    pxpipe = (pxpipe_url or DEFAULT_PXPIPE_BASE).rstrip("/")
    written: list[str] = []
    skipped: list[str] = []

    bash = base / ".venv" / "bin" / "activate"
    if bash.is_file():
        text = bash.read_text(encoding="utf-8", errors="replace")
        new = _upsert_block(text, _bash_block(chat, pxpipe))
        if _write_if_changed(bash, new):
            written.append(str(bash))
        else:
            skipped.append(str(bash))
    ps1 = base / ".venv" / "Scripts" / "Activate.ps1"
    if ps1.is_file():
        text = ps1.read_text(encoding="utf-8", errors="replace")
        new = _upsert_block(text, _ps1_block(chat, pxpipe))
        if _write_if_changed(ps1, new):
            written.append(str(ps1))
        else:
            skipped.append(str(ps1))
    return {
        "ok": True,
        "written": written,
        "skipped": skipped,
        "chat_proxy_url": chat,
        "pxpipe_url": pxpipe,
        "root": str(base),
    }


def _merge_grok_toml(text: str, url: str) -> str:
    """Ставим cli_chat_proxy_base_url, не ломая остальные секции."""
    line = f'cli_chat_proxy_base_url = "{url}"'
    if re.search(r"(?m)^cli_chat_proxy_base_url\s*=", text):
        return re.sub(r"(?m)^cli_chat_proxy_base_url\s*=\s*.*$", line, text, count=1)
    if re.search(r"(?m)^\[endpoints\]\s*$", text):
        return re.sub(r"(?m)^\[endpoints\]\s*$", "[endpoints]\n" + line, text, count=1)
    if text and not text.endswith("\n"):
        text += "\n"
    return text + "\n[endpoints]\n" + line + "\n"


def install_host(
    config_path: Optional[Path] = None,
    url: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Опциональный merge ~/.grok/config.toml. Init это не вызывает."""
    path = config_path or Path.home() / ".grok" / "config.toml"
    target = (url or DEFAULT_INSTALL_CHAT_PROXY).rstrip("/")
    if not path.is_file():
        return {
            "ok": False,
            "error": f"нет файла {path}",
            "path": str(path),
            "dry_run": dry_run,
        }
    old = path.read_text(encoding="utf-8", errors="replace")
    new = _merge_grok_toml(old, target)
    changed = new != old
    if changed and not dry_run:
        path.write_text(new, encoding="utf-8")
    return {
        "ok": True,
        "path": str(path),
        "changed": changed,
        "dry_run": dry_run,
        "chat_proxy_url": target,
    }


def default_chat_proxy_url() -> str:
    return os.environ.get("GROK_CLI_CHAT_PROXY_BASE_URL") or DEFAULT_INSTALL_CHAT_PROXY
