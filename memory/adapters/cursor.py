# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from memory.stream_context import apply_stream_env

from .grok import extract_handoff
from .persist import persist_role_handoff


class CursorAdapter:
    name = "cursor"

    def __init__(self, cfg: dict | None = None) -> None:
        self.cfg = cfg or {}
        self.command = self.cfg.get("command")

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
                "cursor adapter not configured in project_config.supervisor.adapters.cursor"
            )
        if not shutil.which(str(self.command)):
            raise RuntimeError(f"{self.command} not on PATH")
        cmd = [str(self.command), "-p", prompt]
        env = apply_stream_env(os.environ.copy())
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
                f"cursor failed rc={r.returncode}: {(r.stderr or '')[:500]}"
            )
        data = extract_handoff(combined)
        return persist_role_handoff(workdir, data)
