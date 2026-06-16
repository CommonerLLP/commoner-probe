import unittest
from pathlib import Path

from commoner_probe.topics import TopicProfile, load_topic

ROOT = Path(__file__).resolve().parents[1]


class TopicTests(unittest.TestCase):
    def test_load_topic_returns_topic_profile(self):
        topic = load_topic(ROOT / "examples" / "topics" / "libraries.json")
        self.assertIsInstance(topic, TopicProfile)
        self.assertEqual(topic.name, "libraries")

    def test_searches_returns_group_query_pairs(self):
        topic = load_topic(ROOT / "examples" / "topics" / "libraries.json")
        pairs = topic.searches()
        self.assertIsInstance(pairs, list)
        self.assertTrue(all(isinstance(p, tuple) and len(p) == 2 for p in pairs))
        self.assertGreater(len(pairs), 0)

    def test_searches_max_buckets(self):
        topic = load_topic(ROOT / "examples" / "topics" / "libraries.json")
        pairs = topic.searches(max_buckets=2)
        self.assertEqual(len(pairs), 2)

    def test_no_classify_method(self):
        topic = load_topic(ROOT / "examples" / "topics" / "libraries.json")
        self.assertFalse(hasattr(topic, "classify"))

    def test_legacy_keys_ignored(self):
        """load_topic should silently ignore tag_rules/classifier/fallback_tag."""
        import json
        import tempfile
        data = {
            "name": "test",
            "description": "",
            "search_groups": {"a": ["query1"]},
            "lok_sabha_ministries": [],
            "rajya_sabha_ministry_likes": [],
            "fallback_tag": "legacy",
            "tag_rules": [{"tag": "x", "patterns": ["x"]}],
            "classifier": {"mode": "regex"},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            path = f.name
        topic = load_topic(path)
        self.assertEqual(topic.name, "test")
        self.assertFalse(hasattr(topic, "classify"))

    def test_lok_sabha_ministries_loaded(self):
        topic = load_topic(ROOT / "examples" / "topics" / "libraries.json")
        self.assertIn("CULTURE", topic.lok_sabha_ministries)

    def test_rajya_sabha_ministry_likes_loaded(self):
        topic = load_topic(ROOT / "examples" / "topics" / "libraries.json")
        self.assertIn("EDUCATION", topic.rajya_sabha_ministry_likes)


if __name__ == "__main__":
    unittest.main()
