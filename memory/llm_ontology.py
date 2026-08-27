# -*- coding: utf-8 -*-
"""Онтология MultiLLM: типы, CRUD, снимок. Файл {wid}.llm_ontology.json."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .agent_lock import agent_lock
from .workspace import memory_paths

_LLM_STATE_FILE = "llm_ontology.json"
_LOCK_NAME = "llm_ontology"

_EMPTY_STATE: dict[str, list] = {
    "providers": [],
    "sessions": [],
    "comparisons": [],
    "tool_calls": [],
    "decisions": [],
}


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
        return {
            "variant_id": self.variant_id,
            "base_prompt": self.base_prompt,
            "model_specific_adaptations": self.model_specific_adaptations,
            "token_estimate": self.token_estimate,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PromptVariant":
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
    models_used: list[str] = field(default_factory=list)
    shared_context_ref: Optional[str] = None
    prompt_variants: list[PromptVariant] = field(default_factory=list)
    created_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
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


def _get_llm_paths(
    cwd: Path | None = None, *, base_dir: Path | None = None
) -> dict[str, Any]:
    mp = memory_paths(cwd=cwd)
    wid = mp["workspace_id"]
    root = Path(base_dir) if base_dir is not None else Path(mp["dir"])
    return {
        "file": root / f"{wid}.{_LLM_STATE_FILE}",
        "dir": root,
        "workspace_id": wid,
    }


def _empty() -> dict[str, Any]:
    return {k: list(v) for k, v in _EMPTY_STATE.items()}


def _read_llm_state(
    cwd: Path | None = None, *, base_dir: Path | None = None
) -> dict[str, Any]:
    paths = _get_llm_paths(cwd, base_dir=base_dir)
    f = paths["file"]
    if not f.exists():
        return _empty()
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
        out = _empty()
        for key in out:
            val = data.get(key, [])
            out[key] = val if isinstance(val, list) else []
        return out
    except Exception:
        return _empty()


def _write_llm_state(
    state: dict[str, Any],
    cwd: Path | None = None,
    *,
    base_dir: Path | None = None,
) -> None:
    paths = _get_llm_paths(cwd, base_dir=base_dir)
    f: Path = paths["file"]
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(f)


def _append(
    bucket: str,
    payload: dict[str, Any],
    cwd: Path | None = None,
    *,
    base_dir: Path | None = None,
) -> Path:
    paths = _get_llm_paths(cwd, base_dir=base_dir)
    with agent_lock(paths["dir"], name=_LOCK_NAME):
        state = _read_llm_state(cwd, base_dir=base_dir)
        state.setdefault(bucket, []).append(payload)
        _write_llm_state(state, cwd, base_dir=base_dir)
    return paths["file"]


def create_llm_provider(
    provider: LLMProvider,
    cwd: Path | None = None,
    *,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    """Создание записи в онтологии. Атомарно под блокировкой."""
    path = _append("providers", provider.to_dict(), cwd, base_dir=base_dir)
    return {"created": provider.id, "file": str(path)}


def create_llm_session(
    session: MultiLLMSession,
    cwd: Path | None = None,
    *,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    """Создание сессии в онтологии. Атомарно под блокировкой."""
    path = _append("sessions", session.to_dict(), cwd, base_dir=base_dir)
    return {"created": session.session_id, "file": str(path)}


create_multi_llm_session = create_llm_session


def record_model_comparison(
    result: ModelComparisonResult,
    cwd: Path | None = None,
    *,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    """Запись результата сравнения. Под блокировкой, атомарно."""
    path = _append("comparisons", result.to_dict(), cwd, base_dir=base_dir)
    return {"recorded": result.result_id, "session": result.session_id, "file": str(path)}


def record_decision(
    decision: Decision,
    cwd: Path | None = None,
    *,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    """Запись human approval decision. Атомарно под блокировкой."""
    path = _append("decisions", decision.to_dict(), cwd, base_dir=base_dir)
    return {
        "recorded": decision.decision_id,
        "session": decision.session_id,
        "file": str(path),
    }


def record_cross_tool_call(
    call: CrossModelToolCall,
    cwd: Path | None = None,
    *,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    """Запись кросс-вызова. Атомарная запись под блокировкой."""
    path = _append("tool_calls", call.to_dict(), cwd, base_dir=base_dir)
    return {"recorded": call.call_id, "file": str(path)}


record_cross_model_tool_call = record_cross_tool_call


def query_llm_sessions(
    task_id: str | None = None,
    model: str | None = None,
    cwd: Path | None = None,
    *,
    base_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Запрос сессий. Фильтр по task_id и источнику (если указан). Чтение без блокировки."""
    state = _read_llm_state(cwd, base_dir=base_dir)
    items = state.get("sessions", [])
    if task_id:
        items = [s for s in items if s.get("task_id") == task_id]
    if model:
        items = [s for s in items if model in (s.get("models_used") or [])]
    return items


def get_llm_ontology_snapshot(
    cwd: Path | None = None, *, base_dir: Path | None = None
) -> dict[str, Any]:
    """Снимок для включения в общий снимок памяти."""
    return _read_llm_state(cwd, base_dir=base_dir)


__all__ = [
    "LLMProvider",
    "PromptVariant",
    "MultiLLMSession",
    "ModelComparisonResult",
    "Decision",
    "CrossModelToolCall",
    "create_llm_provider",
    "create_llm_session",
    "create_multi_llm_session",
    "record_model_comparison",
    "record_decision",
    "record_cross_tool_call",
    "record_cross_model_tool_call",
    "query_llm_sessions",
    "get_llm_ontology_snapshot",
]
