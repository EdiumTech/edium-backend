import hashlib
import json
import os
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


DATE_FIELDS = {
    "created_at",
    "updated_at",
    "expires_at",
    "next_notify_at",
    "start_before",
    "opened_at",
    "started_at",
    "deadline_at",
    "submitted_at",
    "expired_at",
    "revoked_at",
    "extended_at",
    "purge_after",
}


class ContestConflict(Exception):
    pass


def _json_default(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    raise TypeError(f"Unsupported JSON value: {type(value)!r}")


def _decode_dates(payload: dict) -> dict:
    for field in DATE_FIELDS:
        value = payload.get(field)
        if isinstance(value, str):
            payload[field] = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return payload


class Repository:
    """Small-volume application metadata stored beside resumes in private S3.

    Object keys are deterministic, so retried requests overwrite the same record
    instead of creating duplicates. Herald provides a second idempotency boundary
    for email delivery.
    """

    def __init__(self):
        self.bucket = os.environ["RESUME_BUCKET"]
        self.client = boto3.client(
            "s3",
            endpoint_url="https://storage.yandexcloud.net",
            region_name="ru-central1",
            aws_access_key_id=os.environ["S3_ACCESS_KEY"],
            aws_secret_access_key=os.environ["S3_SECRET_KEY"],
            config=Config(
                signature_version="s3v4",
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )

    def ensure_schema(self) -> None:
        return None

    def _get(self, key: str) -> dict | None:
        value, _ = self._get_with_etag(key)
        return value

    def _get_with_etag(self, key: str) -> tuple[dict | None, str | None]:
        try:
            result = self.client.get_object(Bucket=self.bucket, Key=key)
            body = result["Body"].read()
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code in {"NoSuchKey", "NoSuchObject", "404"}:
                return None, None
            raise
        return _decode_dates(json.loads(body.decode("utf-8"))), result.get("ETag")

    def _put(self, key: str, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType="application/json; charset=utf-8",
            CacheControl="no-store",
        )

    def _put_if_absent(self, key: str, payload: dict) -> None:
        self._conditional_put(key, payload, IfNoneMatch="*")

    def _put_if_match(self, key: str, payload: dict, etag: str) -> None:
        self._conditional_put(key, payload, IfMatch=etag)

    def _conditional_put(self, key: str, payload: dict, **condition) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=body,
                ContentType="application/json; charset=utf-8",
                CacheControl="no-store",
                **condition,
            )
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code in {"PreconditionFailed", "ConditionalRequestConflict", "409", "412"}:
                raise ContestConflict() from error
            raise

    def _delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def _list(self, prefix: str) -> list[dict]:
        rows = []
        continuation_token = None
        while True:
            params = {"Bucket": self.bucket, "Prefix": prefix, "MaxKeys": 1000}
            if continuation_token:
                params["ContinuationToken"] = continuation_token
            page = self.client.list_objects_v2(**params)
            for item in page.get("Contents", []):
                value = self._get(item["Key"])
                if value is not None:
                    rows.append(value)
            if not page.get("IsTruncated"):
                return rows
            continuation_token = page["NextContinuationToken"]

    @staticmethod
    def _upload_key(upload_id: str) -> str:
        return f"metadata/uploads/{upload_id}.json"

    @staticmethod
    def _application_key(application_id: str) -> str:
        return f"metadata/applications/{application_id}.json"

    @staticmethod
    def _contest_key(contest_id: str) -> str:
        return f"metadata/contests/{contest_id}.json"

    def allow_request(self, bucket_key: str, expires_at: datetime, limit: int) -> bool:
        digest = hashlib.sha256(bucket_key.encode()).hexdigest()
        key = f"metadata/rate-limits/{digest}.json"
        now = datetime.now(timezone.utc)
        record = self._get(key)
        count = 0
        if record and record["expires_at"] > now:
            count = int(record["request_count"])
        if count >= limit:
            return False
        self._put(key, {"request_count": count + 1, "expires_at": expires_at})
        return True

    def allow_contest_request(self, bucket_key: str, expires_at: datetime, limit: int) -> bool:
        """Optimistic counter used for token and runner endpoints.

        A conflict is retried from the latest object, so concurrent requests cannot
        both consume the same remaining slot.
        """
        digest = hashlib.sha256(bucket_key.encode()).hexdigest()
        key = f"metadata/contest-rate-limits/{digest}.json"
        for _ in range(5):
            now = datetime.now(timezone.utc)
            record, etag = self._get_with_etag(key)
            count = int(record["request_count"]) if record and record["expires_at"] > now else 0
            if count >= limit:
                return False
            next_record = {"request_count": count + 1, "expires_at": expires_at}
            try:
                if etag:
                    self._put_if_match(key, next_record, etag)
                else:
                    self._put_if_absent(key, next_record)
                return True
            except ContestConflict:
                continue
        return False

    def get_upload(self, upload_id: str) -> dict | None:
        return self._get(self._upload_key(upload_id))

    def create_upload(self, upload: dict) -> None:
        self._put(self._upload_key(upload["upload_id"]), upload)

    def finalize_application(self, upload_id: str, application: dict) -> tuple[dict, bool]:
        upload = self.get_upload(upload_id)
        if not upload:
            raise KeyError("upload_not_found")
        if upload["status"] == "attached" and upload.get("application_id"):
            existing = self.get_application(upload["application_id"])
            if not existing:
                raise RuntimeError("attached_application_missing")
            return existing, False
        if upload["status"] != "pending":
            raise ValueError("upload_not_pending")

        existing = self.get_application(application["application_id"])
        created = existing is None
        if created:
            self._put(self._application_key(application["application_id"]), application)
        else:
            application = existing
        upload["status"] = "attached"
        upload["application_id"] = application["application_id"]
        self._put(self._upload_key(upload_id), upload)
        return application, created

    def list_applications(self, status: str | None = None) -> list[dict]:
        rows = [
            item
            for item in self._list("metadata/applications/")
            if item.get("status") != "deleting" and (not status or item.get("status") == status)
        ]
        rows.sort(key=lambda item: item["created_at"], reverse=True)
        return rows[:100]

    def get_application(self, application_id: str) -> dict | None:
        return self._get(self._application_key(application_id))

    def create_contest(self, contest: dict) -> bool:
        try:
            self._put_if_absent(self._contest_key(contest["contest_id"]), contest)
            return True
        except ContestConflict:
            return False

    def get_contest(self, contest_id: str) -> dict | None:
        return self._get(self._contest_key(contest_id))

    def get_contest_with_etag(self, contest_id: str) -> tuple[dict | None, str | None]:
        return self._get_with_etag(self._contest_key(contest_id))

    def save_contest(self, contest: dict, etag: str) -> None:
        self._put_if_match(self._contest_key(contest["contest_id"]), contest, etag)

    def find_contest_by_application(self, application_id: str) -> dict | None:
        rows = [
            item
            for item in self._list("metadata/contests/")
            if item.get("application_id") == application_id
        ]
        rows.sort(key=lambda item: item["created_at"], reverse=True)
        return rows[0] if rows else None

    def list_contests(self, state: str | None = None) -> list[dict]:
        rows = [
            item
            for item in self._list("metadata/contests/")
            if not state or item.get("state") == state
        ]
        rows.sort(key=lambda item: item["created_at"], reverse=True)
        return rows[:200]

    def pending_contest_notifications(self, now: datetime) -> list[dict]:
        rows = [
            item
            for item in self._list("metadata/contests/")
            if item.get("next_notify_at")
            and item["next_notify_at"] <= now
            and (
                item.get("invitation_notification_status") == "pending"
                or item.get("completion_notification_status") == "pending"
            )
        ]
        rows.sort(key=lambda item: item["next_notify_at"])
        return rows[:50]

    def update_contest_notifications(
        self,
        contest_id: str,
        *,
        invitation_status: str,
        completion_status: str,
        attempts: int,
        next_attempt_at: datetime,
    ) -> None:
        for _ in range(5):
            contest, etag = self.get_contest_with_etag(contest_id)
            if not contest or not etag:
                return
            contest.update(
                {
                    "invitation_notification_status": invitation_status,
                    "completion_notification_status": completion_status,
                    "notify_attempts": attempts,
                    "next_notify_at": next_attempt_at,
                }
            )
            try:
                self.save_contest(contest, etag)
                return
            except ContestConflict:
                continue

    def delete_contests_for_application(self, application_id: str) -> None:
        for contest in self.list_contests():
            if contest.get("application_id") == application_id:
                self._delete(self._contest_key(contest["contest_id"]))

    def purge_retained_contests(self, now: datetime) -> int:
        removed = 0
        for contest in self.list_contests():
            if contest.get("purge_after") and contest["purge_after"] <= now:
                self._delete(self._contest_key(contest["contest_id"]))
                removed += 1
        return removed

    def update_status(self, application_id: str, status: str, updated_at: datetime) -> bool:
        application = self.get_application(application_id)
        if not application:
            return False
        application["status"] = status
        application["updated_at"] = updated_at
        self._put(self._application_key(application_id), application)
        return True

    def mark_deleting(self, application_id: str, updated_at: datetime) -> dict | None:
        application = self.get_application(application_id)
        if not application:
            return None
        application["status"] = "deleting"
        application["updated_at"] = updated_at
        self._put(self._application_key(application_id), application)
        return application

    def purge_application(self, application_id: str) -> None:
        self._delete(self._application_key(application_id))
        self._delete(self._upload_key(application_id))

    def expired_uploads(self, now: datetime) -> list[dict]:
        return [
            item
            for item in self._list("metadata/uploads/")
            if item.get("status") == "pending" and item["expires_at"] < now
        ][:100]

    def delete_upload(self, upload_id: str) -> None:
        upload = self.get_upload(upload_id)
        if upload and upload.get("status") == "pending":
            self._delete(self._upload_key(upload_id))

    def deleting_applications(self) -> list[dict]:
        return [
            item
            for item in self._list("metadata/applications/")
            if item.get("status") == "deleting"
        ][:100]

    def pending_notifications(self, now: datetime) -> list[dict]:
        rows = [
            item
            for item in self._list("metadata/applications/")
            if item.get("status") != "deleting"
            and item["next_notify_at"] <= now
            and (
                item.get("team_notification_status") == "pending"
                or item.get("candidate_notification_status") == "pending"
            )
        ]
        rows.sort(key=lambda item: item["next_notify_at"])
        return rows[:50]

    def update_notifications(
        self,
        application_id: str,
        *,
        team_status: str,
        candidate_status: str,
        attempts: int,
        next_attempt_at: datetime,
    ) -> None:
        application = self.get_application(application_id)
        if not application:
            return
        application.update(
            {
                "team_notification_status": team_status,
                "candidate_notification_status": candidate_status,
                "notify_attempts": attempts,
                "next_notify_at": next_attempt_at,
            }
        )
        self._put(self._application_key(application_id), application)
