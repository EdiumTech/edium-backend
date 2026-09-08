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
        self._send(
            application["email"],
            "Заявка в команду Edium получена",
            f"{application['first_name']}, спасибо! Заявка получена.\n\n"
            "Если увидим подходящее направление для сотрудничества, свяжемся с тобой.\n",
            f"candidate:{application['application_id']}:confirmation",
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
