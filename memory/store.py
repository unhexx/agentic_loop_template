"""Хранилище памяти: чтение, обновление, snapshot, query, lock, compaction."""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .schema import MemoryState, Pattern, normalize, parse_markdown, render_markdown
from .workspace import memory_paths
from .llm_ontology import (
    CrossModelToolCall,
    Decision,
    LLMProvider,
    ModelComparisonResult,
    MultiLLMSession,
    PromptVariant,
    create_llm_provider,
    create_llm_session,
    create_multi_llm_session,
    get_llm_ontology_snapshot,
    query_llm_sessions,
    record_cross_model_tool_call,
    record_cross_tool_call,
    record_decision,
    record_model_comparison,
)

MAX_PATTERNS_PER_CATEGORY = 30
MAX_DISTILLATIONS = 20


@contextmanager
def _file_lock(lock_path: Path, timeout: float = 10.0) -> Iterator[None]:
    """Простой cross-platform lock через эксклюзивное создание файла."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + timeout
    fd: int | None = None
    while time.time() < deadline:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("utf-8"))
            break
        except FileExistsError:
            time.sleep(0.05)
        except Exception:
            break
    try:
        yield
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        try:
            lock_path.unlink(missing_ok=True)
        except Exception:
            pass


def read_memory(cwd: Path | None = None) -> MemoryState:
    """Читает память с диска (пустое состояние если файла нет)."""
    paths = memory_paths(cwd=cwd)
    mem_file: Path = paths["file"]
    if not mem_file.exists():
        return MemoryState()
    try:
        text = mem_file.read_text(encoding="utf-8")
    except Exception:
        return MemoryState()
    return parse_markdown(text)


def _merge_patterns(
    state: MemoryState,
    new_patterns: list[dict[str, Any]],
) -> int:
    """Добавляет/инкрементирует паттерны. Возвращает число реально смерженных."""
    merged = 0
    for item in new_patterns:
        category = str(item.get("category", "Common Failure Patterns")).strip()
        description = str(item.get("description", "")).strip()
        if not description:
            continue
        norm = normalize(description)
        bucket = state.patterns.setdefault(category, [])
        found = False
        for p in bucket:
            if normalize(p.description) == norm:
                p.count += 1
                merged += 1
                found = True
                break
        if not found:
            bucket.append(Pattern(description=description, count=1))
            merged += 1
    return merged


def _compact(state: MemoryState) -> dict[str, int]:
    """Ограничивает размер категорий и списка дистилляций."""
    dropped = 0
    capped = 0
    for category, items in list(state.patterns.items()):
        if len(items) > MAX_PATTERNS_PER_CATEGORY:
            items.sort(key=lambda p: (-p.count, p.description.lower()))
            dropped += len(items) - MAX_PATTERNS_PER_CATEGORY
            state.patterns[category] = items[:MAX_PATTERNS_PER_CATEGORY]
            capped += 1
    if len(state.recent_distillations) > MAX_DISTILLATIONS:
        dropped += len(state.recent_distillations) - MAX_DISTILLATIONS
        state.recent_distillations = state.recent_distillations[-MAX_DISTILLATIONS:]
    return {"categories_capped": capped, "patterns_dropped": dropped}


def update_memory(
    new_patterns: list[dict[str, Any]] | None = None,
    distillation: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> dict[str, Any]:
    """Обновляет память: merge + compaction + атомарная запись."""
    paths = memory_paths(cwd=cwd)
    mem_file: Path = paths["file"]
    lock_file: Path = paths["lock"]

    with _file_lock(lock_file):
        state = read_memory(cwd=cwd)
        merged = _merge_patterns(state, new_patterns or [])
        if distillation and distillation.get("summary"):
            state.recent_distillations.append(
                {
                    "date": distillation.get("date", ""),
                    "summary": distillation.get("summary", ""),
                }
            )
        compaction = _compact(state)
        mem_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = mem_file.with_suffix(".md.tmp")
        tmp.write_text(render_markdown(state), encoding="utf-8")
        tmp.replace(mem_file)

    return {
        "workspace_id": paths["workspace_id"],
        "patterns_merged": merged,
        "compaction": compaction,
        "file": str(mem_file),
    }


def snapshot(cwd: Path | None = None) -> dict[str, Any]:
    """Возвращает snapshot для Orchestrator в начале цикла."""
    paths = memory_paths(cwd=cwd)
    state = read_memory(cwd=cwd)
    base = {
        "workspace_id": paths["workspace_id"],
        "file": str(paths["file"]),
        **state.to_snapshot_dict(),
    }
    # CROSS-MEMORY-002: включаем снимок онтологии (без нарушения совместимости)
    try:
        base["llm_ontology"] = get_llm_ontology_snapshot(cwd=cwd)
    except Exception:
        base["llm_ontology"] = {}
    return base


def query_memory(
    category: str | None = None,
    top_n: int = 5,
    contains: str | None = None,
    cwd: Path | None = None,
) -> list[dict[str, Any]]:
    """Точечный запрос паттернов (по категории, подстроке, топ-N по count)."""
    state = read_memory(cwd=cwd)
    results: list[dict[str, Any]] = []

    categories = [category] if category else list(state.patterns.keys())
    needle = (contains or "").strip().lower()

    for cat in categories:
        for p in state.patterns.get(cat, []):
            if needle and needle not in p.description.lower():
                continue
            results.append(
                {"category": cat, "description": p.description, "count": p.count}
            )

    results.sort(key=lambda x: (-int(x["count"]), x["description"].lower()))
    return results[: max(1, top_n)]


def update_from_json_payload(payload: str, cwd: Path | None = None) -> dict[str, Any]:
    """CLI helper: update из JSON строки (--json)."""
    data = json.loads(payload)
    patterns = data.get("patterns", [])
    distillation = data.get("distillation")
    return update_memory(new_patterns=patterns, distillation=distillation, cwd=cwd)


# Реэкспорт онтологии MultiLLM из memory.llm_ontology (совместимость импортов).
