# -*- coding: utf-8 -*-
"""Извлечение буллетов/заголовков и дедуп паттернов."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

from memory.schema import normalize

HEADING_HINTS = re.compile(
    r"boundar|must not|never do|definition of done|guiding principle|"
    r"permanent rule|coding standard|preferred development|what agents|"
    r"hard constraint|exact command|key decision|do not|never |"
    r"neural-network agent|getting unstuck|working on the parser|commit & pr",
    re.I,
)

NEVER_LINE = re.compile(
    r"^\s*(?:[-*•]|\d+[.)])\s+(?:never |do not |don't |must not |запрещ|нельзя |never\b)",
    re.I,
)

MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
PLACEHOLDER = re.compile(r"\{\{[^}]+\}\}")
WIN_PATH = re.compile(r"(?:C:\\Users\\|C:/_PROJECT|C:\\_PROJECT|\\\\Users\\\\)", re.I)
FOREIGN_LOOP = re.compile(
    r"localrepo-agentic-loop-template|agent-loop-template-upgrade-loop",
    re.I,
)
AGENT_DOC_HINT = re.compile(
    r"agent(?:ic)?|playbook|AGENTS\.md|Agent-Init|agentic.loop",
    re.I,
)


def _extract_bullets(text: str) -> List[str]:
    bullets: List[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(("- ", "* ", "• ")):
            body = s[2:].strip()
            if len(body) < 20 or len(body) > 300:
                continue
            low = body.lower()
            if low.startswith(
                (
                    "context:",
                    "observation:",
                    "root cause:",
                    "**lesson id**",
                    "**context**",
                    "**observation**",
                    "**recommendation**",
                    "**date**",
                )
            ):
                continue
            if "short memorable name" in low or "when/where observed" in low:
                continue
            if PLACEHOLDER.search(body):
                continue
            bullets.append(body)
        m = re.match(r"^\*\*Recommendation\*\*:\s*(.+)$", s, re.I)
        if m:
            body = m.group(1).strip()
            if 20 <= len(body) <= 300:
                bullets.append(body)
    return bullets


def _strip_md(s: str) -> str:
    s = re.sub(r"[*`_]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _extract_heading_rules(text: str) -> List[str]:
    """Правила из секций Boundaries / MUST NOT / DoD / Permanent Rules."""
    lines = text.splitlines()
    out: List[str] = []
    capture = False
    for raw in lines:
        s = raw.strip()
        if s.startswith("#"):
            title = re.sub(r"^#+\s*", "", s)
            capture = bool(HEADING_HINTS.search(title))
            continue
        if not capture:
            continue
        m = re.match(r"^(?:[-*•]|\d+[.)]|\[[ xX]\])\s+(.*)$", s)
        if not m:
            continue
        body = _strip_md(m.group(1))
        if body.startswith("[ ]") or body.startswith("[x]") or body.startswith("[X]"):
            body = body[3:].strip()
        if 20 <= len(body) <= 300 and not PLACEHOLDER.search(body):
            out.append(body)
    return out


def _extract_never_lines(text: str) -> List[str]:
    out: List[str] = []
    for raw in text.splitlines():
        if not NEVER_LINE.search(raw):
            continue
        body = _strip_md(re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", raw.strip()))
        if 20 <= len(body) <= 300:
            out.append(body)
    return out


def _classify(text: str) -> str:
    if re.search(r"\b(never|skip|forgot|error|fail|avoid|must not|do not)\b", text, re.I):
        return "Common Failure Patterns"
    if re.search(
        r"\b(always|prefer|use|strategy|narrow|exact command|contracts first)\b",
        text,
        re.I,
    ):
        return "Effective Loop Strategies"
    if re.search(r"\b(schema|provenance|fixture|zod|pii|secret|allowlist)\b", text, re.I):
        return "Project Playbook Patterns"
    return "Effective Loop Strategies"


def _read_capped(path: Path, cap: int = 200_000) -> str:
    try:
        raw = path.read_bytes()[:cap]
    except OSError:
        return ""
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        try:
            return raw.decode("utf-16", errors="replace")
        except Exception:
            return ""
    text = raw.decode("utf-8", errors="replace")
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
    return text

def dedupe(patterns: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen = set()
    out: List[Dict[str, str]] = []
    for p in patterns:
        key = (p.get("category", ""), normalize(p.get("description", "")))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        item = {"category": p["category"], "description": p["description"]}
        if p.get("source"):
            item["source"] = str(p["source"])
        out.append(item)
    return out
