# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from memory.proxy.policy import ProxyNotReady, assert_ready


def extract_json_object(text: str) -> Dict[str, Any]:
    """
    Find and parse the last JSON object embedded in free-form text.

    Prefer a real decode from each ``{`` (handles nested braces); fall back
    to a greedy regex match if needed.
    """
    if not text:
        raise ValueError("no JSON object in adapter output")
    decoder = json.JSONDecoder()
    last: Optional[Dict[str, Any]] = None
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "{":
            try:
                obj, end = decoder.raw_decode(text, i)
                if isinstance(obj, dict):
                    last = obj
                i = end
                continue
            except json.JSONDecodeError:
                pass
        i += 1
    if last is not None:
        return last
    matches = list(re.finditer(r"\{[\s\S]*\}", text))
    if not matches:
        raise ValueError("no JSON object in adapter output")
    return json.loads(matches[-1].group(0))


def apply_proxy_env(env: Dict[str, str], workdir: Path) -> Dict[str, str]:
    """Маршрут Grok CLI: required — локальный хоп; off — без Agentix URL."""
    from memory.proxy.config import (
        DEFAULT_GATEWAY_BASE,
        DEFAULT_INSTALL_CHAT_PROXY,
        effective_mode,
        load_proxy_config,
    )

    pcfg = load_proxy_config(workdir)
    mode = effective_mode(pcfg)
    gateway = str(pcfg.get("gateway_base") or DEFAULT_GATEWAY_BASE)
    chat = str(pcfg.get("chat_proxy") or DEFAULT_INSTALL_CHAT_PROXY)
    if mode == "off":
        env.pop("GROK_CLI_CHAT_PROXY_BASE_URL", None)
        env.pop("AGENTIX_GATEWAY_URL", None)
        return env
    if mode == "required":
        env["GROK_CLI_CHAT_PROXY_BASE_URL"] = chat
        env["AGENTIX_GATEWAY_URL"] = gateway
        return env
    env.setdefault("GROK_CLI_CHAT_PROXY_BASE_URL", chat)
    env.setdefault("AGENTIX_GATEWAY_URL", gateway)
    return env


class GrokAdapter:
    name = "grok"

    def __init__(self, cfg: dict | None = None) -> None:
        self.cfg = cfg or {}
        self.command = self.cfg.get("command") or "grok"

    def run_role_turn(
        self,
        role: str,
        prompt: str,
        handoff_in_path: Optional[Path],
        workdir: Path,
        timeout_s: int,
    ) -> Path:
        if not self.command:
            raise RuntimeError(
                "grok adapter not configured in project_config.supervisor.adapters.grok"
            )
        if not shutil.which(self.command):
            raise RuntimeError(f"{self.command} not on PATH")
        assert_ready(workdir, adapter_name="grok")
        env = os.environ.copy()
        env["AGENTIX_PROJECT_ROOT"] = str(Path(workdir).resolve())
        try:
            apply_proxy_env(env, workdir)
        except ProxyNotReady:
            raise
        except Exception as exc:
            raise ProxyNotReady(f"proxy env: {exc}") from exc
        # grok --help: -p/--single PROMPT for single-turn stdout; cwd via subprocess
        cmd = [self.command, "-p", prompt]
        r = subprocess.run(
            cmd,
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=env,
        )
        combined = (r.stdout or "") + "\n" + (r.stderr or "")
        if r.returncode != 0 and not combined.strip():
            raise RuntimeError(
                f"grok failed rc={r.returncode}: {(r.stderr or '')[:500]}"
            )
        data = extract_json_object(combined)
        out = Path(workdir) / ".agent" / "last_handoff.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return out
