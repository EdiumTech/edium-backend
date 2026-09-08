import base64
import hashlib
import hmac
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

from botocore.exceptions import ClientError

from mailer import Mailer
from repository import Repository
from storage import InvalidResume, ResumeStorage
from validation import STATUSES, ValidationError, validate_application, validate_upload


_repository = None
_storage = None


def repository() -> Repository:
    global _repository
    if _repository is None:
        _repository = Repository()
        _repository.ensure_schema()
    return _repository


def storage() -> ResumeStorage:
    global _storage
    if _storage is None:
        _storage = ResumeStorage()
    return _storage


def response(status: int, payload: dict, event: dict | None = None) -> dict:
    origin = ""
    if event:
        origin = header(event, "origin") or ""
    allowed = {item.strip() for item in os.getenv("ALLOWED_ORIGINS", "https://edium.online").split(",") if item.strip()}
    cors_origin = origin if origin in allowed else next(iter(allowed), "https://edium.online")
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json; charset=utf-8",
            "Cache-Control": "no-store",
            "Access-Control-Allow-Origin": cors_origin,
            "Access-Control-Allow-Headers": "Content-Type, Authorization, Idempotency-Key",
            "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
            "Vary": "Origin",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
        "body": json.dumps(payload, ensure_ascii=False, default=json_default),
    }


def json_default(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    raise TypeError()


def header(event: dict, name: str) -> str | None:
    headers = event.get("headers") or {}
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None


def body_json(event: dict) -> dict:
    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        raise ValidationError("Некорректный JSON.", {})
    if not isinstance(payload, dict):
        raise ValidationError("Некорректный запрос.", {})
    return payload


def source_ip(event: dict) -> str:
    request_context = event.get("requestContext") or {}
    identity = request_context.get("identity") or {}
    return identity.get("sourceIp") or request_context.get("http", {}).get("sourceIp") or "unknown"


def ensure_started_recently(payload: dict) -> None:
    value = payload.get("startedAt")
    try:
        started = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - started.astimezone(timezone.utc)
    except (TypeError, ValueError):
        raise ValidationError("Обнови страницу и попробуй ещё раз.", {})
    if age < timedelta(seconds=3) or age > timedelta(hours=24):
        raise ValidationError("Обнови страницу и попробуй ещё раз.", {})


def deterministic_upload_id(idempotency_key: str) -> str:
    digest = hmac.new(os.environ["IP_HASH_SALT"].encode(), idempotency_key.encode(), hashlib.sha256).digest()[:16]
    return str(uuid.UUID(bytes=digest, version=4))


def rate_limit(event: dict, action: str, limit: int, minutes: int) -> bool:
    now = datetime.now(timezone.utc)
    window = int(now.timestamp()) // (minutes * 60)
    digest = hmac.new(
        os.environ["IP_HASH_SALT"].encode(),
        f"{source_ip(event)}:{action}:{window}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return repository().allow_request(f"{action}:{digest}", now + timedelta(minutes=minutes + 1), limit)


def require_admin(event: dict) -> None:
    authorization = header(event, "authorization") or ""
    expected = f"Bearer {os.environ['ADMIN_TOKEN']}"
    if not hmac.compare_digest(authorization.encode(), expected.encode()):
        raise PermissionError()


def create_upload(event: dict) -> dict:
    payload = body_json(event)
    ensure_started_recently(payload)
    values = validate_upload(payload)
    if not rate_limit(event, "upload", 5, 15):
        return response(429, {"message": "Слишком много попыток. Попробуй через 15 минут."}, event)
    idempotency_key = header(event, "idempotency-key") or ""
    if not re.fullmatch(r"[0-9a-f-]{36}", idempotency_key):
        return response(400, {"message": "Обнови страницу и попробуй ещё раз."}, event)
    upload_id = deterministic_upload_id(idempotency_key)
    now = datetime.now(timezone.utc)
    existing = repository().get_upload(upload_id)
    if existing and existing["status"] == "attached":
        return response(409, {"message": "Эта заявка уже сохранена."}, event)
    if existing:
        object_key = existing["object_key"]
        values = {
            "file_name": existing["original_name"],
            "media_type": existing["declared_type"],
            "size": existing["size_bytes"],
        }
    else:
        object_key = f"pending/{uuid.uuid4().hex}"
        repository().create_upload(
            {
                "upload_id": upload_id,
                "object_key": object_key,
                "original_name": values["file_name"],
                "declared_type": values["media_type"],
                "size_bytes": values["size"],
                "status": "pending",
                "created_at": now,
                "expires_at": now + timedelta(hours=1),
            }
        )
    upload_form = storage().upload_form(
        object_key=object_key,
        upload_id=upload_id,
        media_type=values["media_type"],
        size=values["size"],
    )
    return response(201, {"uploadId": upload_id, "expiresIn": 600, "upload": upload_form}, event)


def create_application(event: dict) -> dict:
    payload = body_json(event)
    ensure_started_recently(payload)
    values = validate_application(payload)
    if not rate_limit(event, "application", 5, 60):
        return response(429, {"message": "Слишком много попыток. Попробуй позже."}, event)
    upload = repository().get_upload(values["upload_id"])
    if not upload:
        return response(400, {"message": "Сессия загрузки истекла.", "fieldErrors": {"resume": "Выбери файл заново."}}, event)
    if upload["status"] == "attached" and upload.get("application_id"):
        return response(200, {"saved": True, "applicationId": upload["application_id"], "duplicate": True}, event)
    if upload["expires_at"] < datetime.now(timezone.utc):
        return response(400, {"message": "Сессия загрузки истекла.", "fieldErrors": {"resume": "Выбери файл заново."}}, event)
    try:
        actual_type = storage().validate_uploaded(
            object_key=upload["object_key"],
            upload_id=upload["upload_id"],
            declared_type=upload["declared_type"],
            declared_size=upload["size_bytes"],
        )
    except (InvalidResume, ClientError):
        return response(400, {"message": "Не удалось проверить резюме.", "fieldErrors": {"resume": "Проверь формат файла и загрузи его ещё раз."}}, event)

    now = datetime.now(timezone.utc)
    application = {
        "application_id": upload["upload_id"],
        "created_at": now,
        "updated_at": now,
        "first_name": values["first_name"],
        "last_name": values["last_name"],
        "telegram": values["telegram"],
        "phone": values["phone"],
        "email": values["email"],
        "direction": values["direction"],
        "motivation": values["motivation"],
        "portfolio_url": values["portfolio_url"],
        "resume_object_key": upload["object_key"],
        "resume_name": upload["original_name"],
        "resume_size": upload["size_bytes"],
        "resume_media_type": actual_type,
        "status": "new",
        "team_notification_status": "pending",
        "candidate_notification_status": "pending" if values["email"] else "not_requested",
        "notify_attempts": 0,
        "next_notify_at": now,
    }
    saved, created = repository().finalize_application(upload["upload_id"], application)
    return response(201 if created else 200, {"saved": True, "applicationId": saved["application_id"], "duplicate": not created}, event)


def public_application(item: dict, detail: bool = False) -> dict:
    result = {
        "id": item["application_id"],
        "createdAt": item["created_at"],
        "firstName": item["first_name"],
        "lastName": item["last_name"],
        "direction": item.get("direction"),
        "status": item["status"],
    }
    if detail:
        result.update(
            {
                "updatedAt": item["updated_at"],
                "telegram": item["telegram"],
                "phone": item["phone"],
                "email": item.get("email"),
                "motivation": item["motivation"],
                "portfolioUrl": item.get("portfolio_url"),
                "resumeName": item["resume_name"],
                "resumeSize": item["resume_size"],
                "resumeMediaType": item["resume_media_type"],
            }
        )
    return result


def admin_route(event: dict, method: str, path: str) -> dict:
    require_admin(event)
    if method == "GET" and path == "/v1/admin/applications":
        status = (event.get("queryStringParameters") or {}).get("status")
        if status and status not in STATUSES:
            return response(400, {"message": "Неизвестный статус."}, event)
        items = repository().list_applications(status)
        return response(200, {"applications": [public_application(item) for item in items]}, event)

    match = re.fullmatch(r"/v1/admin/applications/([0-9a-f-]{36})(/resume)?", path)
    if not match:
        return response(404, {"message": "Не найдено."}, event)
    application_id, resume_suffix = match.groups()
    application = repository().get_application(application_id)
    if not application or application.get("status") == "deleting":
        return response(404, {"message": "Заявка не найдена."}, event)
    if method == "GET" and resume_suffix:
        url = storage().download_url(object_key=application["resume_object_key"], original_name=application["resume_name"])
        return response(200, {"downloadUrl": url, "expiresIn": 60}, event)
    if method == "GET":
        return response(200, {"application": public_application(application, detail=True)}, event)
    if method == "PATCH" and not resume_suffix:
        status = body_json(event).get("status")
        if status not in STATUSES:
            return response(400, {"message": "Неизвестный статус."}, event)
        repository().update_status(application_id, status, datetime.now(timezone.utc))
        return response(200, {"saved": True, "status": status}, event)
    if method == "DELETE" and not resume_suffix:
        marked = repository().mark_deleting(application_id, datetime.now(timezone.utc))
        if marked:
            storage().delete(marked["resume_object_key"])
            repository().purge_application(application_id)
        return response(200, {"deleted": True}, event)
    return response(405, {"message": "Метод не поддерживается."}, event)


def api(event, context=None):
    method = (event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method") or "GET").upper()
    path = event.get("path") or event.get("rawPath") or "/"
    if method == "OPTIONS":
        return response(204, {}, event)
    try:
        if method == "POST" and path == "/v1/uploads":
            return create_upload(event)
        if method == "POST" and path == "/v1/applications":
            return create_application(event)
        if path.startswith("/v1/admin/"):
            return admin_route(event, method, path)
        return response(404, {"message": "Не найдено."}, event)
    except ValidationError as error:
        payload = {"message": error.message}
        if error.field_errors:
            payload["fieldErrors"] = error.field_errors
        return response(400, payload, event)
    except PermissionError:
        return response(401, {"message": "Требуется авторизация."}, event)
    except Exception:
        # Intentionally do not log request data or exception details: they may contain PII.
        return response(500, {"message": "Временная ошибка сервера. Попробуй ещё раз."}, event)


def maintenance(event, context=None):
    repo = repository()
    store = storage()
    now = datetime.now(timezone.utc)
    cleaned = 0
    for upload in repo.expired_uploads(now):
        try:
            store.delete(upload["object_key"])
            repo.delete_upload(upload["upload_id"])
            cleaned += 1
        except Exception:
            continue
    for application in repo.deleting_applications():
        try:
            store.delete(application["resume_object_key"])
            repo.purge_application(application["application_id"])
            cleaned += 1
        except Exception:
            continue

    mailer = Mailer()
    delivered = 0
    if mailer.enabled:
        for application in repo.pending_notifications(now):
            team_status = application["team_notification_status"]
            candidate_status = application["candidate_notification_status"]
            attempts = int(application["notify_attempts"]) + 1
            try:
                if team_status == "pending":
                    mailer.send_team(application)
                    team_status = "sent"
                if candidate_status == "pending":
                    mailer.send_candidate(application)
                    candidate_status = "sent"
                delivered += 1
            except Exception:
                pass
            delay_minutes = min(24 * 60, 5 * (2 ** min(attempts, 8)))
            repo.update_notifications(
                application["application_id"],
                team_status=team_status,
                candidate_status=candidate_status,
                attempts=attempts,
                next_attempt_at=now + timedelta(minutes=delay_minutes),
            )
    return {"statusCode": 200, "body": json.dumps({"cleaned": cleaned, "delivered": delivered})}
