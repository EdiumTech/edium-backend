import re
from dataclasses import dataclass
from email.utils import parseaddr
from urllib.parse import urlparse


MAX_FILE_SIZE = 10 * 1024 * 1024
PDF_TYPE = "application/pdf"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
ALLOWED_TYPES = {PDF_TYPE: ".pdf", DOCX_TYPE: ".docx"}
STATUSES = {"new", "reviewing", "contacted", "accepted", "rejected"}


@dataclass
class ValidationError(Exception):
    message: str
    field_errors: dict[str, str]


def clean_text(value, *, maximum: int, required: bool = False) -> str | None:
    if value is None:
        value = ""
    if not isinstance(value, str):
        return None
    value = " ".join(value.strip().split())
    if required and not value:
        return None
    if len(value) > maximum:
        return None
    return value or None


def normalize_telegram(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if re.match(r"^https?://", candidate, re.I):
        parsed = urlparse(candidate)
        if parsed.hostname and parsed.hostname.lower() in {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}:
            candidate = next((part for part in parsed.path.split("/") if part), "")
        else:
            return None
    elif candidate.lower().startswith(("t.me/", "telegram.me/")):
        candidate = candidate.split("/", 1)[1].split("/", 1)[0]
    candidate = candidate.removeprefix("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", candidate):
        return None
    return f"@{candidate}"


def normalize_phone(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value.startswith("00"):
        value = "+" + value[2:]
    if not value.startswith("+"):
        return None
    normalized = "+" + re.sub(r"[\s().-]", "", value[1:])
    if not re.fullmatch(r"\+[1-9]\d{6,14}", normalized):
        return None
    return normalized


def valid_email(value: object) -> str | None:
    value = clean_text(value, maximum=254)
    if not value:
        return None
    _, parsed = parseaddr(value)
    if parsed != value or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        return None
    return value.lower()


def valid_url(value: object) -> str | None:
    value = clean_text(value, maximum=500)
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        return None
    return value


def validate_upload(payload: object) -> dict:
    errors: dict[str, str] = {}
    if not isinstance(payload, dict):
        raise ValidationError("Некорректный запрос.", {"resume": "Добавь резюме заново."})
    file_name = clean_text(payload.get("fileName"), maximum=180, required=True)
    media_type = payload.get("mediaType")
    size = payload.get("fileSize")
    if not file_name:
        errors["resume"] = "У файла должно быть корректное имя."
    if media_type not in ALLOWED_TYPES:
        errors["resume"] = "Поддерживаются только PDF и DOCX."
    if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_FILE_SIZE:
        errors["resume"] = "Файл должен быть непустым и не больше 10 МБ."
    if file_name and media_type and not file_name.lower().endswith(ALLOWED_TYPES[media_type]):
        errors["resume"] = "Расширение файла не соответствует выбранному формату."
    if payload.get("company"):
        raise ValidationError("Не удалось принять запрос.", {})
    if errors:
        raise ValidationError("Проверь резюме.", errors)
    return {"file_name": file_name, "media_type": media_type, "size": size}


def validate_application(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise ValidationError("Некорректный запрос.", {})
    errors: dict[str, str] = {}
    result = {
        "upload_id": clean_text(payload.get("uploadId"), maximum=64, required=True),
        "first_name": clean_text(payload.get("firstName"), maximum=80, required=True),
        "last_name": clean_text(payload.get("lastName"), maximum=80, required=True),
        "telegram": normalize_telegram(payload.get("telegram")),
        "phone": normalize_phone(payload.get("phone")),
        "email": valid_email(payload.get("email")) if payload.get("email") else None,
        "direction": clean_text(payload.get("direction"), maximum=120),
        "motivation": payload.get("motivation", "").strip() if isinstance(payload.get("motivation"), str) else "",
        "portfolio_url": valid_url(payload.get("portfolioUrl")) if payload.get("portfolioUrl") else None,
    }
    if not result["upload_id"] or not re.fullmatch(r"[0-9a-f-]{36}", result["upload_id"]):
        errors["resume"] = "Сессия загрузки истекла. Выбери файл заново."
    if not result["first_name"]:
        errors["firstName"] = "Укажи имя короче 80 символов."
    if not result["last_name"]:
        errors["lastName"] = "Укажи фамилию короче 80 символов."
    if not result["telegram"]:
        errors["telegram"] = "Укажи корректный @username или ссылку t.me."
    if not result["phone"]:
        errors["phone"] = "Укажи телефон в международном формате."
    if payload.get("email") and not result["email"]:
        errors["email"] = "Проверь адрес электронной почты."
    if not 40 <= len(result["motivation"]) <= 4000:
        errors["motivation"] = "Письмо должно содержать от 40 до 4000 символов."
    if payload.get("portfolioUrl") and not result["portfolio_url"]:
        errors["portfolioUrl"] = "Укажи безопасную ссылку http:// или https://."
    if payload.get("company"):
        raise ValidationError("Не удалось принять запрос.", {})
    if errors:
        raise ValidationError("Проверь поля формы.", errors)
    return result
