# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from memory.proxy.policy import assert_ready


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
        # Живой адаптер не ходит в публичный апстрим, пока pxpipe молчит.
        assert_ready(workdir, adapter_name="grok")
        env = os.environ.copy()
        env["AGENTIX_PROJECT_ROOT"] = str(Path(workdir).resolve())
        try:
            from memory.proxy.config import effective_mode, load_proxy_config

            pcfg = load_proxy_config(workdir)
            if effective_mode(pcfg) != "off":
                env.setdefault(
                    "GROK_CLI_CHAT_PROXY_BASE_URL",
                    str(pcfg.get("chat_proxy") or "http://127.0.0.1:8110/v1"),
                )
                env.setdefault(
                    "AGENTIX_GATEWAY_URL",
                    str(pcfg.get("gateway_base") or "http://127.0.0.1:8110"),
                )
        except Exception:
            pass
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
