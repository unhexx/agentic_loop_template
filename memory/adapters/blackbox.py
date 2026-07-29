# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from .grok import extract_json_object


class BlackboxAdapter:
    name = "blackbox"

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
                "blackbox adapter not configured in project_config.supervisor.adapters.blackbox"
            )
        if not shutil.which(str(self.command)):
            raise RuntimeError(f"{self.command} not on PATH")
        cmd = [str(self.command), "-p", prompt]
        r = subprocess.run(
            cmd,
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        combined = (r.stdout or "") + "\n" + (r.stderr or "")
        if r.returncode != 0 and not combined.strip():
            raise RuntimeError(
                f"blackbox failed rc={r.returncode}: {(r.stderr or '')[:500]}"
            )
        data = extract_json_object(combined)
        out = Path(workdir) / ".agent" / "last_handoff.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return out
