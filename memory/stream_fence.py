# -*- coding: utf-8 -*-
"""Английский забор disjoint-потока: owned_paths, горячие файлы, язык коммитов.

Без активного потока возвращает пустую строку, чтобы не менять промпт
одиночного запуска.
"""
from __future__ import annotations

from memory.stream_context import owned_paths_csv, stream_name, worktree_path

# Полный забор клеим после сжатия; запас сверх prompt_token_cap, текст не режем.
FENCE_OVERHEAD_CHARS = 1024


def fence_block() -> str:
    """Английский забор текущего потока или пустая строка, если потока нет."""
    name = stream_name()
    if not name:
        return ""
    owned = owned_paths_csv() or ""
    wt = worktree_path() or ""
    return (
        "\n## Stream fence (mandatory)\n"
        f"You are stream `{name}` in worktree `{wt}`.\n"
        f"You may create or edit ONLY these owned_paths: {owned}\n"
        "Edits outside owned_paths fail the merge gate and BLOCK the stream.\n"
        "Do not edit DEVELOPMENT_STANDARDS.md, VERSION, schemas/, "
        "package __init__, or another stream's paths.\n"
        "Agent instructions and prompts stay English. "
        "Commits, code comments, and human docs stay Russian. "
        "Never reveal AI or agent authorship.\n"
    )
