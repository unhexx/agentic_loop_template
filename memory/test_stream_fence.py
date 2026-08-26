# -*- coding: utf-8 -*-
"""Юнит-тесты английского забора disjoint-потока."""
from __future__ import annotations

import ast
import os
import threading
from pathlib import Path

import pytest

from memory.stream_context import use_stream
from memory.stream_fence import FENCE_OVERHEAD_CHARS, fence_block

_STREAM_ENV = ("AGENTIX_STREAM", "AGENTIX_OWNED_PATHS", "AGENTIX_WORKTREE")


@pytest.fixture
def no_stream_env(monkeypatch):
    for key in _STREAM_ENV:
        monkeypatch.delenv(key, raising=False)


def test_fence_empty_without_stream(no_stream_env) -> None:
    assert fence_block() == ""


def test_fence_empty_is_byte_stable(no_stream_env) -> None:
    first = fence_block()
    second = fence_block()
    assert first == ""
    assert second == first
    assert isinstance(first, str)
    assert first.encode("utf-8") == b""


def test_fence_contains_owned_paths(no_stream_env) -> None:
    with use_stream(
        name="harness",
        owned_paths="memory/,tools/",
        worktree="/tmp/wt-harness",
    ):
        text = fence_block()
    assert "harness" in text
    assert "memory/,tools/" in text
    assert "memory/" in text
    assert "tools/" in text
    assert text != ""


def test_fence_mentions_worktree_and_hot_files(no_stream_env) -> None:
    wt = "/tmp/agentic-loop-worktrees/feature-harness"
    with use_stream(name="harness", owned_paths="memory/", worktree=wt):
        text = fence_block()
    assert wt in text
    assert "## Stream fence (mandatory)" in text
    assert "DEVELOPMENT_STANDARDS.md" in text
    assert "VERSION" in text
    assert "schemas/" in text
    assert "package __init__" in text
    assert "another stream's paths" in text
    assert "merge gate" in text
    assert "BLOCK" in text


def test_fence_language_and_authorship_rules(no_stream_env) -> None:
    with use_stream(name="docs", owned_paths="docs/", worktree="/tmp/wt-docs"):
        text = fence_block()
    assert "Agent instructions and prompts stay English." in text
    assert "Commits, code comments, and human docs stay Russian." in text
    assert "Never reveal AI or agent authorship." in text


def test_fence_from_env_fallback(monkeypatch) -> None:
    monkeypatch.setenv("AGENTIX_STREAM", "docs")
    monkeypatch.setenv("AGENTIX_OWNED_PATHS", "docs/")
    monkeypatch.setenv("AGENTIX_WORKTREE", "/tmp/from-env")
    text = fence_block()
    assert "docs" in text
    assert "docs/" in text
    assert "/tmp/from-env" in text
    assert "Stream fence" in text


def test_fence_contextvar_overrides_env(monkeypatch) -> None:
    monkeypatch.setenv("AGENTIX_STREAM", "from-env")
    monkeypatch.setenv("AGENTIX_OWNED_PATHS", "env/")
    monkeypatch.setenv("AGENTIX_WORKTREE", "/env-wt")
    with use_stream(name="from-cv", owned_paths="cv/", worktree="/cv-wt"):
        text = fence_block()
    assert "`from-cv`" in text
    assert "`/cv-wt`" in text
    assert "cv/" in text
    assert "from-env" not in text
    assert "/env-wt" not in text


def test_fence_clears_after_use_stream_when_env_empty(no_stream_env) -> None:
    with use_stream(name="harness", owned_paths="memory/", worktree="/tmp/wt"):
        assert "harness" in fence_block()
    assert fence_block() == ""


def test_fence_does_not_mutate_os_environ(no_stream_env) -> None:
    before = dict(os.environ)
    with use_stream(name="harness", owned_paths="memory/", worktree="/tmp/wt"):
        body = fence_block()
    assert "harness" in body
    assert dict(os.environ) == before
    assert fence_block() == ""
    assert dict(os.environ) == before


def test_fence_overhead_constant() -> None:
    assert FENCE_OVERHEAD_CHARS == 1024
    assert isinstance(FENCE_OVERHEAD_CHARS, int)


def test_realistic_fence_fits_overhead(no_stream_env) -> None:
    name = "harness"
    owned = (
        "memory/stream_context.py,memory/adapters/grok.py,"
        "memory/adapters/blackbox.py,memory/adapters/cursor.py,"
        "memory/adapters/proc.py,memory/adapters/persist.py,"
        "memory/test_stream_context.py"
    )
    wt = (
        "/home/unhex/.grok/worktrees/project-agentic-loop-template/"
        "subagent-01a03fae-b45f-7850-b58d-3a478d80d707"
    )
    with use_stream(name=name, owned_paths=owned, worktree=wt):
        text = fence_block()
    assert len(text) <= FENCE_OVERHEAD_CHARS
    assert f"`{name}`" in text
    assert owned in text
    assert wt in text
    assert text.startswith("\n## Stream fence (mandatory)\n")
    assert text.endswith("\n")


def test_stream_fence_source_does_not_import_supervisor() -> None:
    src = Path(__file__).resolve().parent / "stream_fence.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "supervisor" not in alias.name.split(".")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert "supervisor" not in mod.split(".")
            for alias in node.names:
                assert "supervisor" not in alias.name.split(".")


def test_fence_thread_isolation(no_stream_env) -> None:
    seen: dict[str, str] = {}
    barrier = threading.Barrier(2, timeout=5)

    def worker(name: str) -> None:
        with use_stream(name=name, owned_paths=name + "/", worktree="/tmp/" + name):
            barrier.wait()
            seen[name] = fence_block()

    t1 = threading.Thread(target=worker, args=("alpha",))
    t2 = threading.Thread(target=worker, args=("beta",))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert not t1.is_alive()
    assert not t2.is_alive()
    assert "`alpha`" in seen["alpha"]
    assert "alpha/" in seen["alpha"]
    assert "`beta`" in seen["beta"]
    assert "beta/" in seen["beta"]
    assert "alpha" not in seen["beta"]
    assert "beta" not in seen["alpha"]
    assert fence_block() == ""
