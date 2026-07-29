# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import Optional, Protocol


class RoleAdapter(Protocol):
    name: str

    def run_role_turn(
        self,
        role: str,
        prompt: str,
        handoff_in_path: Optional[Path],
        workdir: Path,
        timeout_s: int,
    ) -> Path:
        """Run one role turn; write handoff JSON; return its path."""
        ...
