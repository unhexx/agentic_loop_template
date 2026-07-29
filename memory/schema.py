"""Схема и сериализация институциональной памяти (markdown + dataclasses)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


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


# Расширение схемы онтологии памяти.
# Поддержка типизированных сущностей для параллельных сессий с разными источниками.
# Минимальный срез: dataclass + сериализация/десериализация. Связи через идентификаторы для последующей интеграции.

from typing import Optional, List


@dataclass
class LLMProvider:
    """Провайдер внешнего сервиса."""
    id: str
    type: str
    base_url: str
    capabilities: dict = field(default_factory=dict)
    cost_profile: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        # Явная сериализация для контроля состава полей и совместимости.
        return {
            "id": self.id,
            "type": self.type,
            "base_url": self.base_url,
            "capabilities": self.capabilities,
            "cost_profile": self.cost_profile,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LLMProvider":
        # Явная сборка: игнорируем лишние ключи, подставляем дефолты для опциональных.
        # Это обеспечивает устойчивость при эволюции схемы и загрузке старых записей.
        d = d or {}
        return cls(
            id=d["id"],
            type=d["type"],
            base_url=d["base_url"],
            capabilities=d.get("capabilities", {}),
            cost_profile=d.get("cost_profile", {}),
        )


@dataclass
class PromptVariant:
    """Вариант формулировки запроса."""
    variant_id: str
    base_prompt: str
    model_specific_adaptations: dict = field(default_factory=dict)
    token_estimate: int = 0

    def to_dict(self) -> dict[str, Any]:
        # Явная сериализация для контроля состава полей и совместимости.
        return {
            "variant_id": self.variant_id,
            "base_prompt": self.base_prompt,
            "model_specific_adaptations": self.model_specific_adaptations,
            "token_estimate": self.token_estimate,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PromptVariant":
        # Явная сборка: игнорируем лишние ключи, подставляем дефолты для опциональных.
        # Это обеспечивает устойчивость при эволюции схемы и загрузке старых записей.
        d = d or {}
        return cls(
            variant_id=d["variant_id"],
            base_prompt=d["base_prompt"],
            model_specific_adaptations=d.get("model_specific_adaptations", {}),
            token_estimate=d.get("token_estimate", 0),
        )


@dataclass
class MultiLLMSession:
    """Сессия параллельной работы с несколькими источниками."""
    session_id: str
    task_id: Optional[str] = None
    models_used: List[str] = field(default_factory=list)
    shared_context_ref: Optional[str] = None
    prompt_variants: List[PromptVariant] = field(default_factory=list)
    created_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        # Явная сериализация (с рекурсией для вложенных PromptVariant).
        return {
            "session_id": self.session_id,
            "task_id": self.task_id,
            "models_used": list(self.models_used),
            "shared_context_ref": self.shared_context_ref,
            "prompt_variants": [v.to_dict() for v in self.prompt_variants],
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MultiLLMSession":
        # Явная сборка вложенных объектов + get для устойчивости.
        d = d or {}
        variants = [PromptVariant.from_dict(v) for v in d.get("prompt_variants", [])]
        return cls(
            session_id=d["session_id"],
            task_id=d.get("task_id"),
            models_used=d.get("models_used", []),
            shared_context_ref=d.get("shared_context_ref"),
            prompt_variants=variants,
            created_at=d.get("created_at"),
        )


@dataclass
class ModelComparisonResult:
    """Результат сравнения в сессии."""
    result_id: str
    session_id: str
    model_a: str
    model_b: str
    metrics: dict = field(default_factory=dict)
    winner: Optional[str] = None
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        # Явная сериализация для контроля состава полей и совместимости.
        return {
            "result_id": self.result_id,
            "session_id": self.session_id,
            "model_a": self.model_a,
            "model_b": self.model_b,
            "metrics": self.metrics,
            "winner": self.winner,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ModelComparisonResult":
        # Явная сборка: игнорируем лишние ключи, подставляем дефолты для опциональных.
        # Это обеспечивает устойчивость при эволюции схемы и загрузке старых записей.
        d = d or {}
        return cls(
            result_id=d["result_id"],
            session_id=d["session_id"],
            model_a=d["model_a"],
            model_b=d["model_b"],
            metrics=d.get("metrics", {}),
            winner=d.get("winner"),
            rationale=d.get("rationale", ""),
        )


@dataclass
class Decision:
    """Human approval decision for multi-model workspace results."""
    decision_id: str
    session_id: str
    approved_model: str
    approved_output: str
    rationale: str = ""
    policy: Optional[str] = None
    timestamp: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "session_id": self.session_id,
            "approved_model": self.approved_model,
            "approved_output": self.approved_output,
            "rationale": self.rationale,
            "policy": self.policy,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Decision":
        d = d or {}
        return cls(
            decision_id=d["decision_id"],
            session_id=d["session_id"],
            approved_model=d["approved_model"],
            approved_output=d["approved_output"],
            rationale=d.get("rationale", ""),
            policy=d.get("policy"),
            timestamp=d.get("timestamp"),
        )


@dataclass
class CrossModelToolCall:
    """Вызов инструмента в контексте кросс-сессии."""
    call_id: str
    session_id: str
    tool_name: str
    model: str
    input: dict = field(default_factory=dict)
    output: Optional[str] = None
    latency_ms: float = 0.0
    policy_decision: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        # Явная сериализация для контроля состава полей и совместимости (аналогично ModelComparisonResult).
        return {
            "call_id": self.call_id,
            "session_id": self.session_id,
            "tool_name": self.tool_name,
            "model": self.model,
            "input": self.input,
            "output": self.output,
            "latency_ms": self.latency_ms,
            "policy_decision": self.policy_decision,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CrossModelToolCall":
        # Явная сборка (аналогично ModelComparisonResult): игнорируем лишние ключи,
        # подставляем дефолты. Устойчивость к изменениям схемы и старым данным.
        d = d or {}
        return cls(
            call_id=d["call_id"],
            session_id=d["session_id"],
            tool_name=d["tool_name"],
            model=d["model"],
            input=d.get("input", {}),
            output=d.get("output"),
            latency_ms=d.get("latency_ms", 0.0),
            policy_decision=d.get("policy_decision"),
        )


# Явный экспорт для чистого импорта из memory.schema (без *).
__all__ = [
    "MemoryState",
    "Pattern",
    "normalize",
    "parse_markdown",
    "render_markdown",
    # CROSS-MEMORY-002
    "LLMProvider",
    "MultiLLMSession",
    "PromptVariant",
    "ModelComparisonResult",
    "Decision",
    "CrossModelToolCall",
]
