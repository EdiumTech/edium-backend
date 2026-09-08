import io
import importlib.util
import os
from pathlib import Path
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


class ClientError(Exception):
    def __init__(self, response, operation_name):
        super().__init__(response.get("Error", {}).get("Code", operation_name))
        self.response = response


boto3_module = types.ModuleType("boto3")
boto3_module.client = lambda *args, **kwargs: None
botocore_module = types.ModuleType("botocore")
botocore_config_module = types.ModuleType("botocore.config")
botocore_config_module.Config = lambda **kwargs: kwargs
botocore_exceptions_module = types.ModuleType("botocore.exceptions")
botocore_exceptions_module.ClientError = ClientError

APP_DIR = Path(__file__).parents[1] / "functions" / "app"
spec = importlib.util.spec_from_file_location("join_repository_under_test", APP_DIR / "repository.py")
repository_module = importlib.util.module_from_spec(spec)
with patch.dict(
    sys.modules,
    {
        "boto3": boto3_module,
        "botocore": botocore_module,
        "botocore.config": botocore_config_module,
        "botocore.exceptions": botocore_exceptions_module,
    },
):
    spec.loader.exec_module(repository_module)
Repository = repository_module.Repository


class FakeS3:
    def __init__(self):
        self.objects = {}

    def get_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, *, Bucket, Key, Body, **kwargs):
        self.objects[Key] = Body

    def delete_object(self, *, Bucket, Key):
        self.objects.pop(Key, None)

    def list_objects_v2(self, *, Bucket, Prefix, MaxKeys, **kwargs):
        keys = sorted(key for key in self.objects if key.startswith(Prefix))
        return {"Contents": [{"Key": key} for key in keys], "IsTruncated": False}


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeS3()
        self.environment = patch.dict(
            os.environ,
            {
                "RESUME_BUCKET": "private-test-bucket",
                "S3_ACCESS_KEY": "synthetic-access",
                "S3_SECRET_KEY": "synthetic-secret",
            },
        )
        self.client = patch.object(repository_module.boto3, "client", return_value=self.fake)
        self.environment.start()
        self.client.start()
        self.repository = Repository()

    def tearDown(self):
        self.client.stop()
        self.environment.stop()

    def test_finalize_is_idempotent_and_preserves_datetimes(self):
        now = datetime.now(timezone.utc)
        upload_id = "11111111-1111-4111-8111-111111111111"
        self.repository.create_upload(
            {
                "upload_id": upload_id,
                "object_key": f"pending/{upload_id}",
                "original_name": "resume.pdf",
                "declared_type": "application/pdf",
                "size_bytes": 42,
                "status": "pending",
                "created_at": now,
                "expires_at": now + timedelta(hours=1),
            }
        )
        application = {
            "application_id": upload_id,
            "created_at": now,
            "updated_at": now,
            "status": "new",
            "next_notify_at": now,
        }

        saved, created = self.repository.finalize_application(upload_id, application)
        duplicate, created_again = self.repository.finalize_application(upload_id, application)

        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(saved["application_id"], duplicate["application_id"])
        self.assertIsInstance(duplicate["created_at"], datetime)

    def test_rate_limit_counts_within_window(self):
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)

        self.assertTrue(self.repository.allow_request("upload:test", expires_at, 2))
        self.assertTrue(self.repository.allow_request("upload:test", expires_at, 2))
        self.assertFalse(self.repository.allow_request("upload:test", expires_at, 2))


if __name__ == "__main__":
    unittest.main()
