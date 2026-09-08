import importlib
import json
import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


APP_DIR = Path(__file__).parents[1] / "functions" / "app"
sys.path.insert(0, str(APP_DIR))
os.environ.update(
    {
        "ADMIN_TOKEN": "test-admin-secret",
        "IP_HASH_SALT": "test-rate-salt",
        "ALLOWED_ORIGINS": "http://localhost:4173",
    }
)

repository_module = types.ModuleType("repository")
repository_module.Repository = object
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
            "direction": "Дизайн",
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


if __name__ == "__main__":
    unittest.main()
