# -*- coding: utf-8 -*-
"""Тесты Hub CLI: list, export, discover."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory.playbooks import discover_items, export_hub_index, list_playbooks, seed_initial_playbooks


class TestPlaybooksHub(unittest.TestCase):
    def setUp(self):
        seed_initial_playbooks()

    def test_list_returns_items(self):
        items = list_playbooks()
        self.assertIsInstance(items, list)
        self.assertGreater(len(items), 0)
        first = items[0]
        self.assertIn("id", first)
        self.assertIn("scope", first)
        self.assertIn("bullet_count", first)

    def test_export_hub_format(self):
        data = export_hub_index(fmt="hub")
        self.assertEqual(data["version"], "1.0")
        self.assertIn("items", data)
        self.assertIn("playbooks", data)
        self.assertGreater(data["item_count"], 0)
        hub_path = Path(".agent/HUB_INDEX.json")
        self.assertTrue(hub_path.exists())

    def test_discover_returns_scored_results(self):
        results = discover_items("git sync", k=3)
        self.assertIsInstance(results, list)
        if results:
            self.assertIn("content", results[0])
            self.assertIn("score", results[0])

    def test_hub_index_matches_schema_shape(self):
        schema_path = Path("docs/hub/api-schema.json")
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        hub_defs = schema["definitions"]["HubIndex"]["properties"]
        data = export_hub_index(fmt="hub")
        for key in ("version", "generated_at", "items"):
            self.assertIn(key, data)
            self.assertIn(key, hub_defs)


if __name__ == "__main__":
    unittest.main()