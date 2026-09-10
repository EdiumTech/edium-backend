import json
import os
import urllib.error
import urllib.request


class Mailer:
    def __init__(self):
        self.mode = os.getenv("EMAIL_MODE", "disabled")
        self.team_email = os.getenv("TEAM_EMAIL", "")
        self.herald_url = os.getenv(
            "HERALD_EMAIL_URL", "https://api.edium.online/herald/v1/emails"
        )
        self.herald_api_key = os.getenv("HERALD_API_KEY", "")
        self.admin_url = os.getenv("ADMIN_URL", "https://edium.online/join/admin/")
        self.contest_url = os.getenv("CONTEST_URL", "https://edium.online/join/contest/")

    @property
    def enabled(self) -> bool:
        return self.mode == "herald" and bool(
            self.team_email and self.herald_url and self.herald_api_key
        )

    def send_team(self, application: dict) -> None:
        direction = application.get("direction") or "не указано"
        link = f"{self.admin_url}#application={application['application_id']}"
        self._send(
            self.team_email,
            "Новая заявка в команду Edium",
            f"{application['first_name']} {application['last_name']}\n"
            f"Направление: {direction}\n\n"
            f"Открыть закрытую карточку:\n{link}\n",
            f"candidate:{application['application_id']}:team",
        )

    def send_candidate(self, application: dict) -> None:
        direction = application.get("direction") or "не указано"
        self._send(
            application["email"],
            "Мы получили твою заявку в Edium",
            f"Привет, {application['first_name']}!\n\n"
            "Спасибо за интерес к Edium — твоя заявка у нас.\n"
            f"Направление: {direction}.\n\n"
            "Что дальше:\n"
            "— команда посмотрит резюме и ответы;\n"
            "— если опыт подойдёт под одну из текущих задач, мы напишем или позвоним "
            "по указанным контактам.\n\n"
            "Обычно первичный просмотр занимает несколько рабочих дней. "
            "Пока ничего дополнительно отправлять не нужно.\n\n"
            "До связи!\n"
            "Команда Edium\n",
            f"candidate:{application['application_id']}:confirmation",
        )

    def send_contest_invitation(self, application: dict, contest: dict, invite_url: str) -> None:
        if not application.get("email"):
            return
        start_before = contest["start_before"].astimezone().strftime("%d.%m.%Y %H:%M %Z")
        self._send(
            application["email"],
            "Приглашение на контест Edium",
            f"{application['first_name']}, приглашаем тебя пройти небольшой программный контест Edium.\n\n"
            f"Продолжительность: {contest['duration_minutes']} минут.\n"
            f"Начать можно до: {start_before}.\n"
            "Таймер запустится только после того, как ты прочитаешь правила и явно подтвердишь начало.\n\n"
            f"Персональная ссылка:\n{invite_url}\n",
            f"contest:{contest['contest_id']}:invitation:{contest.get('invitation_send_version', 1)}",
        )

    def send_contest_completion(self, application: dict, contest: dict) -> None:
        status = "завершён кандидатом" if contest["state"] == "submitted" else "завершён по времени"
        link = f"{self.admin_url}#application={application['application_id']}"
        self._send(
            self.team_email,
            f"Контест Edium: {application['first_name']} {application['last_name']}",
            f"Контест кандидата {application['first_name']} {application['last_name']} {status}.\n\n"
            f"Открыть результат в закрытом разделе:\n{link}\n",
            f"contest:{contest['contest_id']}:completion:{contest['state']}",
        )

    def _send(
        self, recipient: str, subject: str, body: str, idempotency_key: str
    ) -> None:
        payload = json.dumps(
            {"to": recipient, "subject": subject, "text_body": body},
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            self.herald_url,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.herald_api_key}",
                "Idempotency-Key": idempotency_key,
                "Content-Type": "application/json; charset=utf-8",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                if response.status != 202:
                    raise RuntimeError(f"Herald returned HTTP {response.status}")
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"Herald returned HTTP {error.code}") from error
        except urllib.error.URLError as error:
            raise RuntimeError("Herald is unavailable") from error
