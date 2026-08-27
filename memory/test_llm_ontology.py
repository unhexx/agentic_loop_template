# -*- coding: utf-8 -*-
"""Онтология MultiLLM: roundtrip, CRUD, лок, реэкспорт, без записи в home."""

from __future__ import annotations

from pathlib import Path

import memory.llm_ontology as ont
import memory.schema as schema_mod
import memory.store as store_mod


def _provider(**kwargs) -> ont.LLMProvider:
    base = dict(id="p1", type="openai", base_url="https://api.example")
    base.update(kwargs)
    return ont.LLMProvider(**base)


def _session(**kwargs) -> ont.MultiLLMSession:
    base = dict(session_id="s1", task_id="t1", models_used=["grok", "gpt"])
    base.update(kwargs)
    return ont.MultiLLMSession(**base)


def _patch_memory_paths(monkeypatch, tmp_path: Path, wid: str = "snap-test") -> dict:
    paths = {
        "workspace_id": wid,
        "dir": tmp_path,
        "file": tmp_path / f"{wid}.md",
        "lock": tmp_path / f"{wid}.lock",
    }
    monkeypatch.setattr(store_mod, "memory_paths", lambda cwd=None: paths)
    monkeypatch.setattr(ont, "memory_paths", lambda cwd=None: paths)
    return paths


def test_provider_roundtrip_ignores_extra():
    src = {
        "id": "p1",
        "type": "openai",
        "base_url": "https://api.example",
        "unknown": "drop-me",
    }
    got = ont.LLMProvider.from_dict(src)
    assert got.id == "p1"
    assert got.capabilities == {}
    assert "unknown" not in got.to_dict()


def test_session_roundtrip_nested_variant():
    src = {
        "session_id": "s1",
        "prompt_variants": [
            {
                "variant_id": "v1",
                "base_prompt": "hello",
                "extra": 1,
            }
        ],
        "noise": True,
    }
    got = ont.MultiLLMSession.from_dict(src)
    assert got.models_used == []
    assert got.prompt_variants[0].variant_id == "v1"
    assert got.prompt_variants[0].token_estimate == 0
    dumped = got.to_dict()
    assert "noise" not in dumped
    assert "extra" not in dumped["prompt_variants"][0]


def test_remaining_types_drop_extra_keys():
    cmp = ont.ModelComparisonResult.from_dict(
        {
            "result_id": "r1",
            "session_id": "s1",
            "model_a": "a",
            "model_b": "b",
            "junk": 1,
        }
    )
    assert cmp.metrics == {}
    assert "junk" not in cmp.to_dict()
    dec = ont.Decision.from_dict(
        {
            "decision_id": "d1",
            "session_id": "s1",
            "approved_model": "grok",
            "approved_output": "ok",
            "extra": True,
        }
    )
    assert dec.rationale == ""
    assert "extra" not in dec.to_dict()
    call = ont.CrossModelToolCall.from_dict(
        {
            "call_id": "c1",
            "session_id": "s1",
            "tool_name": "read",
            "model": "grok",
            "noise": "x",
        }
    )
    assert call.latency_ms == 0.0
    assert "noise" not in call.to_dict()
    variant = ont.PromptVariant.from_dict(
        {"variant_id": "v1", "base_prompt": "hi", "unknown": 2}
    )
    assert variant.token_estimate == 0
    assert "unknown" not in variant.to_dict()


def test_from_dict_helper_drops_unknown():
    got = ont._from_dict(
        ont.LLMProvider,
        {"id": "p1", "type": "openai", "base_url": "u", "unknown": 1},
    )
    assert got.id == "p1"
    assert "unknown" not in got.to_dict()


def test_create_session_and_query(tmp_path: Path):
    ont.create_llm_session(_session(), base_dir=tmp_path)
    by_task = ont.query_llm_sessions(task_id="t1", base_dir=tmp_path)
    assert len(by_task) == 1
    assert isinstance(by_task[0], dict)
    assert by_task[0]["session_id"] == "s1"
    by_model = ont.query_llm_sessions(model="grok", base_dir=tmp_path)
    assert len(by_model) == 1
    none = ont.query_llm_sessions(model="claude", base_dir=tmp_path)
    assert none == []


def test_crud_roundtrip(tmp_path: Path):
    created = ont.create_llm_provider(_provider(), base_dir=tmp_path)
    assert created["created"] == "p1"
    assert Path(created["file"]).parent == tmp_path
    sess = ont.create_multi_llm_session(_session(), base_dir=tmp_path)
    assert sess["created"] == "s1"
    cmp = ont.record_model_comparison(
        ont.ModelComparisonResult(
            result_id="r1", session_id="s1", model_a="grok", model_b="gpt"
        ),
        base_dir=tmp_path,
    )
    assert cmp["recorded"] == "r1"
    assert cmp["session"] == "s1"
    dec = ont.record_decision(
        ont.Decision(
            decision_id="d1",
            session_id="s1",
            approved_model="grok",
            approved_output="ok",
        ),
        base_dir=tmp_path,
    )
    assert dec["recorded"] == "d1"
    call = ont.record_cross_model_tool_call(
        ont.CrossModelToolCall(
            call_id="c1", session_id="s1", tool_name="read", model="grok"
        ),
        base_dir=tmp_path,
    )
    assert call["recorded"] == "c1"
    snap = ont.get_llm_ontology_snapshot(base_dir=tmp_path)
    assert snap["providers"][0]["id"] == "p1"
    assert snap["sessions"][0]["session_id"] == "s1"
    assert snap["comparisons"][0]["result_id"] == "r1"
    assert snap["decisions"][0]["decision_id"] == "d1"
    assert snap["tool_calls"][0]["call_id"] == "c1"


def test_snapshot_collections(tmp_path: Path):
    ont.create_llm_provider(_provider(), base_dir=tmp_path)
    snap = ont.get_llm_ontology_snapshot(base_dir=tmp_path)
    for key in ("providers", "sessions", "comparisons", "tool_calls", "decisions"):
        assert key in snap
    assert snap["providers"][0]["id"] == "p1"


def test_corrupt_json_returns_empty(tmp_path: Path):
    paths = ont._get_llm_paths(base_dir=tmp_path)
    paths["file"].write_text("{not json", encoding="utf-8")
    snap = ont.get_llm_ontology_snapshot(base_dir=tmp_path)
    assert snap["providers"] == []
    assert snap["sessions"] == []


def test_store_snapshot_includes_ontology(tmp_path: Path, monkeypatch):
    _patch_memory_paths(monkeypatch, tmp_path)
    ont.create_llm_provider(_provider(), base_dir=tmp_path)
    snap = store_mod.snapshot()
    assert snap["llm_ontology"]["providers"][0]["id"] == "p1"
    assert snap["workspace_id"] == "snap-test"
    assert "patterns" in snap


def test_schema_and_store_reexports():
    assert schema_mod.MultiLLMSession is ont.MultiLLMSession
    assert schema_mod.LLMProvider is ont.LLMProvider
    assert schema_mod.PromptVariant is ont.PromptVariant
    assert schema_mod.ModelComparisonResult is ont.ModelComparisonResult
    assert schema_mod.Decision is ont.Decision
    assert schema_mod.CrossModelToolCall is ont.CrossModelToolCall
    assert store_mod.create_llm_session is ont.create_llm_session
    assert store_mod.create_multi_llm_session is ont.create_llm_session
    assert store_mod.get_llm_ontology_snapshot is ont.get_llm_ontology_snapshot
    assert store_mod.record_cross_model_tool_call is ont.record_cross_tool_call
    assert ont.create_multi_llm_session is ont.create_llm_session
    assert ont.record_cross_model_tool_call is ont.record_cross_tool_call


def test_lock_file_during_write(tmp_path: Path, monkeypatch):
    seen: list[bool] = []
    lock = tmp_path / "llm_ontology.lock"
    orig_replace = Path.replace

    def wrapped(self: Path, target: Path) -> Path:
        if Path(target).name.endswith("llm_ontology.json"):
            seen.append(lock.is_file())
        return orig_replace(self, target)

    monkeypatch.setattr(Path, "replace", wrapped)
    ont.create_llm_provider(_provider(), base_dir=tmp_path)
    assert seen
    assert any(seen)
    assert not lock.exists()


def test_writes_stay_in_base_dir(tmp_path: Path):
    home_mem = Path.home() / ".grok" / "agentic-loop-memory"
    before = set()
    if home_mem.is_dir():
        before = {p.name for p in home_mem.glob("*.llm_ontology.json")}
    ont.create_llm_provider(_provider(), base_dir=tmp_path)
    files = list(tmp_path.glob("*.llm_ontology.json"))
    assert len(files) == 1
    assert files[0].parent == tmp_path
    if home_mem.is_dir():
        after = {p.name for p in home_mem.glob("*.llm_ontology.json")}
        assert after == before
