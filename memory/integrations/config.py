# -*- coding: utf-8 -*-
"""Чтение opt-in флагов Linear/Jira/Slack из project_config.json."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from memory.logutil import get_logger

log = get_logger("memory.integrations")

_TRACKER_PROVIDERS = frozenset({"linear", "jira"})


def load_project_cfg(workdir: Path) -> Dict[str, Any]:
    """Тот же порядок, что у супервизора: json, затем example, иначе {}."""
    workdir = Path(workdir)
    for name in ("project_config.json", "project_config.example.json"):
        path = workdir / ".agent" / name
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            log.warning("integrations config unreadable %s: %s", path.name, exc)
            continue
        if isinstance(data, dict):
            return data
    return {}


def issue_tracker_section(cfg: Mapping[str, Any]) -> Dict[str, Any]:
    integ = cfg.get("integrations")
    if not isinstance(integ, dict):
        return {}
    section = integ.get("issue_tracker")
    return dict(section) if isinstance(section, dict) else {}


def slack_section(cfg: Mapping[str, Any]) -> Dict[str, Any]:
    integ = cfg.get("integrations")
    if not isinstance(integ, dict):
        return {}
    section = integ.get("slack")
    return dict(section) if isinstance(section, dict) else {}


def tracker_provider(cfg: Mapping[str, Any]) -> Optional[str]:
    raw = str(issue_tracker_section(cfg).get("provider") or "").strip().lower()
    return raw if raw in _TRACKER_PROVIDERS else None


def tracker_enabled(cfg: Mapping[str, Any]) -> bool:
    """Включено только при enabled: true и известном provider."""
    section = issue_tracker_section(cfg)
    if section.get("enabled") is not True:
        return False
    provider = tracker_provider(cfg)
    if provider is None:
        log.warning("issue_tracker.enabled but provider is missing/unknown")
        return False
    return True


def slack_enabled(cfg: Mapping[str, Any], *, force: bool = False) -> bool:
    """Slack: enabled и (enterprise_governance или slack.force). CLI --force обходит гейт."""
    if force:
        return True
    section = slack_section(cfg)
    if section.get("enabled") is not True:
        return False
    if section.get("force") is True:
        return True
    tier = cfg.get("tier")
    flags: Any = {}
    if isinstance(tier, dict):
        flags = tier.get("feature_flags") or {}
    if not isinstance(flags, dict):
        flags = {}
    if flags.get("enterprise_governance") is True:
        return True
    return False


def tracker_project_id(cfg: Mapping[str, Any]) -> str:
    return str(issue_tracker_section(cfg).get("project_id") or "").strip()


def tracker_base_url(cfg: Mapping[str, Any]) -> Optional[str]:
    raw = issue_tracker_section(cfg).get("base_url")
    if isinstance(raw, str) and raw.strip():
        return raw.strip().rstrip("/")
    return None
