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