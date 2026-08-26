# -*- coding: utf-8 -*-
"""Контекст текущего disjoint-потока: ContextVar, затем переменные окружения."""
from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional

_STREAM_NAME: ContextVar[Optional[str]] = ContextVar("agentix_stream_name", default=None)
_OWNED_PATHS: ContextVar[Optional[str]] = ContextVar("agentix_owned_paths", default=None)
_WORKTREE: ContextVar[Optional[str]] = ContextVar("agentix_worktree", default=None)


@contextmanager
def use_stream(*, name: str, owned_paths: str, worktree: str) -> Iterator[None]:
    """Выставляет имя потока, CSV owned_paths и путь worktree на время блока."""
    t_name = _STREAM_NAME.set(name)
    t_owned = _OWNED_PATHS.set(owned_paths)
    t_wt = _WORKTREE.set(worktree)
    try:
        yield
    finally:
        _STREAM_NAME.reset(t_name)
        _OWNED_PATHS.reset(t_owned)
        _WORKTREE.reset(t_wt)


def stream_name() -> Optional[str]:
    """ContextVar then os.environ.get('AGENTIX_STREAM')."""
    v = _STREAM_NAME.get()
    if v is not None:
        return v
    return os.environ.get("AGENTIX_STREAM")


def owned_paths_csv() -> Optional[str]:
    """ContextVar then AGENTIX_OWNED_PATHS."""
    v = _OWNED_PATHS.get()
    if v is not None:
        return v
    return os.environ.get("AGENTIX_OWNED_PATHS")


def worktree_path() -> Optional[str]:
    """ContextVar then AGENTIX_WORKTREE."""
    v = _WORKTREE.get()
    if v is not None:
        return v
    return os.environ.get("AGENTIX_WORKTREE")
