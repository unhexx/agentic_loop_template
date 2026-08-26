# -*- coding: utf-8 -*-
"""Запись пула вопросов под agent_lock: tmp+replace, без lock-файла хаба."""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List

from memory.agent_lock import lock_path
from memory import questions_collector as qc


def _agent(tmp_path: Path) -> Path:
    agent = tmp_path / ".agent"
    agent.mkdir()
    return agent


def _hub_question_locks() -> List[Path]:
    return [
        Path.cwd() / ".agent" / "questions.lock",
        Path(__file__).resolve().parent.parent / ".agent" / "questions.lock",
        Path(__file__).resolve().parent / ".agent" / "questions.lock",
    ]


def test_append_question_under_lock_releases(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    qid = qc.append_question("нужен ли lock на пул?", agent_dir=agent)
    assert qid == "Q-001"
    pool = agent / "QUESTIONS_POOL.json"
    data = json.loads(pool.read_text(encoding="utf-8"))
    assert data["questions"][0]["question"] == "нужен ли lock на пул?"
    assert data["questions"][0]["status"] == "open"
    md = agent / "QUESTIONS_POOL.md"
    assert md.is_file()
    assert "нужен ли lock на пул?" in md.read_text(encoding="utf-8")
    assert not lock_path(agent, "questions").exists()
    assert not (agent / "QUESTIONS_POOL.json.tmp").exists()


def test_mark_reviewed_under_lock_releases(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    qid = qc.append_question("закрыть вопрос?", agent_dir=agent)
    n = qc.mark_reviewed([qid], "сделано", "operator", agent_dir=agent)
    assert n == 1
    data = json.loads((agent / "QUESTIONS_POOL.json").read_text(encoding="utf-8"))
    assert data["questions"][0]["status"] == "resolved"
    assert data["questions"][0]["resolution"] == "сделано"
    assert data["questions"][0]["resolved_by"] == "operator"
    assert not lock_path(agent, "questions").exists()


def test_explicit_agent_dir_does_not_create_hub_lock(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    before = {p: p.exists() for p in _hub_question_locks()}
    qid = qc.append_question("не трогать хаб", agent_dir=agent)
    assert qid.startswith("Q-")
    written = json.loads((agent / "QUESTIONS_POOL.json").read_text(encoding="utf-8"))
    assert written["questions"][0]["question"] == "не трогать хаб"
    assert written["questions"][0]["id"] == qid
    for path, existed in before.items():
        if not existed:
            assert not path.exists(), path


def test_append_question_none_uses_module_globals(tmp_path: Path, monkeypatch) -> None:
    pool = tmp_path / "QUESTIONS_POOL.json"
    md = tmp_path / "QUESTIONS_POOL.md"
    monkeypatch.setattr(qc, "POOL_JSON", pool)
    monkeypatch.setattr(qc, "POOL_MD", md)
    qid = qc.append_question("через глобали модуля")
    assert qid == "Q-001"
    data = json.loads(pool.read_text(encoding="utf-8"))
    assert data["questions"][0]["question"] == "через глобали модуля"
    assert md.is_file()
    assert "через глобали модуля" in md.read_text(encoding="utf-8")
    assert not (tmp_path / "questions.lock").exists()


def test_two_threads_max_held_one(tmp_path: Path, monkeypatch) -> None:
    agent = _agent(tmp_path)
    held = 0
    max_held = 0
    counter = threading.Lock()
    names: List[str] = []
    orig = qc.agent_lock
    hub_before = {p: p.exists() for p in _hub_question_locks()}

    @contextmanager
    def wrapping_lock(*args, **kwargs) -> Iterator[None]:
        nonlocal held, max_held
        names.append(str(kwargs.get("name")))
        with orig(*args, **kwargs):
            with counter:
                held += 1
                if held > max_held:
                    max_held = held
            # окно, чтобы второй поток успел подойти к lock, пока первый ещё внутри
            time.sleep(0.05)
            try:
                yield
            finally:
                with counter:
                    held -= 1

    monkeypatch.setattr(qc, "agent_lock", wrapping_lock)
    barrier = threading.Barrier(2)
    errors: List[BaseException] = []

    def worker(text: str) -> None:
        try:
            barrier.wait(timeout=5)
            qc.append_question(text, agent_dir=agent)
        except BaseException as exc:
            errors.append(exc)

    t1 = threading.Thread(target=worker, args=("поток-а",))
    t2 = threading.Thread(target=worker, args=("поток-б",))
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)
    assert not t1.is_alive() and not t2.is_alive()
    assert errors == []
    assert max_held == 1
    assert names == ["questions", "questions"]
    data = json.loads((agent / "QUESTIONS_POOL.json").read_text(encoding="utf-8"))
    questions = data.get("questions")
    assert isinstance(questions, list)
    assert len(questions) >= 1
    texts = {q.get("question") for q in questions}
    assert texts & {"поток-а", "поток-б"}
    assert not lock_path(agent, "questions").exists()
    for path, existed in hub_before.items():
        if not existed:
            assert not path.exists(), path
