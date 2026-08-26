# -*- coding: utf-8 -*-
"""Супервизор режет промпт, снимок и знания по лимитам из конфига и env."""
from __future__ import annotations

import json
from pathlib import Path

from memory.supervisor import (
    _knowledge_block,
    _maybe_compress_prompt,
    _state_snapshot_for_workdir,
    build_role_prompt,
)


def _write_coder_prompt(workdir: Path, body: str) -> None:
    prompts = workdir / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    (prompts / "short_coder_prompt.md").write_text(body, encoding="utf-8")
    (workdir / ".agent").mkdir(parents=True, exist_ok=True)


def _write_budget(workdir: Path, **budget) -> None:
    agent = workdir / ".agent"
    agent.mkdir(parents=True, exist_ok=True)
    (agent / "project_config.json").write_text(
        json.dumps({"context_budget": budget}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_default_role_prompt_truncated_to_8000(tmp_path: Path):
    _write_coder_prompt(tmp_path, "A" * 9000)
    prompt = build_role_prompt("Coder", None, tmp_path)
    assert "A" * 8000 in prompt
    assert "A" * 8001 not in prompt


def test_config_prompt_body_chars_truncates(tmp_path: Path):
    _write_coder_prompt(tmp_path, "A" * 200)
    _write_budget(tmp_path, prompt_body_chars=50)
    prompt = build_role_prompt("Coder", None, tmp_path)
    assert "A" * 50 in prompt
    assert "A" * 51 not in prompt


def test_env_prompt_body_chars_wins_over_config(tmp_path: Path, monkeypatch):
    _write_coder_prompt(tmp_path, "A" * 200)
    _write_budget(tmp_path, prompt_body_chars=50)
    monkeypatch.setenv("AGENTIX_PROMPT_BODY_CHARS", "20")
    prompt = build_role_prompt("Coder", None, tmp_path)
    assert "A" * 20 in prompt
    assert "A" * 21 not in prompt


def test_maybe_compress_prompt_uses_token_cap(tmp_path: Path, monkeypatch):
    _write_budget(tmp_path, prompt_token_cap=10, compress_when_over=True)
    import memory.compressor as compressor
    import memory.context_budget as budget

    monkeypatch.setattr(budget, "estimate_tokens", lambda text, **_k: 99_999)
    monkeypatch.setattr(
        compressor, "compress_text", lambda text, cap, **_k: {"text": "COMPRESSED"}
    )
    assert _maybe_compress_prompt("hello world " * 40, tmp_path) == "COMPRESSED"


def test_compress_when_over_false_skips(tmp_path: Path, monkeypatch):
    _write_budget(tmp_path, prompt_token_cap=1, compress_when_over=False)
    import memory.compressor as compressor
    import memory.context_budget as budget

    monkeypatch.setattr(budget, "estimate_tokens", lambda text, **_k: 99_999)
    monkeypatch.setattr(
        compressor, "compress_text", lambda text, cap, **_k: {"text": "COMPRESSED"}
    )
    text = "KEEP_ME_UNTOUCHED"
    assert _maybe_compress_prompt(text, tmp_path) == text


def test_invalid_prompt_body_chars_uses_default(tmp_path: Path):
    _write_coder_prompt(tmp_path, "A" * 9000)
    _write_budget(tmp_path, prompt_body_chars=0)
    prompt = build_role_prompt("Coder", None, tmp_path)
    assert "A" * 8000 in prompt
    assert "A" * 8001 not in prompt


def test_state_snapshot_respects_snap_json_chars(tmp_path: Path, monkeypatch):
    import memory.state as state_mod

    monkeypatch.setattr(
        state_mod, "snapshot", lambda *a, **k: {"blob": "Z" * 500}
    )
    _write_budget(tmp_path, snap_json_chars=40)
    out = _state_snapshot_for_workdir(tmp_path)
    assert len(out) == 40
    assert out.startswith("{")


def test_knowledge_budget_tokens_passed_to_compressor(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_coder_prompt(tmp_path, "# Coder\nDo code.\n")
    _write_budget(tmp_path, knowledge_budget_tokens=1)
    from memory.knowledge import db_path, upsert

    db = db_path(cwd=tmp_path)
    upsert(
        source="docs/git.md",
        title="Git preflight",
        content="Always run preflight_git.sh before worktree sync.",
        category="playbook",
        db=db,
        provenance="test",
    )
    import memory.compressor as compressor

    seen: dict = {}
    real = compressor.compress_text

    def wrapped(text, budget, *a, **k):
        seen["budget"] = budget
        return real(text, budget, *a, **k)

    monkeypatch.setattr(compressor, "compress_text", wrapped)
    prompt = build_role_prompt(
        "Coder",
        handoff_in={"summary": "git preflight before sync"},
        workdir=tmp_path,
    )
    assert "Local knowledge" in prompt
    assert seen.get("budget") == 1
    block = _knowledge_block(
        "Coder", {"summary": "git preflight before sync"}, tmp_path
    )
    assert "Local knowledge" in block
    assert len(block) < 400
