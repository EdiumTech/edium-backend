import importlib
import copy
import json
import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


APP_DIR = Path(__file__).parents[1] / "functions" / "app"
sys.path.insert(0, str(APP_DIR))
os.environ.update(
    {
        "ADMIN_TOKEN": "test-admin-secret",
        "IP_HASH_SALT": "test-rate-salt",
        "ALLOWED_ORIGINS": "http://localhost:4173",
        "CONTEST_TOKEN_KEY": "test-contest-token-key",
    }
)

repository_module = types.ModuleType("repository")
repository_module.Repository = object
repository_module.ContestConflict = type("ContestConflict", (Exception,), {})
storage_module = types.ModuleType("storage")
storage_module.InvalidResume = type("InvalidResume", (Exception,), {})
storage_module.ResumeStorage = object
mailer_module = types.ModuleType("mailer")
mailer_module.Mailer = object
botocore_module = types.ModuleType("botocore")
botocore_exceptions = types.ModuleType("botocore.exceptions")
botocore_exceptions.ClientError = type("ClientError", (Exception,), {})
sys.modules["repository"] = repository_module
sys.modules["storage"] = storage_module
sys.modules["mailer"] = mailer_module
sys.modules["botocore"] = botocore_module
sys.modules["botocore.exceptions"] = botocore_exceptions

handler = importlib.import_module("handler")


class FakeRepository:
    def __init__(self, upload):
        self.upload = upload
        self.saved = None

    def allow_request(self, *args):
        return True

    def get_upload(self, upload_id):
        return self.upload

    def finalize_application(self, upload_id, application):
        self.saved = application
        return application, True


class FakeStorage:
    def validate_uploaded(self, **kwargs):
        return "application/pdf"


class FakeContestRepository:
    def __init__(self, contest):
        self.contest = copy.deepcopy(contest)
        self.version = 1

    def allow_contest_request(self, *args):
        return True

    def get_contest_with_etag(self, contest_id):
        if contest_id != self.contest["contest_id"]:
            return None, None
        return copy.deepcopy(self.contest), f'"v{self.version}"'

    def save_contest(self, contest, etag):
        if etag != f'"v{self.version}"':
            raise repository_module.ContestConflict()
        self.contest = copy.deepcopy(contest)
        self.version += 1


class FakeAdminContestRepository:
    def __init__(self, email=None):
        self.application = {
            "application_id": "1d0a3842-8fb8-4270-b552-2c28910f1538",
            "first_name": "Анна",
            "last_name": "Тестова",
            "email": email,
            "status": "reviewing",
        }
        self.contest = None
        self.version = 0

    def get_application(self, application_id):
        return self.application if application_id == self.application["application_id"] else None

    def find_contest_by_application(self, application_id):
        return self.contest

    def create_contest(self, contest):
        self.contest = copy.deepcopy(contest)
        self.version = 1
        return True

    def allow_contest_request(self, *args):
        return True

    def get_contest_with_etag(self, contest_id):
        if not self.contest or contest_id != self.contest["contest_id"]:
            return None, None
        return copy.deepcopy(self.contest), f'"v{self.version}"'

    def save_contest(self, contest, etag):
        if etag != f'"v{self.version}"':
            raise repository_module.ContestConflict()
        self.contest = copy.deepcopy(contest)
        self.version += 1


def event(path, payload, headers=None):
    return {
        "httpMethod": "POST",
        "path": path,
        "headers": {"Origin": "http://localhost:4173", **(headers or {})},
        "body": json.dumps(payload),
        "requestContext": {"identity": {"sourceIp": "192.0.2.10"}},
    }


class HandlerTests(unittest.TestCase):
    def setUp(self):
        now = datetime.now(timezone.utc)
        self.upload = {
            "upload_id": "1d0a3842-8fb8-4270-b552-2c28910f1538",
            "object_key": "pending/random",
            "original_name": "resume.pdf",
            "declared_type": "application/pdf",
            "size_bytes": 24,
            "status": "pending",
            "expires_at": now + timedelta(minutes=30),
        }
        self.repo = FakeRepository(self.upload)
        handler._repository = self.repo
        handler._storage = FakeStorage()

    def test_application_is_saved_before_any_email_work(self):
        payload = {
            "uploadId": self.upload["upload_id"],
            "firstName": "Анна",
            "lastName": "Тестова",
            "telegram": "@annatest",
            "phone": "+4915123456789",
            "email": "anna@example.test",
            "direction": "Фронтенд",
            "motivation": "Мне интересно проектировать понятные образовательные продукты для людей.",
            "portfolioUrl": "https://example.test/portfolio",
            "startedAt": (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),
            "company": "",
        }
        result = handler.api(event("/v1/applications", payload))
        self.assertEqual(result["statusCode"], 201)
        self.assertTrue(json.loads(result["body"])["saved"])
        self.assertEqual(self.repo.saved["team_notification_status"], "pending")
        self.assertEqual(self.repo.saved["candidate_notification_status"], "pending")

    def test_duplicate_finalize_returns_success(self):
        self.upload.update({"status": "attached", "application_id": self.upload["upload_id"]})
        payload = {
            "uploadId": self.upload["upload_id"],
            "firstName": "Анна",
            "lastName": "Тестова",
            "telegram": "@annatest",
            "phone": "+4915123456789",
            "direction": "Фронтенд",
            "motivation": "Мне интересно проектировать понятные образовательные продукты для людей.",
            "startedAt": (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),
        }
        result = handler.api(event("/v1/applications", payload))
        body = json.loads(result["body"])
        self.assertEqual(result["statusCode"], 200)
        self.assertTrue(body["saved"])
        self.assertTrue(body["duplicate"])

    def test_admin_route_requires_bearer_token(self):
        result = handler.api({"httpMethod": "GET", "path": "/v1/admin/applications", "headers": {}})
        self.assertEqual(result["statusCode"], 401)

    def contest_request(self, method, path, token, payload=None):
        return {
            "httpMethod": method,
            "path": path,
            "headers": {"Origin": "http://localhost:4173", "Authorization": f"Contest {token}"},
            "body": json.dumps(payload or {}),
            "requestContext": {"identity": {"sourceIp": "192.0.2.11"}},
        }

    def test_contest_start_and_submit_are_idempotent(self):
        now = datetime.now(timezone.utc)
        contest, _ = handler.new_contest(self.upload["upload_id"], 90, now + timedelta(days=1), now)
        token = handler.attach_token_hash(contest, os.environ["CONTEST_TOKEN_KEY"])
        for task_id in handler.task_ids():
            contest["answers"][task_id] = {"source": "function solve(input) { return input }"}
        handler._repository = FakeContestRepository(contest)

        first = handler.api(self.contest_request("POST", "/v1/contest/start", token))
        second = handler.api(self.contest_request("POST", "/v1/contest/start", token))
        self.assertEqual(first["statusCode"], 200)
        self.assertEqual(second["statusCode"], 200)
        self.assertEqual(json.loads(first["body"])["contest"]["deadlineAt"], json.loads(second["body"])["contest"]["deadlineAt"])

        submitted = handler.api(self.contest_request("POST", "/v1/contest/submit", token))
        duplicate = handler.api(self.contest_request("POST", "/v1/contest/submit", token))
        self.assertEqual(submitted["statusCode"], 200)
        self.assertEqual(duplicate["statusCode"], 200)
        self.assertTrue(json.loads(duplicate["body"])["submitted"])

    def test_invalid_contest_token_is_indistinguishable_from_missing(self):
        handler._repository = FakeContestRepository({"contest_id": self.upload["upload_id"]})
        result = handler.api(self.contest_request("GET", "/v1/contest", "not-a-token"))
        self.assertEqual(result["statusCode"], 404)
        self.assertEqual(json.loads(result["body"])["code"], "invalid_invite")

    def test_admin_can_issue_link_without_candidate_email(self):
        handler._repository = FakeAdminContestRepository(email=None)
        result = handler.api({
            "httpMethod": "POST",
            "path": f"/v1/admin/applications/{self.upload['upload_id']}/contest",
            "headers": {"Origin": "http://localhost:4173", "Authorization": "Bearer test-admin-secret"},
            "body": json.dumps({"durationMinutes": 90, "startWithinDays": 7}),
        })
        body = json.loads(result["body"])
        self.assertEqual(result["statusCode"], 201)
        self.assertFalse(body["contest"]["hasEmail"])
        self.assertIn("#invite=", body["contest"]["inviteUrl"])
        self.assertEqual(handler._repository.contest["invitation_notification_status"], "not_requested")

    def test_full_invitation_start_save_submit_and_hr_read_flow(self):
        handler._repository = FakeAdminContestRepository(email="anna@example.test")
        application_id = self.upload["upload_id"]
        admin_headers = {"Origin": "http://localhost:4173", "Authorization": "Bearer test-admin-secret"}
        issued = handler.api({
            "httpMethod": "POST", "path": f"/v1/admin/applications/{application_id}/contest",
            "headers": admin_headers, "body": json.dumps({"durationMinutes": 90, "startWithinDays": 7}),
        })
        invite_url = json.loads(issued["body"])["contest"]["inviteUrl"]
        token = invite_url.split("#invite=", 1)[1]

        opened = handler.api(self.contest_request("POST", "/v1/contest/open", token))
        started = handler.api(self.contest_request("POST", "/v1/contest/start", token))
        self.assertEqual(json.loads(opened["body"])["contest"]["state"], "opened")
        current = json.loads(started["body"])["contest"]

        for task_id in handler.task_ids():
            saved = handler.api(self.contest_request("PATCH", f"/v1/contest/answers/{task_id}", token, {
                "source": "function solve(input) { return input }", "revision": current["revision"],
            }))
            self.assertEqual(saved["statusCode"], 200)
            current = json.loads(saved["body"])["contest"]

        submitted = handler.api(self.contest_request("POST", "/v1/contest/submit", token))
        self.assertEqual(json.loads(submitted["body"])["contest"]["state"], "submitted")
        self.assertEqual(handler._repository.contest["completion_notification_status"], "pending")

        hr_result = handler.api({
            "httpMethod": "GET", "path": f"/v1/admin/applications/{application_id}/contest", "headers": admin_headers,
        })
        hr_contest = json.loads(hr_result["body"])["contest"]
        self.assertEqual(hr_contest["state"], "submitted")
        self.assertEqual(set(hr_contest["answers"]), handler.task_ids())
        self.assertNotIn("token_hash", hr_contest)

    def test_invite_pins_application_direction_and_client_cannot_choose_runner_suite(self):
        handler._repository = FakeAdminContestRepository()
        handler._repository.application["direction"] = "Разработка"
        issued = handler.api({
            "httpMethod": "POST", "path": f"/v1/admin/applications/{self.upload['upload_id']}/contest",
            "headers": {"Authorization": "Bearer test-admin-secret"},
            "body": json.dumps({"direction": "Маркетинг"}),
        })
        admin_view = json.loads(issued["body"])["contest"]
        self.assertEqual(admin_view["direction"], "Разработка")
        self.assertTrue(admin_view["task_set_version"].endswith("-development"))
        token = admin_view["inviteUrl"].split("#invite=", 1)[1]
        handler._repository.application["direction"] = "Маркетинг"
        started = handler.api(self.contest_request("POST", "/v1/contest/start", token))
        contest = json.loads(started["body"])["contest"]
        self.assertEqual(contest["direction"], "Разработка")
        task_id = contest["tasks"][0]["id"]
        source = "function solve(input) { return input }"
        with patch.object(handler.SandboxRunner, "run", return_value={"passed": 2, "total": 8, "tests": []}) as run:
            result = handler.api(self.contest_request("POST", "/v1/contest/run", token, {
                "taskId": task_id, "source": source, "taskSetVersion": "edium-js-2026-09-v2-marketing",
            }))
            self.assertEqual(result["statusCode"], 200)
            run.assert_called_once_with(task_id, source, contest["taskSetVersion"], "javascript")
            rejected = handler.api(self.contest_request("POST", "/v1/contest/run", token, {
                "taskId": "ai-evidence", "source": source,
            }))
            self.assertEqual(rejected["statusCode"], 404)
            self.assertEqual(run.call_count, 1)

    def issue_mobile_contest(self):
        handler._repository = FakeAdminContestRepository()
        handler._repository.application["direction"] = "Мобильная разработка"
        with patch.dict(os.environ, {"RUNNER_LANGUAGES": "javascript,kotlin,swift"}):
            issued = handler.api({
                "httpMethod": "POST", "path": f"/v1/admin/applications/{self.upload['upload_id']}/contest",
                "headers": {"Authorization": "Bearer test-admin-secret"}, "body": "{}",
            })
        self.assertEqual(issued["statusCode"], 201)
        token = json.loads(issued["body"])["contest"]["inviteUrl"].split("#invite=", 1)[1]
        started = handler.api(self.contest_request("POST", "/v1/contest/start", token))
        self.assertEqual(started["statusCode"], 200)
        return token, json.loads(started["body"])["contest"]

    def test_mobile_invitation_requires_both_native_runtimes_before_persisting_or_queuing_email(self):
        for configured in (None, "", "javascript", "kotlin", "swift", "javascript,kotlin", "javascript,swift", "python", "KOTLIN,SWIFT"):
            with self.subTest(configured=configured), patch.dict(os.environ):
                if configured is None:
                    os.environ.pop("RUNNER_LANGUAGES", None)
                else:
                    os.environ["RUNNER_LANGUAGES"] = configured
                repo = FakeAdminContestRepository(email="anna@example.test")
                repo.application["direction"] = "Мобильная разработка"
                handler._repository = repo
                with patch.object(repo, "create_contest", wraps=repo.create_contest) as persist, patch.object(handler, "attach_token_hash") as token:
                    result = handler.api({
                        "httpMethod": "POST", "path": f"/v1/admin/applications/{self.upload['upload_id']}/contest",
                        "headers": {"Authorization": "Bearer test-admin-secret"}, "body": "{}",
                    })
                    self.assertEqual(result["statusCode"], 503)
                    self.assertEqual(json.loads(result["body"])["code"], "runtime_unavailable")
                    self.assertIn("Kotlin и Swift", json.loads(result["body"])["message"])
                    persist.assert_not_called()
                    token.assert_not_called()
                self.assertIsNone(repo.contest)
                self.assertEqual(repo.version, 0)

    def test_explicit_both_native_capabilities_allow_one_mobile_invitation(self):
        repo = FakeAdminContestRepository(email="anna@example.test")
        repo.application["direction"] = "Мобильная разработка"
        handler._repository = repo
        with patch.dict(os.environ, {"RUNNER_LANGUAGES": " kotlin, swift "}), patch.object(repo, "create_contest", wraps=repo.create_contest) as persist:
            result = handler.api({
                "httpMethod": "POST", "path": f"/v1/admin/applications/{self.upload['upload_id']}/contest",
                "headers": {"Authorization": "Bearer test-admin-secret"}, "body": "{}",
            })
            self.assertEqual(result["statusCode"], 201)
            persist.assert_called_once()
        self.assertEqual(repo.contest["invitation_notification_status"], "pending")
        self.assertEqual(repo.contest["task_set_version"], "edium-mobile-2026-09-v3")

    def test_js_invitation_remains_available_without_runner_url_or_declared_capabilities(self):
        repo = FakeAdminContestRepository(email=None)
        repo.application["direction"] = "Бэкенд"
        handler._repository = repo
        with patch.dict(os.environ, {"RUNNER_URL": "", "RUNNER_LANGUAGES": ""}):
            result = handler.api({
                "httpMethod": "POST", "path": f"/v1/admin/applications/{self.upload['upload_id']}/contest",
                "headers": {"Authorization": "Bearer test-admin-secret"}, "body": "{}",
            })
        self.assertEqual(result["statusCode"], 201)
        self.assertEqual(repo.contest["task_set_version"], "edium-js-2026-09-v3-backend")

    def test_mobile_api_rejects_forbidden_languages_before_calling_runner_or_saving(self):
        token, contest = self.issue_mobile_contest()
        self.assertEqual(contest["languages"], ["kotlin", "swift"])
        self.assertEqual(contest["language"], "mixed")
        with patch.object(handler.SandboxRunner, "run") as run:
            for index, task in enumerate(contest["tasks"]):
                invalid_languages = ["javascript", "python", "", {}, ["kotlin"]]
                if index < 2:
                    invalid_languages.append("swift" if index == 0 else "kotlin")
                for language in invalid_languages:
                    for method, path in [("POST", "/v1/contest/run"), ("PATCH", f"/v1/contest/answers/{task['id']}")]:
                        with self.subTest(task=task["id"], language=language, method=method):
                            result = handler.api(self.contest_request(method, path, token, {
                                "taskId": task["id"], "source": "code", "language": language, "revision": contest["revision"],
                            }))
                            self.assertEqual(result["statusCode"], 400)
                            self.assertEqual(json.loads(result["body"])["code"], "invalid_language")
            run.assert_not_called()
        self.assertEqual(handler._repository.contest["answers"], {})
        self.assertEqual(handler._repository.contest["revision"], contest["revision"])

    def test_mobile_api_dispatches_each_language_and_persists_it_for_hr(self):
        token, contest = self.issue_mobile_contest()
        tasks, version = contest["tasks"], contest["taskSetVersion"]
        for task in tasks:
            for language, option in task["languages"].items():
                with patch.object(handler.SandboxRunner, "run", return_value={"passed": 0, "total": task["testCount"], "tests": []}) as run:
                    result = handler.api(self.contest_request("POST", "/v1/contest/run", token, {
                        "taskId": task["id"], "source": option["starterCode"], "language": language,
                    }))
                    self.assertEqual(result["statusCode"], 200)
                    run.assert_called_once_with(task["id"], option["starterCode"], version, language)
                saved = handler.api(self.contest_request("PATCH", f"/v1/contest/answers/{task['id']}", token, {
                    "source": option["starterCode"], "language": language, "revision": contest["revision"],
                }))
                self.assertEqual(saved["statusCode"], 200)
                contest = json.loads(saved["body"])["contest"]
                self.assertEqual(contest["answers"][task["id"]]["language"], language)
        submitted = handler.api(self.contest_request("POST", "/v1/contest/submit", token))
        self.assertEqual(submitted["statusCode"], 200)
        self.assertEqual({answer["language"] for answer in handler._repository.contest["answers"].values()}, {"kotlin", "swift"})

    def test_mobile_api_omitted_language_uses_task_default_and_submit_requires_both_mandatory_tasks(self):
        token, contest = self.issue_mobile_contest()
        tasks, version = contest["tasks"], contest["taskSetVersion"]
        for task in tasks:
            with patch.object(handler.SandboxRunner, "run", return_value={"passed": 0, "total": task["testCount"], "tests": []}) as run:
                result = handler.api(self.contest_request("POST", "/v1/contest/run", token, {
                    "taskId": task["id"], "source": task["starterCode"],
                }))
                self.assertEqual(result["statusCode"], 200)
                run.assert_called_once_with(task["id"], task["starterCode"], version, task["defaultLanguage"])
        first = tasks[0]
        saved = handler.api(self.contest_request("PATCH", f"/v1/contest/answers/{first['id']}", token, {
            "source": first["starterCode"], "revision": contest["revision"],
        }))
        self.assertEqual(saved["statusCode"], 200)
        self.assertEqual(json.loads(saved["body"])["contest"]["answers"][first["id"]]["language"], "kotlin")
        submitted = handler.api(self.contest_request("POST", "/v1/contest/submit", token))
        self.assertEqual(submitted["statusCode"], 409)
        self.assertEqual(json.loads(submitted["body"])["code"], "incomplete")


if __name__ == "__main__":
    unittest.main()
