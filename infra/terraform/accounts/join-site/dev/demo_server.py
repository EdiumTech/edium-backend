"""Loopback-only demo of the real API, domain rules and language dispatcher.

Everything except the API/domain/runner implementation is disposable in-memory
demo data. Cloud storage, metadata credentials and email are never constructed.
"""

import argparse
import copy
import html
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import types
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
import uuid


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = "http://127.0.0.1:4173"
ADMIN_KEY = "demo-admin"
DEMO_DIRECTIONS = [
    ("backend", "Бэкенд", "Аня"),
    ("frontend", "Фронтенд", "Саша"),
    ("mobile", "Мобильная разработка", "Оля"),
    ("ai", "AI/ML", "Марк"),
    ("systems", "Системная разработка", "Лена"),
]


class ContestConflict(Exception):
    pass


class CloudDisabled:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("Cloud services are disabled in the localhost demo")


def load_api():
    # Install explicit dependency substitutes before importing the real handler.
    # This makes the demo independent of boto3 and production credentials.
    substitutes = {
        "repository": {"Repository": CloudDisabled, "ContestConflict": ContestConflict},
        "storage": {"ResumeStorage": CloudDisabled, "InvalidResume": type("InvalidResume", (Exception,), {})},
        "mailer": {"Mailer": CloudDisabled},
        "botocore": {},
        "botocore.exceptions": {"ClientError": type("ClientError", (Exception,), {})},
    }
    for name, attributes in substitutes.items():
        module = types.ModuleType(name)
        module.__dict__.update(attributes)
        sys.modules[name] = module
    os.environ.update({
        "ADMIN_TOKEN": ADMIN_KEY,
        "IP_HASH_SALT": "localhost-demo-rate-salt",
        "ALLOWED_ORIGINS": FRONTEND,
        "CONTEST_TOKEN_KEY": "localhost-demo-invitation-key",
        "CONTEST_URL": f"{FRONTEND}/join/contest/",
    })
    sys.path.insert(0, str(ROOT / "functions" / "app"))
    return importlib.import_module("handler")


class MemoryRepository:
    def __init__(self):
        self.applications = {}
        self.contests = {}
        self.versions = {}
        self.rate_counters = {}
        self.lock = threading.RLock()

    def get_application(self, application_id):
        with self.lock:
            return copy.deepcopy(self.applications.get(application_id))

    def list_applications(self, status=None):
        with self.lock:
            rows = [copy.deepcopy(item) for item in self.applications.values()
                    if item["status"] != "deleting" and (not status or item["status"] == status)]
        return sorted(rows, key=lambda item: item["created_at"], reverse=True)

    def create_contest(self, contest):
        contest_id = contest["contest_id"]
        with self.lock:
            if contest_id in self.contests:
                return False
            self.contests[contest_id] = copy.deepcopy(contest)
            self.versions[contest_id] = 1
            return True

    def get_contest_with_etag(self, contest_id):
        with self.lock:
            if contest_id not in self.contests:
                return None, None
            return copy.deepcopy(self.contests[contest_id]), str(self.versions[contest_id])

    def save_contest(self, contest, etag):
        contest_id = contest["contest_id"]
        with self.lock:
            if etag != str(self.versions.get(contest_id)):
                raise ContestConflict()
            self.contests[contest_id] = copy.deepcopy(contest)
            self.versions[contest_id] += 1

    def find_contest_by_application(self, application_id):
        with self.lock:
            return next((copy.deepcopy(item) for item in self.contests.values()
                         if item["application_id"] == application_id), None)

    def list_contests(self, state=None):
        with self.lock:
            return [copy.deepcopy(item) for item in self.contests.values()
                    if not state or item["state"] == state]

    def allow_contest_request(self, bucket_key, expires_at, limit):
        now = datetime.now(timezone.utc)
        with self.lock:
            self.rate_counters = {key: value for key, value in self.rate_counters.items()
                                  if value[1] > now}
            count = self.rate_counters.get(bucket_key, (0, expires_at))[0]
            if count >= limit:
                return False
            self.rate_counters[bucket_key] = (count + 1, expires_at)
            return True

    allow_request = allow_contest_request

    def update_status(self, application_id, status, now):
        with self.lock:
            self.applications[application_id].update(status=status, updated_at=now)

    def mark_deleting(self, application_id, now):
        self.update_status(application_id, "deleting", now)
        return self.get_application(application_id)

    def delete_contests_for_application(self, application_id):
        with self.lock:
            for contest in self.list_contests():
                if contest["application_id"] == application_id:
                    self.contests.pop(contest["contest_id"], None)
                    self.versions.pop(contest["contest_id"], None)

    def purge_application(self, application_id):
        with self.lock:
            self.applications.pop(application_id, None)


class DemoStorage:
    def __init__(self, port):
        self.port = port

    def download_url(self, *, object_key, original_name):
        return f"http://127.0.0.1:{self.port}/dev/resume"

    def delete(self, object_key):
        pass


def configure_runner(api):
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required for the QuickJS runner")
    if not (ROOT / "runner" / "node_modules" / "quickjs-emscripten").exists():
        raise RuntimeError("Install the runner dependencies first: cd runner && npm ci")
    slots = threading.BoundedSemaphore(2)

    class LocalRunner(api.SandboxRunner):
        @property
        def supported_languages(self):
            # Preview invitations can expose both mobile editors before the
            # local toolchains are installed. Execution still fails explicitly.
            return {"javascript", "kotlin", "swift", "python", "go"}

        def run(self, task_id, source, task_set_version, language="javascript"):
            if not slots.acquire(blocking=False):
                raise api.RunnerUnavailable("local_runner_busy")
            try:
                result = subprocess.run(
                    [node, str(ROOT / "dev" / "run-task.js")],
                    input=json.dumps({"taskId": task_id, "source": source, "taskSetVersion": task_set_version, "language": language}),
                    text=True, capture_output=True, timeout=120, check=True,
                    cwd=ROOT / "runner",
                )
                payload = json.loads(result.stdout)
                if payload == {"error": "runtime_unavailable"}:
                    raise api.ContestError("runtime_unavailable", "Среда выполнения этого языка пока недоступна локально.", 503)
                tasks = api.public_tasks(task_set_version)
                expected_total = next(task["testCount"] for task in tasks if task["id"] == task_id)
                return self._sanitize(payload, expected_total)
            except (subprocess.SubprocessError, ValueError, StopIteration, KeyError) as error:
                raise api.RunnerUnavailable("local_runner_unavailable") from error
            finally:
                slots.release()

    api.SandboxRunner = LocalRunner


def api_event(method, path, payload=None, headers=None):
    parsed = urlsplit(path)
    return {
        "httpMethod": method,
        "path": parsed.path,
        "queryStringParameters": {key: values[-1] for key, values in parse_qs(parsed.query).items()},
        "headers": {"Origin": FRONTEND, **(headers or {})},
        "body": json.dumps(payload or {}),
        "requestContext": {"identity": {"sourceIp": "127.0.0.1"}},
    }


def make_demo(port=8787):
    api = load_api()
    repo = MemoryRepository()
    api._repository = repo
    api._storage = DemoStorage(port)
    configure_runner(api)
    now = datetime.now(timezone.utc)
    links = []
    rows = [*DEMO_DIRECTIONS, ("new-application", "Бэкенд", "Костя")]
    for index, (slug, direction, first_name) in enumerate(rows):
        application_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"edium-local-demo:{slug}"))
        repo.applications[application_id] = {
            "application_id": application_id,
            "created_at": now - timedelta(minutes=index), "updated_at": now,
            "first_name": first_name, "last_name": "Демо",
            "telegram": f"@demo_{slug.replace('-', '_')}", "phone": "+79990000000",
            "email": f"{slug}@example.test", "direction": direction,
            "motivation": "Хочу делать полезные штуки для Edium. Это синтетическая заявка для локального просмотра.",
            "portfolio_url": None, "resume_object_key": f"demo/{application_id}",
            "resume_name": "demo-resume.txt", "resume_size": 100, "resume_media_type": "text/plain",
            "status": "reviewing" if index < len(DEMO_DIRECTIONS) else "new",
        }
        if slug == "new-application":
            continue
        result = api.api(api_event(
            "POST", f"/v1/admin/applications/{application_id}/contest",
            {"direction": direction, "durationMinutes": 90, "startWithinDays": 7},
            {"Authorization": f"Bearer {ADMIN_KEY}"},
        ))
        if result["statusCode"] != 201:
            raise RuntimeError(f"Unable to seed demo direction: {slug}")
        contest = json.loads(result["body"])["contest"]
        languages = sorted({language for task in contest["tasks"] for language in task["languages"]})
        links.append({"direction": direction, "url": contest["inviteUrl"], "languages": languages,
                      "applicationId": application_id, "slug": slug})
    return api, repo, links


def demo_index(links):
    captions = {
        "backend": "Вебхуки, сбои сервиса и квоты. Пусть сервер переживёт ещё один понедельник.",
        "frontend": "Карточки, контраст и длинные кнопки. Сделай интерфейс удобным для людей.",
        "mobile": "Офлайн-синхронизация, разрешения и загрузки. Kotlin и Swift пригодятся оба.",
        "ai": "Датасеты, проверка ответов и контекст для AI. Помоги модели подружиться с реальностью.",
        "systems": "Кнопки устройств, очереди команд и информационные экраны. Железо тоже иногда устраивает понедельник.",
    }
    language_labels = {"javascript": "JavaScript", "kotlin": "Kotlin", "swift": "Swift", "python": "Python", "go": "Go"}
    cards = []
    for index, item in enumerate(links, 1):
        cards.append(
            f'<a class="track" href="{html.escape(item["url"], quote=True)}">'
            f'<div class="track-meta"><span>{index:02d}</span><span class="language">{html.escape(" + ".join(language_labels[value] for value in item["languages"]))}</span></div>'
            f'<h2>{html.escape(item["direction"])}</h2>'
            f'<p>{html.escape(captions[item["slug"]])}</p>'
            '<span class="track-link">Открыть контест <span aria-hidden="true">↗</span></span></a>'
        )
    return f'''<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Попробуй себя в Edium · локальное демо</title>
  <style>
    :root {{ color-scheme: light; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #181818; background: #f7f7f5; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; }}
    a {{ color: inherit; }}
    .shell {{ max-width: 1140px; margin: 0 auto; padding: 28px 32px 36px; }}
    header {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; padding-bottom: 28px; border-bottom: 1px solid #e4e7dd; }}
    .brand {{ display: inline-flex; align-items: center; gap: 10px; font-size: 28px; font-weight: 750; letter-spacing: -1.1px; }}
    .brand-icon {{ width: 34px; height: 34px; display: grid; place-items: center; border-radius: 12px; color: #181818; background: #dfff45; font-size: 27px; font-weight: 600; }}
    .preview {{ padding: 8px 12px; border: 1px solid #e0e5d9; border-radius: 30px; color: #697260; font-size: 12px; }}
    .hero {{ padding: 54px 0 34px; max-width: 790px; }}
    .eyebrow {{ margin: 0 0 16px; color: #6558d3; font-size: 12px; font-weight: 650; text-transform: uppercase; letter-spacing: 1.5px; }}
    h1 {{ margin: 0; max-width: 740px; font-size: clamp(32px, 4.7vw, 54px); font-weight: 650; line-height: 1.09; letter-spacing: -2.2px; }}
    .hero p:last-child {{ margin: 20px 0 0; max-width: 620px; color: #6a6f63; font-size: 16px; line-height: 1.6; }}
    .tracks {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }}
    .track {{ display: flex; flex-direction: column; min-height: 232px; padding: 24px; text-decoration: none; background: #fff; border: 1px solid #e5e9de; border-radius: 20px; transition: border-color .16s ease, transform .16s ease, box-shadow .16s ease; }}
    .track:nth-child(3n + 1) {{ background: #f0f4e8; border-color: #e3e9d8; }}
    .track:nth-child(3n + 2) {{ background: #f4f1f9; border-color: #e9e3f0; }}
    .track:hover {{ border-color: #a0ae8d; transform: translateY(-3px); box-shadow: 0 8px 24px #27321b0a; }}
    .track:focus-visible, .admin-button:focus-visible {{ outline: 3px solid #6558d3; outline-offset: 4px; }}
    .track-meta {{ display: flex; align-items: center; justify-content: space-between; color: #727866; font-size: 12px; }}
    .language {{ display: inline-block; padding: 4px 7px; border: 1px solid #d5dbcd; border-radius: 7px; font-size: 10px; font-weight: 700; letter-spacing: .5px; }}
    .track h2 {{ margin: 21px 0 10px; font-size: 21px; font-weight: 650; letter-spacing: -.5px; }}
    .track p {{ flex: 1; margin: 0 0 22px; color: #6b7165; font-size: 14px; line-height: 1.5; }}
    .track-link {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; color: #4c5d3d; font-size: 13px; font-weight: 650; }}
    .track-link span {{ font-size: 21px; line-height: 1; }}
    .admin {{ display: flex; align-items: center; justify-content: space-between; gap: 24px; margin-top: 24px; padding: 24px 26px; background: #fff; border: 1px solid #e4e7dd; border-radius: 20px; }}
    .admin h2 {{ margin: 0 0 7px; font-size: 17px; font-weight: 650; }}
    .admin p {{ margin: 0; color: #74796c; font-size: 13px; line-height: 1.5; }}
    code {{ display: inline-block; margin-left: 3px; padding: 2px 6px; background: #f0f3eb; border-radius: 5px; color: #3d4b31; font-size: 12px; }}
    .admin-button {{ flex-shrink: 0; display: inline-block; padding: 12px 19px; background: #181818; border-radius: 12px; color: #fff; font-size: 13px; text-decoration: none; }}
    footer {{ display: flex; justify-content: space-between; gap: 16px; margin-top: 25px; color: #898e81; font-size: 11px; line-height: 1.5; }}
    @media (max-width: 820px) {{ .tracks {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
    @media (max-width: 540px) {{ .shell {{ padding: 20px; }} header {{ padding-bottom: 20px; }} .hero {{ padding: 36px 0 28px; }} h1 {{ letter-spacing: -1.3px; }} .tracks {{ grid-template-columns: 1fr; }} .track {{ min-height: 215px; }} .admin {{ align-items: flex-start; flex-direction: column; }} footer {{ flex-direction: column; gap: 6px; }} }}
    @media (prefers-reduced-motion: reduce) {{ .track {{ transition: none; }} }}
  </style>
</head>
<body>
  <div class="shell">
    <header><div class="brand"><span class="brand-icon" aria-hidden="true">e</span>edium</div><span class="preview">Локальное демо</span></header>
    <main>
      <section class="hero" aria-labelledby="page-title"><p class="eyebrow">5 направлений · разные языки и задачи</p><h1 id="page-title">Рабочие будни.<br>Только чуть смешнее.</h1><p>Выбери направление и попробуй себя в команде Edium. Прикладные задачи, немного рабочего абсурда и все тесты сразу.</p></section>
      <section class="tracks" aria-label="Выбери направление">{"".join(cards)}</section>
      <section class="admin" aria-labelledby="admin-title"><div><h2 id="admin-title">Посмотреть со стороны команды</h2><p>Заявки, приглашения и решения кандидатов. Ключ для входа: <code>{ADMIN_KEY}</code></p></div><a class="admin-button" href="{FRONTEND}/join/admin/">Открыть HR-раздел ↗</a></section>
    </main>
    <footer><span>Синтетические заявки · настоящая проверка решений</span><span>Прогресс сбрасывается при перезапуске локального API</span></footer>
  </div>
</body>
</html>'''


def make_http_handler(api, links):
    class DemoHandler(BaseHTTPRequestHandler):
        server_version = "EdiumLocalDemo"

        def log_message(self, format, *args):
            # Candidate source, headers, invitation tokens and contact details
            # should not be printed by the HTTP access logger.
            pass

        def handle_request(self):
            origin = self.headers.get("Origin")
            if origin and origin != FRONTEND:
                self.send_error(403, "This demo accepts only the local frontend origin")
                return
            path = urlsplit(self.path).path
            if self.command == "GET" and path == "/":
                body = demo_index(links)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body.encode())
                return
            if self.command == "GET" and path == "/dev/resume":
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write("Это синтетическое резюме для локального просмотра Edium.\n".encode())
                return
            # This runner is a contest preview, not an application-upload server.
            if not (path.startswith("/v1/contest") or path.startswith("/v1/admin/")):
                self.send_error(404)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 <= size <= 131072:
                    self.send_error(413)
                    return
                body = self.rfile.read(size).decode("utf-8") if size else "{}"
            except (ValueError, UnicodeDecodeError):
                self.send_error(400)
                return
            event = api_event(self.command, self.path, headers=dict(self.headers))
            event["body"] = body
            result = api.api(event)
            self.send_response(result["statusCode"])
            for key, value in result["headers"].items():
                self.send_header(key, value)
            self.end_headers()
            if result["statusCode"] != 204:
                self.wfile.write(result["body"].encode("utf-8"))

        do_GET = do_POST = do_PATCH = do_DELETE = do_OPTIONS = handle_request

    return DemoHandler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--links", action="store_true", help="Print deterministic demo links without starting HTTP")
    args = parser.parse_args()
    api, _, links = make_demo(args.port)
    if args.links:
        print(json.dumps(links, ensure_ascii=False, indent=2))
        return
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_http_handler(api, links))
    server.daemon_threads = True
    print(f"Demo index: http://127.0.0.1:{args.port}/", flush=True)
    print(f"HR: {FRONTEND}/join/admin/ · key: {ADMIN_KEY}", flush=True)
    for item in links:
        print(f"{item['direction']}: {item['url']}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
