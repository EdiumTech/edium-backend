import json
from pathlib import Path
import sys
import unittest

APP_DIR = Path(__file__).resolve().parents[1] / "functions" / "app"
sys.path.insert(0, str(APP_DIR))

from contest_content import (  # noqa: E402
    LANGUAGE,
    LEGACY_TASK_SET_VERSION,
    TASK_SET_VERSION,
    public_tasks,
    resolve_task_set,
    task_ids,
    track_label,
)


class ContestContentTests(unittest.TestCase):
    def test_each_direction_has_a_distinct_pinned_js_assignment(self):
        directions = ["Разработка", "AI/ML", "Электроника", "Дизайн", "Продукт", "Маркетинг"]
        versions = [resolve_task_set(direction) for direction in directions]
        self.assertEqual(LANGUAGE, "javascript")
        self.assertEqual(len(set(versions + [TASK_SET_VERSION])), 7)
        all_ids = set()
        for direction, version in zip(directions + ["Общий"], versions + [TASK_SET_VERSION]):
            self.assertEqual(track_label(version), direction)
            self.assertEqual(len(task_ids(version)), 3)
            self.assertFalse(all_ids.intersection(task_ids(version)))
            all_ids.update(task_ids(version))

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
        for operation in (public_tasks, task_ids, track_label):
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
