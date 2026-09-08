import importlib.util
import json
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


APP_DIR = Path(__file__).parents[1] / "functions" / "app"
sys.path.insert(0, str(APP_DIR))
spec = importlib.util.spec_from_file_location("join_mailer_under_test", APP_DIR / "mailer.py")
mailer_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mailer_module)


class FakeResponse:
    status = 202

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class MailerTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(
            os.environ,
            {
                "EMAIL_MODE": "herald",
                "TEAM_EMAIL": "team@example.test",
                "HERALD_EMAIL_URL": "https://api.example.test/herald/v1/emails",
                "HERALD_API_KEY": "test-secret",
                "ADMIN_URL": "https://edium.online/join/admin/",
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()

    @staticmethod
    def application():
        return {
            "application_id": "1d0a3842-8fb8-4270-b552-2c28910f1538",
            "first_name": "Анна",
            "last_name": "Тестова",
            "direction": "Дизайн",
            "email": "anna@example.test",
        }

    @mock.patch("urllib.request.urlopen", return_value=FakeResponse())
    def test_team_mail_uses_protected_idempotent_herald_request(self, urlopen):
        mailer = mailer_module.Mailer()
        mailer.send_team(self.application())

        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(
            request.headers["Idempotency-key"],
            "candidate:1d0a3842-8fb8-4270-b552-2c28910f1538:team",
        )
        self.assertEqual(request.headers["Authorization"], "Bearer test-secret")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["to"], "team@example.test")
        self.assertNotIn("resume", payload)

    @mock.patch("urllib.request.urlopen", return_value=FakeResponse())
    def test_candidate_confirmation_has_separate_idempotency_key(self, urlopen):
        mailer = mailer_module.Mailer()
        mailer.send_candidate(self.application())
        request = urlopen.call_args.args[0]
        self.assertTrue(request.headers["Idempotency-key"].endswith(":confirmation"))
        self.assertEqual(json.loads(request.data)["to"], "anna@example.test")

    def test_disabled_without_api_key(self):
        with mock.patch.dict(os.environ, {"HERALD_API_KEY": ""}):
            self.assertFalse(mailer_module.Mailer().enabled)

    @mock.patch("urllib.request.urlopen", return_value=FakeResponse())
    def test_contest_resend_has_new_idempotency_boundary(self, urlopen):
        contest = {
            "contest_id": "22222222-2222-4222-8222-222222222222",
            "duration_minutes": 90,
            "start_before": datetime(2026, 9, 10, tzinfo=timezone.utc),
            "invitation_send_version": 2,
        }
        mailer_module.Mailer().send_contest_invitation(self.application(), contest, "https://edium.online/join/contest/#invite=synthetic")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.headers["Idempotency-key"], "contest:22222222-2222-4222-8222-222222222222:invitation:2")

    @mock.patch("urllib.request.urlopen", side_effect=mailer_module.urllib.error.URLError("offline"))
    def test_herald_outage_is_reported_for_outbox_retry(self, _urlopen):
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            mailer_module.Mailer().send_candidate(self.application())


if __name__ == "__main__":
    unittest.main()
