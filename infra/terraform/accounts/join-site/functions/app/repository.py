import os
from datetime import datetime, timezone

import ydb


SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS uploads (
      upload_id Utf8 NOT NULL,
      object_key Utf8 NOT NULL,
      original_name Utf8 NOT NULL,
      declared_type Utf8 NOT NULL,
      size_bytes Uint64 NOT NULL,
      status Utf8 NOT NULL,
      created_at Timestamp NOT NULL,
      expires_at Timestamp NOT NULL,
      application_id Utf8,
      PRIMARY KEY (upload_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS applications (
      application_id Utf8 NOT NULL,
      created_at Timestamp NOT NULL,
      updated_at Timestamp NOT NULL,
      first_name Utf8 NOT NULL,
      last_name Utf8 NOT NULL,
      telegram Utf8 NOT NULL,
      phone Utf8 NOT NULL,
      email Utf8,
      direction Utf8,
      motivation Utf8 NOT NULL,
      portfolio_url Utf8,
      resume_object_key Utf8 NOT NULL,
      resume_name Utf8 NOT NULL,
      resume_size Uint64 NOT NULL,
      resume_media_type Utf8 NOT NULL,
      status Utf8 NOT NULL,
      team_notification_status Utf8 NOT NULL,
      candidate_notification_status Utf8 NOT NULL,
      notify_attempts Uint32 NOT NULL,
      next_notify_at Timestamp NOT NULL,
      PRIMARY KEY (application_id),
      INDEX applications_by_status GLOBAL ON (status, created_at)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rate_limits (
      bucket_key Utf8 NOT NULL,
      request_count Uint32 NOT NULL,
      expires_at Timestamp NOT NULL,
      PRIMARY KEY (bucket_key)
    )
    """,
]


def _rows(result_sets):
    return result_sets[0].rows if result_sets else []


def _as_dict(row) -> dict:
    return {key: value for key, value in row.items()}


class Repository:
    def __init__(self):
        self.driver = ydb.Driver(
            endpoint=os.environ["YDB_ENDPOINT"],
            database=os.environ["YDB_DATABASE"],
            credentials=ydb.iam.MetadataUrlCredentials(),
        )
        self.driver.wait(fail_fast=True, timeout=8)
        self.pool = ydb.SessionPool(self.driver, size=5)
        self._schema_ready = False

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        for statement in SCHEMA:
            self.pool.retry_operation_sync(lambda session, query=statement: session.execute_scheme(query))
        self._schema_ready = True

    def allow_request(self, bucket_key: str, expires_at: datetime, limit: int) -> bool:
        query = """
        DECLARE $bucket_key AS Utf8;
        DECLARE $expires_at AS Timestamp;
        SELECT request_count, expires_at FROM rate_limits WHERE bucket_key = $bucket_key;
        """
        upsert = """
        DECLARE $bucket_key AS Utf8;
        DECLARE $request_count AS Uint32;
        DECLARE $expires_at AS Timestamp;
        UPSERT INTO rate_limits (bucket_key, request_count, expires_at)
        VALUES ($bucket_key, $request_count, $expires_at);
        """

        def operation(session):
            transaction = session.transaction(ydb.SerializableReadWrite()).begin()
            rows = _rows(transaction.execute(query, {"$bucket_key": bucket_key, "$expires_at": expires_at}))
            now = datetime.now(timezone.utc)
            count = 0
            if rows and rows[0].expires_at > now:
                count = int(rows[0].request_count)
            if count >= limit:
                transaction.rollback()
                return False
            transaction.execute(
                upsert,
                {"$bucket_key": bucket_key, "$request_count": count + 1, "$expires_at": expires_at},
                commit_tx=True,
            )
            return True

        return self.pool.retry_operation_sync(operation)

    def get_upload(self, upload_id: str) -> dict | None:
        query = """
        DECLARE $upload_id AS Utf8;
        SELECT * FROM uploads WHERE upload_id = $upload_id;
        """
        rows = self.pool.retry_operation_sync(
            lambda session: _rows(session.transaction().execute(query, {"$upload_id": upload_id}, commit_tx=True))
        )
        return _as_dict(rows[0]) if rows else None

    def create_upload(self, upload: dict) -> None:
        query = """
        DECLARE $upload_id AS Utf8;
        DECLARE $object_key AS Utf8;
        DECLARE $original_name AS Utf8;
        DECLARE $declared_type AS Utf8;
        DECLARE $size_bytes AS Uint64;
        DECLARE $status AS Utf8;
        DECLARE $created_at AS Timestamp;
        DECLARE $expires_at AS Timestamp;
        UPSERT INTO uploads
          (upload_id, object_key, original_name, declared_type, size_bytes, status, created_at, expires_at)
        VALUES
          ($upload_id, $object_key, $original_name, $declared_type, $size_bytes, $status, $created_at, $expires_at);
        """
        params = {f"${key}": value for key, value in upload.items()}
        self.pool.retry_operation_sync(lambda session: session.transaction().execute(query, params, commit_tx=True))

    def finalize_application(self, upload_id: str, application: dict) -> tuple[dict, bool]:
        select_query = """
        DECLARE $upload_id AS Utf8;
        SELECT status, application_id FROM uploads WHERE upload_id = $upload_id;
        """
        existing_query = """
        DECLARE $application_id AS Utf8;
        SELECT * FROM applications WHERE application_id = $application_id;
        """
        write_query = """
        DECLARE $application_id AS Utf8;
        DECLARE $created_at AS Timestamp;
        DECLARE $updated_at AS Timestamp;
        DECLARE $first_name AS Utf8;
        DECLARE $last_name AS Utf8;
        DECLARE $telegram AS Utf8;
        DECLARE $phone AS Utf8;
        DECLARE $email AS Utf8?;
        DECLARE $direction AS Utf8?;
        DECLARE $motivation AS Utf8;
        DECLARE $portfolio_url AS Utf8?;
        DECLARE $resume_object_key AS Utf8;
        DECLARE $resume_name AS Utf8;
        DECLARE $resume_size AS Uint64;
        DECLARE $resume_media_type AS Utf8;
        DECLARE $status AS Utf8;
        DECLARE $team_notification_status AS Utf8;
        DECLARE $candidate_notification_status AS Utf8;
        DECLARE $notify_attempts AS Uint32;
        DECLARE $next_notify_at AS Timestamp;
        DECLARE $upload_id AS Utf8;
        UPSERT INTO applications (
          application_id, created_at, updated_at, first_name, last_name, telegram, phone, email,
          direction, motivation, portfolio_url, resume_object_key, resume_name, resume_size,
          resume_media_type, status, team_notification_status, candidate_notification_status,
          notify_attempts, next_notify_at
        ) VALUES (
          $application_id, $created_at, $updated_at, $first_name, $last_name, $telegram, $phone, $email,
          $direction, $motivation, $portfolio_url, $resume_object_key, $resume_name, $resume_size,
          $resume_media_type, $status, $team_notification_status, $candidate_notification_status,
          $notify_attempts, $next_notify_at
        );
        UPDATE uploads SET status = "attached", application_id = $application_id WHERE upload_id = $upload_id;
        """

        def operation(session):
            transaction = session.transaction(ydb.SerializableReadWrite()).begin()
            upload_rows = _rows(transaction.execute(select_query, {"$upload_id": upload_id}))
            if not upload_rows:
                transaction.rollback()
                raise KeyError("upload_not_found")
            upload = upload_rows[0]
            if upload.status == "attached" and upload.application_id:
                existing_rows = _rows(transaction.execute(existing_query, {"$application_id": upload.application_id}))
                transaction.rollback()
                if not existing_rows:
                    raise RuntimeError("attached_application_missing")
                return _as_dict(existing_rows[0]), False
            if upload.status != "pending":
                transaction.rollback()
                raise ValueError("upload_not_pending")
            params = {f"${key}": value for key, value in application.items()}
            params["$upload_id"] = upload_id
            transaction.execute(write_query, params, commit_tx=True)
            return application, True

        return self.pool.retry_operation_sync(operation)

    def list_applications(self, status: str | None = None) -> list[dict]:
        if status:
            query = """
            DECLARE $status AS Utf8;
            SELECT application_id, created_at, first_name, last_name, direction, status
            FROM applications VIEW applications_by_status
            WHERE status = $status
            ORDER BY status, created_at DESC LIMIT 100;
            """
            params = {"$status": status}
        else:
            query = """
            SELECT application_id, created_at, first_name, last_name, direction, status
            FROM applications WHERE status != "deleting" ORDER BY created_at DESC LIMIT 100;
            """
            params = {}
        rows = self.pool.retry_operation_sync(
            lambda session: _rows(session.transaction().execute(query, params, commit_tx=True))
        )
        return [_as_dict(row) for row in rows]

    def get_application(self, application_id: str) -> dict | None:
        query = """
        DECLARE $application_id AS Utf8;
        SELECT * FROM applications WHERE application_id = $application_id;
        """
        rows = self.pool.retry_operation_sync(
            lambda session: _rows(session.transaction().execute(query, {"$application_id": application_id}, commit_tx=True))
        )
        return _as_dict(rows[0]) if rows else None

    def update_status(self, application_id: str, status: str, updated_at: datetime) -> bool:
        if not self.get_application(application_id):
            return False
        query = """
        DECLARE $application_id AS Utf8;
        DECLARE $status AS Utf8;
        DECLARE $updated_at AS Timestamp;
        UPDATE applications SET status = $status, updated_at = $updated_at
        WHERE application_id = $application_id;
        """
        self.pool.retry_operation_sync(
            lambda session: session.transaction().execute(
                query, {"$application_id": application_id, "$status": status, "$updated_at": updated_at}, commit_tx=True
            )
        )
        return True

    def mark_deleting(self, application_id: str, updated_at: datetime) -> dict | None:
        application = self.get_application(application_id)
        if not application:
            return None
        self.update_status(application_id, "deleting", updated_at)
        return application

    def purge_application(self, application_id: str) -> None:
        query = """
        DECLARE $application_id AS Utf8;
        DELETE FROM applications WHERE application_id = $application_id;
        DELETE FROM uploads WHERE application_id = $application_id;
        """
        self.pool.retry_operation_sync(
            lambda session: session.transaction().execute(query, {"$application_id": application_id}, commit_tx=True)
        )

    def expired_uploads(self, now: datetime) -> list[dict]:
        query = """
        DECLARE $now AS Timestamp;
        SELECT upload_id, object_key FROM uploads WHERE status = "pending" AND expires_at < $now LIMIT 100;
        """
        rows = self.pool.retry_operation_sync(
            lambda session: _rows(session.transaction().execute(query, {"$now": now}, commit_tx=True))
        )
        return [_as_dict(row) for row in rows]

    def delete_upload(self, upload_id: str) -> None:
        query = """
        DECLARE $upload_id AS Utf8;
        DELETE FROM uploads WHERE upload_id = $upload_id AND status = "pending";
        """
        self.pool.retry_operation_sync(
            lambda session: session.transaction().execute(query, {"$upload_id": upload_id}, commit_tx=True)
        )

    def deleting_applications(self) -> list[dict]:
        query = """
        SELECT application_id, resume_object_key FROM applications WHERE status = "deleting" LIMIT 100;
        """
        rows = self.pool.retry_operation_sync(
            lambda session: _rows(session.transaction().execute(query, commit_tx=True))
        )
        return [_as_dict(row) for row in rows]

    def pending_notifications(self, now: datetime) -> list[dict]:
        query = """
        DECLARE $now AS Timestamp;
        SELECT * FROM applications
        WHERE (team_notification_status = "pending" OR candidate_notification_status = "pending")
          AND next_notify_at <= $now AND status != "deleting"
        ORDER BY next_notify_at LIMIT 50;
        """
        rows = self.pool.retry_operation_sync(
            lambda session: _rows(session.transaction().execute(query, {"$now": now}, commit_tx=True))
        )
        return [_as_dict(row) for row in rows]

    def update_notifications(
        self,
        application_id: str,
        *,
        team_status: str,
        candidate_status: str,
        attempts: int,
        next_attempt_at: datetime,
    ) -> None:
        query = """
        DECLARE $application_id AS Utf8;
        DECLARE $team_status AS Utf8;
        DECLARE $candidate_status AS Utf8;
        DECLARE $attempts AS Uint32;
        DECLARE $next_attempt_at AS Timestamp;
        UPDATE applications SET
          team_notification_status = $team_status,
          candidate_notification_status = $candidate_status,
          notify_attempts = $attempts,
          next_notify_at = $next_attempt_at
        WHERE application_id = $application_id;
        """
        params = {
            "$application_id": application_id,
            "$team_status": team_status,
            "$candidate_status": candidate_status,
            "$attempts": attempts,
            "$next_attempt_at": next_attempt_at,
        }
        self.pool.retry_operation_sync(lambda session: session.transaction().execute(query, params, commit_tx=True))
