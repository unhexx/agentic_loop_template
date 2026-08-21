# -*- coding: utf-8 -*-
"""
Sidecar идентификаторов: pxpipe теряет плотный hex, поэтому вытаскиваем
SHA/UUID до сжатия и кладём их в нативный текстовый блок FIDELITY.
Исходники не переписываем.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

FIDELITY_BUDGET_TOKENS = 96
_BLOCK_START = "--- FIDELITY ---"
_BLOCK_END = "--- END FIDELITY ---"

_UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_SHA256 = re.compile(r"\b[0-9a-fA-F]{64}\b")
_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")
_WID = re.compile(r"\b[A-Za-z0-9_-]{2,40}-[0-9a-f]{12}\b")


def is_fidelity_block(text: str) -> bool:
    return (text or "").lstrip().startswith(_BLOCK_START)


def split_sidecar(text: str) -> tuple[str, str]:
    """Отделяем sidecar от полезной нагрузки, чтобы дистилляция её не пропускала."""
    s = text or ""
    stripped = s.lstrip()
    if not stripped.startswith(_BLOCK_START):
        return "", s
    if _BLOCK_END not in stripped:
        return stripped, ""
    head, rest = stripped.split(_BLOCK_END, 1)
    return head + _BLOCK_END + "\n", rest.lstrip("\n")


def is_fidelity_only(text: str) -> bool:
    sidecar, rest = split_sidecar(text)
    return bool(sidecar) and not rest.strip()


def _estimate(text: str) -> int:
    try:
        from memory.context_budget import estimate_tokens

        return estimate_tokens(text)
    except Exception:
        return max(1, len(text) // 4)


def extract_ids(text: str, project_root: Optional[Path] = None) -> List[str]:
    """Уникальные идентификаторы из текста запроса (не из git log)."""
    if not text:
        return []
    seen: Set[str] = set()
    ordered: List[str] = []

    def _add(val: str) -> None:
        if val and val not in seen:
            seen.add(val)
            ordered.append(val)

    for m in _UUID.finditer(text):
        _add(m.group(0))
    for m in _SHA256.finditer(text):
        _add(m.group(0).lower())

    # Короткий hex оставляем, если есть цифра — иначе вылезают слова вроде defaced.
    # project_root не фильтрует: идентификатор уже в теле запроса.
    for m in _SHA.finditer(text):
        low = m.group(0).lower()
        if any(ch.isdigit() for ch in low):
            _add(low)

    for m in _WID.finditer(text):
        _add(m.group(0))
    return ordered


def format_block(ids: List[str]) -> str:
    lines = [_BLOCK_START]
    used = 0
    for item in ids:
        extra = _estimate(item + "\n")
        if used + extra > FIDELITY_BUDGET_TOKENS and lines[1:]:
            break
        lines.append(item)
        used += extra
    lines.append(_BLOCK_END)
    return "\n".join(lines) + "\n"


def _prepend_to_content(content: Any, block: str) -> Any:
    if isinstance(content, str):
        if is_fidelity_block(content):
            return content
        return block + "\n" + content
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and isinstance(first.get("text"), str):
            if is_fidelity_block(first["text"]):
                return content
            rest = list(content)
            rest[0] = dict(first)
            rest[0]["text"] = block + "\n" + first["text"]
            return rest
        if isinstance(first, str):
            rest = list(content)
            rest[0] = block + "\n" + first
            return rest
    return content


def prepend_block(obj: Dict[str, Any], block: str) -> Dict[str, Any]:
    """Вставляем блок в первый текстовый ход, чтобы head-truncate его сохранил."""
    out = dict(obj)
    for key in ("messages", "input"):
        items = out.get(key)
        if not isinstance(items, list) or not items:
            continue
        new_items = list(items)
        for i, msg in enumerate(new_items):
            if not isinstance(msg, dict):
                continue
            if isinstance(msg.get("content"), (str, list)):
                cloned = dict(msg)
                cloned["content"] = _prepend_to_content(msg.get("content"), block)
                new_items[i] = cloned
                out[key] = new_items
                return out
            if isinstance(msg.get("text"), str):
                cloned = dict(msg)
                cloned["text"] = block + "\n" + msg["text"]
                new_items[i] = cloned
                out[key] = new_items
                return out
    # не нашли куда вклеить — добавляем отдельный ход в начало
    item = {"role": "user", "content": block}
    if isinstance(out.get("input"), list):
        out["input"] = [item] + list(out["input"])
    elif isinstance(out.get("messages"), list):
        out["messages"] = [item] + list(out["messages"])
    else:
        out["input"] = [item]
    return out


def apply(
    obj: Dict[str, Any],
    project_root: Optional[Path] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    blob = str(obj)
    try:
        import json

        blob = json.dumps(obj, ensure_ascii=False)
    except Exception:
        pass
    ids = extract_ids(blob, project_root)
    if not ids:
        return obj, {"fidelity_ids": 0}
    block = format_block(ids)
    return prepend_block(obj, block), {
        "fidelity_ids": len(ids),
        "fidelity_tokens": _estimate(block),
    }
