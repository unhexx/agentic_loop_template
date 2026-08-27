# NG11 agent_dir harvester / eval / resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship NG11 as Agentix **3.11.1**: additive `agent_dir=` on `meta_harvester`, `eval_harness`, and `resume`; named `agent_lock` on writers; tmp+replace for trajectory JSON/MD. No new CLI, no dashboard/supervisor wiring, no P8-12 split.

**Architecture:** Copy the playbooks/ledger pattern in place. Module `Path(".agent/…")` globals stay as cwd defaults. Explicit `agent_dir` is the `.agent` directory. Index RMW uses unlocked helpers inside one lock section (`agent_lock` is not reentrant). `update_performance_ledger` releases `"ledger"` before `append_cycle(..., agent_dir=)`.

**Tech Stack:** Python 3.10+, stdlib only. Existing `memory.agent_lock.agent_lock`. No new extra.

**Spec:** [`../specs/2026-08-27-ng11-agent-dir-harvester-di-design.md`](../specs/2026-08-27-ng11-agent-dir-harvester-di-design.md)

**Out of scope:** `experience_harvester.py`, dashboard, supervisor, `--agent-dir`, `PROMPT_COMPRESSION_GUIDE.md` locking, `store.py`, P8-12 split, Hub/MCP/messenger.

**House rules:** comments and commit messages in natural Russian (`DEVELOPMENT_STANDARDS.md` §1). Public names English. Do not mention AI/agents in commits. Do not commit live `.agent/`. Do not edit `memory/store.py`, `memory/experience_harvester.py`, `memory/dashboard/**`, `memory/supervisor.py`.

---

## File map

| Path | Action |
|------|--------|
| `memory/resume.py` | Add `_last_handoff` / `_loop_state`; `agent_dir=` on `load_last_handoff` and `build_resume_context`. No lock. |
| `memory/eval_harness.py` | Add `_trajectories`; `agent_dir=` on `_load_index` / `replay_recent`. No lock. `score_trajectory` unchanged. |
| `memory/meta_harvester.py` | Path helpers, named locks, tmp+replace, thread `agent_dir=` through disk functions. `basic_replay_harness` unchanged. |
| `memory/test_meta_lock.py` | Create. Harvest / sft / ledger lock and cwd-leak tests. |
| `memory/test_p5_p7.py` | Rewrite `TestResume` / `TestEvalHarness` to pass `agent_dir=`. Leave audit/questions classes alone. |
| `memory/test_meta_harvester.py` | Stop assigning Path globals. Pass `agent_dir=tmp/.agent`. |
| `VERSION` | `3.11.1` (last commit of this plan) |
| `CHANGELOG.md` | `[3.11.1]` section |
| `ROADMAP.md` | Drop NG11 Future bullet; milestone v3.11.1; badge |
| `README.md`, `docs/README.md` | Version badges only |

Do not add `memory/agent_paths.py`. Do not edit `architecture.md`.

**Interpreter:** prefer `.venv/bin/python`. Worktrees may use SSOT `/home/unhex/_PROJECT/agentic_loop_template/.venv/bin/python`. Prefix tests with `PYTHONPATH=.`.

---

### Task 1: Resume `agent_dir=`

**Files:**
- Modify: `memory/test_p5_p7.py` (`TestResume` only)
- Modify: `memory/resume.py`

- [ ] **Step 1: Rewrite `TestResume` so it fails until `agent_dir=` exists**

Replace class `TestResume` in `memory/test_p5_p7.py`. Keep `TestAuditLog` / `TestQuestionsAgentDir` untouched. Add `import os` at the top if missing.

```python
class TestResume(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.agent = Path(self.tmp.name) / ".agent"
        self.agent.mkdir()
        self._cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self._cwd)
        self.tmp.cleanup()

    def test_build_context_no_handoff(self):
        ctx = resume.build_resume_context(agent_dir=self.agent)
        self.assertFalse(ctx["resumable"])
        self.assertEqual(ctx["recommended_next_role"], "Orchestrator")

    def test_build_context_with_handoff(self):
        (self.agent / "last_handoff.json").write_text(
            json.dumps({
                "handoff_to": "Coder",
                "role": "Orchestrator",
                "status": "IN_PROGRESS",
                "cycle_number": 5,
                "summary": "test",
            }),
            encoding="utf-8",
        )
        ctx = resume.build_resume_context(agent_dir=self.agent)
        self.assertTrue(ctx["resumable"])
        self.assertEqual(ctx["recommended_next_role"], "Coder")

    def test_build_resume_context_agent_dir_not_cwd(self):
        (self.agent / "last_handoff.json").write_text(
            json.dumps({
                "handoff_to": "Tester",
                "role": "Coder",
                "status": "IN_PROGRESS",
                "cycle_number": 2,
                "summary": "elsewhere",
            }),
            encoding="utf-8",
        )
        (self.agent / "LOOP_STATE.md").write_text("# loop\n", encoding="utf-8")
        elsewhere = Path(self.tmp.name) / "cwd"
        elsewhere.mkdir()
        os.chdir(elsewhere)
        ctx = resume.build_resume_context(agent_dir=self.agent)
        self.assertTrue(ctx["resumable"])
        self.assertEqual(ctx["recommended_next_role"], "Tester")
        self.assertIn("loop", ctx["loop_state_excerpt"])
        self.assertFalse((elsewhere / ".agent").exists())
```

- [ ] **Step 2: Run the resume tests (expect FAIL)**

```bash
PYTHONPATH=. python -m pytest memory/test_p5_p7.py::TestResume -q
```

Expected: FAIL (`TypeError: build_resume_context() got an unexpected keyword argument 'agent_dir'`).

- [ ] **Step 3: Implement path helpers and `agent_dir=` in `memory/resume.py`**

Keep module globals. Readers do not mkdir. Comments in Russian.

```python
def _last_handoff(agent_dir: Optional[Path] = None) -> Path:
    """Явный каталог .agent или cwd-дефолт LAST_HANDOFF."""
    return Path(agent_dir) / "last_handoff.json" if agent_dir is not None else LAST_HANDOFF


def _loop_state(agent_dir: Optional[Path] = None) -> Path:
    return Path(agent_dir) / "LOOP_STATE.md" if agent_dir is not None else LOOP_STATE


def load_last_handoff(agent_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    path = _last_handoff(agent_dir)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    parent = path.parent
    if parent.exists():
        alt = list(parent.glob("handoff_*.json"))
        if alt:
            candidates = sorted(alt, key=lambda p: p.stat().st_mtime, reverse=True)
            return json.loads(candidates[0].read_text(encoding="utf-8"))
    return None


def build_resume_context(agent_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Собирает компактный контекст для возобновления цикла после сбоя."""
    handoff = load_last_handoff(agent_dir)
    loop_path = _loop_state(agent_dir)
    loop_note = loop_path.read_text(encoding="utf-8") if loop_path.exists() else ""
    ctx: Dict[str, Any] = {
        "resumable": handoff is not None,
        "last_handoff_to": handoff.get("handoff_to") if handoff else None,
        "last_role": handoff.get("role") if handoff else None,
        "last_status": handoff.get("status") if handoff else None,
        "cycle_number": handoff.get("cycle_number") if handoff else None,
        "summary": handoff.get("summary") if handoff else None,
        "next_input_files": handoff.get("next_input_files", []) if handoff else [],
        "issues_found": handoff.get("issues_found", []) if handoff else [],
        "loop_state_excerpt": loop_note[:500] if loop_note else "",
        "recommended_next_role": _next_role(handoff),
    }
    return ctx
```

`_cli` still calls `build_resume_context()` with no kwargs (cwd default). `_next_role` unchanged. No `agent_lock`.

- [ ] **Step 4: Re-run resume tests (expect PASS)**

```bash
PYTHONPATH=. python -m pytest memory/test_p5_p7.py::TestResume -q
```

Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add memory/resume.py memory/test_p5_p7.py
git commit -m "Пробросил каталог .agent в resume без смены cwd"
```

---

### Task 2: Eval harness `agent_dir=`

**Files:**
- Modify: `memory/test_p5_p7.py` (`TestEvalHarness` only)
- Modify: `memory/eval_harness.py`

- [ ] **Step 1: Rewrite `TestEvalHarness`**

```python
class TestEvalHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.agent = Path(self.tmp.name) / ".agent"
        self.agent.mkdir()
        self._cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self._cwd)
        self.tmp.cleanup()

    def test_score_trajectory(self):
        traj = {
            "id": "T-001",
            "cycle": 1,
            "confidence": 0.9,
            "tests_failed": 0,
            "process_violations": 0,
            "elapsed_minutes": 1.5,
            "outcome": "DONE",
        }
        s = eval_harness.score_trajectory(traj)
        self.assertGreater(s["score"], 50)
        self.assertEqual(s["outcome"], "DONE")

    def test_replay_empty(self):
        results = eval_harness.replay_recent(3, agent_dir=self.agent)
        self.assertEqual(results, [])

    def test_replay_recent_agent_dir_not_cwd(self):
        index = {
            "trajectories": [{
                "id": "T-002",
                "cycle": 2,
                "confidence": 0.9,
                "tests_failed": 0,
                "process_violations": 0,
                "elapsed_minutes": 1.0,
                "outcome": "DONE",
            }]
        }
        (self.agent / "TRAJECTORIES.json").write_text(
            json.dumps(index), encoding="utf-8"
        )
        elsewhere = Path(self.tmp.name) / "cwd"
        elsewhere.mkdir()
        os.chdir(elsewhere)
        results = eval_harness.replay_recent(5, agent_dir=self.agent)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["trajectory_id"], "T-002")
        self.assertFalse((elsewhere / ".agent").exists())
```

- [ ] **Step 2: Run eval tests (expect FAIL)**

```bash
PYTHONPATH=. python -m pytest memory/test_p5_p7.py::TestEvalHarness -q
```

Expected: FAIL (`unexpected keyword argument 'agent_dir'`).

- [ ] **Step 3: Implement in `memory/eval_harness.py`**

Add `Optional` to the typing import. `score_trajectory` unchanged. No lock. Readers do not mkdir.

```python
from typing import Any, Dict, List, Optional


def _trajectories(agent_dir: Optional[Path] = None) -> Path:
    """Явный каталог .agent или cwd-дефолт TRAJECTORIES."""
    return Path(agent_dir) / "TRAJECTORIES.json" if agent_dir is not None else TRAJECTORIES


def _load_index(agent_dir: Optional[Path] = None) -> Dict[str, Any]:
    path = _trajectories(agent_dir)
    if not path.exists():
        return {"trajectories": []}
    return json.loads(path.read_text(encoding="utf-8"))


def replay_recent(limit: int = 5, *, agent_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    index = _load_index(agent_dir)
    trajs = index.get("trajectories", [])
    recent = trajs[-limit:] if trajs else []
    return [score_trajectory(t) for t in recent]
```

`_cli` still calls `replay_recent(args.recent)` with no `agent_dir`.

- [ ] **Step 4: Re-run eval + resume (expect PASS)**

```bash
PYTHONPATH=. python -m pytest memory/test_p5_p7.py::TestResume memory/test_p5_p7.py::TestEvalHarness -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add memory/eval_harness.py memory/test_p5_p7.py
git commit -m "Пробросил каталог .agent в eval_harness для replay"
```

---

### Task 3: Failing lock tests for `meta_harvester`

**Files:**
- Create: `memory/test_meta_lock.py`

- [ ] **Step 1: Write `memory/test_meta_lock.py` in full**

Copy the lock-spy style of `memory/test_playbooks_lock.py` / `memory/test_audit_lock.py`. Do not harvest into the clone's live `.agent/`.

```python
# -*- coding: utf-8 -*-
"""Явный каталог .agent и именные локи meta_harvester."""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from memory.agent_lock import agent_lock as real_lock, lock_path


def _handoff(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "role": "Reviewer",
                "summary": "Успешно завершил задачу по sync",
                "confidence": 0.91,
                "metrics": {
                    "tests_total": 12,
                    "tests_failed": 0,
                    "coverage": 88.0,
                    "tool_calls": 7,
                    "elapsed_minutes": 9.5,
                },
                "process_tags": [],
                "lessons_learned": ["Всегда использовать явный маркер завершения"],
                "git_branch": "feature/sync-verify",
                "last_commit": "Улучшил верификацию с маркером SYNC_DONE",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_harvest_agent_dir_not_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import memory.meta_harvester as mh

    agent = tmp_path / "one" / ".agent"
    agent.mkdir(parents=True)
    cwd = tmp_path / "two"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    handoff = _handoff(tmp_path / "h.json")

    tid = mh.harvest_from_handoff(handoff, cycle=42, outcome="DONE", agent_dir=agent)
    assert tid is not None
    assert (agent / "TRAJECTORIES.json").is_file()
    assert (agent / "META_PROPOSALS.md").is_file()
    assert not (cwd / ".agent").exists()
    body = json.loads((agent / "TRAJECTORIES.json").read_text(encoding="utf-8"))
    assert body["trajectories"][0]["cycle"] == 42
    assert not list(agent.glob("*.tmp"))


def test_harvest_lock_name_and_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import memory.meta_harvester as mh

    agent = tmp_path / ".agent"
    agent.mkdir()
    lock_roots: list[Path] = []
    lock_names: list[str] = []
    orig = mh.agent_lock

    @contextmanager
    def spy_lock(agent_dir, *, name="agent", timeout=30.0):
        lock_roots.append(Path(agent_dir).resolve())
        lock_names.append(name)
        with orig(agent_dir, name=name, timeout=timeout):
            yield

    monkeypatch.setattr(mh, "agent_lock", spy_lock)
    mh.harvest_from_handoff(_handoff(tmp_path / "h.json"), cycle=7, outcome="DONE", agent_dir=agent)
    assert "trajectories" in lock_names
    assert all(r == agent.resolve() for r in lock_roots)
    hub = Path.cwd() / ".agent" / "trajectories.lock"
    assert hub.resolve() != lock_path(agent, "trajectories").resolve() or not hub.exists()


def test_harvest_lock_held_during_replace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import memory.meta_harvester as mh

    agent = tmp_path / ".agent"
    agent.mkdir()
    lp = lock_path(agent, "trajectories")
    seen: list[bool] = []
    orig_replace = Path.replace

    def wrapped(self: Path, target: Path) -> Path:
        if Path(target).name == "TRAJECTORIES.json":
            seen.append(lp.is_file())
        return orig_replace(self, target)

    monkeypatch.setattr(Path, "replace", wrapped)
    mh.harvest_from_handoff(_handoff(tmp_path / "h.json"), cycle=8, outcome="DONE", agent_dir=agent)
    assert seen
    assert any(seen)
    assert not lp.exists()


def test_harvest_releases_lock(tmp_path: Path) -> None:
    import memory.meta_harvester as mh

    agent = tmp_path / ".agent"
    mh.harvest_from_handoff(_handoff(tmp_path / "h.json"), cycle=9, outcome="DONE", agent_dir=agent)
    assert not lock_path(agent, "trajectories").exists()
    assert not list(agent.glob("*.tmp"))


def test_two_harvest_threads_max_held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import memory.meta_harvester as mh

    agent = tmp_path / ".agent"
    agent.mkdir()
    current = 0
    max_held = 0
    gate = threading.Lock()

    @contextmanager
    def counting_lock(agent_dir, *, name="agent", timeout=30.0):
        nonlocal current, max_held
        with real_lock(agent_dir, name=name, timeout=timeout):
            with gate:
                current += 1
                max_held = max(max_held, current)
            try:
                time.sleep(0.05)
                yield
            finally:
                with gate:
                    current -= 1

    monkeypatch.setattr(mh, "agent_lock", counting_lock)
    errors: list[BaseException] = []
    handoffs = []
    for i, cycle in enumerate((11, 12, 13, 14)):
        p = _handoff(tmp_path / f"h{i}.json")
        handoffs.append((p, cycle))

    def worker(item: tuple[Path, int]) -> None:
        try:
            mh.harvest_from_handoff(item[0], cycle=item[1], outcome="DONE", agent_dir=agent)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(item,)) for item in handoffs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
        assert not t.is_alive()
    assert errors == []
    assert max_held == 1
    body = json.loads((agent / "TRAJECTORIES.json").read_text(encoding="utf-8"))
    cycles = {t["cycle"] for t in body["trajectories"]}
    assert cycles == {11, 12, 13, 14}
    assert not lock_path(agent, "trajectories").exists()


def test_export_sft_default_uses_sft_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import memory.meta_harvester as mh

    agent = tmp_path / ".agent"
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    names: list[str] = []
    orig = mh.agent_lock

    @contextmanager
    def spy(agent_dir, *, name="agent", timeout=30.0):
        names.append(name)
        with orig(agent_dir, name=name, timeout=timeout):
            yield

    monkeypatch.setattr(mh, "agent_lock", spy)
    mh.seed_example_trajectory(agent_dir=agent)
    report = mh.export_sft(agent_dir=agent)
    assert report["written"] >= 1
    assert "sft" in names
    assert (agent / "sft" / "train.jsonl").is_file()
    assert not (cwd / ".agent").exists()


def test_export_sft_out_skips_sft_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import memory.meta_harvester as mh

    agent = tmp_path / ".agent"
    names: list[str] = []
    orig = mh.agent_lock

    @contextmanager
    def spy(agent_dir, *, name="agent", timeout=30.0):
        names.append(name)
        with orig(agent_dir, name=name, timeout=timeout):
            yield

    monkeypatch.setattr(mh, "agent_lock", spy)
    mh.seed_example_trajectory(agent_dir=agent)
    dest = tmp_path / "other.jsonl"
    names.clear()
    report = mh.export_sft(out=dest, agent_dir=agent)
    assert report["written"] >= 1
    assert dest.is_file()
    assert "sft" not in names


def test_update_performance_ledger_passes_agent_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import memory.meta_harvester as mh

    agent = tmp_path / ".agent"
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    mh.update_performance_ledger("P-1", "impact", agent_dir=agent)
    assert (agent / "LOOP_PERFORMANCE.md").is_file()
    text = (agent / "LOOP_PERFORMANCE.md").read_text(encoding="utf-8")
    assert "P-1" in text
    assert (agent / "PERFORMANCE_LEDGER.json").is_file()
    assert not (cwd / ".agent" / "LOOP_PERFORMANCE.md").exists()
    assert not (cwd / ".agent" / "PERFORMANCE_LEDGER.json").exists()


def test_ledger_lock_not_nested(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import memory.agent_lock as al
    import memory.meta_harvester as mh

    agent = tmp_path / ".agent"
    depth = 0
    max_depth = 0
    orig = al.agent_lock

    @contextmanager
    def counting(agent_dir, *, name="agent", timeout=30.0):
        nonlocal depth, max_depth
        if name == "ledger":
            depth += 1
            max_depth = max(max_depth, depth)
            try:
                with orig(agent_dir, name=name, timeout=timeout):
                    yield
            finally:
                depth -= 1
        else:
            with orig(agent_dir, name=name, timeout=timeout):
                yield

    monkeypatch.setattr(al, "agent_lock", counting)
    monkeypatch.setattr(mh, "agent_lock", counting)
    mh.update_performance_ledger("P-2", agent_dir=agent)
    assert max_depth == 1
    assert depth == 0
```

- [ ] **Step 2: Run lock tests (expect FAIL)**

```bash
PYTHONPATH=. python -m pytest memory/test_meta_lock.py -q
```

Expected: FAIL (`unexpected keyword argument 'agent_dir'` and/or `AttributeError: agent_lock`). Do not implement yet.

- [ ] **Step 3: Commit tests only**

```bash
git add memory/test_meta_lock.py
git commit -m "Добавил тесты лока и agent_dir для meta_harvester"
```

---

### Task 4: Implement `meta_harvester` path helpers, locks, tmp+replace

**Files:**
- Modify: `memory/meta_harvester.py`

- [ ] **Step 1: Add imports and path/lock helpers after the module Path globals**

Keep existing globals (`TRAJECTORIES_INDEX`, `TRAJECTORIES_DIR`, `META_PROPOSALS_MD`, `PROJECT_CONFIG`, `SFT_PATH`). Add:

```python
from memory.agent_lock import agent_lock
from memory.logutil import get_logger

log = get_logger("memory.meta_harvester")


def _trajectories_index(agent_dir: Optional[Path] = None) -> Path:
    """Явный каталог .agent или cwd-дефолт индекса траекторий."""
    return Path(agent_dir) / "TRAJECTORIES.json" if agent_dir is not None else TRAJECTORIES_INDEX


def _trajectories_dir(agent_dir: Optional[Path] = None) -> Path:
    return Path(agent_dir) / "TRAJECTORIES" if agent_dir is not None else TRAJECTORIES_DIR


def _meta_proposals_md(agent_dir: Optional[Path] = None) -> Path:
    return Path(agent_dir) / "META_PROPOSALS.md" if agent_dir is not None else META_PROPOSALS_MD


def _project_config_path(agent_dir: Optional[Path] = None) -> Path:
    return Path(agent_dir) / "project_config.json" if agent_dir is not None else PROJECT_CONFIG


def _sft_path(agent_dir: Optional[Path] = None) -> Path:
    return Path(agent_dir) / "sft" / "train.jsonl" if agent_dir is not None else SFT_PATH


def _loop_performance_md(agent_dir: Optional[Path] = None) -> Path:
    return Path(agent_dir) / "LOOP_PERFORMANCE.md" if agent_dir is not None else Path(".agent/LOOP_PERFORMANCE.md")


def _ensure_agent_dir(agent_dir: Optional[Path] = None) -> None:
    """Гарантирует каталог индекса и TRAJECTORIES/."""
    _trajectories_index(agent_dir).parent.mkdir(parents=True, exist_ok=True)
    _trajectories_dir(agent_dir).mkdir(parents=True, exist_ok=True)


def _trajectories_lock(agent_dir: Optional[Path] = None):
    """Секция на родителе индекса — имя trajectories, не agent."""
    return agent_lock(_trajectories_index(agent_dir).parent, name="trajectories")


def _atomic_write_text(path: Path, text: str) -> None:
    """Пишет через *.tmp и os.replace, без усечения целевого файла."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".json":
        tmp = path.with_suffix(".json.tmp")
    elif path.suffix == ".md":
        tmp = path.with_suffix(".md.tmp")
    else:
        tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
```

- [ ] **Step 2: Replace `_load_index` / `_save_index` / `_write_human_summary` / `load_config`**

`load_config(agent_dir=None)` reads `_project_config_path(agent_dir)` instead of `PROJECT_CONFIG`.

```python
def _empty_index() -> Dict[str, Any]:
    return {"trajectories": [], "proposals": [], "updated_at": _now_iso()}


def _load_index_unlocked(agent_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Чтение без lock: RMW уже держит секцию."""
    _ensure_agent_dir(agent_dir)
    path = _trajectories_index(agent_dir)
    if not path.exists():
        return _empty_index()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        log.warning("trajectories index corrupt, renaming to bak: %s", path)
        try:
            path.rename(path.with_suffix(".json.bak"))
        except Exception:
            pass
        return _empty_index()


def _write_index_unlocked(data: Dict[str, Any], agent_dir: Optional[Path] = None) -> None:
    """tmp+replace JSON + md. Вызывающий уже в секции trajectories."""
    data["updated_at"] = _now_iso()
    path = _trajectories_index(agent_dir)
    _atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))
    _write_human_summary(data, agent_dir=agent_dir)


def _load_index(agent_dir: Optional[Path] = None) -> Dict[str, Any]:
    with _trajectories_lock(agent_dir):
        return _load_index_unlocked(agent_dir)


def _save_index(data: Dict[str, Any], agent_dir: Optional[Path] = None) -> None:
    with _trajectories_lock(agent_dir):
        _write_index_unlocked(data, agent_dir)
```

`_write_human_summary(data, agent_dir=None)`: same markdown as today, but `cfg = load_config(agent_dir)` and write via `_atomic_write_text(_meta_proposals_md(agent_dir), "\n".join(lines))`. Do not change the report text.

- [ ] **Step 3: Wrap RMW public functions**

**`harvest_from_handoff(..., quality_signals=None, agent_dir=None)`** (positional `agent_dir` at the end, like `seed_initial_playbooks`):

- `cfg = load_config(agent_dir)`
- Replace `index = _load_index()` + `_save_index(index)` with one section:

```python
    with _trajectories_lock(agent_dir):
        index = _load_index_unlocked(agent_dir)
        trajs = index.setdefault("trajectories", [])
        for existing in trajs:
            if existing.get("cycle") == cycle and existing.get("outcome") == outcome:
                return existing.get("id")
        tid = _next_traj_id(trajs, cycle)
        # ... existing trajectory dict unchanged ...
        trajs.append(trajectory)
        _write_index_unlocked(index, agent_dir)
        return tid
```

Do not call `_save_index` inside this `with` (not reentrant).

**`get_recent_trajectories(limit=5, *, agent_dir=None)`:** `index = _load_index(agent_dir)` then the same slice.

**`export_sft(out=None, min_confidence=..., recent=100, *, agent_dir=None)`:**

```python
    dest = Path(out) if out is not None else _sft_path(agent_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    index = _load_index(agent_dir)
    trajs = list(index.get("trajectories") or [])
    if recent:
        trajs = trajs[-int(recent) :]
    written = 0
    skipped = 0

    def _append() -> None:
        nonlocal written, skipped
        with dest.open("a", encoding="utf-8") as fh:
            for traj in trajs:
                if not _traj_qualifies(traj, min_confidence):
                    skipped += 1
                    continue
                rec = _sft_record(traj)
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                written += 1

    if out is None:
        with agent_lock(dest.parent, name="sft"):
            _append()
    else:
        _append()
    return {
        "written": written,
        "skipped": skipped,
        "path": str(dest),
        "min_confidence": min_confidence,
    }
```

**`analyze_for_proposals(recent=5, min_confidence=0.8, *, agent_dir=None)`:** do not call `get_recent_trajectories` (that would lock, then you lock again). Inside one `"trajectories"` section: `_load_index_unlocked`, slice trajs from the index the same way `get_recent_trajectories` does (`list(reversed(...))[:recent]` then confidence filter), keep heuristic bodies unchanged, `_write_index_unlocked` at the end. If no trajs after filter, `return []` without write.

**`generate_proposals(limit=3, *, agent_dir=None)`:** `return analyze_for_proposals(agent_dir=agent_dir)[:limit]`.

**`seed_example_trajectory(agent_dir=None)`:** one `"trajectories"` section, unlocked load/write, same example payload.

**`apply_safe_proposals(dry_run=True, ids=None, *, agent_dir=None)`:**

```python
    applied_ids: List[Tuple[str, str]] = []  # id, impact
    with _trajectories_lock(agent_dir):
        index = _load_index_unlocked(agent_dir)
        props = index.get("proposals", [])
        to_apply = [p for p in props if p.get("safe_to_auto") and p.get("status", "pending") == "pending"]
        if ids:
            idset = set(ids)
            to_apply = [p for p in to_apply if p.get("id") in idset]
        guide_path = Path("PROMPT_COMPRESSION_GUIDE.md")  # cwd, не agent_dir
        applied = 0
        for p in to_apply:
            if dry_run:
                print(f"[DRY-RUN] Would apply {p['id']} to {p.get('target_file')}: {p['title']}")
                continue
            did_edit = False
            # ... existing GUIDE edit body unchanged ...
            p["status"] = "applied"
            p["applied_at"] = _now_iso()
            if did_edit:
                p["notes"] = p.get("notes", "") + " (auto-appended to file)"
            applied_ids.append((p["id"], p.get("expected_impact", "applied via meta")))
            applied += 1
        if applied > 0:
            _write_index_unlocked(index, agent_dir)
    for pid, impact in applied_ids:
        update_performance_ledger(pid, impact, agent_dir=agent_dir)
    return applied
```

Dry-run: no index write, no ledger, no GUIDE. Return value today is `applied`, which stays 0 on dry-run even when `to_apply` is non-empty (the loop `continue`s before `applied += 1`). Keep returning `applied` so `test_meta_harvester.py` still sees an int.

**`update_performance_ledger(proposal_id, impact="", cycle_stats=None, *, agent_dir=None)`:**

```python
    _ensure_agent_dir(agent_dir)
    ledger = _loop_performance_md(agent_dir)
    with agent_lock(ledger.parent, name="ledger"):
        lines: List[str] = []
        if ledger.exists():
            lines = ledger.read_text(encoding="utf-8").splitlines()
        lines.append(f"- { _now_iso() } | proposal {proposal_id} | {impact or 'applied'}")
        _atomic_write_text(ledger, "\n".join(lines[-50:]) + "\n")
    try:
        import importlib.util
        pl_path = Path(__file__).parent / "performance_ledger.py"
        spec = importlib.util.spec_from_file_location("pl", str(pl_path))
        pl = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pl)
        if cycle_stats:
            pl.append_cycle(agent_dir=agent_dir, **cycle_stats)
        else:
            pl.append_cycle(
                agent_dir=agent_dir,
                cycle=0,
                outcome="META_APPLIED",
                notes=f"proposal:{proposal_id} impact:{impact}",
                meta_applied=1,
            )
    except Exception as e:
        print(f"[performance_ledger] non-fatal: {e}", file=sys.stderr)
```

Release `"ledger"` before `append_cycle`. Do not change the file-location import. `basic_replay_harness` untouched. `_cli` still calls functions with no `agent_dir`. No `--agent-dir` flag.

- [ ] **Step 4: Run lock tests (expect PASS)**

```bash
PYTHONPATH=. python -m pytest memory/test_meta_lock.py -q
```

Expected: PASS (9 passed). If `test_harvest_lock_name_and_parent` sees extra names, harvest should only take `"trajectories"` (config read is unlocked). If `test_ledger_lock_not_nested` hangs, you nested `"ledger"`: release before `append_cycle`. If `test_export_sft_default_uses_sft_lock` never sees `"sft"`, the lock is missing on the default dest.

- [ ] **Step 5: Commit**

```bash
git add memory/meta_harvester.py
git commit -m "Пробросил agent_dir и именные локи в meta_harvester"
```

---

### Task 5: Rewrite script-style `test_meta_harvester.py`

**Files:**
- Modify: `memory/test_meta_harvester.py`

- [ ] **Step 1: Stop assigning Path globals**

Inside `test_basic()`, after creating `tmp_path`, set `agent = tmp_path / ".agent"` and pass `agent_dir=agent` into harvest / get_recent / analyze / apply_safe / seed / update_performance_ledger. Keep `export_sft(out=sft, ...)` as an explicit dest; also pass `agent_dir=agent` so the index is read from tmp.

Remove:

```python
        orig_index = mh.TRAJECTORIES_INDEX
        orig_md = mh.META_PROPOSALS_MD
        mh.TRAJECTORIES_INDEX = tmp_path / "TRAJECTORIES.json"
        mh.META_PROPOSALS_MD = tmp_path / "META_PROPOSALS.md"
```

and the restore at the end.

Replace the ledger assertion:

```python
        mh.update_performance_ledger("P-DEMO-001", "demo impact on compression", agent_dir=agent)
        ledger = agent / "LOOP_PERFORMANCE.md"
        assert ledger.exists()
```

`load_config()` with no kwargs may still hit cwd (repo `.agent/project_config.json`). That is allowed. Do not call harvest without `agent_dir` from this script.

- [ ] **Step 2: Run the script and pytest**

```bash
PYTHONPATH=. python memory/test_meta_harvester.py
PYTHONPATH=. python -m pytest memory/test_meta_lock.py memory/test_p5_p7.py -q
```

Expected: script prints `=== Все базовые тесты пройдены ===`. Pytest PASS. Clone `.agent/LOOP_PERFORMANCE.md` mtime must not jump from this script (it used to write cwd).

- [ ] **Step 3: Commit**

```bash
git add memory/test_meta_harvester.py
git commit -m "Перевёл test_meta_harvester на явный agent_dir"
```

---

### Task 6: Version 3.11.1 and docs

**Files:**
- Modify: `VERSION`, `CHANGELOG.md`, `ROADMAP.md`, `README.md`, `docs/README.md`

- [ ] **Step 1: Full pytest (expect PASS, VERSION still 3.11.0 until this commit)**

```bash
PYTHONPATH=. python -m pytest -q memory/
```

Expected: PASS. If the worktree has no `.venv`:

```bash
PYTHONPATH=. /home/unhex/_PROJECT/agentic_loop_template/.venv/bin/python -m pytest -q memory/
```

- [ ] **Step 2: Bump docs**

`VERSION` file contents:

```
3.11.1
```

`CHANGELOG.md` insert after `## [Unreleased]`:

```markdown
## [3.11.1] - 2026-08-27

### Added
- Additive `agent_dir=` on `memory.meta_harvester`, `memory.eval_harness`, `memory.resume`. Named `agent_lock` on harvester writers: `trajectories` (`TRAJECTORIES.json` + `META_PROPOSALS.md`), `sft` (default `sft/train.jsonl`), `ledger` (`LOOP_PERFORMANCE.md`). tmp+replace for the JSON/MD indexes. `update_performance_ledger` passes `agent_dir` into `append_cycle` after releasing the md lock.
- Design spec: [`docs/superpowers/specs/2026-08-27-ng11-agent-dir-harvester-di-design.md`](docs/superpowers/specs/2026-08-27-ng11-agent-dir-harvester-di-design.md)

### Changed
- `VERSION` → 3.11.1
- ROADMAP: NG11 Future bullet removed; milestone v3.11.1

Patch, not 3.12.0: no `--agent-dir`, wizard/proxy/`--concurrent` default unchanged, dashboard and supervisor not wired.
```

`ROADMAP.md`:

- Badge `version-3.11.1`
- Status line: add **v3.11 NG11 harvester DI** — **COMPLETE**; **Next:** Future
- Delete the bullet `` `agent_dir=` / `agent_lock` on `meta_harvester`, `eval_harness`, `resume` cwd writers (NG11) ``
- Milestones table, new first row: `| **v3.11.1** | NG11: `agent_dir=` + named locks on meta_harvester / eval_harness / resume |`

`README.md` and `docs/README.md`: badge `version-3.11.1`. In `docs/README.md` Version paragraph, change `**Agentix 3.11.0**` to `**Agentix 3.11.1**` and add one clause: `NG11 agent_dir=` on harvest/eval/resume. Do not rewrite `architecture.md`.

Do not commit live `.agent/`. Do not bump examples that are not badges.

- [ ] **Step 3: Commit release**

```bash
git add VERSION CHANGELOG.md ROADMAP.md README.md docs/README.md
git commit -m "Обновил версию до 3.11.1: NG11 agent_dir для harvester"
```

---

## Pytest (canonical)

```bash
PYTHONPATH=. python -m pytest memory/test_meta_lock.py memory/test_p5_p7.py memory/test_playbooks_lock.py memory/test_performance_ledger.py memory/test_agent_lock.py -q
```

Then full `PYTHONPATH=. python -m pytest -q memory/` before push.

## Done when

- `resume.build_resume_context(agent_dir=)` and `eval_harness.replay_recent(..., agent_dir=)` read tmp `.agent` after `chdir` elsewhere.
- `harvest_from_handoff(..., agent_dir=)` writes `TRAJECTORIES.json` + `META_PROPOSALS.md` under that dir, not cwd; lock name `"trajectories"`; lock held during `Path.replace`; released after; two threads `max_held == 1`; no leftover `*.tmp`.
- Default `export_sft(agent_dir=)` takes `"sft"`; explicit `out=` does not.
- `update_performance_ledger(..., agent_dir=)` writes `LOOP_PERFORMANCE.md` and `PERFORMANCE_LEDGER.json` under that dir; `"ledger"` is not nested.
- `test_p5_p7.py` / `test_meta_harvester.py` pass `agent_dir=` instead of assigning Path globals.
- No `--agent-dir`, no dashboard/supervisor/`experience_harvester` edits.
- VERSION 3.11.1, NG11 dropped from ROADMAP Future.
- Comments and commits in Russian.

## Self-review (plan vs spec)

| Spec | Task |
|------|------|
| G1 additive `agent_dir=` | T1 resume, T2 eval, T4 harvester |
| G2 named locks, readers unlocked | T3/T4; resume/eval have no lock |
| G3 tmp+replace JSON/MD; sft append | T4 `_atomic_write_text` + `export_sft` |
| G4 unlocked RMW | T4 harvest / analyze / apply_safe / seed |
| G5 ledger release before `append_cycle` | T4 `update_performance_ledger` + T3 `test_ledger_lock_not_nested` |
| G6 tests | T1–T3, T5 |
| G7 3.11.1 last | T6 |
| NG1–NG10 | File map + out of scope |
| `basic_replay_harness` pure | T4 "untouched" |
| GUIDE stays cwd | T4 `apply_safe` |
| CLI no new flags | T4 `_cli` unchanged |
| One implementation PR worth of files | Tasks 1–6; human can squash |
