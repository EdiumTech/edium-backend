import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


APP_DIR = Path(__file__).parents[1] / "functions" / "app"
sys.path.insert(0, str(APP_DIR))

from contest import (  # noqa: E402
    ContestError,
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
)
from contest_content import task_ids  # noqa: E402


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
        explanation = "Объяснение решения с описанием алгоритма, обработки конфликтов и оценки сложности. " * 3
        self.assertTrue(save_answer(self.contest, task_id, {"source": "function solve(i){return i}", "explanation": explanation}, revision, self.now))
        with self.assertRaisesRegex(ContestError, "новая версия"):
            save_answer(self.contest, task_id, {"source": "changed"}, revision, self.now)
        for missing_task_id in task_ids() - {task_id}:
            self.contest["answers"][missing_task_id] = {"source": "function solve(input) { return input }", "explanation": explanation}
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
            self.contest["answers"][task_id] = {"source": "function solve(input) { return input }", "explanation": "Подробное объяснение алгоритма, его граничных случаев и оценки сложности. " * 4}
        original_deadline = self.contest["deadline_at"]
        extend_contest(self.contest, 60, self.now + timedelta(minutes=1))
        self.assertEqual(self.contest["deadline_at"], original_deadline + timedelta(minutes=60))
        with self.assertRaises(ContestError):
            extend_contest(self.contest, 60, self.now + timedelta(minutes=2))
        self.assertTrue(submit_contest(self.contest, self.now + timedelta(minutes=3)))
        self.assertEqual(self.contest["purge_after"], self.now + timedelta(minutes=3, days=180))
        self.assertFalse(submit_contest(self.contest, self.now + timedelta(minutes=4)))
        self.assertEqual(candidate_view(self.contest)["state"], "submitted")

    def test_partial_explanation_is_saved_but_cannot_be_submitted(self):
        task_id = next(iter(task_ids()))
        start_contest(self.contest, self.now)
        self.assertTrue(save_answer(self.contest, task_id, {"source": "function solve(){}", "explanation": "черновик"}, self.contest["revision"], self.now))
        with self.assertRaisesRegex(ContestError, "Дополни объяснение|Добавь решение"):
            submit_contest(self.contest, self.now)


if __name__ == "__main__":
    unittest.main()
