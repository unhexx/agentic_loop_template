# -*- coding: utf-8 -*-
"""Атомарная запись last_handoff.json (tmp+replace)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def save_handoff(workdir: Path, data: Dict[str, Any]) -> Path:
    """Пишет last_handoff.json через tmp+replace, чтобы не отдавать оборванный JSON."""
    agent = Path(workdir) / ".agent"
    agent.mkdir(parents=True, exist_ok=True)
    p = agent / "last_handoff.json"
    text = json.dumps(data, ensure_ascii=False, indent=2)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)
    return p
