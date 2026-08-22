# -*- coding: utf-8 -*-
"""Тесты P5/P7: audit_log, resume, eval_harness."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory import audit_log, eval_harness, questions_collector, resume


class TestAuditLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        audit_log.AUDIT_JSON = Path(self.tmp.name) / "AUDIT_LOG.json"
        audit_log.AUDIT_MD = Path(self.tmp.name) / "AUDIT_LOG.md"

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_and_list(self):
        e = audit_log.append_entry("test_action", "tester", 1, {"ok": True})
        self.assertIn("signature", e)
        entries = audit_log.list_entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["action"], "test_action")

    def test_append_entry_agent_dir_does_not_use_globals(self):
        agent = Path(self.tmp.name) / "agent"
        agent.mkdir()
        e = audit_log.append_entry(
            "dashboard.stop",
            "operator",
            12,
            {"ok": True},
            approval_required=True,
            approved=True,
            agent_dir=agent,
        )
        self.assertEqual(e["role"], "operator")
        written = json.loads((agent / "AUDIT_LOG.json").read_text(encoding="utf-8"))
        self.assertEqual(written["entries"][-1]["action"], "dashboard.stop")
        self.assertFalse(audit_log.AUDIT_JSON.exists())


class TestQuestionsAgentDir(unittest.TestCase):
    def test_mark_reviewed_agent_dir(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        agent = Path(tmp.name) / "agent"
        agent.mkdir()
        (agent / "QUESTIONS_POOL.json").write_text(
            json.dumps(
                {
                    "questions": [
                        {"id": "Q-001", "question": "x", "status": "open"}
                    ],
                    "last_escalated_cycle": 0,
                }
            ),
            encoding="utf-8",
        )
        n = questions_collector.mark_reviewed(
            ["Q-001"], "done", "operator", agent_dir=agent
        )
        self.assertEqual(n, 1)
        data = json.loads((agent / "QUESTIONS_POOL.json").read_text(encoding="utf-8"))
        self.assertEqual(data["questions"][0]["status"], "resolved")
        self.assertEqual(data["questions"][0]["resolved_by"], "operator")
        self.assertEqual(data["questions"][0]["resolution"], "done")


    def test_torn_pool_not_bak(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        agent = Path(tmp.name) / "agent"
        agent.mkdir()
        pool = agent / "QUESTIONS_POOL.json"
        pool.write_text("{", encoding="utf-8")
        questions_collector.TORN_RETRY_S = 0
        self.addCleanup(lambda: setattr(questions_collector, "TORN_RETRY_S", 0.020))
        n = questions_collector.mark_reviewed(
            ["Q-001"], "x", "operator", agent_dir=agent
        )
        self.assertEqual(n, 0)
        self.assertTrue(pool.exists())
        self.assertFalse(pool.with_suffix(".json.bak").exists())
        self.assertEqual(pool.read_text(encoding="utf-8"), "{")

    def test_torn_pool_retry_then_ok(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        agent = Path(tmp.name) / "agent"
        agent.mkdir()
        pool = agent / "QUESTIONS_POOL.json"
        pool.write_text("{", encoding="utf-8")
        valid = json.dumps(
            {
                "questions": [{"id": "Q-001", "question": "x", "status": "open"}],
                "last_escalated_cycle": 0,
            }
        )

        def repair(_s):
            pool.write_text(valid, encoding="utf-8")

        from unittest import mock

        with mock.patch("memory.questions_collector.time.sleep", repair):
            n = questions_collector.mark_reviewed(
                ["Q-001"], "fixed", "operator", agent_dir=agent
            )
        self.assertEqual(n, 1)
        data = json.loads(pool.read_text(encoding="utf-8"))
        self.assertEqual(data["questions"][0]["status"], "resolved")


class TestAuditTorn(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        audit_log.AUDIT_JSON = Path(self.tmp.name) / "AUDIT_LOG.json"
        audit_log.AUDIT_MD = Path(self.tmp.name) / "AUDIT_LOG.md"
        audit_log.TORN_RETRY_S = 0

    def tearDown(self):
        audit_log.TORN_RETRY_S = 0.020
        self.tmp.cleanup()

    def test_torn_audit_not_wiped(self):
        audit_log.AUDIT_JSON.write_text("{", encoding="utf-8")
        audit_log.append_entry("dashboard.stop", "operator", 1)
        self.assertEqual(audit_log.AUDIT_JSON.read_text(encoding="utf-8"), "{")

    def test_append_without_entries_key(self):
        audit_log.AUDIT_JSON.write_text(
            json.dumps({"updated_at": "t"}), encoding="utf-8"
        )
        e = audit_log.append_entry("dashboard.stop", "operator", 1)
        self.assertTrue(e["id"].startswith("A-"))
        data = json.loads(audit_log.AUDIT_JSON.read_text(encoding="utf-8"))
        self.assertEqual(len(data["entries"]), 1)
        self.assertEqual(data["entries"][0]["action"], "dashboard.stop")


class TestResume(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        resume.LAST_HANDOFF = Path(self.tmp.name) / "last_handoff.json"
        resume.LOOP_STATE = Path(self.tmp.name) / "LOOP_STATE.md"

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_context_no_handoff(self):
        ctx = resume.build_resume_context()
        self.assertFalse(ctx["resumable"])
        self.assertEqual(ctx["recommended_next_role"], "Orchestrator")

    def test_build_context_with_handoff(self):
        resume.LAST_HANDOFF.write_text(
            json.dumps({"handoff_to": "Coder", "role": "Orchestrator", "status": "IN_PROGRESS",
                        "cycle_number": 5, "summary": "test"}),
            encoding="utf-8",
        )
        ctx = resume.build_resume_context()
        self.assertTrue(ctx["resumable"])
        self.assertEqual(ctx["recommended_next_role"], "Coder")


class TestEvalHarness(unittest.TestCase):
    def test_score_trajectory(self):
        traj = {"id": "T-001", "cycle": 1, "confidence": 0.9, "tests_failed": 0,
                "process_violations": 0, "elapsed_minutes": 1.5, "outcome": "DONE"}
        s = eval_harness.score_trajectory(traj)
        self.assertGreater(s["score"], 50)
        self.assertEqual(s["outcome"], "DONE")

    def test_replay_empty(self):
        old = eval_harness.TRAJECTORIES
        eval_harness.TRAJECTORIES = Path("/nonexistent/trajectories.json")
        results = eval_harness.replay_recent(3)
        self.assertEqual(results, [])
        eval_harness.TRAJECTORIES = old


if __name__ == "__main__":
    unittest.main()