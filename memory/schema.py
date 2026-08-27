"""Схема и сериализация институциональной памяти (markdown + dataclasses)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .llm_ontology import (
    CrossModelToolCall,
    Decision,
    LLMProvider,
    ModelComparisonResult,
    MultiLLMSession,
    PromptVariant,
)


@dataclass
class Pattern:
    """Повторяющийся паттерн внутри категории."""

    description: str
    count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {"description": self.description, "count": self.count}


@dataclass
class MemoryState:
    """Полное состояние памяти проекта."""

    patterns: dict[str, list[Pattern]] = field(default_factory=dict)
    recent_distillations: list[dict[str, str]] = field(default_factory=list)

    def to_snapshot_dict(self) -> dict[str, Any]:
        return {
            "patterns": {
                cat: [p.to_dict() for p in items]
                for cat, items in self.patterns.items()
            },
            "recent_distillations": list(self.recent_distillations),
        }


def normalize(description: str) -> str:
    """Нормализация текста для дедупликации паттернов."""
    text = description.strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def parse_markdown(text: str) -> MemoryState:
    """Парсит markdown-файл памяти в MemoryState."""
    state = MemoryState()
    if not text.strip():
        return state

    lines = text.splitlines()
    current_category: str | None = None
    in_distillations = False

    for raw in lines:
        line = raw.rstrip()
        if line.startswith("## Recent Distillations"):
            in_distillations = True
            current_category = None
            continue

        if in_distillations:
            m = re.match(r"^###\s+(.+?)\s+—\s+(.+)$", line)
            if m:
                state.recent_distillations.append(
                    {"date": m.group(1).strip(), "summary": m.group(2).strip()}
                )
            continue

        if line.startswith("## "):
            current_category = line[3:].strip()
            state.patterns.setdefault(current_category, [])
            continue

        if current_category and line.startswith("- "):
            body = line[2:].strip()
            m = re.match(r"^(.+?)\s+\(seen\s+(\d+)\s+times\)\s*$", body)
            if m:
                desc, cnt = m.group(1).strip(), int(m.group(2))
            else:
                desc, cnt = body, 1
            state.patterns[current_category].append(Pattern(description=desc, count=cnt))

    return state


def render_markdown(state: MemoryState) -> str:
    """Сериализует MemoryState в человекочитаемый markdown."""
    lines: list[str] = ["# Agentic Loop Memory", ""]

    for category in sorted(state.patterns.keys()):
        lines.append(f"## {category}")
        items = sorted(
            state.patterns[category],
            key=lambda p: (-p.count, p.description.lower()),
        )
        if not items:
            lines.append("")
            continue
        for p in items:
            lines.append(f"- {p.description} (seen {p.count} times)")
        lines.append("")

    lines.append("## Recent Distillations")
    lines.append("")
    if not state.recent_distillations:
        lines.append("(none)")
    else:
        for d in state.recent_distillations[-20:]:
            lines.append(f"### {d.get('date', '')} — {d.get('summary', '')}")
    lines.append("")
    return "\n".join(lines)


# Реэкспорт MultiLLM-типов: from memory.schema import MultiLLMSession по-прежнему работает.
__all__ = [
    "MemoryState",
    "Pattern",
    "normalize",
    "parse_markdown",
    "render_markdown",
    "LLMProvider",
    "MultiLLMSession",
    "PromptVariant",
    "ModelComparisonResult",
    "Decision",
    "CrossModelToolCall",
]
