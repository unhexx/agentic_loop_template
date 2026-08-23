# -*- coding: utf-8 -*-
"""
Хуки шлюза: кэш → (fidelity) → дистилляция старых ходов.

Каждый хук fail-open. JSON tool-call конверты не трогаем.
Middleware только на POST /v1/responses|chat/completions|messages.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from memory.proxy import audit as audit_mod
from memory.proxy import cache as cache_mod

MIDDLEWARE_PATHS = (
    "/v1/responses",
    "/v1/chat/completions",
    "/v1/messages",
)

_OLD_TURN_BUDGET = 400


def is_middleware_path(path: str) -> bool:
    raw = (path or "").split("?", 1)[0]
    return raw in MIDDLEWARE_PATHS


def _estimate(text: str) -> int:
    try:
        from memory.context_budget import estimate_tokens

        return estimate_tokens(text)
    except Exception:
        return max(1, len(text) // 4)


def _distill_string(text: str) -> str:
    from memory.compressor import distill_text, truncate_to_budget

    out = distill_text(text)
    if _estimate(out) > _OLD_TURN_BUDGET:
        out = truncate_to_budget(out, _OLD_TURN_BUDGET)
    return out


def _looks_like_tool(msg: Any) -> bool:
    if not isinstance(msg, dict):
        return False
    if msg.get("tool_calls"):
        return True
    if msg.get("type") in {
        "function_call",
        "function_call_output",
        "tool",
        "tool_result",
        "tool_use",
    }:
        return True
    return False


def _distill_text_keep_sidecar(text: str) -> str:
    """Сжимаем полезную нагрузку, sidecar не выкидываем и не считаем «нельзя трогать весь ход»."""
    try:
        from memory.proxy.fidelity import is_fidelity_only, split_sidecar
    except Exception:
        return _distill_string(text)
    if is_fidelity_only(text):
        return text
    sidecar, rest = split_sidecar(text)
    if not rest:
        return sidecar or text
    return sidecar + _distill_string(rest)


def _distill_message(msg: Any) -> Any:
    if _looks_like_tool(msg) or not isinstance(msg, dict):
        return msg
    out = dict(msg)
    content = out.get("content")
    if isinstance(content, str):
        out["content"] = _distill_text_keep_sidecar(content)
    elif isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                p = dict(part)
                p["text"] = _distill_text_keep_sidecar(part["text"])
                parts.append(p)
            elif isinstance(part, str):
                parts.append(_distill_text_keep_sidecar(part))
            else:
                parts.append(part)
        out["content"] = parts
    elif isinstance(out.get("text"), str):
        out["text"] = _distill_text_keep_sidecar(out["text"])
    return out


def distill_old_turns(
    obj: Dict[str, Any],
    *,
    keep_recent: int,
    budget_tokens: int,
) -> Tuple[Dict[str, Any], str]:
    """Сжимаем старые текстовые ходы, последние keep_recent не трогаем."""
    key = None
    if isinstance(obj.get("messages"), list):
        key = "messages"
    elif isinstance(obj.get("input"), list):
        key = "input"
    if key is None:
        return obj, "none"
    items = list(obj[key])
    raw_tokens = _estimate(json.dumps(obj, ensure_ascii=False))
    if raw_tokens <= budget_tokens:
        return obj, "under_budget"
    keep_recent = max(0, int(keep_recent))
    if len(items) <= keep_recent:
        return obj, "kept"
    old, recent = items[:-keep_recent], items[-keep_recent:]
    new_old = [_distill_message(m) for m in old]
    out = dict(obj)
    out[key] = new_old + recent
    return out, "distilled"


def process_request(
    body: bytes,
    *,
    path: str,
    headers: Mapping[str, str],
    cfg: Dict[str, Any],
    project_root: Optional[Path],
) -> Tuple[bytes, Dict[str, Any]]:
    """Возвращает (тело для апстрима, мета для аудита)."""
    meta: Dict[str, Any] = {
        "cache_hit": False,
        "distill": "none",
        "parse_error": False,
        "passthrough": False,
        "sha256": cache_mod.request_sha256(body),
        "bytes_in": len(body or b""),
        "cache_key": None,
        "fidelity": False,
    }
    hdrs = {str(k).lower(): str(v) for k, v in headers.items()}
    if hdrs.get("x-agentix-passthrough") == "1" or not cfg.get("compress_body", True):
        meta["passthrough"] = True
        return body, meta
    try:
        obj = json.loads(body.decode("utf-8"))
        if not isinstance(obj, dict):
            raise ValueError("not object")
    except Exception:
        meta["parse_error"] = True
        return body, meta

    stream = bool(obj.get("stream"))
    has_tools = bool(obj.get("tools"))
    meta["stream"] = stream
    meta["has_tools"] = has_tools
    key = cache_mod.canonical_key(obj)
    meta["cache_key"] = key
    if (
        cfg.get("exact_cache", True)
        and project_root is not None
        and key
        and not stream
        and not has_tools
    ):
        hit = cache_mod.lookup(project_root, key)
        if hit is not None:
            meta["cache_hit"] = True
            meta["cached_status"] = hit[0]
            meta["cached_type"] = hit[1]
            meta["cached_body"] = hit[2]
            return body, meta

    # Distill first; prepend sidecar after so FIDELITY is not treated as the whole turn.
    fidelity_block = None
    if cfg.get("fidelity", True):
        try:
            from memory.proxy.fidelity import extract_ids, format_block

            blob = json.dumps(obj, ensure_ascii=False)
            ids = extract_ids(blob, project_root)
            if ids:
                fidelity_block = format_block(ids)
                meta["fidelity"] = True
                meta["fidelity_ids"] = len(ids)
                meta["fidelity_tokens"] = _estimate(fidelity_block)
        except Exception:
            fidelity_block = None

    try:
        obj, action = distill_old_turns(
            obj,
            keep_recent=int(cfg.get("keep_recent_turns") or 2),
            budget_tokens=int(cfg.get("body_budget_tokens") or 24000),
        )
        meta["distill"] = action
    except Exception:
        meta["distill"] = "error"

    if fidelity_block:
        try:
            from memory.proxy.fidelity import prepend_block

            obj = prepend_block(obj, fidelity_block)
        except Exception:
            pass

    try:
        new_body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    except Exception:
        return body, meta
    meta["bytes_out"] = len(new_body)
    return new_body, meta


def maybe_store_cache(
    *,
    project_root: Optional[Path],
    cfg: Dict[str, Any],
    meta: Dict[str, Any],
    status: int,
    content_type: str,
    response_body: bytes,
    request_obj_stream: bool = False,
) -> None:
    if not cfg.get("exact_cache", True):
        return
    if meta.get("cache_hit") or meta.get("parse_error") or request_obj_stream:
        return
    if meta.get("has_tools") or meta.get("stream"):
        return
    key = meta.get("cache_key")
    if not key or project_root is None:
        return
    if status >= 400:
        return
    cache_mod.store(
        project_root,
        str(key),
        status=status,
        content_type=content_type,
        body=response_body,
    )


def write_audit(
    project_root: Optional[Path],
    event: Dict[str, Any],
) -> None:
    try:
        audit_mod.append_event(project_root, event)
    except Exception:
        return
