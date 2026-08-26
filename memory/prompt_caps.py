# -*- coding: utf-8 -*-
"""Лимиты тела промпта, JSON-снимка и блока знаний.

Порядок: переменные окружения → секция context_budget → встроенные дефолты.
Битое значение сбрасывает только свой ключ, остальные лимиты остаются в силе.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from memory.logutil import get_logger

log = get_logger("memory.prompt_caps")

DEFAULT_PROMPT_BODY_CHARS = 8000
DEFAULT_SNAP_JSON_CHARS = 4000
DEFAULT_KNOWLEDGE_BUDGET_TOKENS = 800
DEFAULT_PROMPT_TOKEN_CAP = 8000

ENV_PROMPT_BODY_CHARS = "AGENTIX_PROMPT_BODY_CHARS"
ENV_SNAP_JSON_CHARS = "AGENTIX_SNAP_JSON_CHARS"
ENV_KNOWLEDGE_BUDGET_TOKENS = "AGENTIX_KNOWLEDGE_BUDGET_TOKENS"
ENV_PROMPT_TOKEN_CAP = "AGENTIX_PROMPT_TOKEN_CAP"

# Один warning на ключ за жизнь процесса — иначе тесты и повторные вызовы шумят.
_WARNED_KEYS: set[str] = set()

_FIELDS: tuple[tuple[str, str, int], ...] = (
    ("prompt_body_chars", ENV_PROMPT_BODY_CHARS, DEFAULT_PROMPT_BODY_CHARS),
    ("snap_json_chars", ENV_SNAP_JSON_CHARS, DEFAULT_SNAP_JSON_CHARS),
    ("knowledge_budget_tokens", ENV_KNOWLEDGE_BUDGET_TOKENS, DEFAULT_KNOWLEDGE_BUDGET_TOKENS),
    ("prompt_token_cap", ENV_PROMPT_TOKEN_CAP, DEFAULT_PROMPT_TOKEN_CAP),
)


@dataclass(frozen=True)
class PromptCaps:
    prompt_body_chars: int = DEFAULT_PROMPT_BODY_CHARS
    snap_json_chars: int = DEFAULT_SNAP_JSON_CHARS
    knowledge_budget_tokens: int = DEFAULT_KNOWLEDGE_BUDGET_TOKENS
    prompt_token_cap: int = DEFAULT_PROMPT_TOKEN_CAP


def _brief(raw: Any) -> Any:
    """Короткое представление для лога: без длинных тел и без промптов."""
    if isinstance(raw, str) and len(raw) > 64:
        return raw[:64] + "..."
    return raw


def _parse_positive_int(raw: Any) -> Optional[int]:
    """Целое > 0; bool, дробный float, ноль и мусор — отказ."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw > 0 else None
    if isinstance(raw, float):
        if raw.is_integer() and raw > 0:
            return int(raw)
        return None
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            n = int(s, 10)
        except ValueError:
            try:
                f = float(s)
            except ValueError:
                return None
            if not f.is_integer() or f <= 0:
                return None
            n = int(f)
        return n if n > 0 else None
    return None


def _warn_invalid(key: str, raw: Any, default: int) -> None:
    if key in _WARNED_KEYS:
        return
    _WARNED_KEYS.add(key)
    log.warning(
        "некорректный лимит %s=%r, оставляю %s",
        key,
        _brief(raw),
        default,
    )


def _env_raw(environ: Mapping[str, str], env_name: str) -> Optional[str]:
    if env_name not in environ:
        return None
    val = environ[env_name]
    if not isinstance(val, str):
        val = str(val)
    if val.strip() == "":
        return None
    return val


def _resolve_one(
    key: str,
    env_name: str,
    default: int,
    budget: Mapping[str, Any],
    environ: Mapping[str, str],
) -> int:
    env_val = _env_raw(environ, env_name)
    if env_val is not None:
        parsed = _parse_positive_int(env_val)
        if parsed is not None:
            return parsed
        _warn_invalid(key, env_val, default)
        return default
    if key not in budget:
        return default
    cfg_val = budget[key]
    parsed = _parse_positive_int(cfg_val)
    if parsed is not None:
        return parsed
    _warn_invalid(key, cfg_val, default)
    return default


def resolve_prompt_caps(
    cfg: Optional[Mapping[str, Any]] = None,
    *,
    environ: Optional[Mapping[str, str]] = None,
) -> PromptCaps:
    """Собрать лимиты: env → context_budget → дефолты. Битые значения не роняют вызов."""
    env_map: Mapping[str, str]
    if environ is None:
        env_map = os.environ
    else:
        env_map = environ
    budget: Mapping[str, Any] = {}
    if isinstance(cfg, Mapping):
        raw_budget = cfg.get("context_budget")
        if isinstance(raw_budget, Mapping):
            budget = raw_budget
    values: dict[str, int] = {}
    for key, env_name, default in _FIELDS:
        values[key] = _resolve_one(key, env_name, default, budget, env_map)
    return PromptCaps(**values)


def caps_from_workdir(load_config_fn: Callable[..., Any], workdir: Any) -> PromptCaps:
    """Прочитать конфиг workdir и собрать лимиты. Исключение load_config_fn не глотаем."""
    return resolve_prompt_caps(load_config_fn(workdir))
