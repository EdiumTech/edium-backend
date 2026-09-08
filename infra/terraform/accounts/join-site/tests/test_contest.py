import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


APP_DIR = Path(__file__).parents[1] / "functions" / "app"
sys.path.insert(0, str(APP_DIR))

from contest import (  # noqa: E402
    ContestError,
    answer_language,
    assigned_task_set,
    attach_token_hash,
    candidate_view,
    contest_id_from_token,
    extend_contest,
    new_contest,
    open_contest,
    revoke_contest,
    save_answer,
    start_contest,
    submit_contest,
    token_matches,
    update_review,
)
from contest_content import LEGACY_TASK_SET_VERSION, public_tasks, resolve_task_set, task_ids  # noqa: E402


class ContestTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
        self.key = "test-contest-token-key"
        self.contest, _ = new_contest("11111111-1111-4111-8111-111111111111", 90, self.now + timedelta(days=7), self.now)
        self.token = attach_token_hash(self.contest, self.key)

    def test_token_is_valid_but_not_stored_in_record(self):
        self.assertEqual(contest_id_from_token(self.token), self.contest["contest_id"])
        self.assertTrue(token_matches(self.contest, self.token, self.key))
        self.assertNotIn(self.token, str(self.contest))
        self.assertFalse(token_matches(self.contest, self.token[:-1] + "A", self.key))

    def test_same_application_gets_same_contest_id(self):
        duplicate, _ = new_contest("11111111-1111-4111-8111-111111111111", 60, self.now + timedelta(days=1), self.now)
        self.assertEqual(duplicate["contest_id"], self.contest["contest_id"])

    def test_token_for_another_contest_cannot_read_this_one(self):
        other, _ = new_contest("33333333-3333-4333-8333-333333333333", 60, self.now + timedelta(days=1), self.now)
        attach_token_hash(other, self.key)
        self.assertFalse(token_matches(other, self.token, self.key))

    def test_start_is_idempotent_and_uses_server_time(self):
        open_contest(self.contest, self.now + timedelta(minutes=1))
        self.assertTrue(start_contest(self.contest, self.now + timedelta(minutes=2)))
        deadline = self.contest["deadline_at"]
        revision = self.contest["revision"]
        self.assertFalse(start_contest(self.contest, self.now + timedelta(minutes=20)))
        self.assertEqual(self.contest["deadline_at"], deadline)
        self.assertEqual(self.contest["revision"], revision)

    def test_save_detects_stale_tab_and_blocks_after_submit(self):
        task_id = next(iter(task_ids()))
        start_contest(self.contest, self.now)
        revision = self.contest["revision"]
        self.assertTrue(save_answer(self.contest, task_id, {"source": "function solve(i){return i}"}, revision, self.now))
        with self.assertRaisesRegex(ContestError, "новая версия"):
            save_answer(self.contest, task_id, {"source": "changed"}, revision, self.now)
        for missing_task_id in task_ids() - {task_id}:
            self.contest["answers"][missing_task_id] = {"source": "function solve(input) { return input }"}
        submit_contest(self.contest, self.now + timedelta(minutes=1))
        with self.assertRaises(ContestError):
            save_answer(self.contest, task_id, {"source": "changed"}, self.contest["revision"], self.now + timedelta(minutes=2))

    def test_expired_and_revoked_contests_cannot_start(self):
        expired, _ = new_contest("11111111-1111-4111-8111-111111111111", 90, self.now + timedelta(minutes=1), self.now)
        self.assertTrue(start_contest(expired, self.now + timedelta(minutes=2)))
        self.assertEqual(expired["state"], "expired")
        revoke_contest(self.contest, self.now)
        with self.assertRaises(ContestError):
            start_contest(self.contest, self.now)

    def test_extend_once_and_submit_twice(self):
        start_contest(self.contest, self.now)
        for task_id in task_ids():
            self.contest["answers"][task_id] = {"source": "function solve(input) { return input }"}
        original_deadline = self.contest["deadline_at"]
        extend_contest(self.contest, 60, self.now + timedelta(minutes=1))
        self.assertEqual(self.contest["deadline_at"], original_deadline + timedelta(minutes=60))
        with self.assertRaises(ContestError):
            extend_contest(self.contest, 60, self.now + timedelta(minutes=2))
        self.assertTrue(submit_contest(self.contest, self.now + timedelta(minutes=3)))
        self.assertEqual(self.contest["purge_after"], self.now + timedelta(minutes=3, days=180))
        self.assertFalse(submit_contest(self.contest, self.now + timedelta(minutes=4)))
        self.assertEqual(candidate_view(self.contest)["state"], "submitted")

    def test_answers_need_only_source_and_submission_requires_every_task(self):
        task_id = next(iter(task_ids()))
        start_contest(self.contest, self.now)
        self.assertTrue(save_answer(self.contest, task_id, {"source": "function solve(){}"}, self.contest["revision"], self.now))
        self.assertEqual(set(self.contest["answers"][task_id]), {"source", "language", "updated_at"})
        with self.assertRaisesRegex(ContestError, "Добавь решение"):
            submit_contest(self.contest, self.now)

    def test_invitation_pins_direction_and_rejects_tasks_from_other_sets(self):
        design, _ = new_contest("design-app", 90, self.now + timedelta(days=1), self.now, direction="Дизайн")
        version = resolve_task_set("Дизайн")
        self.assertEqual(design["task_set_version"], version)
        self.assertEqual(candidate_view(design)["direction"], "Дизайн")
        self.assertTrue(candidate_view(design)["trackLabel"])
        start_contest(design, self.now)
        foreign_id = next(iter(task_ids(LEGACY_TASK_SET_VERSION) - task_ids(version)))
        with self.assertRaisesRegex(ContestError, "Задача не найдена"):
            save_answer(design, foreign_id, {"source": "function solve() {}"}, design["revision"], self.now)
        with self.assertRaisesRegex(ContestError, "Неизвестная задача"):
            update_review(design, {"tasks": {foreign_id: {"score": 3}}}, self.now)
        for task_id in task_ids(version):
            save_answer(design, task_id, {"source": "function solve() {}"}, design["revision"], self.now)
        self.assertTrue(submit_contest(design, self.now))

    def test_existing_v1_assignments_stay_v1_without_explanation_requirement(self):
        self.contest["task_set_version"] = LEGACY_TASK_SET_VERSION
        self.contest.pop("direction", None)
        self.assertEqual(assigned_task_set(self.contest), LEGACY_TASK_SET_VERSION)
        start_contest(self.contest, self.now)
        for task_id in task_ids(LEGACY_TASK_SET_VERSION):
            save_answer(self.contest, task_id, {"source": "function solve() {}"}, self.contest["revision"], self.now)
        self.assertTrue(submit_contest(self.contest, self.now))
        self.contest.pop("task_set_version")
        self.assertEqual(candidate_view(self.contest)["taskSetVersion"], LEGACY_TASK_SET_VERSION)

    def test_source_limit_is_utf8_bytes(self):
        start_contest(self.contest, self.now)
        with self.assertRaisesRegex(ContestError, "64 КБ"):
            save_answer(self.contest, next(iter(task_ids())), {"source": "ё" * 33000}, self.contest["revision"], self.now)

    def mobile_contest(self):
        contest, _ = new_contest("mobile-app", 90, self.now + timedelta(days=1), self.now, direction="Мобильная разработка")
        start_contest(contest, self.now)
        return contest, public_tasks(assigned_task_set(contest))

    def test_mobile_metadata_and_omitted_language_use_each_task_default(self):
        contest, tasks = self.mobile_contest()
        self.assertEqual(candidate_view(contest)["language"], "mixed")
        self.assertEqual(candidate_view(contest)["languages"], ["kotlin", "swift"])
        for task, expected in zip(tasks, ["kotlin", "swift", "kotlin"]):
            self.assertEqual(answer_language(contest, task["id"]), expected)
            save_answer(contest, task["id"], {"source": task["starterCode"]}, contest["revision"], self.now)
            self.assertEqual(contest["answers"][task["id"]]["language"], expected)

    def test_mobile_wrong_languages_fail_before_saving_and_cannot_expand_allowed_options(self):
        contest, tasks = self.mobile_contest()
        for task in tasks:
            for language in ("javascript", "python", "Kotlin", "", {}, ["kotlin"]):
                with self.subTest(task=task["id"], language=language):
                    revision = contest["revision"]
                    with self.assertRaises(ContestError) as error:
                        save_answer(contest, task["id"], {"source": "code", "language": language}, revision, self.now)
                    self.assertEqual(error.exception.code, "invalid_language")
                    self.assertEqual(contest["revision"], revision)
                    self.assertNotIn(task["id"], contest["answers"])
        for task, language in [(tasks[0], "swift"), (tasks[1], "kotlin")]:
            with self.assertRaises(ContestError) as error:
                answer_language(contest, task["id"], language)
            self.assertEqual(error.exception.code, "invalid_language")

    def test_mobile_language_change_is_a_revision_even_when_source_is_unchanged(self):
        contest, tasks = self.mobile_contest()
        task_id = tasks[2]["id"]
        save_answer(contest, task_id, {"source": "same source", "language": "kotlin"}, contest["revision"], self.now)
        revision = contest["revision"]
        self.assertTrue(save_answer(contest, task_id, {"source": "same source", "language": "swift"}, revision, self.now))
        self.assertEqual(contest["revision"], revision + 1)
        self.assertEqual(contest["answers"][task_id]["language"], "swift")
        self.assertFalse(save_answer(contest, task_id, {"source": "same source", "language": "swift"}, contest["revision"], self.now))

    def test_mobile_submission_requires_swift_task_and_revalidates_stored_languages(self):
        contest, tasks = self.mobile_contest()
        for task in (tasks[0], tasks[2]):
            save_answer(contest, task["id"], {"source": task["starterCode"], "language": "kotlin"}, contest["revision"], self.now)
        with self.assertRaises(ContestError) as error:
            submit_contest(contest, self.now)
        self.assertEqual(error.exception.code, "incomplete")
        contest["answers"][tasks[1]["id"]] = {"source": "code", "language": "kotlin"}
        with self.assertRaises(ContestError) as error:
            submit_contest(contest, self.now)
        self.assertEqual(error.exception.code, "invalid_language")
        save_answer(contest, tasks[1]["id"], {"source": tasks[1]["starterCode"], "language": "swift"}, contest["revision"], self.now)
        self.assertTrue(submit_contest(contest, self.now))

    def test_js_assignment_cannot_switch_to_mobile_languages(self):
        start_contest(self.contest, self.now)
        task_id = next(iter(task_ids()))
        self.assertEqual(answer_language(self.contest, task_id), "javascript")
        for language in ("kotlin", "swift"):
            with self.assertRaises(ContestError) as error:
                save_answer(self.contest, task_id, {"source": "code", "language": language}, self.contest["revision"], self.now)
            self.assertEqual(error.exception.code, "invalid_language")


if __name__ == "__main__":
    unittest.main()
