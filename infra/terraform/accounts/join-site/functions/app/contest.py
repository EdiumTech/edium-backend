import base64
import copy
import hashlib
import hmac
import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from contest_content import LANGUAGE, TASKS, TASK_SET_VERSION, task_ids


CONTEST_STATES = {"invited", "opened", "started", "submitted", "expired", "revoked"}
FINAL_STATES = {"submitted", "expired", "revoked"}
MAX_SOURCE_LENGTH = 65536
MAX_EXPLANATION_LENGTH = 2400
RETENTION_DAYS = 180
TASKS_BY_ID = {task["id"]: task for task in TASKS}


class ContestError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def invitation_token(contest_id: str, token_key: str) -> str:
    identifier = uuid.UUID(contest_id).bytes
    verifier = hmac.new(token_key.encode(), b"edium-contest:" + identifier, hashlib.sha256).digest()
    return _b64url(identifier + verifier)


def invitation_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def contest_id_from_token(token: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{64}", token or ""):
        raise ContestError("invalid_invite", "Ссылка на контест недействительна.", 404)
    try:
        raw = _unb64url(token)
        if len(raw) != 48:
            raise ValueError()
        return str(uuid.UUID(bytes=raw[:16]))
    except (ValueError, TypeError):
        raise ContestError("invalid_invite", "Ссылка на контест недействительна.", 404)


def token_matches(contest: dict, token: str, token_key: str) -> bool:
    expected = invitation_token(contest["contest_id"], token_key)
    return hmac.compare_digest(expected, token) and hmac.compare_digest(
        contest["token_hash"], invitation_token_hash(token)
    )


def new_contest(application_id: str, duration_minutes: int, start_before: datetime, now: datetime | None = None) -> tuple[dict, str]:
    now = now or utcnow()
    if duration_minutes < 15 or duration_minutes > 240:
        raise ContestError("invalid_duration", "Продолжительность должна быть от 15 до 240 минут.")
    if start_before <= now or start_before > now + timedelta(days=30):
        raise ContestError("invalid_start_before", "Срок начала должен быть в пределах следующих 30 дней.")
    # One stable key per application makes concurrent invitation creation safe:
    # both requests compete on the same conditional Object Storage write.
    contest_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"edium-contest:{application_id}"))
    # The key is supplied by the caller after creation, so the stored record never contains the raw token.
    record = {
        "contest_id": contest_id,
        "application_id": application_id,
        "task_set_version": TASK_SET_VERSION,
        "language": LANGUAGE,
        "state": "invited",
        "duration_minutes": duration_minutes,
        "start_before": start_before,
        "created_at": now,
        "updated_at": now,
        "opened_at": None,
        "started_at": None,
        "deadline_at": None,
        "submitted_at": None,
        "expired_at": None,
        "revoked_at": None,
        "extended_at": None,
        "purge_after": None,
        "extension_count": 0,
        "revision": 1,
        "answers": {},
        "review": {"tasks": {}, "conclusion": ""},
        "audit": [{"action": "invited", "at": now}],
        "invitation_notification_status": "pending",
        "invitation_send_version": 1,
        "completion_notification_status": "not_requested",
        "notify_attempts": 0,
        "next_notify_at": now,
    }
    return record, contest_id


def attach_token_hash(contest: dict, token_key: str) -> str:
    token = invitation_token(contest["contest_id"], token_key)
    contest["token_hash"] = invitation_token_hash(token)
    return token


def expire_if_due(contest: dict, now: datetime) -> bool:
    if contest["state"] in {"invited", "opened"} and now >= contest["start_before"]:
        _finish(contest, "expired", now)
        return True
    if contest["state"] == "started" and now >= contest["deadline_at"]:
        _finish(contest, "expired", now)
        return True
    return False


def open_contest(contest: dict, now: datetime) -> bool:
    if expire_if_due(contest, now):
        return True
    if contest["state"] == "invited":
        contest["state"] = "opened"
        contest["opened_at"] = now
        _touch(contest, "opened", now)
        return True
    return False


def start_contest(contest: dict, now: datetime) -> bool:
    if expire_if_due(contest, now):
        return True
    if contest["state"] == "started":
        return False
    if contest["state"] in FINAL_STATES:
        raise ContestError(contest["state"], "Контест уже завершён или отозван.", 409)
    contest["state"] = "started"
    contest["started_at"] = now
    contest["deadline_at"] = now + timedelta(minutes=int(contest["duration_minutes"]))
    _touch(contest, "started", now)
    return True


def save_answer(contest: dict, task_id: str, payload: dict, expected_revision: int, now: datetime) -> bool:
    if expire_if_due(contest, now):
        raise ContestError("expired", "Время истекло. Сохранена последняя серверная версия.", 410)
    if contest["state"] != "started":
        raise ContestError("not_started", "Сначала запусти контест.", 409)
    if expected_revision != int(contest["revision"]):
        raise ContestError("revision_conflict", "В другой вкладке уже сохранена более новая версия.", 409)
    if task_id not in task_ids():
        raise ContestError("unknown_task", "Задача не найдена.", 404)
    source = payload.get("source")
    explanation = payload.get("explanation", "")
    link = payload.get("url") or None
    if not isinstance(source, str) or len(source) > MAX_SOURCE_LENGTH:
        raise ContestError("invalid_source", "Код должен быть короче 64 КБ.")
    task = TASKS_BY_ID[task_id]
    maximum = min(int(task.get("maxExplanation", MAX_EXPLANATION_LENGTH)), MAX_EXPLANATION_LENGTH)
    if not isinstance(explanation, str) or len(explanation) > maximum:
        raise ContestError("invalid_explanation", f"Описание решения должно быть короче {maximum} символов.")
    if link and not valid_url(link):
        raise ContestError("invalid_url", "Укажи корректную ссылку http или https.")
    previous = contest["answers"].get(task_id)
    next_answer = {"source": source, "explanation": explanation, "url": link, "updated_at": now}
    if previous and all(previous.get(key) == next_answer.get(key) for key in ("source", "explanation", "url")):
        return False
    contest["answers"][task_id] = next_answer
    _touch(contest, f"answer_saved:{task_id}", now, audit=False)
    return True


def submit_contest(contest: dict, now: datetime) -> bool:
    if expire_if_due(contest, now):
        return True
    if contest["state"] == "submitted":
        return False
    if contest["state"] != "started":
        raise ContestError("not_started", "Контест нельзя отправить в текущем состоянии.", 409)
    for task in TASKS:
        answer = contest.get("answers", {}).get(task["id"], {})
        if not str(answer.get("source", "")).strip():
            raise ContestError("incomplete", f"Добавь решение задачи «{task['title']}». ", 409)
        minimum = int(task.get("minExplanation", 0))
        if len(str(answer.get("explanation", "")).strip()) < minimum:
            raise ContestError("incomplete", f"Дополни объяснение задачи «{task['title']}» минимум до {minimum} символов.", 409)
    _finish(contest, "submitted", now)
    return True


def revoke_contest(contest: dict, now: datetime) -> bool:
    if contest["state"] == "revoked":
        return False
    if contest["state"] in {"submitted", "expired"}:
        raise ContestError("already_finished", "Завершённый контест нельзя отозвать.", 409)
    contest["state"] = "revoked"
    contest["revoked_at"] = now
    contest["purge_after"] = now + timedelta(days=RETENTION_DAYS)
    _touch(contest, "revoked", now)
    return True


def extend_contest(contest: dict, minutes: int, now: datetime) -> bool:
    if minutes < 15 or minutes > 10080:
        raise ContestError("invalid_extension", "Продление должно быть от 15 минут до 7 дней.")
    if int(contest.get("extension_count", 0)) >= 1:
        raise ContestError("already_extended", "Контест уже продлевали один раз.", 409)
    if contest["state"] in FINAL_STATES:
        raise ContestError("already_finished", "Завершённый контест нельзя продлить.", 409)
    field = "deadline_at" if contest["state"] == "started" else "start_before"
    contest[field] = contest[field] + timedelta(minutes=minutes)
    contest["extension_count"] = 1
    contest["extended_at"] = now
    _touch(contest, "extended", now)
    return True


def update_review(contest: dict, payload: dict, now: datetime) -> None:
    tasks = payload.get("tasks", {})
    conclusion = payload.get("conclusion", "")
    if not isinstance(tasks, dict) or not isinstance(conclusion, str) or len(conclusion) > 6000:
        raise ContestError("invalid_review", "Проверь заметки и общий вывод.")
    cleaned = {}
    for task_id, value in tasks.items():
        if task_id not in task_ids() or not isinstance(value, dict):
            raise ContestError("invalid_review", "Неизвестная задача в оценке.")
        note = value.get("note", "")
        score = value.get("score")
        if not isinstance(note, str) or len(note) > 4000 or score not in {None, 1, 2, 3, 4, 5}:
            raise ContestError("invalid_review", "Оценка должна быть от 1 до 5, заметка — до 4000 символов.")
        cleaned[task_id] = {"note": note, "score": score}
    contest["review"] = {"tasks": cleaned, "conclusion": conclusion}
    _touch(contest, "review_updated", now)


def mark_invitation_pending(contest: dict, now: datetime) -> None:
    if contest["state"] in FINAL_STATES:
        raise ContestError("already_finished", "Приглашение уже недоступно.", 409)
    contest["invitation_notification_status"] = "pending"
    contest["invitation_send_version"] = int(contest.get("invitation_send_version", 1)) + 1
    contest["notify_attempts"] = 0
    contest["next_notify_at"] = now
    _touch(contest, "invitation_resent", now)


def valid_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and len(value) <= 1000
    except (TypeError, ValueError):
        return False


def candidate_view(contest: dict) -> dict:
    return {
        "id": contest["contest_id"],
        "state": contest["state"],
        "language": contest["language"],
        "taskSetVersion": contest["task_set_version"],
        "durationMinutes": contest["duration_minutes"],
        "startBefore": contest["start_before"],
        "serverNow": utcnow(),
        "openedAt": contest.get("opened_at"),
        "startedAt": contest.get("started_at"),
        "deadlineAt": contest.get("deadline_at"),
        "submittedAt": contest.get("submitted_at"),
        "expiredAt": contest.get("expired_at"),
        "revision": contest["revision"],
        "answers": contest.get("answers", {}),
    }


def _touch(contest: dict, action: str, now: datetime, audit: bool = True) -> None:
    contest["revision"] = int(contest.get("revision", 0)) + 1
    contest["updated_at"] = now
    if audit:
        contest.setdefault("audit", []).append({"action": action, "at": now})


def _finish(contest: dict, state: str, now: datetime) -> None:
    contest["state"] = state
    contest[f"{state}_at"] = now
    contest["purge_after"] = now + timedelta(days=RETENTION_DAYS)
    contest["completion_notification_status"] = "pending"
    contest["next_notify_at"] = now
    _touch(contest, state, now)


def clone(contest: dict) -> dict:
    return copy.deepcopy(contest)
