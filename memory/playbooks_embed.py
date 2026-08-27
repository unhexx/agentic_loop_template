# -*- coding: utf-8 -*-
"""HTTP-эмбеддинги и кэш векторов для рейтинга playbooks (P8-10)."""
from __future__ import annotations

import hashlib
import json
import math
import os
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from memory.agent_lock import agent_lock
from memory.logutil import get_logger

log = get_logger("memory.playbooks")

DEFAULT_MODEL = "text-embedding-3-small"
DEFAULT_TIMEOUT = 5.0
CACHE_NAME = "PLAYBOOKS.embeddings.json"


def cache_path(agent_dir: Optional[Path] = None) -> Path:
    """Явный каталог .agent или cwd-дефолт."""
    if agent_dir is not None:
        return Path(agent_dir) / CACHE_NAME
    return Path(".agent") / CACHE_NAME


def cache_key(model: str, text: str) -> str:
    """Ключ кэша: sha256(model + LF + text)."""
    return hashlib.sha256(f"{model}\n{text}".encode("utf-8")).hexdigest()


def cosine_01(a: List[float], b: List[float]) -> Optional[float]:
    """Косинус [-1, 1] → [0, 1] через (x+1)/2. Размерность/нуль → None."""
    if not a or not b or len(a) != len(b):
        return None
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        fx = float(x)
        fy = float(y)
        dot += fx * fy
        na += fx * fx
        nb += fy * fy
    if na <= 0.0 or nb <= 0.0:
        return None
    c = dot / (math.sqrt(na) * math.sqrt(nb))
    if c > 1.0:
        c = 1.0
    elif c < -1.0:
        c = -1.0
    return (c + 1.0) / 2.0


def _strip_or_none(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def resolve_embed_settings(
    cfg: Dict[str, Any],
    environ: Optional[Mapping[str, str]] = None,
) -> Optional[Tuple[str, str, Optional[str]]]:
    """URL из конфига, затем AGENTIX_EMBED_BASE. Ключ: конфиг → AGENTIX_EMBED_API_KEY → OPENAI_API_KEY."""
    env: Mapping[str, str] = os.environ if environ is None else environ
    url = _strip_or_none(cfg.get("embedding_base_url")) or _strip_or_none(
        env.get("AGENTIX_EMBED_BASE")
    )
    model = _strip_or_none(cfg.get("embedding_model")) or DEFAULT_MODEL
    key = (
        _strip_or_none(cfg.get("embedding_api_key"))
        or _strip_or_none(env.get("AGENTIX_EMBED_API_KEY"))
        or _strip_or_none(env.get("OPENAI_API_KEY"))
    )
    if not url or not key:
        return None
    return url, model, key


def embed_texts(
    texts: List[str],
    *,
    base_url: str,
    model: str,
    api_key: Optional[str],
    timeout: float = DEFAULT_TIMEOUT,
) -> List[List[float]]:
    """POST {origin}/v1/embeddings. Ошибки HTTP/JSON — исключение, без ловли здесь."""
    if not texts:
        return []
    url = f"{base_url.rstrip('/')}/v1/embeddings"
    payload = json.dumps({"input": texts, "model": model}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=payload, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except TimeoutError as exc:
        raise ValueError("embed_http") from exc
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("embed_http") from exc
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list) or len(data) != len(texts):
        raise ValueError("embed_http")
    ordered = sorted(
        enumerate(data),
        key=lambda pair: int(pair[1].get("index", pair[0]))
        if isinstance(pair[1], dict)
        else pair[0],
    )
    out: List[List[float]] = []
    for _, item in ordered:
        if not isinstance(item, dict) or "embedding" not in item:
            raise ValueError("embed_http")
        vec = item["embedding"]
        if not isinstance(vec, list) or not vec:
            raise ValueError("embed_http")
        out.append([float(x) for x in vec])
    if len(out) != len(texts):
        raise ValueError("embed_http")
    return out


def _empty_vectors() -> Dict[str, List[float]]:
    return {}


def _load_cache_unlocked(agent_dir: Optional[Path], model: str) -> Dict[str, List[float]]:
    """Чтение без lock. Чужой model — пустой dict (не мешаем размерности). Битый файл → bak."""
    path = cache_path(agent_dir)
    if not path.exists():
        return _empty_vectors()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        log.warning("playbooks embeddings cache corrupt, renaming to bak: %s", path)
        try:
            path.rename(path.with_suffix(".json.bak"))
        except Exception:
            pass
        return _empty_vectors()
    if not isinstance(raw, dict):
        return _empty_vectors()
    if raw.get("model") != model:
        return _empty_vectors()
    vecs = raw.get("vectors") or {}
    if not isinstance(vecs, dict):
        return _empty_vectors()
    out: Dict[str, List[float]] = {}
    for key, val in vecs.items():
        if isinstance(key, str) and isinstance(val, list) and val:
            try:
                out[key] = [float(x) for x in val]
            except (TypeError, ValueError):
                continue
    return out


def _write_cache_unlocked(
    agent_dir: Optional[Path],
    model: str,
    vectors: Dict[str, List[float]],
) -> None:
    """tmp+replace. Ключ API не пишем."""
    path = cache_path(agent_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    body = {"model": model, "vectors": vectors}
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _playbooks_lock(agent_dir: Optional[Path]):
    """Тот же name=playbooks на родителе индекса — не второй lock."""
    return agent_lock(cache_path(agent_dir).parent, name="playbooks")


def vectors_for_texts(
    query: str,
    texts: Sequence[str],
    *,
    agent_dir: Optional[Path],
    base_url: str,
    model: str,
    api_key: Optional[str],
    timeout: float = DEFAULT_TIMEOUT,
) -> Tuple[Optional[List[float]], Dict[str, Optional[List[float]]]]:
    """Кэш без lock → один HTTP по пропускам → секция playbooks, merge, tmp+replace."""
    unique: List[str] = list(dict.fromkeys(texts))
    cached = _load_cache_unlocked(agent_dir, model)
    qkey = cache_key(model, query)
    missing: List[str] = []
    if qkey not in cached:
        missing.append(query)
    for text in unique:
        if cache_key(model, text) not in cached:
            missing.append(text)
    missing = list(dict.fromkeys(missing))

    new_vecs: Dict[str, List[float]] = {}
    if missing:
        embedded = embed_texts(
            missing,
            base_url=base_url,
            model=model,
            api_key=api_key,
            timeout=timeout,
        )
        for text, vec in zip(missing, embedded):
            new_vecs[cache_key(model, text)] = vec

    if new_vecs:
        with _playbooks_lock(agent_dir):
            fresh = _load_cache_unlocked(agent_dir, model)
            fresh.update(new_vecs)
            _write_cache_unlocked(agent_dir, model, fresh)
            cached = fresh
    else:
        cached = {**cached, **new_vecs}

    query_vec = cached.get(qkey)
    by_text: Dict[str, Optional[List[float]]] = {
        text: cached.get(cache_key(model, text)) for text in unique
    }
    return query_vec, by_text
