import base64
import hashlib
import hmac
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

from botocore.exceptions import ClientError

from contest import (
    CONTEST_STATES,
    ContestError,
    answer_language,
    assigned_task_set,
    attach_token_hash,
    candidate_view,
    clone,
    contest_id_from_token,
    expire_if_due,
    extend_contest,
    invitation_token,
    mark_invitation_pending,
    new_contest,
    open_contest,
    revoke_contest,
    save_answer,
    start_contest,
    submit_contest,
    token_matches,
    update_review,
    utcnow,
    validate_source,
)
from contest_content import ACTIVE_DIRECTIONS, public_tasks, task_ids, track_label
from mailer import Mailer
from repository import ContestConflict, Repository
from runner import RunnerUnavailable, SandboxRunner
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


def contest_token(event: dict) -> str:
    authorization = header(event, "authorization") or ""
    prefix = "Contest "
    if not authorization.startswith(prefix):
        raise ContestError("invalid_invite", "Ссылка на контест недействительна.", 404)
    return authorization[len(prefix) :]


def invite_url(contest: dict) -> str:
    base = os.getenv("CONTEST_URL", "https://edium.online/join/contest/").rstrip("/") + "/"
    token = invitation_token(contest["contest_id"], os.environ["CONTEST_TOKEN_KEY"])
    return f"{base}#invite={token}"


def get_authorized_contest(event: dict) -> tuple[dict, str, str]:
    token = contest_token(event)
    contest_id = contest_id_from_token(token)
    contest, etag = repository().get_contest_with_etag(contest_id)
    if not contest or not etag or not token_matches(contest, token, os.environ["CONTEST_TOKEN_KEY"]):
        raise ContestError("invalid_invite", "Ссылка на контест недействительна.", 404)
    return contest, etag, token


def contest_rate_limit(event: dict, token: str, action: str, limit: int, minutes: int) -> None:
    now = utcnow()
    window = int(now.timestamp()) // (minutes * 60)
    digest = hmac.new(
        os.environ["IP_HASH_SALT"].encode(),
        f"{source_ip(event)}:{hashlib.sha256(token.encode()).hexdigest()}:{action}:{window}".encode(),
        hashlib.sha256,
    ).hexdigest()
    allowed = repository().allow_contest_request(
        f"contest:{action}:{digest}", now + timedelta(minutes=minutes + 1), limit
    )
    if not allowed:
        raise ContestError("rate_limited", "Слишком много запросов. Подожди немного.", 429)


def save_contest_or_conflict(contest: dict, etag: str) -> None:
    try:
        repository().save_contest(contest, etag)
    except ContestConflict as error:
        raise ContestError("revision_conflict", "В другой вкладке уже сохранена более новая версия.", 409) from error


def save_idempotent_transition(token: str, contest: dict, etag: str, transition) -> dict:
    """Persist start/open/submit exactly once even when two tabs race."""
    for _ in range(5):
        changed = transition(contest, utcnow())
        if not changed:
            return contest
        try:
            repository().save_contest(contest, etag)
            return contest
        except ContestConflict:
            contest_id = contest_id_from_token(token)
            contest, etag = repository().get_contest_with_etag(contest_id)
            if not contest or not etag or not token_matches(contest, token, os.environ["CONTEST_TOKEN_KEY"]):
                raise ContestError("invalid_invite", "Ссылка на контест недействительна.", 404)
    raise ContestError("revision_conflict", "Не удалось сохранить изменение. Попробуй ещё раз.", 409)


def contest_payload(contest: dict, *, include_tasks: bool = True) -> dict:
    payload = candidate_view(contest)
    if include_tasks:
        payload["tasks"] = public_tasks(assigned_task_set(contest))
    return payload


def open_candidate_contest(event: dict) -> dict:
    contest, etag, token = get_authorized_contest(event)
    contest_rate_limit(event, token, "open", 30, 15)
    contest = save_idempotent_transition(token, contest, etag, open_contest)
    return response(200, {"contest": contest_payload(contest)}, event)


def read_candidate_contest(event: dict) -> dict:
    contest, etag, token = get_authorized_contest(event)
    contest_rate_limit(event, token, "read", 120, 15)
    contest = save_idempotent_transition(token, contest, etag, expire_if_due)
    return response(200, {"contest": contest_payload(contest)}, event)


def start_candidate_contest(event: dict) -> dict:
    contest, etag, token = get_authorized_contest(event)
    contest_rate_limit(event, token, "start", 10, 15)
    contest = save_idempotent_transition(token, contest, etag, start_contest)
    if contest["state"] == "expired":
        raise ContestError("expired", "Срок начала контеста истёк.", 410)
    return response(200, {"contest": contest_payload(contest)}, event)


def save_candidate_answer(event: dict, task_id: str) -> dict:
    contest, etag, token = get_authorized_contest(event)
    contest_rate_limit(event, token, "save", 240, 15)
    payload = body_json(event)
    expected = payload.get("revision")
    if not isinstance(expected, int):
        raise ContestError("invalid_revision", "Не удалось определить версию ответа.")
    now = utcnow()
    if expire_if_due(contest, now):
        save_contest_or_conflict(contest, etag)
        raise ContestError("expired", "Время истекло. Сохранена последняя серверная версия.", 410)
    changed = save_answer(contest, task_id, payload, expected, now)
    if changed:
        save_contest_or_conflict(contest, etag)
    return response(200, {"saved": True, "contest": contest_payload(contest, include_tasks=False)}, event)


def run_candidate_tests(event: dict) -> dict:
    contest, etag, token = get_authorized_contest(event)
    contest_rate_limit(event, token, "run", 30, 15)
    now = utcnow()
    if expire_if_due(contest, now):
        save_contest_or_conflict(contest, etag)
        raise ContestError("expired", "Время истекло. Код больше нельзя запускать.", 410)
    if contest["state"] != "started":
        raise ContestError("not_started", "Сначала запусти контест.", 409)
    payload = body_json(event)
    task_id = payload.get("taskId")
    source = payload.get("source")
    version = assigned_task_set(contest)
    if not isinstance(task_id, str) or task_id not in task_ids(version):
        raise ContestError("unknown_task", "Задача не найдена.", 404)
    validate_source(source)
    language = answer_language(contest, task_id, payload.get("language"))
    try:
        result = SandboxRunner().run(task_id, source, version, language)
    except RunnerUnavailable as error:
        raise ContestError("runner_unavailable", "Песочница временно недоступна. Код сохранён; попробуй позже.", 503) from error
    return response(200, {"result": result}, event)


def submit_candidate_contest(event: dict) -> dict:
    contest, etag, token = get_authorized_contest(event)
    contest_rate_limit(event, token, "submit", 10, 15)
    contest = save_idempotent_transition(token, contest, etag, submit_contest)
    return response(200, {"submitted": contest["state"] == "submitted", "contest": contest_payload(contest, include_tasks=False)}, event)


def admin_contest_view(contest: dict, application: dict) -> dict:
    result = clone(contest)
    result.pop("token_hash", None)
    result["id"] = result.pop("contest_id")
    result["applicationId"] = result.pop("application_id")
    result["inviteUrl"] = invite_url(contest) if contest["state"] not in {"submitted", "expired", "revoked"} else None
    result["hasEmail"] = bool(application.get("email"))
    result["tasks"] = public_tasks(assigned_task_set(contest))
    result["trackLabel"] = track_label(assigned_task_set(contest))
    return result


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
        object_key = f"pending/{upload_id}"
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


def public_application(item: dict, detail: bool = False, contest: dict | None = None) -> dict:
    result = {
        "id": item["application_id"],
        "createdAt": item["created_at"],
        "firstName": item["first_name"],
        "lastName": item["last_name"],
        "direction": item.get("direction"),
        "status": item["status"],
        "contestState": contest.get("state") if contest else None,
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
                "contest": admin_contest_view(contest, item) if contest else None,
            }
        )
    return result


def admin_contest_route(event: dict, method: str, application: dict, action: str | None) -> dict:
    now = utcnow()
    existing = repository().find_contest_by_application(application["application_id"])
    if method == "POST" and action is None:
        if existing:
            return response(409, {"message": "Для кандидата уже создан контест.", "contest": admin_contest_view(existing, application)}, event)
        payload = body_json(event)
        duration = payload.get("durationMinutes", 90)
        start_within_days = payload.get("startWithinDays", 7)
        requested_direction = payload.get("direction")
        if not isinstance(duration, int) or not isinstance(start_within_days, int) or start_within_days < 1 or start_within_days > 30:
            raise ContestError("invalid_invitation", "Проверь продолжительность и срок начала.")
        if requested_direction is not None and (
            not isinstance(requested_direction, str) or requested_direction not in ACTIVE_DIRECTIONS
        ):
            raise ContestError("invalid_direction", "Выбери доступный набор задач.")
        # Older admin clients did not send a direction. Keep their behaviour for
        # compatibility, while the current UI assigns the task set explicitly.
        contest_direction = requested_direction or application.get("direction")
        contest, _ = new_contest(
            application["application_id"], duration, now + timedelta(days=start_within_days), now,
            direction=contest_direction,
        )
        required_native_languages = set(candidate_view(contest)["languages"]) - {"javascript"}
        if required_native_languages and not required_native_languages.issubset(SandboxRunner().supported_languages):
            language_labels = {"kotlin": "Kotlin", "swift": "Swift", "python": "Python", "go": "Go"}
            required_labels = " и ".join(language_labels[language] for language in sorted(required_native_languages))
            raise ContestError(
                "runtime_unavailable",
                f"Контест пока недоступен: сначала настройте изолированный запуск {required_labels}.",
                503,
            )
        attach_token_hash(contest, os.environ["CONTEST_TOKEN_KEY"])
        if not application.get("email"):
            contest["invitation_notification_status"] = "not_requested"
        if not repository().create_contest(contest):
            return response(409, {"message": "Контест уже создан."}, event)
        return response(201, {"contest": admin_contest_view(contest, application)}, event)
    if not existing:
        return response(404, {"message": "Контест ещё не создан."}, event)
    contest, etag = repository().get_contest_with_etag(existing["contest_id"])
    if not contest or not etag:
        return response(404, {"message": "Контест не найден."}, event)
    if method == "GET" and action is None:
        return response(200, {"contest": admin_contest_view(contest, application)}, event)
    if method == "POST" and action == "resend":
        if not application.get("email"):
            return response(409, {"message": "У кандидата нет email — скопируй персональную ссылку."}, event)
        mark_invitation_pending(contest, now)
    elif method == "POST" and action == "revoke":
        revoke_contest(contest, now)
    elif method == "POST" and action == "extend":
        minutes = body_json(event).get("minutes", 60)
        if not isinstance(minutes, int):
            raise ContestError("invalid_extension", "Укажи продолжительность продления.")
        extend_contest(contest, minutes, now)
    elif method == "PATCH" and action == "review":
        update_review(contest, body_json(event), now)
    else:
        return response(405, {"message": "Метод не поддерживается."}, event)
    save_contest_or_conflict(contest, etag)
    return response(200, {"saved": True, "contest": admin_contest_view(contest, application)}, event)


def admin_route(event: dict, method: str, path: str) -> dict:
    require_admin(event)
    if method == "GET" and path == "/v1/admin/applications":
        query = event.get("queryStringParameters") or {}
        status = query.get("status")
        contest_state = query.get("contestState")
        if status and status not in STATUSES:
            return response(400, {"message": "Неизвестный статус."}, event)
        if contest_state and contest_state not in CONTEST_STATES and contest_state != "none":
            return response(400, {"message": "Неизвестный статус контеста."}, event)
        items = repository().list_applications(status)
        contests = {item["application_id"]: item for item in repository().list_contests()}
        if contest_state:
            items = [
                item for item in items
                if (contest_state == "none" and item["application_id"] not in contests)
                or contests.get(item["application_id"], {}).get("state") == contest_state
            ]
        return response(
            200,
            {"applications": [public_application(item, contest=contests.get(item["application_id"])) for item in items]},
            event,
        )

    contest_match = re.fullmatch(
        r"/v1/admin/applications/([0-9a-f-]{36})/contest(?:/(resend|revoke|extend|review))?",
        path,
    )
    if contest_match:
        application_id, action = contest_match.groups()
        application = repository().get_application(application_id)
        if not application or application.get("status") == "deleting":
            return response(404, {"message": "Заявка не найдена."}, event)
        return admin_contest_route(event, method, application, action)

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
        contest = repository().find_contest_by_application(application_id)
        return response(200, {"application": public_application(application, detail=True, contest=contest)}, event)
    if method == "PATCH" and not resume_suffix:
        status = body_json(event).get("status")
        if status not in STATUSES:
            return response(400, {"message": "Неизвестный статус."}, event)
        repository().update_status(application_id, status, datetime.now(timezone.utc))
        return response(200, {"saved": True, "status": status}, event)
    if method == "DELETE" and not resume_suffix:
        marked = repository().mark_deleting(application_id, datetime.now(timezone.utc))
        if marked:
            repository().delete_contests_for_application(application_id)
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
        if method == "POST" and path == "/v1/contest/open":
            return open_candidate_contest(event)
        if method == "GET" and path == "/v1/contest":
            return read_candidate_contest(event)
        if method == "POST" and path == "/v1/contest/start":
            return start_candidate_contest(event)
        answer_match = re.fullmatch(r"/v1/contest/answers/([a-z0-9-]{2,64})", path)
        if method == "PATCH" and answer_match:
            return save_candidate_answer(event, answer_match.group(1))
        if method == "POST" and path == "/v1/contest/run":
            return run_candidate_tests(event)
        if method == "POST" and path == "/v1/contest/submit":
            return submit_candidate_contest(event)
        if path.startswith("/v1/admin/"):
            return admin_route(event, method, path)
        return response(404, {"message": "Не найдено."}, event)
    except ContestError as error:
        return response(error.status, {"message": error.message, "code": error.code}, event)
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

    expired = 0
    for item in repo.list_contests("started") + repo.list_contests("invited") + repo.list_contests("opened"):
        contest, etag = repo.get_contest_with_etag(item["contest_id"])
        if not contest or not etag:
            continue
        try:
            if expire_if_due(contest, now):
                repo.save_contest(contest, etag)
                expired += 1
        except ContestConflict:
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

        for contest in repo.pending_contest_notifications(now):
            application = repo.get_application(contest["application_id"])
            if not application or application.get("status") == "deleting":
                continue
            invitation_status = contest.get("invitation_notification_status", "not_requested")
            completion_status = contest.get("completion_notification_status", "not_requested")
            attempts = int(contest.get("notify_attempts", 0)) + 1
            try:
                if invitation_status == "pending":
                    if application.get("email"):
                        mailer.send_contest_invitation(application, contest, invite_url(contest))
                    invitation_status = "sent" if application.get("email") else "not_requested"
                if completion_status == "pending":
                    mailer.send_contest_completion(application, contest)
                    completion_status = "sent"
                delivered += 1
            except Exception:
                pass
            delay_minutes = min(24 * 60, 5 * (2 ** min(attempts, 8)))
            repo.update_contest_notifications(
                contest["contest_id"],
                invitation_status=invitation_status,
                completion_status=completion_status,
                attempts=attempts,
                next_attempt_at=now + timedelta(minutes=delay_minutes),
            )
    cleaned += repo.purge_retained_contests(now)
    return {
        "statusCode": 200,
        "body": json.dumps({"cleaned": cleaned, "expired": expired, "delivered": delivered}),
    }
