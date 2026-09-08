import json
from pathlib import Path
import sys
import unittest

APP_DIR = Path(__file__).resolve().parents[1] / "functions" / "app"
sys.path.insert(0, str(APP_DIR))

from contest_content import (  # noqa: E402
    ACTIVE_DIRECTIONS,
    LANGUAGE,
    LEGACY_TASK_SET_VERSION,
    TASK_SET_VERSION,
    public_tasks,
    resolve_task_set,
    supported_languages,
    task_ids,
    track_label,
)


class ContestContentTests(unittest.TestCase):
    def test_each_active_direction_has_a_distinct_pinned_assignment(self):
        directions = list(ACTIVE_DIRECTIONS)
        versions = [resolve_task_set(direction) for direction in directions]
        self.assertEqual(LANGUAGE, "javascript")
        self.assertEqual(directions, ["Бэкенд", "Фронтенд", "Мобильная разработка", "AI/ML", "Системная разработка (EdiumBoost)"])
        self.assertEqual(len(set(versions + [TASK_SET_VERSION])), 6)
        all_ids = set()
        for direction, version in zip(directions + ["Общий"], versions + [TASK_SET_VERSION]):
            self.assertEqual(track_label(version), direction)
            self.assertEqual(len(task_ids(version)), 3)
            self.assertFalse(all_ids.intersection(task_ids(version)))
            all_ids.update(task_ids(version))

    def test_retired_directions_remain_pinned_for_existing_applications(self):
        for direction, suffix in [("Разработка", "development"), ("Электроника", "electronics"),
                                  ("Дизайн", "design"), ("Продукт", "product"), ("Маркетинг", "marketing")]:
            version = f"edium-js-2026-09-v2-{suffix}"
            self.assertEqual(resolve_task_set(direction), version)
            self.assertEqual(track_label(version), direction)
        self.assertEqual(track_label("edium-js-2026-09-v2-ai-ml"), "AI/ML")
        self.assertEqual(resolve_task_set("AI/ML"), "edium-js-2026-09-v3-ai-ml")

    def test_mobile_assignment_requires_kotlin_and_swift_and_all_other_tracks_use_js(self):
        mobile = public_tasks(resolve_task_set("Мобильная разработка"))
        self.assertEqual([set(task["languages"]) for task in mobile], [{"kotlin"}, {"swift"}, {"kotlin", "swift"}])
        self.assertEqual([task["defaultLanguage"] for task in mobile], ["kotlin", "swift", "kotlin"])
        for direction in ACTIVE_DIRECTIONS:
            version = resolve_task_set(direction)
            expected = ["kotlin", "swift"] if direction == "Мобильная разработка" else ["javascript"]
            self.assertEqual(supported_languages(version), expected)
            for task in public_tasks(version):
                self.assertIn(task["defaultLanguage"], task["languages"])
                self.assertEqual(task["starterCode"], task["languages"][task["defaultLanguage"]]["starterCode"])
                for language, option in task["languages"].items():
                    self.assertIn(language, expected)
                    self.assertTrue(option["label"])
                    self.assertTrue(option["signature"])
                    self.assertTrue(option["starterCode"])

    def test_pinned_js_tasks_cannot_gain_unsupported_mobile_languages(self):
        for version in (LEGACY_TASK_SET_VERSION, "edium-js-2026-09-v2-development", resolve_task_set("Бэкенд")):
            self.assertEqual(supported_languages(version), ["javascript"])
            for task in public_tasks(version):
                self.assertEqual(set(task["languages"]), {"javascript"})
                self.assertEqual(task["defaultLanguage"], "javascript")

    def test_unknown_or_empty_direction_uses_general_tasks(self):
        for direction in (None, "", "  ", "Неизвестное направление"):
            self.assertEqual(resolve_task_set(direction), TASK_SET_VERSION)
        self.assertEqual(resolve_task_set(" Дизайн "), resolve_task_set("Дизайн"))

    def test_public_content_hides_non_example_inputs_but_reports_total(self):
        catalog = json.loads((APP_DIR / "contest_catalog.json").read_text(encoding="utf-8"))
        for version, task_set in catalog["sets"].items():
            for public, full in zip(public_tasks(version), task_set["tasks"]):
                self.assertNotIn("tests", public)
                self.assertNotIn("publicExampleCount", public)
                self.assertEqual(public["testCount"], len(full["tests"]))
                self.assertLess(len(public["publicExamples"]), public["testCount"])
                if version != LEGACY_TASK_SET_VERSION:
                    self.assertNotIn("minExplanation", public)
                    self.assertNotIn("maxExplanation", public)
                    self.assertEqual(public["publicExamples"], [
                        {"input": case["input"], "expected": case["expected"]}
                        for case in full["tests"][:2]
                    ])

    def test_public_nested_data_is_defensively_copied(self):
        original = public_tasks()
        changed = public_tasks()
        changed[0]["publicExamples"][0]["input"].clear()
        self.assertEqual(public_tasks(), original)

    def test_unknown_version_cannot_silently_switch_candidate_tasks(self):
        for operation in (public_tasks, task_ids, track_label, supported_languages):
            with self.assertRaises(ValueError):
                operation("unknown-version")

    def test_legacy_content_keeps_original_ids_and_examples(self):
        self.assertEqual(task_ids(LEGACY_TASK_SET_VERSION),
                         {"quiz-progress", "ai-evidence", "offline-merge"})
        tasks = public_tasks(LEGACY_TASK_SET_VERSION)
        self.assertEqual(tasks[0]["title"], "Прогресс первого квиза")
        self.assertEqual(tasks[0]["publicExamples"][0]["expected"],
                         {"answers": {"q1": "4"}, "completedCount": 1, "revision": 1})
        self.assertEqual([task["testCount"] for task in tasks], [4, 3, 3])


if __name__ == "__main__":
    unittest.main()
