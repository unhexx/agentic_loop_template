# -*- coding: utf-8 -*-
"""Онтология MultiLLM: типы, CRUD, снимок. Файл {wid}.llm_ontology.json."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Optional, TypeVar

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

T = TypeVar("T")


def _from_dict(
    cls: type[T],
    d: dict[str, Any] | None,
    *,
    nested: dict[str, Any] | None = None,
) -> T:
    """Лишние ключи JSON отбрасываем — на диске могут быть поля более новой версии."""
    src = d if isinstance(d, dict) else {}
    nested = nested or {}
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name not in src:
            continue
        val = src[f.name]
        ncls = nested.get(f.name)
        if ncls is not None and isinstance(val, list):
            val = [ncls.from_dict(x) if isinstance(x, dict) else x for x in val]
        elif ncls is not None and isinstance(val, dict):
            val = ncls.from_dict(val)
        kwargs[f.name] = val
    return cls(**kwargs)


class _SerdeMixin:
    """Общая serde, чтобы не копировать to_dict/from_dict в каждом типе."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Any:
        return _from_dict(cls, d)


@dataclass
class LLMProvider(_SerdeMixin):
    """Провайдер внешнего сервиса."""

    id: str
    type: str
    base_url: str
    capabilities: dict = field(default_factory=dict)
    cost_profile: dict = field(default_factory=dict)


@dataclass
class PromptVariant(_SerdeMixin):
    """Вариант формулировки запроса."""

    variant_id: str
    base_prompt: str
    model_specific_adaptations: dict = field(default_factory=dict)
    token_estimate: int = 0


@dataclass
class MultiLLMSession(_SerdeMixin):
    """Сессия параллельной работы с несколькими источниками."""

    session_id: str
    task_id: Optional[str] = None
    models_used: list[str] = field(default_factory=list)
    shared_context_ref: Optional[str] = None
    prompt_variants: list[PromptVariant] = field(default_factory=list)
    created_at: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> MultiLLMSession:
        # asdict разворачивает вложенные датаклассы, обратно их нужно собрать вручную.
        return _from_dict(cls, d, nested={"prompt_variants": PromptVariant})


@dataclass
class ModelComparisonResult(_SerdeMixin):
    """Результат сравнения в сессии."""

    result_id: str
    session_id: str
    model_a: str
    model_b: str
    metrics: dict = field(default_factory=dict)
    winner: Optional[str] = None
    rationale: str = ""


@dataclass
class Decision(_SerdeMixin):
    """Решение человека по результатам сессии."""

    decision_id: str
    session_id: str
    approved_model: str
    approved_output: str
    rationale: str = ""
    policy: Optional[str] = None
    timestamp: Optional[str] = None


@dataclass
class CrossModelToolCall(_SerdeMixin):
    """Вызов инструмента в контексте кросс-сессии."""

    call_id: str
    session_id: str
    tool_name: str
    model: str
    input: dict = field(default_factory=dict)
    output: Optional[str] = None
    latency_ms: float = 0.0
    policy_decision: Optional[str] = None


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


def _persist(
    bucket: str,
    obj: Any,
    cwd: Path | None = None,
    *,
    base_dir: Path | None = None,
    **meta: Any,
) -> dict[str, Any]:
    """Общая запись: публичные функции отличаются только ключами ответа."""
    path = _append(bucket, obj.to_dict(), cwd, base_dir=base_dir)
    return {**meta, "file": str(path)}


def create_llm_provider(
    provider: LLMProvider,
    cwd: Path | None = None,
    *,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    """Создание записи в онтологии. Атомарно под блокировкой."""
    return _persist("providers", provider, cwd, base_dir=base_dir, created=provider.id)


def create_llm_session(
    session: MultiLLMSession,
    cwd: Path | None = None,
    *,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    """Создание сессии в онтологии. Атомарно под блокировкой."""
    return _persist(
        "sessions", session, cwd, base_dir=base_dir, created=session.session_id
    )


create_multi_llm_session = create_llm_session


def record_model_comparison(
    result: ModelComparisonResult,
    cwd: Path | None = None,
    *,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    """Запись результата сравнения. Под блокировкой, атомарно."""
    return _persist(
        "comparisons",
        result,
        cwd,
        base_dir=base_dir,
        recorded=result.result_id,
        session=result.session_id,
    )


def record_decision(
    decision: Decision,
    cwd: Path | None = None,
    *,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    """Запись решения человека. Атомарно под блокировкой."""
    return _persist(
        "decisions",
        decision,
        cwd,
        base_dir=base_dir,
        recorded=decision.decision_id,
        session=decision.session_id,
    )


def record_cross_tool_call(
    call: CrossModelToolCall,
    cwd: Path | None = None,
    *,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    """Запись кросс-вызова. Атомарная запись под блокировкой."""
    return _persist("tool_calls", call, cwd, base_dir=base_dir, recorded=call.call_id)


record_cross_model_tool_call = record_cross_tool_call


def query_llm_sessions(
    task_id: str | None = None,
    model: str | None = None,
    cwd: Path | None = None,
    *,
    base_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Запрос сессий. Фильтр по task_id и источнику. Чтение без блокировки; словари как в JSON."""
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
